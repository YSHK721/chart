"""U-ListSettingsSchema: Tester Settings フォームの schema を束ねる薄い query UC（Phase 8）。

`SettingsSchemaPort` の 6 面を 1 つの DTO（:class:`SettingsSchema`）へ束ねるだけ。規則は
持たない（フォームが「何を・どの順で選べるか」を 1 回で得るための入口）。投入経路とは
無関係（`SubmitJobInteractor` には足さない・既存 backtest verbatim 契約 byte 不変）。
`list_run_options.ListRunOptionsInteractor` と同型。
"""
from __future__ import annotations

from dataclasses import dataclass

from simulator.sim_ui.usecase.settings_schema_ports import (
    SchemaOption,
    SettingsSchemaPort,
    UnsupportedNotice,
)


@dataclass(frozen=True)
class SettingsSchema:
    """Tester Settings フォームの schema（キー順・必須・選択肢・仕様・対象・非対象）。"""

    key_order: "tuple[str, ...]"
    required_keys: "tuple[str, ...]"
    enum_options: "dict[str, list[SchemaOption]]"
    scalar_specs: "dict[str, dict]"
    expert_options: "list[SchemaOption]"
    unsupported: "list[UnsupportedNotice]"


class ListSettingsSchemaInteractor:
    """SettingsSchemaPort から schema を 1 回で取得する query UC。"""

    def __init__(self, *, port: SettingsSchemaPort) -> None:
        self._port = port

    def list(self) -> SettingsSchema:
        return SettingsSchema(
            key_order=self._port.key_order(),
            required_keys=self._port.required_keys(),
            enum_options=self._port.enum_options(),
            scalar_specs=self._port.scalar_specs(),
            expert_options=self._port.expert_options(),
            unsupported=self._port.unsupported(),
        )
