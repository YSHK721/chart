"""zp_store — z(p) 日別成果物のディスク永続キャッシュ Repository（gateway 層・純 I/O・fail-safe）。

ISSUE-092 ④: 永続化の物理 I/O（レイヤ責務違反・ISSUE-091 #5）を compute 層から本 gateway 層へ移設
した（``compute/market_profile_zp_store.py`` は本モジュールへの薄い再エクスポートとして温存）。
クラス・公開シンボル・保存形式・原子的確定・fail-safe は移設前と完全に同一（byte 不変・回帰ゼロ）。

:class:`DwellRollupStore` と同パターンの Store（既存 Store は非改変）。保存単位は 2 種:
  - mgrid: 完了日の分 ffill close グリッド ``{closes[G], open}``（帰無ステップ行列のソース）。
  - znull: 完了日の z(p) 日別成果物 :class:`ZpRollup`（観測占有＋Null B モーメント・不変 DTO）。

配置（新ディレクトリのみ・既存キャッシュ非改変）:
  ``<root>/mgrid/<symbol>/<day_start>.npz``
  ``<root>/znull/<symbol>/g<grid_w>/L<hist>-M<reps>/<day_start>.npz``
znull のパスに帰無パラメータ（履歴日数 L・反復数 M）を含め、パラメータ変更時は別ディレクトリと
なり旧キャッシュと混線しない。保存は tempfile→os.replace の原子的確定、読込は版数/パラメータ
不一致・破損を握り潰して CACHE_MISS（fail-safe・再計算に委ねる）。ソースティック署名（sig）に
よる stale 無効化は DwellRollupStore と同一規則。
"""

from __future__ import annotations

import os as _os
import tempfile as _tempfile
from pathlib import Path as _Path
from typing import Any, Callable

import numpy as np
# ISSUE-183: pandas 依存は撤去（日始端算出は gateway/day_bounds の整数演算へ移行）。

from market_profile_api.cache_layout import CacheLayout
# ISSUE-178: 層間 DTO（不変）。gateway（外側）が compute（内側）の DTO を import する＝依存方向は内向き。
from market_profile_api.compute.rollup_dto import ZpRollup
# ISSUE-183: 日始端の算出は gateway 内の単一定義（``pd.Timestamp(...).normalize()`` と同値）。
from market_profile_api.gateway.day_bounds import next_utc_day_start, utc_day_start


