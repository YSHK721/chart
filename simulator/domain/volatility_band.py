"""E-VolatilityBand: 価格バンド S/T/N（Value Object・詳細設計 §3.3）。

仕様 §2.5 の式を一意実装（D2）:
    S = O · exp(−1.96 · σ̂⁻)
    T = O · exp(z(p_tp) · σ̂⁺)
    N = f_risk · Capital / (O − S)
z(p_tp) は定数表（仕様 §2.5）。丸めは VO 内では行わない（生値保持・NFR-D1）。

domain 層は外部依存ゼロ（標準ライブラリのみ）。
"""
from __future__ import annotations

import math
from dataclasses import dataclass

# 仕様 §2.5（D2）: z(p)=−Φ⁻¹(p/2)。探索グリッド 4 値。
_Z_TP = {0.40: 0.842, 0.50: 0.674, 0.60: 0.524, 0.70: 0.385}
# ストップ固定（D2・α_stop=0.05 → 1.96）。
SL_Z = 1.96


@dataclass(frozen=True)
class VolatilityBand:
    week_id: str
    O: float
    S: float
    T: float
    N: float
    p_tp: float

    def __post_init__(self) -> None:
        if not (self.O - self.S > 0.0):
            raise ValueError("不変条件 O-S>0 違反")
        if not (self.S > 0.0):
            raise ValueError("不変条件 S>0 違反")
        if not (self.T > self.O):
            raise ValueError("不変条件 T>O 違反")

    @staticmethod
    def from_forecast(
        *, week_id: str, O: float, sigma_minus: float, sigma_plus: float,
        p_tp: float, f_risk: float, capital: float,
    ) -> "VolatilityBand":
        if p_tp not in _Z_TP:
            raise ValueError(f"p_tp={p_tp} は探索グリッド {sorted(_Z_TP)} 外")
        z = _Z_TP[p_tp]
        # 演算順序固定（NFR-D1）: exp の引数を (-SL_Z * sigma_minus) の順で評価。
        S = O * math.exp(-SL_Z * sigma_minus)
        T = O * math.exp(z * sigma_plus)
        N = f_risk * capital / (O - S)
        return VolatilityBand(week_id, O, S, T, N, p_tp)
