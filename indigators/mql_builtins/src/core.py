"""層名: core 層（共有プリミティブ・純粋計算）。

責務:
    MetaTrader 組込指標（iRSI / iMFI / iWPR / iStochastic）の純粋数値計算を
    numpy 配列のみで行う共有実装。入出力・描画・pandas・指標パッケージ・
    profit_system には依存しない（循環依存禁止）。各指標パッケージが重複保持
    していた compute_rsi / compute_mfi / compute_wpr / compute_stochastic を
    1:1（docstring 除く本体 AST 一致）で集約した正準実装。

提供するプリミティブ:
    compute_rsi        : iRSI（Wilder RSI・flat→50）。正準 profit_rsi。
    compute_mfi        : iMFI（Money Flow Index・負MF==0→100）。正準 profit_mfi。
    compute_wpr        : iWPR（Williams %R・flat→前値・warm-up i<period-1）。正準 profit_rmm。
    compute_stochastic : iStochastic 生 %K（fast, MODE_MAIN）。正準 profit_stc。

契約統一（共有化に伴う唯一の差分）:
    各関数の ``period`` を **キーワード必須**（デフォルト引数なし）とする。
    各パッケージの既定値（RSI=6 / MFI=14 等）は各 core 側の定数として残置し、
    呼び出し時に ``period=<定数>`` で明示渡しする。本体ロジックは正準と 1:1。

元 MQL 対応（昇順=古→新へ 1:1 変換）:
    iRSI(period, applied) → compute_rsi（Wilder 平滑・diff=price[i]-price[i-1]）。
    iMFI(period)          → compute_mfi（TP=(H+L+C)/3, MF=TP*Volume・非対称加算）。
    iWPR(period)          → compute_wpr（warm-up [0..period-2]=0・最初の有効値 i=period-1）。
    iStochastic(period,1,1,...,MODE_MAIN) → compute_stochastic（生 %K・slowing/Dperiod 恒等）。

依存:
    標準: __future__ / 外部: numpy のみ / プロジェクト内: なし
    （指標パッケージ・profit_system・common・moving_averages を引き込まない。）
"""

from __future__ import annotations

import numpy as np


# ===========================================================================
# iRSI（正準 profit_rsi.compute_rsi の verbatim 本体・契約のみ period 必須化）
# ===========================================================================
def compute_rsi(price: np.ndarray, *, period: int) -> np.ndarray:
    """昇順 価格系列から iRSI 系列（warm-up 0）を返す。

    元 MQL5 ``RSI.mq5`` の iRSI（Wilder 平滑）を昇順で 1:1 再現する::

        diff[i] = price[i] - price[i-1]
        seed (i == period):
            pos = mean_{j=1..period}(max(diff[j], 0))
            neg = mean_{j=1..period}(max(-diff[j], 0))
        main (i > period):
            pos[i] = (pos[i-1]*(period-1) + max(diff[i], 0)) / period
            neg[i] = (neg[i-1]*(period-1) + max(-diff[i], 0)) / period
        RSI[i]:
            neg != 0            -> 100 - 100/(1 + pos/neg)
            neg == 0, pos != 0  -> 100
            neg == 0, pos == 0  -> 50
        warm-up (i < period)    -> 0

    Args:
        price: 昇順（古→新, index 0=最古）の価格系列。
        period: RSI 期間（>=2, キーワード必須）。

    Returns:
        RSI 配列（入力と同長, float64）。``len(price) <= period`` のときは全 0。

    Raises:
        ValueError: ``period < 2``。
    """
    if period < 2:
        raise ValueError(f"period は 2 以上である必要があります: {period}")

    price = np.asarray(price, dtype=np.float64)
    n = price.shape[0]
    out = np.zeros(n, dtype=np.float64)  # warm-up 区間は 0（元 iRSI 既定）
    if n <= period:
        return out  # 元 RSI.mq5: rates_total<=period -> return 0

    # --- seed（i == period）: 最初の period 本の up/down を単純平均
    sum_pos = 0.0
    sum_neg = 0.0
    for i in range(1, period + 1):
        diff = price[i] - price[i - 1]
        sum_pos += diff if diff > 0.0 else 0.0
        sum_neg += -diff if diff < 0.0 else 0.0
    pos = sum_pos / period
    neg = sum_neg / period
    out[period] = _rsi_from_pos_neg(pos, neg)

    # --- main loop（i > period）: Wilder 平滑
    for i in range(period + 1, n):
        diff = price[i] - price[i - 1]
        pos = (pos * (period - 1) + (diff if diff > 0.0 else 0.0)) / period
        neg = (neg * (period - 1) + (-diff if diff < 0.0 else 0.0)) / period
        out[i] = _rsi_from_pos_neg(pos, neg)
    return out


