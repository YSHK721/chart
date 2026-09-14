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

from dataclasses import dataclass

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
    out, _ = _compute_rsi_core(price, period)
    return out


# ---------------------------------------------------------------------------
# 増分計算用の状態授受（ISSUE-249・moving_averages の LwmaState と同じ先例）
#   漸化式の定義は下の共有部品 :func:`_rsi_seed` / :func:`_rsi_advance` の 1 箇所のみ。
#   :func:`compute_rsi`（全件）と :func:`compute_rsi_stateful`（状態継続）はどちらもこれを
#   呼ぶため、二重定義は生じず値は構成上 bit 一致する。
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class RsiState:
    """Wilder 平滑の継続に必要な最小状態（不変）。

    Attributes:
        pos: 平滑済み up 平均。
        neg: 平滑済み down 平均。
        last_price: 直前バーの価格（次バーの diff に要る）。
        count: これまでに消費した価格本数（＝次に来るバーの index）。
    """

    pos: float
    neg: float
    last_price: float
    count: int


def _rsi_seed(price: np.ndarray, period: int) -> "tuple[float, float]":
    """seed（i == period）: 最初の period 本の up/down を単純平均する。"""
    sum_pos = 0.0
    sum_neg = 0.0
    for i in range(1, period + 1):
        diff = price[i] - price[i - 1]
        sum_pos += diff if diff > 0.0 else 0.0
        sum_neg += -diff if diff < 0.0 else 0.0
    return sum_pos / period, sum_neg / period


def _rsi_advance(pos: float, neg: float, diff: float, period: int) -> "tuple[float, float]":
    """main（i > period）: Wilder 平滑を 1 バーぶん進める。"""
    pos = (pos * (period - 1) + (diff if diff > 0.0 else 0.0)) / period
    neg = (neg * (period - 1) + (-diff if diff < 0.0 else 0.0)) / period
    return pos, neg


def _compute_rsi_core(
    price: np.ndarray, period: int, state: "RsiState | None" = None
) -> "tuple[np.ndarray, RsiState | None]":
    """RSI 系列と最終状態を返す唯一の実装（全件・状態継続の共通経路）。

    ``state`` が None なら先頭から seed する（＝従来の全件計算）。``state`` が与えられた
    ときは ``price`` を「その状態の続き」とみなし、seed を行わず漸化のみを進める。
    """
    if period < 2:
        raise ValueError(f"period は 2 以上である必要があります: {period}")

    price = np.asarray(price, dtype=np.float64)
    n = price.shape[0]
    out = np.zeros(n, dtype=np.float64)  # warm-up 区間は 0（元 iRSI 既定）

    if state is None:
        if n <= period:
            return out, None  # 元 RSI.mq5: rates_total<=period -> return 0
        pos, neg = _rsi_seed(price, period)
        out[period] = _rsi_from_pos_neg(pos, neg)
        start = period + 1
        prev = price[period]
        consumed = period + 1
    else:
        if n == 0:
            return out, state
        pos, neg, prev, consumed = state.pos, state.neg, state.last_price, state.count
        start = 0

    for i in range(start, n):
        pos, neg = _rsi_advance(pos, neg, price[i] - prev, period)
        out[i] = _rsi_from_pos_neg(pos, neg)
        prev = price[i]
        consumed += 1
    return out, RsiState(pos=pos, neg=neg, last_price=float(prev), count=consumed)


def compute_rsi_stateful(
    price: np.ndarray, *, period: int, state: "RsiState | None" = None
) -> "tuple[np.ndarray, RsiState | None]":
    """RSI 系列と継続用状態を返す（増分計算の入口）。

    ``state=None`` は :func:`compute_rsi` と完全に同じ全件計算（同じ共有部品を通る）。
    ``state`` を渡すと ``price`` をその続きのバー列として扱い、seed を行わず漸化のみ進める。

    Args:
        price: 昇順（古→新）の価格系列。``state`` 指定時はその状態の**続き**のバーのみ。
        period: RSI 期間（>=2）。
        state: 前回の :class:`RsiState`（None は先頭から）。

    Returns:
        ``(rsi 配列（入力と同長）, 最終 RsiState)``。seed 未達（``state=None`` かつ
        ``len(price) <= period``）のときは ``(全 0, None)``。
    """
    return _compute_rsi_core(price, period, state)


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

    for i in range(period, n):
        pos = 0.0
        neg = 0.0
        for j in range(i - period + 1, i + 1):
            if j < 1:
                continue
            if tp[j] > tp[j - 1]:
                pos += mf[j]
            elif tp[j] < tp[j - 1]:
                neg += mf[j]
            # tp[j] == tp[j-1] は加算しない（非対称・§4.4）
        # 組込 iMFI（MetaQuotes 公式 MFI.mq5 L107-110 / MFI.mq4 L86-89）に厳密準拠:
        #   負MF != 0 -> 100 - 100/(1 + 正MF/負MF) = 100*正MF/(正MF+負MF)
        #   負MF == 0 -> 100（flat window で 正MF==0 かつ 負MF==0 の場合も 100）
        if neg == 0.0:
            out[i] = 100.0  # 負MF==0（all-up / flat window 含む）→ 100
        else:
            out[i] = 100.0 - 100.0 / (1.0 + pos / neg)  # 正MF==0 のとき 0
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

    for i in range(period - 1, n):  # 最初の有効値は i=period-1
        max_high = np.max(high[i - period + 1 : i + 1])
        min_low = np.min(low[i - period + 1 : i + 1])
        if max_high != min_low:
            out[i] = -(max_high - close[i]) * 100.0 / (max_high - min_low)
        else:
            out[i] = out[i - 1]  # 前値を引き継ぐ
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
    for a in range(period - 1, n):
        window = slice(a - period + 1, a + 1)
        ll = low[window].min()
        hh = high[window].max()
        rng = hh - ll
        if rng == 0.0:
            out[a] = 0.0  # ゼロ割は 0（spec 確定）
        else:
            out[a] = 100.0 * (close[a] - ll) / rng
    return out
