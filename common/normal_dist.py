"""normal_dist — 標準正規分布の逆累積分布関数（Acklam 有理近似・scipy 非依存）。

ISSUE-179 項目 3: Acklam 係数 20 個と分岐しきい値 ``0.02425`` が
``tgp_btlm/src/core.py``（ベクトル版）と ``btlm_trail/src/core.py``（スカラ版）へ
完全一致で複製されていた。係数 1 個の訂正が 2 ファイルの同時改変を要求する状態
（拡張ではなく改変＝OCP 違反）を解消するため、ここへ 1 本化する。

出自と挙動不変:
    実体は ``tgp_btlm/src/core.norm_ppf``（ベクトル版）を無改変で移設したもの。
    ``btlm_trail`` のスカラ専用版は、本実装のスカラ経路と **ビット一致** することを
    実測で確認して統合した（p を 3 分岐に跨がる 14,000 点 + 境界値 7 点でサンプル、
    ``np.float64(...).tobytes()`` 比較の不一致 0 件）。

    ``common/stats_boot.py`` は定常ブートストラップ統計核として ``norm_cdf`` を持つが、
    アクター（Hansen SPA の studentize）が異なるため統合しない（ISSUE-179 の範囲外）。

依存: numpy のみ（指標パッケージへ依存しない）。
"""

from __future__ import annotations

import numpy as np

# Acklam の有理近似係数（相対誤差 約 1.15e-9）。
_A = (-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
      1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00)
_B = (-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
      6.680131188771972e+01, -1.328068155288572e+01)
_C = (-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
      -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00)
_D = (7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00,
      3.754408661907416e+00)

# 裾/中央の分岐しきい値（Acklam の定義値）。
_P_LOW = 0.02425


def norm_ppf(p: "np.ndarray | float") -> "np.ndarray | float":
    """標準正規分布の逆累積分布関数（Acklam の有理近似・scipy 非依存）。

    任意分位点をネイティブ 5/95% 帯から正規近似で再構成する際に用いる。
    相対誤差は約 1.15e-9。

    Args:
        p: 確率（0 < p < 1）。スカラまたは numpy 配列。

    Returns:
        対応する分位点。入力がスカラなら float、配列なら ndarray。

    Raises:
        ValueError: p が 0 < p < 1 の範囲外の場合（配列は 1 要素でも範囲外なら拒否）。
    """
    a, b, c, d = _A, _B, _C, _D

    scalar_input = np.isscalar(p)
    pp = np.asarray(p, dtype=np.float64)
    if np.any((pp <= 0.0) | (pp >= 1.0)):
        raise ValueError("p は 0 < p < 1 の範囲で指定してください。")

    out = np.empty_like(pp)
    plow, phigh = _P_LOW, 1.0 - _P_LOW

    lower = pp < plow
    upper = pp > phigh
    middle = ~(lower | upper)

    # 下側裾
    if np.any(lower):
        q = np.sqrt(-2.0 * np.log(pp[lower]))
        out[lower] = (((((c[0]*q + c[1])*q + c[2])*q + c[3])*q + c[4])*q + c[5]) / \
                     ((((d[0]*q + d[1])*q + d[2])*q + d[3])*q + 1.0)
    # 上側裾
    if np.any(upper):
        q = np.sqrt(-2.0 * np.log(1.0 - pp[upper]))
        out[upper] = -(((((c[0]*q + c[1])*q + c[2])*q + c[3])*q + c[4])*q + c[5]) / \
                      ((((d[0]*q + d[1])*q + d[2])*q + d[3])*q + 1.0)
    # 中央
    if np.any(middle):
        q = pp[middle] - 0.5
        r = q * q
        out[middle] = (((((a[0]*r + a[1])*r + a[2])*r + a[3])*r + a[4])*r + a[5]) * q / \
                      (((((b[0]*r + b[1])*r + b[2])*r + b[3])*r + b[4])*r + 1.0)

    return float(out) if scalar_input else out


__all__ = ["norm_ppf"]
