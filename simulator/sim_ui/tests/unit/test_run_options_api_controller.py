"""ListRunOptionsInteractor ＋ RunOptionsApiController の単体検定（Phase 6 拡張）.

固定する不変条件（ea_series_api_controller と同型）:
    1. ListRunOptionsInteractor.list() は port の datasets()/ea_names() を束ねた DTO を返す。
    2. RunOptionsApiController.list() は 200・{ok, datasets:[...dict...], ea_names:[...]}。
    3. datasets は RunProfile.to_dict()（11 プロファイルキー＋dataset＋settlement_currency）を並べる。
    4. JSON 直列化は job_api_controller.ApiResponse を再利用する。
"""
from __future__ import annotations

from simulator.sim_ui.adapter.run_options_api_controller import RunOptionsApiController
from simulator.sim_ui.usecase.list_run_options import ListRunOptionsInteractor
from simulator.sim_ui.usecase.run_options_ports import RunOptionsPort, RunProfile


def _profile(dataset="jp225_m1"):
    # settlement_currency は既定値を持たない必須フィールド（N-11 の判定データ源・D-10 同型の
    # Fail-Stop）。本検定は「翻訳が値を素通しするか」だけを見るため、権威値の出典突合は
    # integration/test_run_options_mt5_gate.py が fixture 直参照で担う（値の二重記述をしない）。
    return RunProfile(
        dataset=dataset, data_path="/x/jp225_m1.csv", symbol="JP225", period="M1",
        contract_size=10.0, digits=1, point_size=0.1, leverage=10.0,
        volume_min=0.01, volume_max=100.0, volume_step=0.01, stops_level=0,
        settlement_currency="JPY",
    )


class _FakePort(RunOptionsPort):
    def __init__(self, profiles, ea):
        self._p = profiles
        self._ea = ea

    def datasets(self):
        return self._p

    def ea_names(self):
        return self._ea


def test_interactor_bundles_datasets_and_ea_names():
    port = _FakePort([_profile()], ["A_EA", "TC24051901"])
    result = ListRunOptionsInteractor(port=port).list()
    assert result.datasets == [_profile()]
    assert result.ea_names == ["A_EA", "TC24051901"]


def test_controller_returns_200_with_datasets_and_ea_names():
    port = _FakePort([_profile()], ["A_EA", "TC24051901"])
    ctrl = RunOptionsApiController(options=ListRunOptionsInteractor(port=port))
    resp = ctrl.list()
    assert resp.status == 200
    assert resp.payload["ok"] is True
    assert resp.payload["ea_names"] == ["A_EA", "TC24051901"]
    assert resp.payload["datasets"][0]["symbol"] == "JP225"
    assert resp.payload["datasets"][0]["contract_size"] == 10.0
    assert resp.payload["datasets"][0]["dataset"] == "jp225_m1"


def test_controller_response_is_json_serializable():
    port = _FakePort([_profile()], ["TC24051901"])
    ctrl = RunOptionsApiController(options=ListRunOptionsInteractor(port=port))
    raw = ctrl.list().to_bytes()
    import json

    assert json.loads(raw)["datasets"][0]["point_size"] == 0.1
