"""適用価格（applied price）の計算（純粋ロジック・外部 I/O 非依存）。

①層名/責務:
    共有プリミティブ層。OHLC 配列から「適用価格」系列（1 次元の価格列）を生成する。
    移動平均・RSI・MACD・ADX など、価格系列を入力に取るあらゆる指標で再利用する横断
    ユーティリティ。特定の指標には属さない。

②含む構造:
    AppliedPrice          : MQL ENUM_APPLIED_PRICE と同一値の列挙。
    close_price / open_price / high_price / low_price : 単純な列選択。
    median_price / typical_price / weighted_price     : 算術合成。
    applied_price         : 列挙値で 7 種を切り替えるディスパッチャ。

③元 MQL 対応:
    MQL の ENUM_APPLIED_PRICE（PRICE_CLOSE / OPEN / HIGH / LOW / MEDIAN / TYPICAL /
    WEIGHTED）に相当。``MovingAverages.mqh`` は価格非依存のため、本モジュールは
    指標側の applied_price 機構を独立した共有層として切り出したもの。

④依存:
    標準: enum
    外部: numpy
    プロジェクト内: なし（core 層と同格の純粋ロジック）
"""

from __future__ import annotations

from enum import IntEnum

import numpy as np


class AppliedPrice(IntEnum):
    """適用価格の種別。値は MQL ``ENUM_APPLIED_PRICE`` と一致させてある。"""

    CLOSE = 1     # PRICE_CLOSE    終値
    OPEN = 2      # PRICE_OPEN     始値
    HIGH = 3      # PRICE_HIGH     高値
    LOW = 4       # PRICE_LOW      安値
    MEDIAN = 5    # PRICE_MEDIAN   (高値 + 安値) / 2
    TYPICAL = 6   # PRICE_TYPICAL  (高値 + 安値 + 終値) / 3
    WEIGHTED = 7  # PRICE_WEIGHTED (高値 + 安値 + 2*終値) / 4
    # --- MQL 外拡張（ENUM_APPLIED_PRICE に対応値なし）------------------------
    OHLC4 = 8     # (始値 + 高値 + 安値 + 終値) / 4。TradingView 等の合成価格。MQL には無いが
    #             # 共有層で正式対応し、全指標で再利用可能にする（案A）。


def _as_float(arr: np.ndarray) -> np.ndarray:
    """入力を float の numpy 配列へ変換する（内部ヘルパー）。"""
    return np.asarray(arr, dtype=float)


# ---------------------------------------------------------------------------
# 単純な列選択（PRICE_CLOSE / OPEN / HIGH / LOW）
# ---------------------------------------------------------------------------
def close_price(close: np.ndarray) -> np.ndarray:
    """終値系列（PRICE_CLOSE）を返す。"""
    return _as_float(close)


def open_price(open_: np.ndarray) -> np.ndarray:
    """始値系列（PRICE_OPEN）を返す。"""
    return _as_float(open_)


def high_price(high: np.ndarray) -> np.ndarray:
    """高値系列（PRICE_HIGH）を返す。"""
    return _as_float(high)


def low_price(low: np.ndarray) -> np.ndarray:
    """安値系列（PRICE_LOW）を返す。"""
    return _as_float(low)


# ---------------------------------------------------------------------------
# 算術合成（PRICE_MEDIAN / TYPICAL / WEIGHTED）
# ---------------------------------------------------------------------------
def median_price(high: np.ndarray, low: np.ndarray) -> np.ndarray:
    """中値系列（PRICE_MEDIAN）= (high + low) / 2 を返す。"""
    return (_as_float(high) + _as_float(low)) / 2.0


def typical_price(
    high: np.ndarray, low: np.ndarray, close: np.ndarray
) -> np.ndarray:
    """代表値系列（PRICE_TYPICAL）= (high + low + close) / 3 を返す。"""
    return (_as_float(high) + _as_float(low) + _as_float(close)) / 3.0


def weighted_price(
    high: np.ndarray, low: np.ndarray, close: np.ndarray
) -> np.ndarray:
    """加重終値系列（PRICE_WEIGHTED）= (high + low + 2*close) / 4 を返す。"""
    return (_as_float(high) + _as_float(low) + 2.0 * _as_float(close)) / 4.0


def ohlc4_price(
    open_: np.ndarray, high: np.ndarray, low: np.ndarray, close: np.ndarray
) -> np.ndarray:
    """OHLC 平均系列（OHLC4・MQL 外拡張）= (open + high + low + close) / 4 を返す。"""
    return (
        _as_float(open_) + _as_float(high) + _as_float(low) + _as_float(close)
    ) / 4.0


# ---------------------------------------------------------------------------
# ディスパッチャ
# ---------------------------------------------------------------------------
def applied_price(
    kind: AppliedPrice | int,
    open_: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
) -> np.ndarray:
    """``kind`` で指定した適用価格系列を OHLC 配列から生成する。

    MQL の ``applied_price`` 引数に相当する切り替え口。各指標は本関数で価格系列を
    用意し、移動平均などの計算関数へ渡す。

    Args:
        kind: 適用価格の種別（``AppliedPrice`` または同値の int）。
        open_: 始値配列。
        high: 高値配列。
        low: 安値配列。
        close: 終値配列。

    Returns:
        指定種別の価格系列（float の np.ndarray）。

    Raises:
        ValueError: ``kind`` が 7 種のいずれにも該当しない場合。
    """
    if kind == AppliedPrice.CLOSE:
        return close_price(close)
    if kind == AppliedPrice.OPEN:
        return open_price(open_)
    if kind == AppliedPrice.HIGH:
        return high_price(high)
    if kind == AppliedPrice.LOW:
        return low_price(low)
    if kind == AppliedPrice.MEDIAN:
        return median_price(high, low)
    if kind == AppliedPrice.TYPICAL:
        return typical_price(high, low, close)
    if kind == AppliedPrice.WEIGHTED:
        return weighted_price(high, low, close)
    if kind == AppliedPrice.OHLC4:
        return ohlc4_price(open_, high, low, close)
    raise ValueError(f"未知の適用価格種別です: {kind!r}")


# ---------------------------------------------------------------------------
# UI ソース値 → 適用価格種別（ISSUE-179 項目 4: 3 重複製の 1 本化）
# ---------------------------------------------------------------------------
# ``moving_averages/src/lwc_chart.py`` ↔ ``btlm_trail/src/core.py`` ↔ ``ma_marod/src/core.py``
# が個別に持っていた同一写像をここへ集約する（写像 1 行の追加が 3 ファイルの同時改変を
# 要求する状態＝OCP 違反の解消）。値・キーは移設元と完全に同一（無改変移設）。
#
# キーは catalog の source enum（小文字）。呼び出し側は ``str(source).lower()`` で引く。
SOURCE_TO_APPLIED: dict[str, AppliedPrice] = {
    "close": AppliedPrice.CLOSE,
    "open": AppliedPrice.OPEN,
    "high": AppliedPrice.HIGH,
    "low": AppliedPrice.LOW,
    "hl2": AppliedPrice.MEDIAN,
    "hlc3": AppliedPrice.TYPICAL,
    "hlcc4": AppliedPrice.WEIGHTED,
    "ohlc4": AppliedPrice.OHLC4,
}
