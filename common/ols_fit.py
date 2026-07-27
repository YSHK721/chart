"""ols_fit — 直線 OLS 当てはめと予測分散の共有プリミティブ（指標横断の単一実装）。

ISSUE-179 項目 3: 設計行列 Φ=[1, x] の OLS 当てはめ（β̂・fitted・残差分散 s²）と
予測分散 s²·(1 + leverage) が ``tgp_btlm/src/reference.py``（全行）と
``btlm_trail/src/core.py``（窓末尾）へ複製されていた。ここへ 1 本化する。

**leverage の 2 形は統合しない（実測根拠）**:
    端点ベクトル形 ``φ₀ᵀ(ΦᵀΦ)⁻¹φ₀``（btlm_trail）と einsum 全行形の末尾要素
    （tgp_btlm reference）は数学的には同値だが、浮動小数の総和順序が異なるため
    **最終ビットが一致しない**（実測: 3000 試行中 232 件で ``tobytes()`` 不一致）。
    挙動不変（bit-for-bit）を絶対条件とするため、両形を :func:`pred_sd_at` /
    :func:`pred_sd_rows` として別々に保持し、各呼び出し側は従来と同一の形を使う。

出自と挙動不変:
    :func:`ols_fit` の本体は上記 2 箇所で **完全に同一** だった部分
    （Φ 構築 → (ΦᵀΦ)⁻¹ → β̂ → fitted → s² = 残差平方和 / (n-2)）を無改変で移設した。
    観測数不足時の扱い（``tgp_btlm`` は ValueError、``btlm_trail`` は NaN 返し）は
    アクターごとに異なる方針であるため呼び出し側に残す（本モジュールは検査しない）。

依存: numpy のみ（指標パッケージへ依存しない）。
"""

from __future__ import annotations

from typing import NamedTuple

import numpy as np


class OlsFit(NamedTuple):
    """直線 OLS の当てはめ結果。

    Attributes:
        phi: 設計行列 Φ=[1, x]（shape (n, 2)）。
        xtx_inv: (ΦᵀΦ)⁻¹（shape (2, 2)）。
        beta: OLS 係数 β̂=[切片, 傾き]。
        fitted: 当てはめ値 Φβ̂。
        s2: 残差分散 s²（自由度 n-2）。
    """

    phi: np.ndarray
    xtx_inv: np.ndarray
    beta: np.ndarray
    fitted: np.ndarray
    s2: float


def ols_fit(x: np.ndarray, z: np.ndarray) -> OlsFit:
    """Φ=[1, x] の最小二乗当てはめを行う。

    Args:
        x: 説明変数（1 次元）。
        z: 目的変数（1 次元・``x`` と同長）。

    Returns:
        :class:`OlsFit`。

    Note:
        観測数の下限検査は行わない（n<3 で s² の自由度が 0 以下になる扱いは
        呼び出し側の方針＝例外か NaN かが分かれるため）。
    """
    n = x.size
    phi = np.column_stack([np.ones(n), x])
    xtx_inv = np.linalg.inv(phi.T @ phi)
    beta = xtx_inv @ phi.T @ z
    fitted = phi @ beta
    residual = z - fitted
    s2 = float(residual @ residual) / (n - phi.shape[1])
    return OlsFit(phi=phi, xtx_inv=xtx_inv, beta=beta, fitted=fitted, s2=s2)


def pred_sd_rows(phi: np.ndarray, xtx_inv: np.ndarray, s2: float) -> np.ndarray:
    """全行の予測標準偏差 √(s²·(1 + leverage)) を返す（einsum 形）。

    ``tgp_btlm/src/reference.OlsBtlmFitter.fit_predict`` と同一の線形代数。
    """
    leverage = np.einsum("ij,jk,ik->i", phi, xtx_inv, phi)
    return np.sqrt(s2 * (1.0 + leverage))


def pred_sd_at(row: np.ndarray, xtx_inv: np.ndarray, s2: float) -> float:
    """1 点 φ₀ の予測標準偏差 √(s²·(1 + φ₀ᵀ(ΦᵀΦ)⁻¹φ₀)) を返す（端点ベクトル形）。

    ``btlm_trail/src/core._window_end_scalar`` と同一の線形代数。
    :func:`pred_sd_rows` の対応要素とは最終ビットが一致しない場合がある（上記実測）。
    """
    leverage = float(row @ xtx_inv @ row)
    return float(np.sqrt(s2 * (1.0 + leverage)))


__all__ = ["OlsFit", "ols_fit", "pred_sd_rows", "pred_sd_at"]
