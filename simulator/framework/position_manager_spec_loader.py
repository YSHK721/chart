"""建玉変更 spec ローダ（framework 層・Phase 7 FR-07/08・CLEAN_ARCH §7/§9）。

`strategy_spec_loader.py` / `sizing_config_loader.py` と**同じ規律**で書く（複製ではなく
同流儀）:
    * pydantic v2 を「検証付き DTO」として境界に限定する。
    * 検証後は domain の VO（:class:`TrailingRule` / :class:`PartialCloseRule`）へ変換して
      返す。pydantic 型を上位層へ漏らさない。
    * 未知キーは ``extra="forbid"``（silent drop を作らない）。
    * pydantic の ValidationError は捕捉し内側の :class:`ConfigError` へ翻訳する。

spec スキーマ（Phase 6 strategy ブロックへ追加のみ・未指定=OFF）:
    strategy:
      trailing:
        granularity: "bar" | "tick"   # 省略時 "bar"
        trigger_points:  number >= 0
        distance_points: number > 0
        step_points:     number >= 0  # 省略時 0（連続）・tighten_only 固定
      partial_close:
        trigger: { profit_points: number >= 0 }
        close_fraction: 0 < number < 1

point_size（点数→価格換算）は呼出側（run_job・Composition Root）が銘柄仕様から与える。
adapter :class:`PositionManager` の構築（volume_step 注入含む）は Composition Root が担い、
本 loader は domain 規則と粒度のみを返す（framework→domain・層方向を守る）。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from simulator.domain.exceptions import ConfigError
from simulator.domain.partial_close_rule import PartialCloseRule
from simulator.domain.trailing_rule import TrailingRule


class _TrailingModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    granularity: Literal["bar", "tick"] = "bar"
    trigger_points: float = Field(ge=0)
    distance_points: float = Field(gt=0)
    step_points: float = Field(default=0.0, ge=0)


class _PartialTriggerModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profit_points: float = Field(ge=0)


class _PartialCloseModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    trigger: _PartialTriggerModel
    close_fraction: float = Field(gt=0, lt=1)


@dataclass(frozen=True)
class PositionChangeSpec:
    """検証済みの建玉変更 spec（domain 規則＋トレーリング粒度）。"""

    trailing_rule: "TrailingRule | None"
    partial_close_rule: "PartialCloseRule | None"
    trailing_granularity: str


def load_position_change_spec(
    strategy_block: Any, *, point_size: float
) -> "PositionChangeSpec | None":
    """strategy ブロックの trailing/partial_close を検証し PositionChangeSpec を返す。

    どちらも無ければ None（OFF）。entry_long/entry_short 等の他キーは無視する
    （strategy_spec_loader が別途担う）。

    Raises:
        ConfigError: マッピングでない／未知キー／型不一致／範囲外。
    """
    if not isinstance(strategy_block, dict):
        raise ConfigError(
            "strategy 設定はマッピング（key: value）である必要があります",
            context={"source_type": type(strategy_block).__name__},
        )
    raw_trailing = strategy_block.get("trailing")
    raw_partial = strategy_block.get("partial_close")
    if raw_trailing is None and raw_partial is None:
        return None

    try:
        trailing_rule = None
        granularity = "bar"
        if raw_trailing is not None:
            tm = _TrailingModel(**raw_trailing) if isinstance(raw_trailing, dict) else _TrailingModel(raw_trailing)
            granularity = tm.granularity
            trailing_rule = TrailingRule(
                trigger_points=tm.trigger_points,
                distance_points=tm.distance_points,
                step_points=tm.step_points,
                point_size=point_size,
            )
        partial_rule = None
        if raw_partial is not None:
            pm = _PartialCloseModel(**raw_partial) if isinstance(raw_partial, dict) else _PartialCloseModel(raw_partial)
            partial_rule = PartialCloseRule(
                trigger_profit_points=pm.trigger.profit_points,
                close_fraction=pm.close_fraction,
                point_size=point_size,
            )
    except (ValidationError, TypeError) as exc:
        raise ConfigError(
            "建玉変更（trailing/partial_close）設定が不正です",
            context={"errors": str(exc)},
        ) from exc

    return PositionChangeSpec(
        trailing_rule=trailing_rule,
        partial_close_rule=partial_rule,
        trailing_granularity=granularity,
    )
