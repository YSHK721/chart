"""MarketdataCsvOHLCRepository: marketdata 形式 CSV から domain.Bar 列を読み込む。

marketdata 系列（`data/marketdata/*.csv`・ヘッダ `date,open,high,low,close,volume[,up,dn]`・
日付 ISO `2012-06-14 10:35:00`・時刻系は UTC＝裁定 2026-08-18）を list[domain.Bar] へ変換する
MarketDataPort 実装。2012 年からの全期間 JP225 実データを sim の実行データセットにするための
リーダ（依頼者承認 2026-09-06。従来はどのリーダも本形式を読めず、全 12 組合せの実測で
「fixture×MT5 ローダ EA」しか動かなかった）。

spread 列は**存在しない**ため spread=0 固定である。spread 依存 EA（MA_Slope 系）へ供給しては
ならない（H-4 裁定・MARKETDATA_TIMESERIES_BOUNDARY_DESIGN §10.2）。その遮断は非対象宣言
N-17（`main/tester_settings/unsupported.py`）が実行前に Fail-Stop で担う。

**窓はフレーム段で先に適用する**（構築時パラメータ ``window``・ISSUE-135 と同じ隔離）。
Bar を全件組み立ててから捨てる後段フィルタは、4,604,080 行の実測で Bar 構築だけに
442.6 秒を要する「作ってから捨てる」浪費になる（ISSUE-450 型）。窓内の行だけを
``frame_to_bars`` へ渡す＝構築数と採用数の差 0 は計算量テストが固定する。
"""
from __future__ import annotations

from typing import Any

import pandas as pd

from simulator.adapter.repository._ohlc_frame import (
    ColumnSpec,
    frame_to_bars,
    read_csv_or_data_error,
)
from simulator.domain.bar import Bar
from simulator.usecase.ports import MarketDataPort

#: marketdata 形式の必須列（`up`/`dn` は任意・使わない）。
_REQUIRED = ("date", "open", "high", "low", "close", "volume")

#: 正規化済み時刻列（読み込み後に 1 回だけベクトル計算で付ける内部列）。
_TIME_COLUMN = "_marketdata_time"


def _extract(df: pd.DataFrame, i: int) -> "dict[str, Any]":
    """marketdata 形式 1 行を domain.Bar 引数へマッピングする。

    時刻は前計算済みの ``_TIME_COLUMN``（datetime64・UTC naive＝MT5 リーダと同じ表現）を
    使う（行ごとの文字列パースをしない）。
    """
    return {
        "time": df[_TIME_COLUMN].iat[i].to_datetime64(),
        "open": float(df["open"].iat[i]),
        "high": float(df["high"].iat[i]),
        "low": float(df["low"].iat[i]),
        "close": float(df["close"].iat[i]),
        "volume": float(df["volume"].iat[i]),
        "spread": 0,
    }


_SPEC = ColumnSpec(required=_REQUIRED, extract=_extract)


def detect_ohlc_form(source_ref: Any) -> str:
    """価格 CSV の形式をヘッダ 1 行の実測で判定する（"mt5_tab" / "comma" / "marketdata" / ""）。

    判定材料はファイル自身のヘッダ（形式の権威はデータ実体）であり、EA 名や拡張子から
    推測しない。読めない・どれでもない・パスでない（None＝データ非供給の modelling 等）
    場合は ""（呼び出し側が既定へ倒す。実測: `Model=3` の job は data_path=None で通る）。
    """
    try:
        with open(source_ref, "rb") as f:
            head = f.readline().decode("utf-8", "replace").strip()
    except (OSError, TypeError, ValueError):
        return ""
    if head.startswith("<DATE>"):
        return "mt5_tab"
    columns = [c.strip() for c in head.split(",")]
    if columns[:1] == ["time"]:
        return "comma"
    if columns[:1] == ["date"]:
        return "marketdata"
    return ""


class MarketdataCsvOHLCRepository(MarketDataPort):
    """marketdata 形式 CSV → list[domain.Bar] へ変換する MarketDataPort 実装。

    ``window``（任意・(start, end) 半開・UTC aware datetime）: 指定時はフレーム段で
    窓内の行だけに絞ってから Bar を組む（窓外の Bar を作らない）。
    """

    def __init__(self, window: "tuple[Any, Any] | None" = None) -> None:
        self._window = window

    def load(self, source_ref: Any, timeframe: Any = None, period: Any = None) -> list[Bar]:
        df = read_csv_or_data_error(source_ref)
        if "date" in df.columns:
            # UTC aware で 1 回だけベクトルパース（date 列の時刻系は UTC＝裁定 2026-08-18）
            times = pd.to_datetime(df["date"], utc=True)
            if self._window is not None:
                start, end = self._window
                df = df.loc[(times >= start) & (times < end)]
                times = times.loc[df.index]
            df = df.assign(**{_TIME_COLUMN: times.dt.tz_localize(None)}).reset_index(drop=True)
        return frame_to_bars(df, _SPEC)
