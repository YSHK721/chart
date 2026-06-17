"""adapter/indicator/madiff.py の MADiff 指標テスト（SPEC §2）。

定義式（厳密）: MADiff[i] = MA(close, period, method) − MA(open, period, method)
- method: SMA / EMA（MQL: pr=2/(period+1)、index0 から再帰）
- 入力は OHLC を保持する pandas.DataFrame（昇順）、出力は同 index の pandas.Series
- 描画開始前（SMA warmup: i < period-1）は np.nan（未定義・SPEC §1.2）。EMA は
  index0 から再帰定義され warmup NaN を持たない（MQL 忠実）。

TDD AAA 構造。F.I.R.S.T（Fast/Independent/Repeatable/Self-Validating/Timely）。
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest


def _ohlc(opens, closes):
    n = len(opens)
    return pd.DataFrame(
        {
            "open": opens,
            "high": [max(o, c) + 1.0 for o, c in zip(opens, closes)],
            "low": [min(o, c) - 1.0 for o, c in zip(opens, closes)],
            "close": closes,
            "volume": [1.0] * n,
            "spread": [0] * n,
        }
    )


# --- TD.1 正常系: SMA -------------------------------------------------------

def test_madiff_sma_equals_ma_close_minus_ma_open():
    # Arrange: period=2 SMA。各位置で MA(close)-MA(open) を手計算で検証する
    from backtest.adapter.indicator.madiff import madiff

    df = _ohlc(opens=[1.0, 2.0, 3.0], closes=[2.0, 4.0, 6.0])

    # Act
    result = madiff(df, period=2, method="sma")

    # Assert: index1 = (close[0:2]平均 - open[0:2]平均) = (3.0 - 1.5) = 1.5
    #         index2 = (5.0 - 2.5) = 2.5
    assert result.iloc[1] == pytest.approx(1.5)
    assert result.iloc[2] == pytest.approx(2.5)


def test_madiff_returns_series_aligned_to_input_index():
    # Arrange: 出力は入力 DataFrame と同じ index・長さ（pandas 型をシグネチャで表明）
    from backtest.adapter.indicator.madiff import madiff

    df = _ohlc(opens=[1.0, 2.0, 3.0, 4.0], closes=[1.5, 2.5, 3.5, 4.5])

    # Act
    result = madiff(df, period=2, method="sma")

    # Assert
    assert isinstance(result, pd.Series)
    assert list(result.index) == list(df.index)
    assert len(result) == len(df)


# --- TD.2 境界値: 描画開始前（warmup） --------------------------------------
# SPEC（BACKTEST_PROCESS.md §1.2）: `i < MAPeriod−1` の足は未確定（描画開始前）。
# NaN 扱いにし、EA 側で参照しない。warmup の 0.0 は「真の MADiff=0」と区別不能で
# ゼロクロス誤判定を招くため、未定義は np.nan で表現する（指標の正しい未定義表現）。

def test_madiff_sma_warmup_is_nan():
    # Arrange: period=3 のとき index0,1 は warmup（MA 計算不能）→ NaN
    from backtest.adapter.indicator.madiff import madiff

    df = _ohlc(opens=[1.0, 2.0, 3.0], closes=[2.0, 3.0, 4.0])

    # Act
    result = madiff(df, period=3, method="sma")

    # Assert: 下限境界 i < period-1 は未定義（NaN）。有効区間 index2 は数値。
    assert np.isnan(result.iloc[0])
    assert np.isnan(result.iloc[1])
    assert not np.isnan(result.iloc[2])


# --- TD.1 正常系: EMA（#1 TC24051901 は MAMethod=EMA）-----------------------

def _mql_ema(price, period):
    """一次情報照合用: 移植元 exponential_ma_on_buffer の出力をそのまま返す。

    production 実装とは独立に、MQL 忠実実装（indigators/moving_averages）を
    呼び出して期待値を得る。これにより self-fulfilling（自己一致）を排除し、
    production が同じ系列を再現しているかを検証する。
    """
    import sys
    from pathlib import Path

    indig = str(Path(__file__).resolve().parents[3] / "indigators")
    if indig not in sys.path:
        sys.path.insert(0, indig)
    from moving_averages import exponential_ma_on_buffer

    arr = np.asarray(price, dtype=float)
    n = len(arr)
    buf = np.zeros(n, dtype=float)
    exponential_ma_on_buffer(n, 0, 0, period, arr, buf)
    return buf


def test_madiff_ema_matches_exponential_ma_on_buffer_mql_seed():
    # Arrange: 期待値は移植元 exponential_ma_on_buffer の出力（一次情報照合）。
    # MQL 忠実シードは buffer[0] = price[0]（価格そのもの）であり、誤シード
    # （prev=0 から回した price[0]*pr）とは index0 から別系列になる。
    from backtest.adapter.indicator.madiff import madiff

    opens = [10.0, 11.0, 12.0]
    closes = [12.0, 14.0, 16.0]
    df = _ohlc(opens=opens, closes=closes)

    exp = _mql_ema(closes, period=2) - _mql_ema(opens, period=2)

    # Act
    result = madiff(df, period=2, method="ema")

    # Assert: index0 は MQL シード（close[0]-open[0] = 12-10 = 2.0）であって
    # 誤シード（close[0]*pr - open[0]*pr = (12-10)*2/3 = 1.333...）ではない。
    assert result.iloc[0] == pytest.approx(exp[0])
    assert result.iloc[1] == pytest.approx(exp[1])
    assert result.iloc[2] == pytest.approx(exp[2])
    # 一次情報の MQL シードを直接固定（index0 = close[0] - open[0]）
    assert result.iloc[0] == pytest.approx(closes[0] - opens[0])


def _buy_cross_indices(values):
    """bullish ゼロクロス（買い）の発生 index。MADiff[i-1] < 0 かつ MADiff[i] > 0。

    TC24051901 の買いシグナル条件（SPEC §3.1）に対応する。MADiff 符号がどこで
    負→正に転じるかを列挙する（戦略本体には触れず符号レベルで固定）。
    """
    return [i for i in range(1, len(values)) if values[i - 1] < 0 and values[i] > 0]


def test_madiff_ema_no_spurious_buy_zero_cross_adversarial():
    # 🔴-1 回帰（adversarial・「この間違いを禁止する」）:
    # レビューが実証した OHLC 系列。MQL 忠実 EMA では買いゼロクロスは index7 のみ。
    # 誤シード（prev=0 から index0=price[0]*pr で開始）だと序盤の EMA レベルが歪み、
    # index4 に spurious な買いゼロクロス（MADiff[3]<0 → MADiff[4]>0）が混入する。
    # 本テストは「index4 に買いクロスが出ない」ことを符号で固定し再発を禁止する。
    from backtest.adapter.indicator.madiff import madiff

    opens = [104.477, 100.771, 98.967, 104.763, 95.466, 103.585, 97.896, 96.443]
    closes = [96.178, 98.085, 103.161, 96.807, 100.816, 101.389, 98.724, 100.477]
    df = _ohlc(opens=opens, closes=closes)

    # Act
    result = madiff(df, period=4, method="ema")

    # Assert: MQL 忠実実装では買いクロスは index7 のみ（index4 の spurious は無い）。
    buys = _buy_cross_indices(result.to_numpy())
    assert buys == [7], f"spurious buy zero-cross detected: {buys} (expected [7])"
    # index4 は MADiff < 0 を維持（誤シードでは > 0 へ反転して買いを誘発する）
    assert result.iloc[4] < 0.0


# --- 🟡-4 SMA 実装の特性化（simple_ma_on_buffer への置換を守る contract）-----
# 現行スカラ SMA を O(n) スライド和（simple_ma_on_buffer）へ置換する。値が一次情報
# （simple_ma_on_buffer の出力）と一致し、かつ warmup（i<period-1）が NaN のままで
# あることを固定する。simple_ma_on_buffer は warmup に 0.0 を書くため、adapter は
# 当該区間を NaN で上書きする責務を持つ（0 混同回避の既存仕様を壊さない）。

def _mql_sma_on_buffer(price, period):
    import sys
    from pathlib import Path

    indig = str(Path(__file__).resolve().parents[3] / "indigators")
    if indig not in sys.path:
        sys.path.insert(0, indig)
    from moving_averages import simple_ma_on_buffer

    arr = np.asarray(price, dtype=float)
    n = len(arr)
    buf = np.zeros(n, dtype=float)
    simple_ma_on_buffer(n, 0, 0, period, arr, buf)
    return buf


def test_madiff_sma_values_match_simple_ma_on_buffer_with_warmup_nan():
    # Arrange: close/open 各系列の SMA が simple_ma_on_buffer の有効区間と一致し、
    # warmup は NaN であることを固定（置換前後で不変であるべき contract）。
    from backtest.adapter.indicator.madiff import madiff

    opens = [1.0, 2.0, 3.0, 4.0, 5.0]
    closes = [2.0, 4.0, 6.0, 8.0, 10.0]
    df = _ohlc(opens=opens, closes=closes)
    period = 2

    buf_close = _mql_sma_on_buffer(closes, period)
    buf_open = _mql_sma_on_buffer(opens, period)

    # Act
    result = madiff(df, period=period, method="sma")
    vals = result.to_numpy()

    # Assert: warmup（index0）は NaN、有効区間は buffer の差と一致
    assert np.isnan(vals[0])
    for i in range(period - 1, len(closes)):
        assert vals[i] == pytest.approx(buf_close[i] - buf_open[i])


# --- TD.4 異常系: period 下限（period<=1）------------------------------------
# 🔴-1 修正で MA 計算を移植元 *_ma_on_buffer に委譲した。これらは period<=1 のとき
# 計算せず buffer を書かない（戻り値 0）。委譲先の沈黙ゼロ出力をそのまま通すと
# 「全位置 0」の誤った MADiff（ゼロクロス誤判定の温床）になるため、period<=1 は
# adapter で明示的に ValueError とする（下限検証・SMA/EMA 共通）。

@pytest.mark.parametrize("method", ["sma", "ema"])
def test_madiff_period_le_1_raises_value_error(method):
    # Arrange
    from backtest.adapter.indicator.madiff import madiff

    df = _ohlc(opens=[1.0, 2.0, 3.0], closes=[2.0, 3.0, 4.0])

    # Act / Assert: period<=1 は委譲先が計算不能（沈黙ゼロ）。明示拒否する。
    with pytest.raises(ValueError):
        madiff(df, period=1, method=method)


# --- TD.4 異常系: 未対応 method --------------------------------------------

def test_madiff_unknown_method_raises_value_error():
    # Arrange
    from backtest.adapter.indicator.madiff import madiff

    df = _ohlc(opens=[1.0, 2.0], closes=[2.0, 3.0])

    # Act / Assert
    with pytest.raises(ValueError):
        madiff(df, period=2, method="bogus")
