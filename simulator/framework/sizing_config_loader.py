"""サイジング設定ローダ（framework 層・CLEAN_ARCH §7/§9）。

既存 `simulator/framework/config_loader.py` と**同じ規律**で書く（複製ではなく同流儀）:
    * pydantic v2 を「検証付き DTO」として境界に限定する。
    * 検証後は usecase のプレーン DTO（`SizingConfig` dataclass）へ変換して返す。
      pydantic 型を usecase 層へ漏らさない。
    * 未知キーは ``extra="forbid"``。silent drop による既定値化を禁じる
      （「設定したつもりで効いていない」という壊れ方を作らない）。
    * pydantic の ValidationError は捕捉し、内側の `ConfigError` へ翻訳する
      （外側ライブラリの例外を上位へ漏らさない）。

仕様（§12.1 依頼者裁定）:
    サイジングは**全戦略共通の設定オプション（既定 OFF）**。戦略ごとの明示指定リストは
    実装しない。したがって戦略名・戦略リストのキーは「未知キー」として拒否される
    （表を持たないこと自体が extra="forbid" によって構造的に保証される）。
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from simulator.domain.exceptions import ConfigError
from simulator.usecase.sizing_models import SizingConfig


class _SizingConfigModel(BaseModel):
    """検証付き DTO（境界限定）。既定値は §12.1（OFF）・§12.6（シード固定）。"""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    # E-4「エッジと破産確率」の入力 6 つ。既定値は参照実装
    # `integrated_position_sizing_calculator.html` Step 1 の入力欄 value と同値
    # （移植先は `simulator.usecase.edge_ruin.EdgeRuinSpec`）。
    win_rate: float = Field(default=0.38, ge=0.0, le=1.0)
    payoff_ratio: float = Field(default=2.74, gt=0.0)
    ruin_level: float = Field(default=0.50, gt=0.0, le=1.0)
    alpha: float = Field(default=0.01, ge=0.0, le=1.0)
    # 上限の根拠: T は「何回のトレードで破産確率を見るか」（参照実装 :286-287）。
    # 1 万回は日次 40 年相当で、実運用の評価地平を十分に覆う。MC の内側ループが
    # T に比例するため、上限が無いと 1 ジョブが事実上終わらなくなる。
    horizon: int = Field(default=250, ge=1, le=10_000)
    split_count: int = Field(default=20, ge=1)
    # §12.6: 決定性のためシードを設定項目化（既定は固定値）。
    seed: int = 1
    # 上限の根拠: 参照実装の既定は 4000（:598）。MC の総計算量は 60 格子 × sims × T で、
    # sims=100_000・T=10_000 では 6×10^10 反復に達し 1 ジョブが終わらない。
    # 誤設定（桁の打ち間違い）が無音の無限待ちにならないよう上限を置く。
    sims: int = Field(default=4000, ge=1, le=100_000)
    # OANDA 口座規約（`account_engine.AccountConfig` の既定と同値）。
    margin_rate: float = Field(default=0.10, gt=0.0, lt=1.0)
    point_value: float = Field(default=1.0, gt=0.0)


def load_sizing_config(source: Any) -> SizingConfig:
    """サイジング設定（マッピング）を検証し `SizingConfig`（プレーン DTO）を返す。

    Raises:
        ConfigError: マッピングでない／未知キー／型不一致／範囲外。
    """
    if not isinstance(source, dict):
        raise ConfigError(
            "サイジング設定はマッピング（key: value）である必要があります",
            context={"source_type": type(source).__name__},
        )
    try:
        model = _SizingConfigModel(**source)
    except ValidationError as exc:
        raise ConfigError(
            f"サイジング設定の検証に失敗しました: {exc.error_count()} 件のエラー",
            context={"validation_errors": exc.errors()},
        ) from exc
    return SizingConfig(
        enabled=model.enabled,
        win_rate=model.win_rate,
        payoff_ratio=model.payoff_ratio,
        ruin_level=model.ruin_level,
        alpha=model.alpha,
        horizon=model.horizon,
        split_count=model.split_count,
        seed=model.seed,
        sims=model.sims,
        margin_rate=model.margin_rate,
        point_value=model.point_value,
    )