def _rsi_from_pos_neg(pos: float, neg: float) -> float:
    """平滑済み up/down 平均（pos/neg）から RSI 値を返す（元 iRSI の場合分け）。"""
    if neg != 0.0:
        return 100.0 - 100.0 / (1.0 + pos / neg)
    if pos != 0.0:
        return 100.0
    return 50.0  # neg==0 かつ pos==0（flat window）-> 50


# ===========================================================================
# iMFI（正準 profit_mfi.compute_mfi の verbatim 本体・契約のみ period 必須化）
# ===========================================================================
def compute_mfi(
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    volume: np.ndarray,
    *,
    period: int,
) -> np.ndarray:
    """昇順 OHLCV から iMFI 系列（warm-up 0）を返す。

    各バー i について::

        TP[i] = (high[i] + low[i] + close[i]) / 3
        MF[i] = TP[i] * volume[i]

    バー i（i>=period）の窓 ``[i-period+1 .. i]`` の各 j（j>=1）で::

        TP[j] > TP[j-1] -> 正MF += MF[j]
        TP[j] < TP[j-1] -> 負MF += MF[j]
        TP[j] == TP[j-1] -> 加算しない（非対称・§4.4）

        負MF != 0 -> MFI[i] = 100 - 100/(1 + 正MF/負MF) = 100*正MF/(正MF+負MF)
        負MF == 0 -> MFI[i] = 100

    組込 iMFI（MetaQuotes 公式 MFI.mq5 L107-110 / MFI.mq4 L86-89）準拠:
    - 負MF == 0（all-up / flat window で正MF==0 も含む） -> 100
    - 負MF >  0 かつ 正MF == 0                          -> 0
    - warm-up（i < period）                            -> 0（NaN ではない。元 iMFI/SetIndexDrawBegin 既定）

    Args:
        high/low/close/volume: 昇順（古→新, index 0=最古）の配列（同長）。
        period: MFI 期間（>=2, キーワード必須）。

    Returns:
        MFI 配列（入力と同長, float64）。

    Raises:
        ValueError: ``period < 2``、または high/low/close/volume の長さ不一致。
    """
    if period < 2:
        raise ValueError(f"period は 2 以上である必要があります: {period}")

    high = np.asarray(high, dtype=np.float64)
    low = np.asarray(low, dtype=np.float64)
    close = np.asarray(close, dtype=np.float64)
    volume = np.asarray(volume, dtype=np.float64)
    if not (high.shape == low.shape == close.shape == volume.shape):
        raise ValueError(
            f"high/low/close/volume の長さが一致しません: "
            f"{high.shape}/{low.shape}/{close.shape}/{volume.shape}"
        )

    n = high.shape[0]
    tp = (high + low + close) / 3.0  # int 切り捨てを持ち込まない（float 精度・§4.1）
    mf = tp * volume
    out = np.zeros(n, dtype=np.float64)  # warm-up 区間は 0（元 iMFI 既定）

    if n <= period:
        return out  # 窓が成立しない（元ループ range(period, n) が空）

    # 旧: 二重ループ（i×j）で窓 [i-period+1 .. i] の正/負 MF を逐次加算（O(n·period)）。
    # j ごとの符号判定（tp[j]≷tp[j-1]）は i に依存しないため、正/負 MF を要素配列として
    # 先に確定し、窓和は「k=0(最古 j=i-period+1)..period-1(最新 j=i)」の period 回シフト加算で
    # 取る。元ループと同一の加算順（古→新）を左結合で保持するためビット一致する
    # （i>=period では j>=1 が常に成立し旧 j<1 ガードは不要・乱数掃引で実証済み）。
    diff = tp[1:] - tp[:-1]  # diff[j-1] が バー j（j>=1）の tp 差
    pos_mf = np.zeros(n, dtype=np.float64)
    neg_mf = np.zeros(n, dtype=np.float64)
    pos_mf[1:] = np.where(diff > 0.0, mf[1:], 0.0)
    neg_mf[1:] = np.where(diff < 0.0, mf[1:], 0.0)

    pos_sum = np.zeros(n, dtype=np.float64)
    neg_sum = np.zeros(n, dtype=np.float64)
    for k in range(period):  # k=0 が窓内最古、k=period-1 が現バー（元ループ加算順）
        pos_sum[period:] += pos_mf[1 + k : n - period + 1 + k]
        neg_sum[period:] += neg_mf[1 + k : n - period + 1 + k]

    # 組込 iMFI（MetaQuotes 公式 MFI.mq5 L107-110 / MFI.mq4 L86-89）に厳密準拠:
    #   負MF != 0 -> 100 - 100/(1 + 正MF/負MF) = 100*正MF/(正MF+負MF)
    #   負MF == 0 -> 100（flat window で 正MF==0 かつ 負MF==0 の場合も 100）
    neg_w = neg_sum[period:]
    with np.errstate(divide="ignore", invalid="ignore"):
        out[period:] = np.where(
            neg_w == 0.0,  # 負MF==0（all-up / flat window 含む）→ 100
            100.0,
            100.0 - 100.0 / (1.0 + pos_sum[period:] / neg_w),  # 正MF==0 のとき 0
        )
    return out


