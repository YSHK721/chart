"""MADiff 指標（adapter・SPEC §2）。

定義式（厳密）:
    MADiff[i] = MA(close, MAPeriod, MAMethod) − MA(open, MAPeriod, MAMethod)

同一バーの「終値の移動平均」と「始値の移動平均」の差。#1〜#4 EA が参照する中核
シグナル源。MAMethod は SMA / EMA（既存 ``moving_averages`` の MQL 忠実実装を再利用）。

adapter 層は pandas を内部利用してよい（CLEAN_ARCH §7）。入力は OHLC を保持する
``pandas.DataFrame``（昇順）、出力は同 index の ``pandas.Series``。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

# moving_averages（MQL 忠実 MA の共有実装）は indigators/ に住む固有名トップパッケージ。
# 解決は tools/dev_paths.txt（唯一源）が済ませている＝本モジュールは sys.path を書き換えない。
# 実行時に書き換えると解決先が import 順に依存し、起動経路ごとに別ツリーを掴み得る
# （ISSUE-279 と同型）。この不変条件は
# simulator/tests/unit/test_adapter_path_resolution_comes_from_the_ledger.py が固定する。
from moving_averages import (
    exponential_ma_on_buffer,
    simple_ma_on_buffer,
)


def _ma_series(price: np.ndarray, period: int, method: str) -> np.ndarray:
    """price 配列（昇順）全位置の MA を返す。method は "sma" / "ema"。"""
    n = len(price)
    out = np.zeros(n, dtype=float)
    m = method.lower()
    if m not in ("sma", "ema"):
        raise ValueError(f"未対応の MAMethod: {method!r}（sma / ema のみ）")
    # 委譲先 *_ma_on_buffer は period<=1 で計算せず buffer を書かない（沈黙ゼロ出力）。
    # 全位置 0 の誤った MA（ゼロクロス誤判定の温床）を防ぐため明示的に下限検証する。
    if period <= 1:
        raise ValueError(f"period は 2 以上が必要です: {period}")
    if m == "sma":
        # MQL 忠実 SMA（移植元 simple_ma_on_buffer に委譲・スライド和で O(n)）。
        # simple_ma_on_buffer は warmup（i < period-1）に 0.0 を書くため、当該区間を
        # NaN で上書きする。warmup の 0.0 は「真の MA=0」と区別不能でゼロクロス誤判定を
        # 招く（SPEC §1.2: PLOT_DRAW_BEGIN=period-1 未満は NaN 扱い・EA は参照しない）。
        simple_ma_on_buffer(n, 0, 0, period, price, out)
        out[: period - 1] = np.nan
    else:  # m == "ema"
        # MQL 忠実 EMA（移植元 exponential_ma_on_buffer に委譲）。シードは
        # buffer[0] = price[0]（価格そのもの）。prev=0 から index0=price[0]*pr で
        # 回す誤シードと別系列になり、序盤の MADiff 符号反転（spurious ゼロクロス）
        # を防ぐ。EMA は index0 から定義（warmup NaN 無し・MQL 忠実）。
        exponential_ma_on_buffer(n, 0, 0, period, price, out)
    return out


def madiff(df: pd.DataFrame, period: int, method: str = "sma") -> pd.Series:
    """MADiff = MA(close) − MA(open) を入力 index に揃えた Series で返す。"""
    close = df["close"].to_numpy(dtype=float)
    open_ = df["open"].to_numpy(dtype=float)
    diff = _ma_series(close, period, method) - _ma_series(open_, period, method)
    return pd.Series(diff, index=df.index)
