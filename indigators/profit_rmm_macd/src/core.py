"""層名: core 層（純粋計算）。

責務:
    PRO!fitRMMMACD（RMM レベルカウント＋MACD連鎖の変種）の純粋計算を numpy ＋
    共有層のみで行う層。入出力・描画・pandas を含まない。

    level_count（4 オシレーター funLevelCount 合算）は profit_rmm/src/core.py の
    level_count 生成パイプライン全体を複製する（iWPR/iRSI/iMFI/
    oscillator_span/level_count_score・クランプ非対称・funLevelCount4ケース・合算・
    warm-up・iWPR 権威・flat→50/負MF==0→100 を完全保持・ロジック改変禁止）。
    **ただし ``compute_rmm_level_count`` の採点ループは profit_rmm 側に無い span NaN
    伝播ブロックを持ち、構造上は verbatim ではない**（ISSUE-175・未裁定。詳細は同関数
    の docstring 参照）。
    その level_count に MACD 連鎖を適用する。**ただし MFIMACD/RSIMACD とは 2 点が
    異なる**:

        重要差分①: macd[i] = slow[i] - fast[i]（MFIMACD の fast-slow と逆。元 L272
            ``MacdBuffer[i] = SlowEmaBuffer[i] - FastEmaBuffer[i]``）。
        重要差分②: histogram[i] = macd[i] - signal[i]（×2.618 係数なし。元 L280
            ``MacdHistogramBuffer[i] = (MacdBuffer[i] - SignalBuffer[i])``）。

    σ 水準線は無い（元は funIndicatorSet を OnCalculate で呼ばず・水準を出力しない）。

含む構造:
    compute_wpr / compute_marod / compute_rsi / compute_mfi / oscillator_span /
        rolling_span / level_count_score : profit_rmm の level_count 算出部の verbatim
        複製（_series_avg/_series_std/oscillator_span/rolling_span は姉妹 profit_rmm と
        同一実装の複製。将来の共有層集約候補。本フェーズでは集約しない）。
    compute_rmm_level_count : 上記を採点・合算して level_count を返す（複製だが span
        NaN 伝播ブロックが profit_rmm 側に無い＝ISSUE-175 未裁定。window で
        全期間スカラ span / 因果ローリング span を切替）。
    _first_finite_index / _ema_chain_from_first_finite : EMA 開始位置ずらし＋warm-up
        NaN 埋めを局所化するヘルパ（共有 EMA の NaN 汚染回避。振る舞い不変）。
    compute_rmmmacd         : level_count → fast/slow EMA → macd(=slow-fast) →
        signal EMA → histogram(=macd-signal・係数なし) を統合した frozen DTO を返す。
    RmmMacdResult           : 計算成果の不変 DTO（σ levels フィールドを持たない）。

元 MQL 対応（``PRO!fitRMMMACD.mq4`` L162-280 を昇順=古→新へ 1:1 変換）:
    level_count 部 = PRO!fitRMM.mq4 と同一（iRSI/iWPR/iMFI/MAROD funLevelCount 合算）。
    iMAOnArray(EMA, FastEMA=4 / SlowEMA=8 / SignalEMA=4) → moving_averages.ma /
    exponential_ma_on_buffer（後者は NaN 初期化 buffer が必要な _ema_from の 1 箇所のみ）。
    MACD = Slow - Fast（L272）。Histogram = MACD - Signal（L280・係数なし）。

依存:
    標準: __future__, dataclasses, sys, pathlib / 外部: numpy
    共有: common（typical_price）, moving_averages（ma, exponential_ma_on_buffer）。
    pandas/描画 import は禁止。
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

# 共有ライブラリ moving_averages / mql_builtins（indigators/ 直下）を絶対 import で再利用する。
from moving_averages import exponential_ma_on_buffer, ma
from mql_builtins import (  # noqa: F401  # 正準 iWPR/iRSI/iMFI（再公開して in-package 参照面を維持）
    compute_mfi,
    compute_rsi,
    compute_wpr,
)
from profit_system import (  # noqa: F401  # 正準 funLevelCount/MAROD（再公開して in-package 参照面を維持）
    compute_marod,
    level_count_score,
)

from common import typical_price

# 元 input の既定値（PRO!fitRMMMACD.mq4）。
DEFAULT_OSC_PERIOD: int = 6
DEFAULT_MA_PERIOD: int = 6
DEFAULT_FAST_EMA: int = 4
DEFAULT_SLOW_EMA: int = 8
DEFAULT_SIGNAL_EMA: int = 4

# 標準化窓 W（各オシレーターのスパン avg±3σ を直近 W 本の過去のみから算出＝look-ahead
# 除去・repaint しない）。None で全期間バッチ（従来 1:1・比較用）。日足 ~半年。
# profit_rmm/src/core.py と同一既定（姉妹指標と整合）。
DEFAULT_WINDOW: int | None = 120

# compute_wpr / compute_rsi / compute_mfi は共有 mql_builtins へ集約済み（上部で import・再公開）。
# 既定 period 定数 DEFAULT_OSC_PERIOD は本パッケージに残置し、呼び出しで period= 明示する。


# MAROD（compute_marod）は共有 profit_system へ集約済み（上部で import・再公開）。


# ===========================================================================
# σ 統計（母σ÷N・全系列）— oscillator_span 用（profit_rmm の verbatim 複製）
# ===========================================================================
# 【複製の明示・将来の集約候補】
#   _series_avg / _series_std / oscillator_span / rolling_span の 4 関数は姉妹指標
#   profit_rmm/src/core.py と **完全に同一実装の複製**である（本フェーズで bit-for-bit
#   一致を確認済み）。本来は共有層（例: profit_system もしくは新規 statistics モジュール）
#   へ集約すべき重複だが、profit_system 改修は別タスクであり、共有層への破壊的波及を
#   避けるため **今フェーズでは集約しない**。将来の集約候補としてここに明示する。
# ===========================================================================
def _series_avg(x: np.ndarray) -> float:
    """系列平均（全系列）。"""
    return float(np.mean(x))


def _series_std(x: np.ndarray) -> float:
    """母標準偏差（÷N・全系列）。"""
    x = np.asarray(x, dtype=np.float64)
    avg = _series_avg(x)
    return float(np.sqrt(np.mean((x - avg) ** 2)))


def oscillator_span(x: np.ndarray, *, clamp: bool) -> float:
    """avg±3σ のスパン（x3p - x3m）を返す（profit_rmm の verbatim 複製）。

    ``clamp=True``（RSI/WPR/MFI）→ x3p=min(100,x3p), x3m=max(0,x3m)。
    ``clamp=False``（MAROD）→ クランプ無し。
    """
    avg = _series_avg(x)
    dev = _series_std(x)
    x3p = avg + 3.0 * dev
    x3m = avg - 3.0 * dev
    if clamp:
        x3p = min(100.0, x3p)
        x3m = max(0.0, x3m)
    return x3p - x3m


def rolling_span(
    x: np.ndarray, window: int, *, clamp: bool, freeze_last: bool = False
) -> np.ndarray:
    """``oscillator_span`` の因果ローリング版（profit_rmm の verbatim 複製）。

    バー i のスパンを区間 ``[i-window+1, i]`` の平均・母標準偏差から
    ``(avg+3σ) - (avg-3σ)``（clamp 時は各端を [0,100] に丸め）で求める。未来を含まない
    ため確定バーのスパン＝レベルカウントは repaint しない。warm-up（``i<window-1``）は
    ``NaN``。

    ``freeze_last``（既定 ``False``）:
        * ``False``: 上記の通り（既定。出力は 1 ビットも変えない）。
        * ``True``: **最終要素 ``out[-1]`` のみ** 基準窓を確定足
          ``[n-1-window .. n-2]``（最終点を除く直前 window 本）へ差し替えてスパンを
          算出する。``out[0..n-2]`` は ``freeze_last=False`` と完全に同一。形成中（足内）
          の最新足をティック粒度で採点する際、スパン（採点の分母）の基準を 1 足 1 回・
          足内で固定（凍結）する用途。平均・母標準偏差・分母 ``window``・クランプは
          本関数の既存定義と厳密に同一で、最終点だけ窓をずらした以外は数値が一致する。
          直前 window 本が満たせない（``n < window + 1``）場合は ``out[-1]=NaN``
          （warm-up と同様）。これは ``profit_system._causal_z`` の freeze_last と整合する。
          （profit_rmm.rolling_span の freeze_last と verbatim 複製で同一実装。）

    Args:
        x: 対象オシレーター系列。
        window: 過去参照本数 W（>=2）。
        clamp: True で各端を [0,100] にクランプ（RSI/WPR/MFI）、False で素値（MAROD）。
        freeze_last: True で最終点のスパン基準を確定足（直前 W 本）へ凍結する。既定
            False で挙動不変。

    Returns:
        各バーのスパン（同長, float64。warm-up は NaN）。
    """
    a = np.asarray(x, dtype=np.float64)
    n = a.size
    out = np.full(n, np.nan, dtype=np.float64)
    if window < 2 or n < window:
        return out
    csum = np.concatenate([[0.0], np.cumsum(a)])
    csq = np.concatenate([[0.0], np.cumsum(a * a)])
    for i in range(window - 1, n):
        lo = i - window + 1
        avg = (csum[i + 1] - csum[lo]) / window
        var = (csq[i + 1] - csq[lo]) / window - avg * avg
        dev = np.sqrt(var) if var > 0.0 else 0.0
        x3p = avg + 3.0 * dev
        x3m = avg - 3.0 * dev
        if clamp:
            x3p = min(100.0, x3p)
            x3m = max(0.0, x3m)
        out[i] = x3p - x3m
    if freeze_last:
        # 最終点 out[-1] のみ、基準窓を確定足 [n-1-window .. n-2]（最終点を除く直前
        # window 本）へ差し替える。out[0..n-2] は上のループ結果のまま不変。
        if n < window + 1:
            out[-1] = np.nan  # 直前 window 本を満たせない（warmup 同様）。
        else:
            lo = n - 1 - window  # 直前 window 本 = a[lo:n-1]（= a[n-1-window .. n-2]）。
            hi = n - 1  # csum 上限 index（確定足 a[lo:n-1] の和 = csum[hi]-csum[lo]）。
            avg = (csum[hi] - csum[lo]) / window
            var = (csq[hi] - csq[lo]) / window - avg * avg
            dev = np.sqrt(var) if var > 0.0 else 0.0
            x3p = avg + 3.0 * dev
            x3m = avg - 3.0 * dev
            if clamp:
                x3p = min(100.0, x3p)
                x3m = max(0.0, x3m)
            out[-1] = x3p - x3m
    return out


# funLevelCount（level_count_score）は共有 profit_system へ集約済み（上部で import・再公開）。


# ===========================================================================
# level_count 合算（profit_rmm/src/core.py compute_rmm の level_count 部の複製。
# ただし span NaN 伝播ブロックの有無で構造差あり＝ISSUE-175 未裁定・下記 docstring 参照）
# ===========================================================================
def compute_rmm_level_count(
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    volume: np.ndarray,
    *,
    osc_period: int = DEFAULT_OSC_PERIOD,
    ma_period: int = DEFAULT_MA_PERIOD,
    window: int | None = DEFAULT_WINDOW,
    freeze_last: bool = False,
) -> np.ndarray:
    """iRSI / iWPR / iMFI / MAROD を funLevelCount で採点・合算した level_count を返す。

    profit_rmm/src/core.py ``compute_rmm`` の level_count 算出パイプラインを移植した
    もの。``window=None`` 時は全期間スカラ span（従来 1:1。同一入力で
    ``compute_rmm(..., window=None).level_count`` と bit-for-bit 一致）。``window=W``
    時は因果ローリング span 配列で各バー span[i] を採点する（look-ahead 除去・repaint
    しない）。warm-up（``i<window-1``）は span NaN → level_count_score NaN →
    level_count NaN（非描画）。

    profit_rmm との差分（ISSUE-175・未裁定）:
        本関数は採点ループ末尾に ``if window is not None and (not np.isfinite(...))``
        で ``lc`` を NaN へ上書きする明示ブロックを持つ。profit_rmm/src/core.py
        ``compute_rmm`` の対応ループに当該ブロックは無く、span NaN は
        ``level_count_score`` の戻り値経由でのみ伝播する。両者の出力が分かれ得るのは
        「非有限 span が存在し、かつ 4 採点のいずれも NaN を生まないバー」（4 オシレー
        ターすべてが境界ちょうど（rsi/wpr/mfi == 50.0・marod == 0.0）または NaN で
        採点分岐がスキップされる場合等）に限られ、それ以外のバーは profit_rmm 側も
        採点値経由で NaN となり一致する。実測（乱数 400 バー＋完全フラット 200 バーの
        2 データセット）では数値差 0 件であり、本ブロックが数値差を生む入力は未確認。
        したがって構造上は verbatim 複製ではないが、確認済みの範囲では出力は一致する。
        どちらが移植元の正解かは元 MQL（``PRO!fitRMM.mq4`` /
        ``PRO!fitRMMMACD.mq4``）が未入手のため未裁定であり、本変更では計算ロジックを
        一切変更せず現状の挙動を維持する。

    Args:
        high/low/close/volume: 昇順 OHLCV（同長）。
        osc_period: オシレーター期間（既定 6、>=2）。
        ma_period: EMA 期間（既定 6）。
        window: 標準化窓 W（既定 120＝因果。None で全期間バッチ）。
        freeze_last: True かつ ``window is not None`` のとき、最終点のスパン基準
            （採点の分母）を確定足（直前 W 本）に凍結する（``rolling_span`` 参照）。
            ``window=None`` 経路では無関係（未使用）。既定 False で挙動不変。

    Returns:
        level_count 系列（入力と同長・float64。因果版 warm-up は NaN）。

    Raises:
        ValueError: ``osc_period < 2``、または HLCV 長不一致。
    """
    if osc_period < 2:
        raise ValueError(f"osc_period は 2 以上である必要があります: {osc_period}")

    high = np.asarray(high, dtype=np.float64)
    low = np.asarray(low, dtype=np.float64)
    close = np.asarray(close, dtype=np.float64)
    volume = np.asarray(volume, dtype=np.float64)
    if not (high.shape == low.shape == close.shape == volume.shape):
        raise ValueError(
            f"HLCV の長さが一致しません: "
            f"{high.shape}/{low.shape}/{close.shape}/{volume.shape}"
        )

    typical = typical_price(high, low, close)
    rsi = compute_rsi(typical, period=osc_period)
    mfi = compute_mfi(high, low, close, volume, period=osc_period)
    wpr = compute_wpr(high, low, close, period=osc_period) + 100.0

    ma_values = ma(typical, "ema", ma_period)
    marod = compute_marod(typical, ma_values)

    n = close.shape[0]
    # スパン（採点の分母）を全期間スカラ（window=None）か因果ローリング（window=W）で用意。
    # 配列化して各バー span[i] を参照する（因果時は warm-up が NaN→採点 NaN→level_count NaN）。
    if window is None:
        rsi_span = np.full(n, oscillator_span(rsi, clamp=True))
        wpr_span = np.full(n, oscillator_span(wpr, clamp=True))
        mfi_span = np.full(n, oscillator_span(mfi, clamp=True))
        marod_span = np.full(n, oscillator_span(marod, clamp=False))
    else:
        rsi_span = rolling_span(rsi, window, clamp=True, freeze_last=freeze_last)
        wpr_span = rolling_span(wpr, window, clamp=True, freeze_last=freeze_last)
        mfi_span = rolling_span(mfi, window, clamp=True, freeze_last=freeze_last)
        marod_span = rolling_span(marod, window, clamp=False, freeze_last=freeze_last)

    level_count = np.zeros(n, dtype=np.float64)
    for i in range(n):
        lc = 0.0
        if rsi[i] < 50.0:
            lc += level_count_score(rsi[i], rsi_span[i], 1)
        elif rsi[i] > 50.0:
            lc += level_count_score(rsi[i], rsi_span[i], 0)
        if wpr[i] < 50.0:
            lc += level_count_score(wpr[i], wpr_span[i], 1)
        elif wpr[i] > 50.0:
            lc += level_count_score(wpr[i], wpr_span[i], 0)
        if mfi[i] < 50.0:
            lc += level_count_score(mfi[i], mfi_span[i], 1)
        elif mfi[i] > 50.0:
            lc += level_count_score(mfi[i], mfi_span[i], 0)
        if marod[i] < 0.0:
            lc += level_count_score(marod[i], marod_span[i], 2)
        elif marod[i] > 0.0:
            lc += level_count_score(marod[i], marod_span[i], 3)
        # 因果版: span が NaN（warm-up）のバーは level_count を NaN（非描画）にする。
        # 全採点が中立（4 オシレーターとも ==境界）でも span NaN を確実に伝播させる。
        if window is not None and (
            not np.isfinite(rsi_span[i])
            or not np.isfinite(wpr_span[i])
            or not np.isfinite(mfi_span[i])
            or not np.isfinite(marod_span[i])
        ):
            lc = np.nan
        level_count[i] = lc

    return level_count


# ===========================================================================
# EMA 開始位置ずらし（warm-up NaN 非汚染）ヘルパ
# ===========================================================================
def _first_finite_index(series: np.ndarray) -> int:
    """``series`` の最初の有限（非 NaN/Inf）要素の index を返す。

    全要素が非有限なら ``series.size``（＝有限スライス長 0）を返す。EMA を有限
    スライスにのみ適用して warm-up NaN による全期間汚染を避けるための開始位置
    として用いる（``compute_rmmmacd`` の EMA 非汚染方針を参照）。
    """
    finite_mask = np.isfinite(series)
    if not finite_mask.any():
        return series.size  # 全非有限 → 有限スライスなし（EMA 非実行）
    return int(np.argmax(finite_mask))


def _ema_chain_from_first_finite(
    series: np.ndarray, start: int, period: int
) -> np.ndarray:
    """``series[start:]`` に共有 EMA を適用し、``[0:start]`` を NaN 埋めして元長で返す。

    共有 EMA（``exponential_ma_on_buffer``）は種 ``buffer[0]=price[0]`` かつ NaN
    ガード無しのため、先頭の warm-up NaN を入れると EMA が全期間 NaN 汚染する。
    本ヘルパは有限スライス ``series[start:]`` にのみ EMA を実行し、warm-up 区間
    ``[0:start]`` を NaN のまま残すことで汚染を局所化する（共有 EMA 実装は触らない）。

    Args:
        series: 入力系列（先頭 ``[0:start]`` が warm-up NaN を含みうる, float64）。
        start: 最初の有限要素 index（``_first_finite_index`` の戻り値）。
        period: EMA 期間。

    Returns:
        EMA 適用結果（``series`` と同長, float64。``[0:start]`` は NaN）。
        有限スライス長 0（``start>=size``）なら全 NaN を返す（EMA 非実行）。
    """
    n = series.size
    out = np.full(n, np.nan, dtype=np.float64)
    seg = series[start:]
    m = seg.shape[0]
    # 共有 EMA（exponential_ma_on_buffer）は period > m（有限スライス長 < EMA 期間）
    # のとき buffer へ何も書かず 0 を返す。seg_out を 0 初期化すると、その偽 0.0 が
    # 活性区間に混入する（本来は EMA 不能＝非描画 NaN であるべき）。そこで seg_out を
    # NaN 初期化し、m >= period のときだけ EMA を実行して書き戻す。m < period では
    # out[start:] を NaN のまま残す（非描画）。共有 EMA 実装は触らない。
    if m >= period:
        seg_out = np.full(m, np.nan, dtype=np.float64)
        exponential_ma_on_buffer(m, 0, 0, period, seg, seg_out)
        out[start:] = seg_out
    return out


# ===========================================================================
# 合成（MACD 連鎖・σ 水準なし）
# ===========================================================================
@dataclass(frozen=True)
class RmmMacdResult:
    """PRO!fitRMMMACD の計算成果（数値のみ・描画非依存の不変 DTO）。

    **σ levels フィールドは持たない**（元は水準を出力しない）。

    Attributes:
        level_count: RMM レベルカウント系列（writeable=False）。
        fast: level_count の EMA(FastEMA) 系列（writeable=False）。
        slow: level_count の EMA(SlowEMA) 系列（writeable=False）。
        macd: slow - fast（重要差分①。writeable=False）。
        signal: EMA(macd, SignalEMA)（writeable=False）。
        histogram: macd - signal（重要差分②・係数なし。writeable=False）。
    """

    level_count: np.ndarray
    fast: np.ndarray
    slow: np.ndarray
    macd: np.ndarray
    signal: np.ndarray
    histogram: np.ndarray

    def __post_init__(self) -> None:
        for name in ("level_count", "fast", "slow", "macd", "signal", "histogram"):
            arr = np.asarray(getattr(self, name), dtype=np.float64)
            arr.setflags(write=False)  # DTO は不変
            object.__setattr__(self, name, arr)


def compute_rmmmacd(
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    volume: np.ndarray,
    *,
    osc_period: int = DEFAULT_OSC_PERIOD,
    ma_period: int = DEFAULT_MA_PERIOD,
    fast: int = DEFAULT_FAST_EMA,
    slow: int = DEFAULT_SLOW_EMA,
    signal: int = DEFAULT_SIGNAL_EMA,
    window: int | None = DEFAULT_WINDOW,
    freeze_last: bool = False,
) -> RmmMacdResult:
    """level_count → fast/slow EMA → macd(=slow-fast) → signal EMA →
    histogram(=macd-signal・係数なし) を統合し RmmMacdResult（frozen DTO）を返す。

    計算順序（元 MQL の 1:1 再現）::

        1. level_count = compute_rmm_level_count(..., window=window)  # 因果/全期間
        2. fast = EMA(level_count, fast) ; slow = EMA(level_count, slow)  # 共有
        3. macd[i] = slow[i] - fast[i]                  # 重要差分①（L272）
        4. signal = EMA(macd, signal)
        5. histogram[i] = macd[i] - signal[i]           # 重要差分②（L280・係数なし）

    **EMA 非汚染（最重要）**: 共有 EMA（exponential_ma_on_buffer）は種 buffer[0]=
    price[0] かつ NaN ガード無しのため、level_count 先頭の warm-up NaN をそのまま
    入れると EMA が全期間 NaN 汚染する。これを避けるため、level_count の最初の有限
    index ``start = argmax(isfinite(level_count))``（全 NaN なら start=n＝EMA 非実行）
    を求め、EMA は ``level_count[start:]`` の有限スライスに対して実行し、結果を元長へ
    戻す際 ``[0:start]`` を NaN 埋めする。fast/slow/macd/signal/histogram すべて
    warm-up 区間を NaN（非描画）にする。共有 EMA 実装は触らない。

    ``window=None``（全期間版）では level_count に NaN が無いため start=0 となり、
    従来挙動（全長 EMA）と一致する。

    σ 水準は算出しない（元は水準を出力しない）。

    Args:
        high/low/close/volume: 昇順 OHLCV（同長）。
        osc_period: オシレーター期間（既定 6, >=2）。
        ma_period: EMA 期間（既定 6）。
        fast: FastEMA 期間（既定 4）。
        slow: SlowEMA 期間（既定 8）。
        signal: SignalEMA 期間（既定 4）。
        window: 標準化窓 W（既定 120＝因果。None で全期間バッチ）。
        freeze_last: True かつ ``window is not None`` のとき、level_count の最終点の
            スパン基準を確定足（直前 W 本）に凍結する（``compute_rmm_level_count`` /
            ``rolling_span`` 参照）。凍結により最終点の level_count が変わると、その点を
            入力とする fast/slow/macd/signal/histogram の最終点も EMA 連鎖で追従する
            （ただし標準化窓の凍結は level_count の最終点のみ）。既定 False で挙動不変。

    Returns:
        RmmMacdResult（level_count/fast/slow/macd/signal/histogram。因果版 warm-up
        は全フィールド NaN）。

    Raises:
        ValueError: ``osc_period < 2`` または HLCV 長不一致（compute_rmm_level_count 経由）。
    """
    level_count = compute_rmm_level_count(
        high, low, close, volume,
        osc_period=osc_period, ma_period=ma_period, window=window,
        freeze_last=freeze_last,
    )

    # EMA 開始位置 = level_count の最初の有限 index。warm-up NaN を EMA に入れない
    # ことで共有 EMA の全期間 NaN 汚染を回避する（_ema_chain_from_first_finite 参照）。
    # macd/signal も同一 start を共有: macd は有限スライス上 NaN を含まず、その
    # first-finite は start と一致するため、全フィールドの warm-up 境界が揃う。
    start = _first_finite_index(level_count)

    fast_buf = _ema_chain_from_first_finite(level_count, start, fast)
    slow_buf = _ema_chain_from_first_finite(level_count, start, slow)
    macd = slow_buf - fast_buf  # 重要差分①: Slow - Fast（元 L272）。warm-up NaN は伝播。
    signal_buf = _ema_chain_from_first_finite(macd, start, signal)
    histogram = macd - signal_buf  # 重要差分②: 係数なし（元 L280）。warm-up NaN は伝播。

    return RmmMacdResult(
        level_count=level_count,
        fast=fast_buf,
        slow=slow_buf,
        macd=macd,
        signal=signal_buf,
        histogram=histogram,
    )
