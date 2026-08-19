"""ListSettingsSchemaInteractor ＋ SettingsSchemaApiController の単体検定（Phase 8 スライス 1）.

固定する不変条件（run_options_api_controller と同型）:
    1. ListSettingsSchemaInteractor.list() は port の 6 面を束ねた DTO を返す。
    2. SettingsSchemaApiController.list() は 200・{ok, key_order, required_keys,
       enum_options, scalar_specs, expert_options, unsupported}。
    3. 選択肢は {token, label} の dict へ、非対象は {unsupported_id, field, reason, (tbd)} へ翻訳する。
    4. JSON 直列化は job_api_controller.ApiResponse を再利用する（同型の to_bytes を書かない）。

本検定は**翻訳だけ**を見る。選択肢の中身が enums 由来であることは
`test_settings_schema_catalog.py` が実カタログで固定する（値の二重記述をしない）。
"""
from __future__ import annotations

import json

from simulator.sim_ui.adapter.settings_schema_api_controller import (
    SettingsSchemaApiController,
)
from simulator.sim_ui.usecase.list_settings_schema import ListSettingsSchemaInteractor
from simulator.sim_ui.usecase.settings_schema_ports import (
    SchemaOption,
    SettingsSchemaPort,
    UnsupportedNotice,
)


class _FakePort(SettingsSchemaPort):
    """6 面それぞれに区別できる値を返す port（素通しの取り違えを検出する）。"""

    def key_order(self):
        return ("Symbol", "Period")

    def required_keys(self):
        return ("Symbol",)

    def enum_options(self):
        return {"Period": [SchemaOption(token="t1", label="L1")]}

    def scalar_specs(self):
        return {"Symbol": {"expert_only": False}}

    def expert_options(self):
        return [SchemaOption(token="A_EA.suffix", label="A_EA")]

    def unsupported(self):
        return [
            UnsupportedNotice(unsupported_id="N-99", field="f", reason="r", tbd="TBD-99"),
            UnsupportedNotice(unsupported_id="N-98", field="g", reason="s"),
        ]


def test_interactor_bundles_every_face_of_the_port() -> None:
    # Arrange
    port = _FakePort()
    # Act
    result = ListSettingsSchemaInteractor(port=port).list()
    # Assert
    assert result.key_order == ("Symbol", "Period")
    assert result.required_keys == ("Symbol",)
    assert result.enum_options == {"Period": [SchemaOption(token="t1", label="L1")]}
    assert result.scalar_specs == {"Symbol": {"expert_only": False}}
    assert result.expert_options == [SchemaOption(token="A_EA.suffix", label="A_EA")]
    assert [n.unsupported_id for n in result.unsupported] == ["N-99", "N-98"]


def test_controller_returns_200_with_the_schema() -> None:
    # Arrange
    ctrl = SettingsSchemaApiController(
        schema=ListSettingsSchemaInteractor(port=_FakePort())
    )
    # Act
    resp = ctrl.list()
    # Assert
    assert resp.status == 200
    assert resp.payload["ok"] is True
    assert resp.payload["key_order"] == ["Symbol", "Period"]
    assert resp.payload["required_keys"] == ["Symbol"]
    assert resp.payload["enum_options"] == {"Period": [{"token": "t1", "label": "L1"}]}
    assert resp.payload["scalar_specs"] == {"Symbol": {"expert_only": False}}
    assert resp.payload["expert_options"] == [{"token": "A_EA.suffix", "label": "A_EA"}]


def test_controller_omits_the_tbd_key_when_the_rule_has_none() -> None:
    """`tbd` が無い非対象に ``null`` を載せない（RunProfile.to_dict と同じ流儀）。"""
    # Arrange
    ctrl = SettingsSchemaApiController(
        schema=ListSettingsSchemaInteractor(port=_FakePort())
    )
    # Act
    unsupported = ctrl.list().payload["unsupported"]
    # Assert
    assert unsupported[0] == {
        "unsupported_id": "N-99",
        "field": "f",
        "reason": "r",
        "tbd": "TBD-99",
    }
    assert unsupported[1] == {"unsupported_id": "N-98", "field": "g", "reason": "s"}


def test_controller_response_is_json_serializable() -> None:
    # Arrange
    ctrl = SettingsSchemaApiController(
        schema=ListSettingsSchemaInteractor(port=_FakePort())
    )
    # Act
    raw = ctrl.list().to_bytes()
    # Assert
    assert json.loads(raw)["enum_options"]["Period"][0]["token"] == "t1"
