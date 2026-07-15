"""market_profile_dwell_store — dwell 日別ロールアップのディスク永続キャッシュ Repository。

ISSUE-040(b): :mod:`market_profile_dwell` に混在していた 3 責務（dwell 集計数学 / ディスクキャッシュ
Repository / tick 読込）のうち、**ディスクキャッシュ Repository** を本モジュールへ切り出した（SRP）。
本モジュールは「完了日（UTC 確定日）の固定グリッド日別ロールアップ ``{kmin, dwell[], cnt[]}`` を
``.npz`` として保存・読込・署名検証・無効化する」永続化の機構のみを担い、集計そのもの（何を dwell と
数えるか）は一切知らない。集計は :mod:`market_profile_dwell` が保持し、本 Store を経由して永続化する。

依存方向（同 adapter 層内・低リスク分割）:
    market_profile_dwell（集計数学 + 走査オーケストレーション） → market_profile_dwell_store（本 I/O）
    本 Store は :mod:`market_profile_dwell` を import しない（循環なし）。可変な設定（cache root /
    形式バージョン / 正準ティック列挙 day_parquet_files）は provider として注入で受け取る。これにより
    本体側の module 変数 ``_CACHE_ROOT`` / ``_CACHE_VERSION`` / ``day_parquet_files`` の**テスト注入
    （monkeypatch）経路を壊さず**（call-time にクロージャで読む）、Store は純 I/O に保つ。

byte 不変（回帰ゼロ）: 保存形式（version/grid_w/empty/sig メタ + kmin + 可変長 dwell/cnt）・
tempfile→os.replace の原子的確定・fail-safe（破損/不整合は CACHE_MISS）は抽出前と同一挙動。
"""

from __future__ import annotations

import os as _os
import tempfile as _tempfile
from pathlib import Path as _Path
from typing import Any, Callable

import numpy as np
import pandas as pd


class DwellRollupStore:
    """dwell 日別ロールアップのディスク永続キャッシュ Repository（純 I/O・fail-safe）。

    保存単位は完了日 1 日ぶんの固定グリッドロールアップ ``{kmin:int, dwell:float[], cnt:float[]}``
    または ``None``（実データ無しの完了日）。読込は ``(status, sig)`` を返し、``status`` は
    :attr:`CACHE_MISS`（要再計算） / ``None``（実データ無し完了日） / ``dict``（ロールアップ）。

    Args:
        root_provider: 注入 cache root を返す（``None`` で default_root_provider にフォールバック）。
            本体側は ``lambda: market_profile_dwell._CACHE_ROOT`` を渡し、テストの tmp 注入を温存する。
        default_root_provider: 既定 cache root（``DATA_DIR/cache/market_profile_dwell``）を返す。
        grid_w: 固定価格グリッド幅(pt)。パスキー ``g<grid_w>`` と読込時の grid 整合検証に使う。
        cache_version_provider: 形式バージョンを返す（call-time 読取＝バージョン切替テストを温存）。
        day_parquet_files: 正準ティック日別 parquet 列挙関数（署名合成に使用・read-only）。
    """

    #: ディスク未ヒット/破損/不整合の番兵（``None``＝「実データ無しの完了日」と区別する）。
    CACHE_MISS = object()

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

    def cache_path(self, symbol: str, day_start: int) -> _Path:
        """日別ロールアップの保存パス ``<root>/<symbol>/v<version>/g<grid_w>/<day_start>.npz``。

        キーに symbol・version・grid_w・day_start を含め混線を防ぐ。
        ISSUE-089: version をパスへ含める。旧レイアウト（g<grid_w> 直下）は版数を npz メタでしか
        持たず、新旧コードのプロセスが併走すると**同一ファイルを異版で書き合う**（旧 8000 サーバと
        新プロセスの間で実際に発生＝byte-parity 再赤化の直接原因）。版数ディレクトリ分離で
        世代間のファイル奪い合いを構造的に排除する（旧世代 dir は GC ツールの孤児対象）。
        """
        return (self.cache_root() / str(symbol) / f"v{self._cache_version_provider()}"
                / f"g{self._grid_w:g}" / f"{int(day_start)}.npz")

    # ------------------------------------------------------------------ #
    # 保存 / 読込（.npz・原子的・fail-safe）
    # ------------------------------------------------------------------ #
    def save_day_rollup(self, path: _Path, roll: "dict | None", sig: str = "") -> None:
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
                kmin=np.int64(int(roll["kmin"])),
                dwell=np.asarray(roll["dwell"], dtype=float),
                cnt=np.asarray(roll["cnt"], dtype=float),
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
        完了日） / ``dict``（ロールアップ）。sig は保存時のソースティック署名（旧形式は ""）。
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
                return {
                    "kmin": int(z["kmin"]),
                    "dwell": np.asarray(z["dwell"], dtype=float),
                    "cnt": np.asarray(z["cnt"], dtype=float),
                }, sig
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
        day = pd.Timestamp(int(day_start), unit="s").normalize()
        parts: list[str] = []
        for p in self._day_parquet_files(day, day + pd.Timedelta(days=1), symbol=symbol):
            try:
                st = p.stat()
                parts.append(f"{p.name}:{int(st.st_mtime)}:{int(st.st_size)}")
            except OSError:
                parts.append(f"{p.name}:?")
        return "|".join(parts)
