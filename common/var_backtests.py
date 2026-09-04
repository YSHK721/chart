"""var_backtests — ストップ被覆検定（Kupiec POF・Christoffersen 独立性）。

①層名/責務:
    共有プリミティブ層。ヒット系列（ストップ抵触の 0/1 列）に対する被覆検定の p 値を返す。
    ブートストラップは一切用いず、対数尤度比と χ²(df=1) だけで完結する。

②含む構造:
    chi2_sf_df1  : χ²(df=1) 生存関数（上側確率）。
    _xlogx_term  : count·log(prob)（count==0 は 0·log0=0 の極限）。
    VarBacktests : Kupiec POF / Christoffersen 独立性。

③配置の理由（ISSUE-479 Wave2 C-3）:
    元は定常ブートストラップ核（stats_boot）と同居していたが、被覆検定はブートストラップを
    使わず、変更を要求するアクターも異なる（SRP 違反）。分割元には後方互換の再エクスポートを
    置かない（同じ実体への入口を 2 つ作ると片方だけが直され取り残しを生む）。実装は無改変。

④依存:
    標準: math / typing（scipy は使わない — χ²・Φ は math.erf/erfc で閉じる）。

出典:
    Kupiec (1995) "Techniques for Verifying the Accuracy of Risk Measurement Models".
    Christoffersen (1998) "Evaluating Interval Forecasts", International Economic Review 39(4).
"""
from __future__ import annotations

import math
from typing import Sequence


def chi2_sf_df1(x: float) -> float:
    """χ²(df=1) 生存関数（上側確率）。x<=0 は p=1。"""
    if x <= 0:
        return 1.0
    return math.erfc(math.sqrt(x / 2.0))


def _xlogx_term(count: int, prob: float) -> float:
    """count * log(prob)。count==0 は極限で 0 扱い（0·log0=0）。"""
    if count == 0:
        return 0.0
    if prob <= 0.0:
        return 0.0
    return count * math.log(prob)


class VarBacktests:
    """ストップ被覆検定（Kupiec・Christoffersen）。χ²(1)・Φ は math.erf/erfc（scipy 禁止）。"""

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