# ===========================================================================
# iWPR（正準 profit_rmm.compute_wpr の verbatim 本体・契約のみ period 必須化）
# ===========================================================================
def compute_wpr(
    high: np.ndarray, low: np.ndarray, close: np.ndarray, *, period: int
) -> np.ndarray:
    """昇順 HLC から生 Williams %R 系列（-100..0, warm-up 0）を返す。

    権威 MQL5 ``WPR.mq5`` を昇順で 1:1 再現する::

        n < period                  -> 全 0（rates_total<period -> return 0）
        warm-up (i < period-1)      -> 0（[0..period-2] を 0 埋め。最初の有効値は i=period-1）
        i >= period-1:
            maxH = max(high[i-period+1 .. i])   # 現バー含む直近 period 本
            minL = min(low[i-period+1 .. i])
            maxH != minL -> wpr[i] = -(maxH - close[i]) * 100 / (maxH - minL)
            maxH == minL -> wpr[i] = wpr[i-1]   # 前値を引き継ぐ

    **iRSI/iMFI の warm-up（i<period）とは 1 本ズレる**（WPR は i<period-1）。

    契約変更（集約に伴う）:
        本ライブラリは compute_rsi/compute_mfi/compute_wpr/compute_stochastic の
        全 4 関数で ``period`` を **キーワード必須（keyword-only）** で受ける。
        特に **compute_wpr は集約前 profit_rmm では ``period`` が位置引数だったが、
        本ライブラリでキーワード必須化した契約変更**である（``compute_wpr(h, l, c, 6)``
        は不可、``compute_wpr(h, l, c, period=6)`` で呼ぶ）。

    Args:
        high/low/close: 昇順（古→新）の HLC 配列（同長）。
        period: WPR 期間（キーワード必須）。

    Returns:
        生 WPR 配列（入力と同長, float64）。範囲 [-100, 0]。
    """
    high = np.asarray(high, dtype=np.float64)
    low = np.asarray(low, dtype=np.float64)
    close = np.asarray(close, dtype=np.float64)
    n = high.shape[0]
    out = np.zeros(n, dtype=np.float64)  # warm-up [0..period-2] は 0
    if n < period:
        return out  # WPR.mq5: rates_total<period -> return 0

    # 旧: i ごとに直近 period 本の max/min を窓走査（O(n·period)）。max/min は要素選択で
    # 加算順に依存しないため sliding_window_view で一括算出してもビット一致する。
    # flat 窓（max==min）の「前値引き継ぎ out[i]=out[i-1]」は逐次依存だが、これは
    # 「直近の非 flat 出力値（無ければ warm-up の 0）」の前方補完（ffill）と等価であり、
    # 値コピーのみで算術を伴わないためビット一致する（乱数掃引で実証済み）。
    windows_high = np.lib.stride_tricks.sliding_window_view(high, period)
    windows_low = np.lib.stride_tricks.sliding_window_view(low, period)
    max_high = windows_high.max(axis=1)  # index w は i=w+period-1 に対応
    min_low = windows_low.min(axis=1)
    denom = max_high - min_low
    close_v = close[period - 1 :]
    with np.errstate(divide="ignore", invalid="ignore"):
        raw = np.where(
            denom != 0.0,
            -(max_high - close_v) * 100.0 / denom,
            np.nan,  # flat 窓は後段 ffill で前値引き継ぎ
        )
    # ffill: flat（nan）位置を直近の非 flat 値で補完。先頭より前の有効値が無ければ
    # out[period-2]=0（warm-up 既定）を引き継ぐ。
    valid = ~np.isnan(raw)
    positions = np.arange(raw.shape[0])
    last_valid = np.where(valid, positions, -1)
    np.maximum.accumulate(last_valid, out=last_valid)
    filled = np.where(last_valid >= 0, raw[np.where(last_valid >= 0, last_valid, 0)], 0.0)
    out[period - 1 :] = filled
    return out


