"""ADX(period)/+DI/−DI 指標（adapter・SPEC §3.5 / §9・PROCESS §1.2）.

#5 PRO!fit_Band が参照する ``iADX(NULL,0,ADX_Period)`` の 3 バッファ
(buf0=ADX 本線 / buf1=+DI / buf2=−DI) を Wilder 式（MetaQuotes iADX 再現）で算出する.

定義（移植元 ``indigators/profit_adx_needle/src/core.py`` の compute_adx に準拠）:
    各バー i (i>=1) について（昇順 OHLC・先頭 i=0 は前足が無く 0 始点 = warmup）:
        +DM = max(H[i]-H[i-1], 0), −DM = max(L[i-1]-L[i], 0)  （非対称ゼロ化）
        TR  = max(|H-L|, |H-prevC|, |L-prevC|)
        +SDI = 100*(+DM)/TR, −SDI = 100*(−DM)/TR  （TR=0 は 0）
        +DI = EMA(+SDI), −DI = EMA(−SDI)  （α=2/(period+1), seed=index0）
        DX  = 100*|+DI − −DI| / (+DI + −DI)  （分母 0 は 0）
        ADX = EMA(DX)

ADX 本線は profit_adx_needle.compute_adx と数値一致する（同一パイプラインを再現）.
+DI/−DI は compute_adx の内部中間（pdi/mdi）に相当し、本関数で外部公開する.

warmup 方針（SPEC §1.2/§9・既存 EMA 方針と整合）:
    EMA ベースのため index0 から再帰定義され warmup NaN を持たない（MQL 忠実）.
    先頭バー(i=0)は +DM/−DM/TR=0（compute_adx と同一）.

adapter 層は pandas/numpy を内部利用してよい（CLEAN_ARCH §7）. 入力は OHLC の
``pandas.Series``（昇順・同 index）、出力は同 index の ``pandas.Series`` ×3
（PandasIndicatorRegistry へそのまま登録可）.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def _ema(values: np.ndarray, period: int) -> np.ndarray:
    """MQL ``iMAOnArray(..., MODE_EMA)`` 相当の EMA（α=2/(period+1), seed=values[0]）.

    profit_adx_needle.core._ema と同一の漸化式（index0 から再帰）. ADX 本線を
    一次情報と一致させるため同一更新式を用いる.
    """
    v = np.asarray(values, dtype=np.float64)
    n = v.size
    out = np.empty(n, dtype=np.float64)
    if n == 0:
        return out
    alpha = 2.0 / (period + 1.0)
    out[0] = v[0]
    for k in range(1, n):
        out[k] = out[k - 1] + alpha * (v[k] - out[k - 1])
    return out


def compute_adx_with_di(
    high: pd.Series, low: pd.Series, close: pd.Series, period: int = 8
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """ADX 本線・+DI・−DI を入力 index に揃えた Series 3 本で返す（SPEC §3.5）.

    Args:
        high/low/close: 各バーの高値/安値/終値（昇順・同 index・同長の pandas.Series）.
        period: 平滑期間（SPEC §3.5 は ADX_Period=8）.

    Returns:
        ``(adx, plus_di, minus_di)``. それぞれ入力 index の pandas.Series.
        ADX 本線は profit_adx_needle.compute_adx と数値一致する.

    Raises:
        ValueError: 配列長が不一致、空、または period<=0 の場合.
    """
    index = high.index
    h = high.to_numpy(dtype=np.float64)
    lo = low.to_numpy(dtype=np.float64)
    c = close.to_numpy(dtype=np.float64)
    if not (h.size == lo.size == c.size):
        raise ValueError(f"HLC の長さが不一致です: {[h.size, lo.size, c.size]}")
    n = h.size
    if n == 0:
        raise ValueError("HLC が空です。")
    if period <= 0:
        raise ValueError(f"period は正値である必要があります: {period}")

    pdm = np.zeros(n, dtype=np.float64)
    mdm = np.zeros(n, dtype=np.float64)
    tr = np.zeros(n, dtype=np.float64)

    up = h[1:] - h[:-1]
    dn = lo[:-1] - lo[1:]
    p = np.where(up < 0.0, 0.0, up)
    m = np.where(dn < 0.0, 0.0, dn)
    # 非対称ゼロ化: 同値は両 0、小さい側を 0（compute_adx と同一）.
    eq = p == m
    p_lt = p < m
    pdm[1:] = np.where(eq, 0.0, np.where(p_lt, 0.0, p))
    mdm[1:] = np.where(eq, 0.0, np.where(p_lt, m, 0.0))

    hl = np.abs(h[1:] - lo[1:])
    hc = np.abs(h[1:] - c[:-1])
    lc = np.abs(lo[1:] - c[:-1])
    tr[1:] = np.maximum(np.maximum(hl, hc), lc)

    with np.errstate(divide="ignore", invalid="ignore"):
        sdi_plus = np.where(tr > 0.0, 100.0 * pdm / tr, 0.0)
        sdi_minus = np.where(tr > 0.0, 100.0 * mdm / tr, 0.0)

    pdi = _ema(sdi_plus, period)
    mdi = _ema(sdi_minus, period)

    denom = pdi + mdi
    with np.errstate(divide="ignore", invalid="ignore"):
        dx = np.where(denom != 0.0, 100.0 * np.abs(pdi - mdi) / denom, 0.0)
    adx = _ema(dx, period)

    return (
        pd.Series(adx, index=index),
        pd.Series(pdi, index=index),
        pd.Series(mdi, index=index),
    )
