"""dwell_rollup_store — dwell 日別ロールアップのディスク永続キャッシュ Repository（gateway 層）。

ISSUE-092 ④: 永続化の物理 I/O（レイヤ責務違反・ISSUE-091 #5）を compute 層から本 gateway 層へ移設
した。旧 compute パスの互換再エクスポートシムは参照ゼロの孤児となり削除済み（ISSUE-479 F-4）。
クラス・公開シンボル・保存形式・原子的確定・fail-safe は移設前と完全に同一（byte 不変・回帰ゼロ）。

ISSUE-040(b): :mod:`market_profile_dwell` に混在していた 3 責務（dwell 集計数学 / ディスクキャッシュ
Repository / tick 読込）のうち、**ディスクキャッシュ Repository** を本モジュールへ切り出した（SRP）。
本モジュールは「完了日（UTC 確定日）の固定グリッド日別ロールアップ ``{kmin, dwell[], cnt[]}`` を
``.npz`` として保存・読込・署名検証・無効化する」永続化の機構のみを担い、集計そのもの（何を dwell と
数えるか）は一切知らない。集計は :mod:`market_profile_dwell` が保持し、本 Store を経由して永続化する。

依存方向（同 adapter 層内・低リスク分割）:
    market_profile_dwell（集計数学 + 走査オーケストレーション） → market_profile_dwell_store（本 I/O）
    本 Store は :mod:`market_profile_dwell` を import しない（循環なし）。可変な設定（cache root /
    形式バージョン / 正準ティック列挙 day_parquet_files）は provider として注入で受け取る。これにより
    設定の**テスト注入（monkeypatch）経路を壊さず**（call-time にクロージャで読む）、Store は純 I/O に
    保つ。ISSUE-183 item5: cache root / 形式バージョンの単一情報源は
    :mod:`market_profile_api.gateway.cache_settings`（``DWELL_CACHE_ROOT`` / ``DWELL_CACHE_VERSION``）。

byte 不変（回帰ゼロ）: 保存形式（version/grid_w/empty/sig メタ + kmin + 可変長 dwell/cnt）・
tempfile→os.replace の原子的確定・fail-safe（破損/不整合は CACHE_MISS）は抽出前と同一挙動。
"""

from __future__ import annotations

import os as _os
import tempfile as _tempfile
from pathlib import Path as _Path
from typing import Any, Callable

import numpy as np
# ISSUE-183: pandas 依存は撤去（日始端算出は gateway/day_bounds の整数演算へ移行）。

# ISSUE-305: 記述子の型は境界モジュールから取る（合成側 cache_layout から取ると循環になる）。
from market_profile_api.cache_layout_descriptor import CacheLayout
# ISSUE-178: 層間 DTO（不変）。gateway（外側）が compute（内側）の DTO を import する＝依存方向は内向き。
from market_profile_api.compute.rollup_dto import DayRollup
# ISSUE-183: 日始端の算出は gateway 内の単一定義（``pd.Timestamp(...).normalize()`` と同値）。
from market_profile_api.gateway.day_bounds import next_utc_day_start, utc_day_start


