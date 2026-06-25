"""BacktestTestPort 実装：Kupiec POF / Christoffersen 独立性（詳細設計 §5.4・D3）。

χ²(1) 生存関数・Φ は math.erf/erfc（scipy 禁止）。
    chi2_sf_df1(x) = erfc(√(x/2))
    norm_cdf(x)    = 0.5(1 + erf(x/√2))

numpy はここに局所化（adapter）。usecase へは float p 値のみ返す。
"""
from __future__ import annotations

import math
from typing import Sequence


def chi2_sf_df1(x: float) -> float:
    """χ²(df=1) 生存関数（上側確率）。x<=0 は p=1。"""
    if x <= 0:
        return 1.0
    return math.erfc(math.sqrt(x / 2.0))


def norm_cdf(x: float) -> float:
    """標準正規 CDF Φ（SPA studentize 用にも供給）。"""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _xlogx_term(count: int, prob: float) -> float:
    """count * log(prob)。count==0 は極限で 0 扱い（0·log0=0）。"""
    if count == 0:
        return 0.0
    if prob <= 0.0:
        return 0.0
    return count * math.log(prob)


class VarBacktests:
    """ストップ被覆検定（Kupiec・Christoffersen）。"""

    def kupiec(self, hit_series: "Sequence[int]", alpha: float = 0.05) -> float:
        n = len(hit_series)
        if n == 0:
            return 1.0
        x = sum(1 for h in hit_series if h)
        pi_hat = x / n
        # LR_POF = -2[ x lnα + (n-x)ln(1-α) - x lnπ̂ - (n-x)ln(1-π̂) ]
        log_l_null = _xlogx_term(x, alpha) + _xlogx_term(n - x, 1 - alpha)
        log_l_alt = _xlogx_term(x, pi_hat) + _xlogx_term(n - x, 1 - pi_hat)
        lr = -2.0 * (log_l_null - log_l_alt)
        if lr < 0:
            lr = 0.0
        return chi2_sf_df1(lr)

    def christoffersen_independence(self, hit_series: "Sequence[int]") -> float:
        # 遷移カウント n_ij（i=前, j=今）
        n00 = n01 = n10 = n11 = 0
        for prev, cur in zip(hit_series, hit_series[1:]):
            if prev == 0 and cur == 0:
                n00 += 1
            elif prev == 0 and cur == 1:
                n01 += 1
            elif prev == 1 and cur == 0:
                n10 += 1
            else:
                n11 += 1
        denom0 = n00 + n01
        denom1 = n10 + n11
        total = denom0 + denom1
        if total == 0:
            return 1.0
        pi01 = n01 / denom0 if denom0 else 0.0
        pi11 = n11 / denom1 if denom1 else 0.0
        pi = (n01 + n11) / total
        # L_null = (1-π)^(n00+n10) π^(n01+n11)
        log_l_null = _xlogx_term(n00 + n10, 1 - pi) + _xlogx_term(n01 + n11, pi)
        # L_alt = (1-π01)^n00 π01^n01 (1-π11)^n10 π11^n11
        log_l_alt = (
            _xlogx_term(n00, 1 - pi01) + _xlogx_term(n01, pi01)
            + _xlogx_term(n10, 1 - pi11) + _xlogx_term(n11, pi11)
        )
        lr = -2.0 * (log_l_null - log_l_alt)
        if lr < 0:
            lr = 0.0
        return chi2_sf_df1(lr)
