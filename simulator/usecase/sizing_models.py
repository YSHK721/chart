"""U-SizingModels: 発注量決定の入出力（usecase のプレーン DTO・CLEAN_ARCH §5）。

pydantic / pandas / HTTP を一切持ち込まない（framework の検証済み DTO は
`simulator/framework/sizing_config_loader.py` が本モジュールの型へ変換して渡す）。

責務分離（SRP）:
    `SizingRule`     — ジョブ全体で不変の規則（エッジ・口座規約・銘柄の量制約）
    `SizingContext`  — 1 回の発注ごとに変わる状態（方向・推定建値・ストップ・有効証拠金）
    `SizingDecision` — 決定の結果（量・採用した f・決められなかった理由）
"""
from __future__ import annotations

from dataclasses import dataclass

from simulator.usecase.edge_ruin import EdgeRuinSpec


@dataclass(frozen=True)
class SizingRule:
    """ジョブ全体で不変のサイジング規則。

    ``margin_rate`` / ``point_value`` は `account_engine` の権威式へそのまま渡す
    （OANDA 規約の値は `account_engine.AccountConfig` の既定が単一ソース）。
    """

    edge: EdgeRuinSpec
    margin_rate: float
    point_value: float
    volume_min: float
    volume_max: float
    volume_step: float

    def __post_init__(self) -> None:
        if not 0.0 < self.margin_rate < 1.0:
            raise ValueError(f"margin_rate は (0,1) の比です: {self.margin_rate}")
        if self.point_value <= 0:
            raise ValueError(f"point_value は正である必要があります: {self.point_value}")


@dataclass(frozen=True)
class SizingContext:
    """1 回の発注量決定に必要な状態。

    ``estimated_entry_price``: 決定時点の**推定**建値（§3.5.5 実証 6: 実際の建値は
    エンジン内部の `derive_quotes` が決め Decorator へ渡らない。§12.2 でこの差が
    `volume_step` 未満に収まることを実測固定する）。
    ``stop_loss_price``: None（ストップ無し）ならリスク距離が定義できない。
    """

    side: str
    estimated_entry_price: float
    stop_loss_price: "float | None"
    equity: float


# 量を決められなかった理由の機械可読な区分。
# 呼び出し側（Decorator）が「落とす」と「ジョブごと失敗させる」を区別するために要る。
# 文字列 `reason`（人間向け）と分けるのは、メッセージ文言の変更で分岐が壊れないようにするため。
BLOCK_NONE = ""
BLOCK_NO_RISK_DISTANCE = "no_risk_distance"   # SL 無し／SL==建値。**fail-stop 対象**
BLOCK_BELOW_MINIMUM = "below_minimum"         # 丸めて下限未満（落とす）
BLOCK_NO_EDGE = "no_edge"                     # f<=0。構築時に排除するため発注時には出ない
#   （🔵-4: f=0 の原因は EV<=0 に限らない。EV>0 でも α が厳しい／T が長いと
#    最小格子点の RoR が α を超えて f=0 になる。原因を断定する文言を使わない）
BLOCK_NO_EQUITY = "no_equity"                 # 有効証拠金 0 以下（落とす）


@dataclass(frozen=True)
class SizingDecision:
    """決定結果。``volume`` が None なら**発注しない**（無音で 0 を建てない）。

    ``blocked`` は決められなかった理由の区分（``BLOCK_*``）。依頼者裁定（2026-08-11）
    により ``BLOCK_NO_RISK_DISTANCE`` だけは**ジョブごと失敗させる**（fail-stop）。
    """

    volume: "float | None"
    fraction: float
    reason: str = ""
    blocked: str = BLOCK_NONE


@dataclass(frozen=True)
class SizingConfig:
    """サイジングの設定（§12.1: 全戦略共通の設定オプション・既定 OFF）。

    戦略ごとの明示指定リストは持たない（§12.1「戦略リストのハードコード禁止」）。
    適用可否は ``enabled`` のみで決まる。
    """

    enabled: bool = False
    win_rate: float = 0.38
    payoff_ratio: float = 2.74
    ruin_level: float = 0.50
    alpha: float = 0.01
    horizon: int = 250
    split_count: int = 20
    seed: int = 1                # §12.6 決定性（既定は固定値）
    sims: int = 4000
    margin_rate: float = 0.10    # OANDA 規約（account_engine.AccountConfig と同値）
    point_value: float = 1.0

    def to_edge_spec(self) -> EdgeRuinSpec:
        """E-4 の入力へ変換する。"""
        return EdgeRuinSpec(
            win_rate=self.win_rate,
            payoff_ratio=self.payoff_ratio,
            ruin_level=self.ruin_level,
            alpha=self.alpha,
            horizon=self.horizon,
            split_count=self.split_count,
            seed=self.seed,
            sims=self.sims,
        )
