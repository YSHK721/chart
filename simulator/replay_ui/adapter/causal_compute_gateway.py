"""CausalComputeGateway — /compute の CausalComputePort 実装（proto do_compute 忠実）。

indicator_ui の実アダプタ ``full_compute`` / ``latest_compute`` を read-only 再利用して計算する
（proto_server:171-177 と同一・偽装なし＝出力はプロトと bit 同一）。usecase から渡る plain バー列
（truncate/tail/forming 適用済）を DataFrame（DatetimeIndex・UTC）へ復元して計算へ渡す。

バー時刻の符号化は candle.time と同一（``index → datetime64[s] → int64``）＝フロントの untilTime と
同基準。DataFrame 復元は ``pd.to_datetime(sec, unit="s")`` で完全逆変換（UTC・秒境界で bit 一致）。

技術隔離（CLEAN_ARCH §6）: pandas / indicator_ui は本ファイル内に閉じる。
"""
from __future__ import annotations

from typing import Any

import pandas as pd

from simulator.replay_ui.adapter import _indicator_ui_bridge
from simulator.replay_ui.adapter.dataset_ports import OhlcSupplyPort, RefValidationPort


class CausalComputeGateway:
    """CausalComputePort 実装。load_source（dataset）+ compute（full/latest）。"""

    def __init__(self, api_path: Any = None, repo_root: Any = None) -> None:
        self._api_path = api_path
        self._repo_root = repo_root

    def _bridge(self):
        # ISSUE-136 ISP: /compute は dataset ＋ 計算 Facade のみを要する（MP controller を import しない）。
        return _indicator_ui_bridge.load_compute(self._api_path, self._repo_root)

    # ---- CausalComputePort ----

    def load_source(self, ref: str, timeframe: "str | None") -> "list[dict]":
        bridge = self._bridge()
        # ISSUE-136 ISP: dataset 具象を役割別の狭いポート型で受ける（検証 2 面／供給 1 面のみに依存）。
        refs: RefValidationPort = bridge.dataset
        ohlc: OhlcSupplyPort = bridge.dataset
        if not refs.is_known(ref):
            raise ValueError(f"unknown datasetRef {ref!r}")
        if timeframe is not None and not refs.is_known_timeframe(timeframe):
            raise ValueError(f"unknown timeframe {timeframe!r}")
        df = ohlc.load_dataframe(ref, timeframe)
        return self._df_to_bars(df)

    def compute(
        self, indicator: str, variant: str, mode: str, bars: "list[dict]", params: dict
    ) -> "list[dict]":
        bridge = self._bridge()
        df = self._bars_to_df(bars)
        p = dict(params or {})
        if mode == "latest":
            return bridge.latest_compute(bridge.adapter, indicator, variant, df, p)
        return bridge.full_compute(bridge.adapter, indicator, variant, df, p)

    # ---- internal (pandas ↔ plain) ----

    @staticmethod
    def _df_to_bars(df: "pd.DataFrame") -> "list[dict]":
        # candle.time と同一符号化（untilTime と同基準・tz 非依存 UTC epoch）。
        # ISSUE-158 ①: 列単位ベクトル化（旧: 行ループ df.iloc＝50k 行で ~1.2s・compute 1 回の 69%）。
        #   出力は旧実装と完全同一（キー順 time→列順・time は int・値は float。等価性は
        #   tests/unit/test_plain_bars_vectorized.py が参照実装との一致で固定）。
        secs = df.index.values.astype("datetime64[s]").astype("int64")
        keys = ["time"] + [str(c).lower() for c in df.columns]
        columns = [secs.tolist()] + [
            df[c].to_numpy(dtype="float64").tolist() for c in df.columns
        ]
        return [dict(zip(keys, row)) for row in zip(*columns)]

    @staticmethod
    def _bars_to_df(bars: "list[dict]") -> "pd.DataFrame":
        # time → DatetimeIndex（UTC・秒境界で df_to_bars の完全逆変換）。他列はそのまま復元。
        times = [int(b["time"]) for b in bars]
        index = pd.to_datetime(times, unit="s")
        cols: "list[str]" = []
        for b in bars:
            for k in b:
                if k != "time" and k not in cols:
                    cols.append(k)
        data = {c: [b.get(c) for b in bars] for c in cols}
        return pd.DataFrame(data, index=index)
