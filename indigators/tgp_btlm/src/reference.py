"""numpy 参照バックエンド: R 非依存の BtlmFitter（デモ・テスト・フォールバック用）。

層名/責務:
    参照実装アダプタ。R/tgp/rpy2 が無い環境でも描画デモとテストが完結するよう、
    btlm の「区分線形ベイズ回帰」のうち**単一区分（木分割なし）= ベイズ線形回帰**に
    退化させた純粋 numpy 実装を提供する。

位置づけ（重要）:
    これは tgp::btlm の忠実再現ではない。木構造（区分分割）と MCMC を持たないため、
    非線形・レジーム変化の表現力は無い。忠実なベイズ木構造線形モデルが必要な場合は
    ``rbridge.TgpBtlmFitter`` を用いること。

数理:
    解析的なベイズ線形回帰（無情報事前 / Zellner-g 近傍の標準形）。設計行列
    Φ=[1, x] に対し OLS 係数 β̂、残差分散 s²、予測分散
    Var[ẑ(x₀)] = s²·(1 + φ₀ᵀ(ΦᵀΦ)⁻¹φ₀) を用い、予測平均±t/正規分位で帯を作る。
    本実装は正規近似（``core.norm_ppf``）で分位点を構成する。

依存:
    標準: __future__ / 外部: numpy / プロジェクト内: core
"""

from __future__ import annotations

import numpy as np

from .core import DEFAULT_Q_HIGH, DEFAULT_Q_LOW, BtlmResult, norm_ppf


class OlsBtlmFitter:
    """単一区分ベイズ線形回帰による BtlmFitter 参照実装（R 非依存）。"""

    def fit_predict(
        self,
        x: np.ndarray,
        z: np.ndarray,
        *,
        q_low: float = DEFAULT_Q_LOW,
        q_high: float = DEFAULT_Q_HIGH,
    ) -> BtlmResult:
        """線形回帰の予測平均と予測区間（正規近似）を返す。

        Raises:
            ValueError: 観測数が 3 未満で分散推定ができない場合。
        """
        x = np.asarray(x, dtype=np.float64).ravel()
        z = np.asarray(z, dtype=np.float64).ravel()
        n = x.size
        if n < 3:
            raise ValueError("分散推定には 3 点以上が必要です。")

        phi = np.column_stack([np.ones(n), x])          # [1, x]
        xtx_inv = np.linalg.inv(phi.T @ phi)
        beta = xtx_inv @ phi.T @ z                       # OLS 係数
        mean = phi @ beta

        residual = z - mean
        dof = n - phi.shape[1]
        s2 = float(residual @ residual) / dof            # 残差分散

        # 予測分散（推定平均の不確実性 + 観測ノイズ）
        leverage = np.einsum("ij,jk,ik->i", phi, xtx_inv, phi)
        pred_sd = np.sqrt(s2 * (1.0 + leverage))

        return BtlmResult(
            mean=mean,
            q_low=mean + norm_ppf(q_low) * pred_sd,
            q_high=mean + norm_ppf(q_high) * pred_sd,
        )
