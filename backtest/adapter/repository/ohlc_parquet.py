"""ParquetOHLCRepository: parquet から domain.Bar 列を読み込む入力アダプタ（MarketDataPort 実装）。

pandas.read_parquet（pyarrow バックエンド）で parquet を読み、CSV と同じ検証・変換・
例外翻訳ロジック（_frame_to_bars）を共有する。OHLCFrame 型は未定義のため list[Bar] を返す。

外側（pandas / pyarrow / OS）例外を内側ドメイン例外へ翻訳する（CLEAN_ARCH §6）:
    OHLC 整合違反 → OHLCInvalidError（domain.Bar 送出・翻訳不要）
    その他 → DataError
"""
from __future__ import annotations

from typing import Any

import pandas as pd

from backtest.adapter.repository.ohlc_csv import _frame_to_bars
from backtest.domain.bar import Bar
from backtest.domain.exceptions import DataError
from backtest.usecase.ports import MarketDataPort


class ParquetOHLCRepository(MarketDataPort):
    """parquet → list[domain.Bar] へ変換する MarketDataPort 実装。"""

    def load(self, source_ref: Any, timeframe: Any = None, period: Any = None) -> list[Bar]:
        try:
            df = pd.read_parquet(source_ref)
        except Exception as exc:  # pyarrow / pandas / OSError 等を内側へ翻訳
            raise DataError(
                f"parquet の読み込みに失敗しました: {source_ref}",
                context={"source_ref": str(source_ref), "cause": repr(exc)},
            ) from exc

        return _frame_to_bars(df, source_ref)
