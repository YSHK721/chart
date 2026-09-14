"""戦略 spec ローダ（framework 層・Phase 6 F-8・CLEAN_ARCH §7/§9）。

`sizing_config_loader.py` と**同じ規律**で書く（複製ではなく同流儀）:
    * pydantic v2 を「検証付き DTO」として境界に限定する。
    * 検証後は domain のプレーン VO（:class:`EntryConditions`）へ変換して返す。
      pydantic 型を上位層へ漏らさない。
    * 未知キーは ``extra="forbid"``。silent drop による「設定したつもりで効かない」を作らない。
    * pydantic の ValidationError は捕捉し、内側の :class:`ConfigError` へ翻訳する。

TBD-11: op は ``>`` / ``<`` のみ（``Literal``）。shift >= 0（``ge=0``）。OR・グループ化・
``>=`` / ``<=`` / ``==`` は構造的に受理不能（Literal 外＝ ValidationError → ConfigError）。

spec スキーマ:
    strategy:
      entry_long:  [ {indicator, shift, op, rhs}, ... ]   # AND 連鎖（順序保存）
      entry_short: [ ... ]
    rhs = number | { indicator, shift }
"""
from __future__ import annotations

from typing import Any, List, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from simulator.domain.entry_conditions import Condition, EntryConditions, IndicatorRef
from simulator.domain.exceptions import ConfigError


class _RefModel(BaseModel):
    """rhs が指標系列を指す場合（{indicator, shift}）の検証付き DTO。"""

    model_config = ConfigDict(extra="forbid")

    indicator: str
    shift: int = Field(ge=0)


class _ConditionModel(BaseModel):
    """1 条件（indicator[bar−shift] op rhs）の検証付き DTO。"""

    model_config = ConfigDict(extra="forbid")

    indicator: str
    shift: int = Field(ge=0)
    op: Literal[">", "<"]
    # 定数（number）または指標参照（{indicator, shift}）。dict は _RefModel・数値は float。
    rhs: "Union[float, _RefModel]"


class _StrategyModel(BaseModel):
    """strategy ブロック全体の検証付き DTO（両側とも省略時は空）。"""

    model_config = ConfigDict(extra="forbid")

    entry_long: List[_ConditionModel] = Field(default_factory=list)
    entry_short: List[_ConditionModel] = Field(default_factory=list)


def _to_condition(model: _ConditionModel) -> Condition:
    rhs: "float | IndicatorRef"
    if isinstance(model.rhs, _RefModel):
        rhs = IndicatorRef(indicator=model.rhs.indicator, shift=model.rhs.shift)
    else:
        rhs = model.rhs
    return Condition(indicator=model.indicator, shift=model.shift, op=model.op, rhs=rhs)


def load_strategy_spec(source: Any) -> "tuple[EntryConditions, EntryConditions]":
    """strategy ブロック（マッピング）を検証し (entry_long, entry_short) を返す。

    Raises:
        ConfigError: マッピングでない／未知キー／未知比較子／shift 負値／型不一致。
    """
    if not isinstance(source, dict):
        raise ConfigError(
            "strategy 設定はマッピング（key: value）である必要があります",
            context={"source_type": type(source).__name__},
        )
    try:
        model = _StrategyModel(**source)
    except ValidationError as exc:
        raise ConfigError(
            f"strategy 設定の検証に失敗しました: {exc.error_count()} 件のエラー",
            context={"validation_errors": exc.errors()},
        ) from exc
    # EntryConditions 側でも op/shift を再検証する（多層防御・ConfigError で対称）。
    entry_long = EntryConditions([_to_condition(c) for c in model.entry_long])
    entry_short = EntryConditions([_to_condition(c) for c in model.entry_short])
    return entry_long, entry_short