class ZpStore:
    """z(p) 日別成果物（mgrid / znull）のディスク永続キャッシュ Repository。"""

    #: ディスク未ヒット/破損/不整合の番兵（``None``＝「実データ無しの完了日」と区別する）。
    CACHE_MISS = object()

    #: :meth:`_znull_relative_parts` のうち世代 dir に当たる位置（0 起点）。GC の掃除単位＝格子 dir。
    #: ISSUE-172: 記述子（:meth:`layout`）はこの位置から導出し、書込パスと同一式を共有する。
    ZNULL_GEN_PART_INDEX = 1

    def __init__(
        self,
        *,
        root_provider: Callable[[], "Any | None"],
        default_root_provider: Callable[[], _Path],
        grid_w: float,
        hist_days: int,
        m_reps: int,
        cache_version_provider: Callable[[], int],
        day_parquet_files: Callable[..., Any],
    ) -> None:
        self._root_provider = root_provider
        self._default_root_provider = default_root_provider
        self._grid_w = float(grid_w)
        self._hist_days = int(hist_days)
        self._m_reps = int(m_reps)
        self._cache_version_provider = cache_version_provider
        self._day_parquet_files = day_parquet_files

    # ------------------------------------------------------------------ #
    # 配置
    # ------------------------------------------------------------------ #
    def cache_root(self) -> _Path:
        override = self._root_provider()
        if override is not None:
            return _Path(override)
        return self._default_root_provider()

    def mgrid_path(self, symbol: str, day_start: int) -> _Path:
        return self.cache_root() / "mgrid" / str(symbol) / f"{int(day_start)}.npz"

    def znull_root(self) -> _Path:
        """znull 系統の走査基点 ``<root>/znull``（GC 記述子と :meth:`null_path` の共通起点）。"""
        return self.cache_root() / "znull"

    def _znull_relative_parts(self, symbol: str, day_start: int) -> "tuple[str, ...]":
        """:meth:`znull_root` からの相対 segment 列 ``(<symbol>, b<bp>, L<hist>-M<reps>, <day>.npz)``。

        ISSUE-172: znull 配置の**唯一の定義**。:meth:`null_path` と :meth:`layout` の双方が
        本メソッドから導出され、二重定義によるドリフトを構造的に排除する。
        """
        return (
            str(symbol),
            f"b{self._grid_w:g}",  # ISSUE-079: bp タグ（旧 g10 と不混在）。
            f"L{self._hist_days}-M{self._m_reps}",
            f"{int(day_start)}.npz",
        )

    def null_path(self, symbol: str, day_start: int) -> _Path:
        return self.znull_root().joinpath(*self._znull_relative_parts(symbol, day_start))

    def layout(self) -> CacheLayout:
        """GC 向けの現行世代記述子（:class:`CacheLayout`）を返す（ISSUE-172）。

        走査基点は :meth:`znull_root`（mgrid は格子非依存ゆえ掃除対象外＝温存）。世代 dir は
        :attr:`ZNULL_GEN_PART_INDEX` が指す格子 segment（``b<bp>``）で、:meth:`_znull_relative_parts`
        から導出するため格子定数 bump に自動追随する。
        """
        gen = self._znull_relative_parts("", 0)[self.ZNULL_GEN_PART_INDEX]
        return CacheLayout(
            name="zp-znull",
            root=self.znull_root(),
            gen_depth=self.ZNULL_GEN_PART_INDEX + 1,  # <sym>/<gen>
            current=frozenset({gen}),
            reason=f"zp znull 旧格子世代（現行 {gen}）",
        )

    # ------------------------------------------------------------------ #
    # 共通 I/O（原子的保存・fail-safe 読込）
    # ------------------------------------------------------------------ #
    def _atomic_save(self, path: _Path, arrs: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
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

    # ------------------------------------------------------------------ #
    # mgrid（分 ffill close グリッド）
    # ------------------------------------------------------------------ #
    def save_mgrid(self, path: _Path, grid: "tuple[np.ndarray, float] | None", sig: str = "") -> None:
        """mgrid（``None``=実データ無し完了日を含む）を保存する。"""
        common = dict(
            version=np.int64(self._cache_version_provider()),
            sig=np.str_(sig),
        )
        if grid is None:
            arrs = dict(**common, empty=np.bool_(True),
                        closes=np.zeros(0, dtype=float), open=np.float64(0.0))
        else:
            closes, open_d = grid
            arrs = dict(**common, empty=np.bool_(False),
                        closes=np.asarray(closes, dtype=float), open=np.float64(open_d))
        self._atomic_save(path, arrs)

    def load_mgrid(self, path: _Path) -> "tuple[Any, str]":
        """(status, sig)。status は CACHE_MISS / None / (closes, open)。"""
        if not path.is_file():
            return self.CACHE_MISS, ""
        try:
            with np.load(path) as z:
                if int(z["version"]) != int(self._cache_version_provider()):
                    return self.CACHE_MISS, ""
                sig = str(z["sig"]) if "sig" in z.files else ""
                if bool(z["empty"]):
                    return None, sig
                return (np.asarray(z["closes"], dtype=float), float(z["open"])), sig
        except Exception:
            return self.CACHE_MISS, ""

    # ------------------------------------------------------------------ #
    # znull（日別 z 成果物: 観測占有＋Null B モーメント）
    # ------------------------------------------------------------------ #
    def save_null(self, path: _Path, roll: "ZpRollup | None", sig: str = "") -> None:
        """znull（``None``=z 未定義/実データ無しの完了日を含む）を保存する。"""
        common = dict(
            version=np.int64(self._cache_version_provider()),
            grid_w=np.float64(self._grid_w),
            hist_days=np.int64(self._hist_days),
            m_reps=np.int64(self._m_reps),
            sig=np.str_(sig),
        )
        if roll is None:
            arrs = dict(**common, empty=np.bool_(True), kmin=np.int64(0),
                        obs=np.zeros(0, dtype=float), mean=np.zeros(0, dtype=float),
                        var=np.zeros(0, dtype=float))
        else:
            arrs = dict(**common, empty=np.bool_(False),
                        kmin=np.int64(int(roll.kmin)),
                        obs=np.asarray(roll.obs, dtype=float),
                        mean=np.asarray(roll.mean, dtype=float),
                        var=np.asarray(roll.var, dtype=float))
        self._atomic_save(path, arrs)

    def load_null(self, path: _Path) -> "tuple[Any, str]":
        """(status, sig)。status は CACHE_MISS / None / :class:`ZpRollup`（不変 DTO・ISSUE-178）。"""
        if not path.is_file():
            return self.CACHE_MISS, ""
        try:
            with np.load(path) as z:
                if int(z["version"]) != int(self._cache_version_provider()):
                    return self.CACHE_MISS, ""
                if float(z["grid_w"]) != self._grid_w:
                    return self.CACHE_MISS, ""
                if int(z["hist_days"]) != self._hist_days or int(z["m_reps"]) != self._m_reps:
                    return self.CACHE_MISS, ""
                sig = str(z["sig"]) if "sig" in z.files else ""
                if bool(z["empty"]):
                    return None, sig
                return ZpRollup(
                    kmin=int(z["kmin"]),
                    obs=np.asarray(z["obs"], dtype=float),
                    mean=np.asarray(z["mean"], dtype=float),
                    var=np.asarray(z["var"], dtype=float),
                ), sig
        except Exception:
            return self.CACHE_MISS, ""

    # ------------------------------------------------------------------ #
    # 無効化（ソースティック署名・DwellRollupStore と同一規則）
    # ------------------------------------------------------------------ #
    def day_source_signature(self, symbol: str, day_start: int) -> str:
        # ISSUE-078: セッション日は UTC 2 日跨ぎ＝両日 parquet を署名に含める（DwellRollupStore と同一規則）。
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
