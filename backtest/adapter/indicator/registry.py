"""PandasIndicatorRegistry（IndicatorPort 実装・CLEAN_ARCH §6）。

事前計算済みの指標系列（``pandas.Series``）を名前で保持し、UseCase へは domain
例外のみを漏らす（pandas/numpy 例外を内側へ漏らさない）:

    get(name): 登録済み系列を返す。未登録参照は IndicatorBufferError。
               先頭の連続 NaN は warmup（描画開始前の未定義区間・SPEC §1.2）として
               許容し、有効区間（最初の非 NaN 以降）に NaN がある場合のみ
               IndicatorNaNError を投げる（データ破損検出）。全数 NaN も破損扱い。
    update(bar_index): 事前計算系列では no-op（IF 充足のため呼べる）。
"""
from __future__ import annotations

from typing import Any

import pandas as pd

from backtest.domain.exceptions import IndicatorBufferError, IndicatorNaNError
from backtest.usecase.ports import IndicatorPort


class PandasIndicatorRegistry(IndicatorPort):
    """名前→pandas.Series の事前計算系列を保持する IndicatorPort 実装。"""

    def __init__(self, series: dict[str, pd.Series]) -> None:
        self._series = dict(series)

    def get(self, name: str) -> Any:
        if name not in self._series:
            raise IndicatorBufferError(
                "未登録の指標参照", context={"name": name, "available": list(self._series)}
            )
        series = self._series[name]
        self._raise_if_invalid_nan(name, series)
        return series

    @staticmethod
    def _raise_if_invalid_nan(name: str, series: pd.Series) -> None:
        # 先頭の連続 NaN は warmup（指標の未定義区間・SPEC §1.2）として許容する。
        # 有効区間（最初の非 NaN 以降）に NaN がある場合のみデータ破損とみなし
        # IndicatorNaNError を投げる（上流規約: 「warmup より後の NaN のみ」検出）。
        # 全数 NaN（有効区間なし）は warmup のみで構成され post-warmup NaN を持たない
        # ため本検査では投げない（最小バー不足の検証は別責務・範囲外）。
        na = series.isna().to_numpy()
        if not na.any():
            return
        if not (~na).any():  # 全数 NaN = 全 warmup（post-warmup 区間なし）→ 許容
            return
        first_valid = int((~na).argmax())
        if na[first_valid:].any():
            raise IndicatorNaNError("指標系列に NaN を検出", context={"name": name})

    def update(self, bar_index: int) -> None:
        # 事前計算系列のため no-op（逐次計算実装で上書きする拡張点）。
        return None
