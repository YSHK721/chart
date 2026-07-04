"""BuildReportPayload の接点付与（agg.contacts）単体テスト。

方針（既存契約非破壊・追加のみ）:
  - contacts_is/contacts_oos が渡されたときのみ agg["contacts"] を載せる（後方互換）。
  - 未指定時は agg に "contacts" キーを一切追加しない（既存 agg キー集合を不変に保つ）。
"""
from __future__ import annotations

from simulator.report_ui.tests.unit.test_build_report_payload import (
    _make_result, _spec, _ea_params, _meta,
)

from simulator.report_ui.usecase.build_report_payload import BuildReportPayload


def _run(contacts_is=None, contacts_oos=None):
    r = _make_result([100.0], [2000], [10100.0])
    return BuildReportPayload().execute(
        result_is=r, result_oos=r, bars_is=[], bars_oos=[],
        spec=_spec(), ea_params=_ea_params(),
        meta_is=_meta("is"), meta_oos=_meta("oos"),
        contacts_is=contacts_is, contacts_oos=contacts_oos,
    )


class TestContactsAttachment:
    def test_contacts_absent_when_not_provided(self):
        # 後方互換: 未指定時は agg に "contacts" を追加しない
        payload = _run()
        assert "contacts" not in payload.segments["is"].agg
        assert "contacts" not in payload.segments["oos"].agg

    def test_contacts_attached_per_segment_when_provided(self):
        cis = [{"time": 1060, "price": 101.0, "dir": "up"}]
        coos = [{"time": 2200, "price": 55.0, "dir": "down"}]
        payload = _run(contacts_is=cis, contacts_oos=coos)
        assert payload.segments["is"].agg["contacts"] == cis
        assert payload.segments["oos"].agg["contacts"] == coos

    def test_empty_contacts_list_is_attached_as_empty(self):
        # 明示的な空リストは「算出したが接点0件」を意味し、載せる（None とは区別）
        payload = _run(contacts_is=[], contacts_oos=[])
        assert payload.segments["is"].agg["contacts"] == []
        assert payload.segments["oos"].agg["contacts"] == []

    def test_other_agg_keys_unchanged_when_contacts_added(self):
        # 追加のみ: contacts 付与で既存 agg キーは不変（balance_curve/heat 等が残る）
        payload = _run(contacts_is=[{"time": 1, "price": 2.0, "dir": "up"}])
        agg = payload.segments["is"].agg
        for k in ("balance_curve", "heat", "entries_hour", "scatter_mfe"):
            assert k in agg