# ===========================================================================
# iStochastic 生 %K（正準 profit_stc.compute_stochastic の verbatim 本体・period 必須化）
# ===========================================================================
def compute_stochastic(
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    *,
    period: int,
) -> np.ndarray:
    """直近 period 本の高安レンジに対する終値位置 %K（生・fast）を返す。

    各 a について（直近 period 本・現足 a を含む）::

        LL = min(low[a-period+1 .. a])
        HH = max(high[a-period+1 .. a])
        %K[a] = 100 * (close[a] - LL) / (HH - LL)

    - warm-up（a < period-1）: 0（元 iStochastic 既定。NaN ではない）。
    - ゼロ割（HH == LL）: 0（spec 確定）。

    Args:
        high: 高値配列（昇順, 古→新）。
        low: 安値配列（昇順）。
        close: 終値配列（昇順）。
        period: Kperiod（>=2, キーワード必須・元 inpPeriodOscillator）。

    Returns:
        %K 配列（入力と同長, float64）。

    Raises:
        ValueError: ``period < 2``、または high/low/close の長さ不一致。
    """
    if period < 2:
        raise ValueError(f"period は 2 以上である必要があります: {period}")

    high = np.asarray(high, dtype=np.float64)
    low = np.asarray(low, dtype=np.float64)
    close = np.asarray(close, dtype=np.float64)
    if not (high.shape == low.shape == close.shape):
        raise ValueError(
            f"high/low/close の長さが一致しません: "
            f"{high.shape}/{low.shape}/{close.shape}"
        )

    n = high.shape[0]
    out = np.zeros(n, dtype=np.float64)  # warm-up 区間は 0（元 iStochastic 既定）
    if n < period:
        return out  # 窓が成立しない（元ループ range(period-1, n) が空）

    # 旧: a ごとに直近 period 本の min/max を窓走査（O(n·period)）。min/max は要素選択で
    # 加算順非依存ゆえ sliding_window_view で一括算出してもビット一致。flat 窓（HH==LL）は
    # ゼロ割を 0 とする（前値引き継ぎ無し）。式は要素単位で旧実装と同一演算（乱数掃引で実証済み）。
    min_low = np.lib.stride_tricks.sliding_window_view(low, period).min(axis=1)
    max_high = np.lib.stride_tricks.sliding_window_view(high, period).max(axis=1)
    rng = max_high - min_low
    close_v = close[period - 1 :]
    with np.errstate(divide="ignore", invalid="ignore"):
        out[period - 1 :] = np.where(
            rng == 0.0, 0.0, 100.0 * (close_v - min_low) / rng
        )
    return out
