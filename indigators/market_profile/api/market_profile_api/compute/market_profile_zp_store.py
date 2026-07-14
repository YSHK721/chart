"""market_profile_zp_store — z(p) 日別成果物のディスク永続キャッシュ Repository（純 I/O・fail-safe）。

:class:`DwellRollupStore` と同パターンの新規 Store（既存 Store は非改変）。保存単位は 2 種:
  - mgrid: 完了日の分 ffill close グリッド ``{closes[G], open}``（帰無ステップ行列のソース）。
  - znull: 完了日の z(p) 日別成果物 ``{kmin, obs[], mean[], var[]}``（観測占有＋Null B モーメント）。

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
import pandas as pd


class ZpStore:
    """z(p) 日別成果物（mgrid / znull）のディスク永続キャッシュ Repository。"""

    #: ディスク未ヒット/破損/不整合の番兵（``None``＝「実データ無しの完了日」と区別する）。
    CACHE_MISS = object()

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

    def null_path(self, symbol: str, day_start: int) -> _Path:
        return (
            self.cache_root() / "znull" / str(symbol)
            / f"g{self._grid_w:g}" / f"L{self._hist_days}-M{self._m_reps}"
            / f"{int(day_start)}.npz"
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
    def save_null(self, path: _Path, roll: "dict | None", sig: str = "") -> None:
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
                        kmin=np.int64(int(roll["kmin"])),
                        obs=np.asarray(roll["obs"], dtype=float),
                        mean=np.asarray(roll["mean"], dtype=float),
                        var=np.asarray(roll["var"], dtype=float))
        self._atomic_save(path, arrs)

    def load_null(self, path: _Path) -> "tuple[Any, str]":
        """(status, sig)。status は CACHE_MISS / None / dict{kmin,obs,mean,var}。"""
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
                return {
                    "kmin": int(z["kmin"]),
                    "obs": np.asarray(z["obs"], dtype=float),
                    "mean": np.asarray(z["mean"], dtype=float),
                    "var": np.asarray(z["var"], dtype=float),
                }, sig
        except Exception:
            return self.CACHE_MISS, ""

    # ------------------------------------------------------------------ #
    # 無効化（ソースティック署名・DwellRollupStore と同一規則）
    # ------------------------------------------------------------------ #
    def day_source_signature(self, symbol: str, day_start: int) -> str:
        # ISSUE-078: セッション日は UTC 2 日跨ぎ＝両日 parquet を署名に含める（DwellRollupStore と同一規則）。
        day = pd.Timestamp(int(day_start), unit="s").normalize()
        parts: list[str] = []
        for p in self._day_parquet_files(day, day + pd.Timedelta(days=1), symbol=symbol):
            try:
                st = p.stat()
                parts.append(f"{p.name}:{int(st.st_mtime)}:{int(st.st_size)}")
            except OSError:
                parts.append(f"{p.name}:?")
        return "|".join(parts)
