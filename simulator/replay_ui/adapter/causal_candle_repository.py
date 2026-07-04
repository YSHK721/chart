"""CausalCandleRepository — /candles の CausalCandlePort 実装（proto load_tick_candles 忠実）。

tick 源（ref="jp225_tick"）: ``jp225_tick_m1.csv`` を読み、日内 close 中央値 ±threshold 超の
外れバーを除去（``_repair_day_outliers``・生 CSV 不変）→ ``resample_ohlc`` で時間足化 → tail(limit)
→ candles JSON。非 tick ref: 既存 ``dataset.load_candles`` へ委譲（proto /candles 非 tick 分岐忠実）。

技術隔離（CLEAN_ARCH §6）: pandas / indicator_ui は本ファイル内に閉じる。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

from simulator.replay_ui.adapter import _indicator_ui_bridge
from simulator.replay_ui.adapter._m1_repair import (
    M1_OUTLIER_THRESHOLD as OUTLIER_THRESHOLD,
)
from simulator.replay_ui.adapter._m1_repair import repair_day_outliers

# 後方互換エイリアス（M1 補正は共有 _m1_repair に一元化・両 repository が public 参照）。
_repair_day_outliers = repair_day_outliers


class CausalCandleRepository:
    """CausalCandlePort 実装。untilTime 切断はしない（proto /candles と同一）。"""

    def __init__(
        self,
        tick_m1_csv: Any,
        api_path: Any = None,
        repo_root: Any = None,
        outlier_threshold: float = OUTLIER_THRESHOLD,
    ) -> None:
        self._tick_m1_csv = Path(tick_m1_csv)
        self._api_path = api_path
        self._repo_root = repo_root
        self._threshold = outlier_threshold
        self._m1_cache: dict = {}

    # ---- CausalCandlePort ----

    def load_candles(
        self, ref: str, timeframe: "str | None", limit: "int | None"
    ) -> "list[dict]":
        if ref == "jp225_tick":
            return self._load_tick_candles(timeframe, limit)
        bridge = _indicator_ui_bridge.load(self._api_path, self._repo_root)
        if not bridge.dataset.is_known(ref):
            raise ValueError(f"unknown {ref}")
        return bridge.dataset.load_candles(ref, timeframe, limit)

    # ---- internal ----

    def _load_tick_m1(self) -> "pd.DataFrame":
        mt = self._tick_m1_csv.stat().st_mtime
        if self._m1_cache.get("mt") != mt:
            df = pd.read_csv(self._tick_m1_csv, parse_dates=["date"]).set_index("date")
            df = _repair_day_outliers(df, self._threshold)  # 読み取り時のみ補正（生 CSV 不変）
            self._m1_cache.update(mt=mt, df=df)
        return self._m1_cache["df"]

    def _load_tick_candles(self, tf: "str | None", limit: "int | None") -> "list[dict]":
        bridge = _indicator_ui_bridge.load(self._api_path, self._repo_root)
        df = self._load_tick_m1()
        rule = None if (tf in (None, "1m")) else bridge.TIMEFRAME_RULES.get(tf)
        r = bridge.resample_ohlc(df, rule)
        if isinstance(limit, int) and limit > 0:
            r = r.tail(limit)
        secs = r.index.values.astype("datetime64[s]").astype("int64")
        return [
            {
                "time": int(secs[i]),
                "open": float(x.open),
                "high": float(x.high),
                "low": float(x.low),
                "close": float(x.close),
            }
            for i, x in enumerate(r.itertuples(index=False))
        ]