class DwellRollupStore:
    """dwell 日別ロールアップのディスク永続キャッシュ Repository（純 I/O・fail-safe）。

    保存単位は完了日 1 日ぶんの固定グリッドロールアップ :class:`DayRollup`（不変 DTO・ISSUE-178）
    または ``None``（実データ無しの完了日）。読込は ``(status, sig)`` を返し、``status`` は
    :attr:`CACHE_MISS`（要再計算） / ``None``（実データ無し完了日） / :class:`DayRollup`。

    Args:
        root_provider: 注入 cache root を返す（``None`` で default_root_provider にフォールバック）。
            既定結線は ``lambda: cache_settings.DWELL_CACHE_ROOT``（ISSUE-183 item5）。テストは
            ``cache_settings.DWELL_CACHE_ROOT`` を差し替えることで tmp 隔離する。
        default_root_provider: 既定 cache root（``DATA_DIR/cache/market_profile_dwell``）を返す。
        grid_w: 固定価格グリッド幅(pt)。パスキー ``g<grid_w>`` と読込時の grid 整合検証に使う。
        cache_version_provider: 形式バージョンを返す（call-time 読取＝バージョン切替テストを温存）。
        day_parquet_files: 正準ティック日別 parquet 列挙関数（署名合成に使用・read-only）。
    """

    #: ディスク未ヒット/破損/不整合の番兵（``None``＝「実データ無しの完了日」と区別する）。
    CACHE_MISS = object()

    #: :meth:`_relative_parts` のうち世代 dir に当たる位置（0 起点）。GC の掃除単位＝版数 dir。
    #: ISSUE-172: 記述子（:meth:`layout`）はこの位置から導出し、書込パスと同一式を共有する。
    GEN_PART_INDEX = 1

    def __init__(
        self,
        *,
        root_provider: Callable[[], "Any | None"],
        default_root_provider: Callable[[], _Path],
        grid_w: float,
        cache_version_provider: Callable[[], int],
        day_parquet_files: Callable[..., Any],
    ) -> None:
        self._root_provider = root_provider
        self._default_root_provider = default_root_provider
        self._grid_w = float(grid_w)
        self._cache_version_provider = cache_version_provider
        self._day_parquet_files = day_parquet_files

    # ------------------------------------------------------------------ #
    # 配置（root / path）
    # ------------------------------------------------------------------ #
    def cache_root(self) -> _Path:
        """ディスクキャッシュの基点を返す（注入 root 優先・無ければ既定）。"""
        override = self._root_provider()
        if override is not None:
            return _Path(override)
        return self._default_root_provider()

    def _relative_parts(self, symbol: str, day_start: int) -> "tuple[str, ...]":
        """cache root からの相対パス segment 列 ``(<symbol>, v<version>, g<grid_w>, <day>.npz)``。

        ISSUE-172: 配置の**唯一の定義**。:meth:`cache_path`（書込・読込）と :meth:`layout`
        （GC 記述子）の双方が本メソッドから導出され、二重定義によるドリフトを構造的に排除する。
        """
        return (
            str(symbol),
            f"v{self._cache_version_provider()}",
            f"g{self._grid_w:g}",
            f"{int(day_start)}.npz",
        )

    def cache_path(self, symbol: str, day_start: int) -> _Path:
        """日別ロールアップの保存パス ``<root>/<symbol>/v<version>/g<grid_w>/<day_start>.npz``。

        キーに symbol・version・grid_w・day_start を含め混線を防ぐ。
        ISSUE-089: version をパスへ含める。旧レイアウト（g<grid_w> 直下）は版数を npz メタでしか
        持たず、新旧コードのプロセスが併走すると**同一ファイルを異版で書き合う**（旧 8000 サーバと
        新プロセスの間で実際に発生＝byte-parity 再赤化の直接原因）。版数ディレクトリ分離で
        世代間のファイル奪い合いを構造的に排除する（旧世代 dir は GC ツールの孤児対象）。
        """
        return self.cache_root().joinpath(*self._relative_parts(symbol, day_start))

    def layout(self) -> CacheLayout:
        """GC 向けの現行世代記述子（:class:`CacheLayout`）を返す（ISSUE-172）。

        世代 dir は :attr:`GEN_PART_INDEX` が指す版数 segment（``v<version>``）。旧レイアウト
        （``<sym>/g<grid_w>/`` 直下）も同階層に現れるため、同一の走査で孤児として列挙される。
        ``current`` は :meth:`_relative_parts` から導出するため、版数 bump に自動追随する。
        """
        gen = self._relative_parts("", 0)[self.GEN_PART_INDEX]
        return CacheLayout(
            name="dwell",
            root=self.cache_root(),
            gen_depth=self.GEN_PART_INDEX + 1,  # <sym>/<gen>
            current=frozenset({gen}),
            reason=f"dwell 旧世代（現行 {gen}）",
        )

    # ------------------------------------------------------------------ #
    # 保存 / 読込（.npz・原子的・fail-safe）
    # ------------------------------------------------------------------ #
    def save_day_rollup(self, path: _Path, roll: "DayRollup | None", sig: str = "") -> None:
        """ロールアップ（``None``=実データ無し完了日を含む）を ``.npz`` へ原子的に保存する。

        可変長 ``dwell``/``cnt`` と ``kmin`` を保持し、``version``/``grid_w``/``empty``/``sig`` メタを併記する。
        ``sig`` はソースティック署名（無効化用）。tmp へ書いてから :func:`os.replace` で確定し破損を残さない。
        """
        path.parent.mkdir(parents=True, exist_ok=True)
        common = dict(
            version=np.int64(self._cache_version_provider()),
            grid_w=np.float64(self._grid_w),
            sig=np.str_(sig),
        )
        if roll is None:
            arrs = dict(
                **common, empty=np.bool_(True),
                kmin=np.int64(0), dwell=np.zeros(0, dtype=float), cnt=np.zeros(0, dtype=float),
            )
        else:
            arrs = dict(
                **common, empty=np.bool_(False),
                kmin=np.int64(int(roll.kmin)),
                dwell=np.asarray(roll.dwell, dtype=float),
                cnt=np.asarray(roll.cnt, dtype=float),
            )
        fd, tmp_name = _tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".", suffix=".tmp.npz")
        _os.close(fd)
        tmp = _Path(tmp_name)
        try:
            with open(tmp, "wb") as fh:
                np.savez_compressed(fh, **arrs)
            _os.replace(tmp, path)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise

    def load_day_rollup(self, path: _Path) -> "tuple[Any, str]":
        """ディスクから日別ロールアップと署名を読む。未ヒット/破損/不整合は ``(CACHE_MISS, "")``。

        戻り値 ``(status, sig)``: status は :attr:`CACHE_MISS`（要再計算） / ``None``（実データ無しの
        完了日） / :class:`DayRollup`（不変 DTO）。sig は保存時のソースティック署名（旧形式は ""）。
        破損・形式/グリッド不整合は例外を握り潰し ``(CACHE_MISS, "")`` として再計算に委ねる（fail-safe）。
        """
        if not path.is_file():
            return self.CACHE_MISS, ""
        try:
            with np.load(path) as z:
                if int(z["version"]) != int(self._cache_version_provider()):
                    return self.CACHE_MISS, ""
                if float(z["grid_w"]) != float(self._grid_w):
                    return self.CACHE_MISS, ""
                sig = str(z["sig"]) if "sig" in z.files else ""
                if bool(z["empty"]):
                    return None, sig
                return DayRollup(
                    kmin=int(z["kmin"]),
                    dwell=np.asarray(z["dwell"], dtype=float),
                    cnt=np.asarray(z["cnt"], dtype=float),
                ), sig
        except Exception:
            return self.CACHE_MISS, ""

    # ------------------------------------------------------------------ #
    # 無効化（ソースティック署名）
    # ------------------------------------------------------------------ #
    def day_source_signature(self, symbol: str, day_start: int) -> str:
        """完了日 ``day_start`` のソースティック署名（日次 parquet の name:mtime:size を連結）。

        キャッシュ無効化に使う。完了日を空でキャッシュした後にティック parquet が届く/更新されると署名が
        変わり、呼び出し側が stale-empty を検出して再計算する。ファイル無し（休場等）は空文字。
        注入 ``day_parquet_files``（read-only・正準経路）と ``stat`` のみ＝ソース非改変・低コスト。
        ISSUE-078: セッション日 [start, end) は UTC 暦日を 2 日跨ぐ（境界=夏21:00/冬22:00 UTC・
        DST 25h 日でも終端は翌 UTC 日内）ため、start の UTC 日と翌 UTC 日の両 parquet を署名に含める。
        """
        # ISSUE-183: 列挙は UNIX 秒（int・UTC 日始端）契約。旧 ``pd.Timestamp(...).normalize()`` と同値。
        day = utc_day_start(day_start)
        parts: list[str] = []
        for p in self._day_parquet_files(day, next_utc_day_start(day), symbol=symbol):
            try:
                st = p.stat()
                parts.append(f"{p.name}:{int(st.st_mtime)}:{int(st.st_size)}")
            except OSError:
                parts.append(f"{p.name}:?")
        return "|".join(parts)
