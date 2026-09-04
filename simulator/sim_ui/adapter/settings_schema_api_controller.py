"""A-SettingsSchemaApiController: `GET /settings-schema` の HTTP 表現 ⇄ schema UC の変換（Phase 8）。

責務（SRP）: **翻訳だけ**。`ListSettingsSchemaInteractor` から schema を得て JSON で返す。
選択肢の決め方（列挙からの反復導出）は `adapter/tester_settings_schema_catalog.py` が
単一ソースで持ち、ここには写さない（`run_options_api_controller` と同型）。

応答:
    200  {ok: true, key_order: [...], required_keys: [...],
          enum_options: {キー: [{token, label}, ...]}, scalar_specs: {キー: {...}},
          expert_options: [{token, label}, ...],
          unsupported: [{unsupported_id, field, reason, tbd?}, ...]}

JSON 直列化は既存 `job_api_controller.ApiResponse` を再利用する（同型の to_bytes を書かない）。
"""
from __future__ import annotations

from simulator.sim_ui.adapter.job_api_controller import ApiResponse
from simulator.sim_ui.usecase.list_settings_schema import ListSettingsSchemaInteractor

#: schema のパス（sim core が受ける prefix 除去後の形）。
SETTINGS_SCHEMA_PATH = "/settings-schema"


class SettingsSchemaApiController:
    """`GET /settings-schema` の入出力変換。"""

    def __init__(self, *, schema: ListSettingsSchemaInteractor) -> None:
        self._schema = schema

    @property
    def schema(self) -> ListSettingsSchemaInteractor:
        """schema UC（合成根の検定が実物の結線を確かめるための面）。"""
        return self._schema

    def list(self) -> ApiResponse:
        result = self._schema.list()
        return ApiResponse(
            200,
            {
                "ok": True,
                "key_order": list(result.key_order),
                "required_keys": list(result.required_keys),
                "enum_options": {
                    key: [option.to_dict() for option in options]
                    for key, options in result.enum_options.items()
                },
                "scalar_specs": dict(result.scalar_specs),
                "expert_options": [option.to_dict() for option in result.expert_options],
                "unsupported": [notice.to_dict() for notice in result.unsupported],
            },
        )
