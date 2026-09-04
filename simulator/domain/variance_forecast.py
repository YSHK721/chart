"""E-VarianceForecast: 半実現ボラ予測（Value Object・詳細設計 §3.2）。

σ̂⁺/σ̂⁻（上下半実現ボラ予測）と σ̂ᵗᵒᵗᵃˡ_prev（前週実現 GK 週次ボラ・E1 閾値用）。
estimable=False はノートレード確定（sigma_* は None 可）。

domain 層は外部依存ゼロ（標準ライブラリのみ）。
"""
from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class VarianceForecast:
    week_id: str
    sigma_plus: float | None
    sigma_minus: float | None
    sigma_total_prev: float | None
    estimable: bool

    def __post_init__(self) -> None:
        if self.estimable:
            for nm, v in (("sigma_plus", self.sigma_plus),
                          ("sigma_minus", self.sigma_minus)):
                if v is None or not math.isfinite(v) or v <= 0.0:
                    raise ValueError(f"estimable=True では {nm} は有限正数")

    @staticmethod
    def no_trade(week_id: str, sigma_total_prev: float | None = None) -> "VarianceForecast":
        return VarianceForecast(week_id, None, None, sigma_total_prev, estimable=False)
