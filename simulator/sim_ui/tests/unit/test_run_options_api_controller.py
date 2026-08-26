"""ListRunOptionsInteractor ＋ RunOptionsApiController の単体検定（Phase 6 拡張）.

固定する不変条件（ea_series_api_controller と同型）:
    1. ListRunOptionsInteractor.list() は port の datasets()/ea_names() を束ねた DTO を返す。
    2. RunOptionsApiController.list() は 200・{ok, datasets:[...dict...], ea_names:[...]}。
    3. datasets は RunProfile.to_dict()（11 プロファイルキー＋dataset＋settlement_currency）を並べる。
    4. JSON 直列化は job_api_controller.ApiResponse を再利用する。
"""
from __future__ import annotations

from marketdata.symbol_spec_snapshot import OANDA_JAPAN_MT5_LIVE, load_spec_fields
from simulator.sim_ui.adapter.run_options_api_controller import RunOptionsApiController
from simulator.sim_ui.usecase.list_run_options import ListRunOptionsInteractor
from simulator.sim_ui.usecase.run_options_ports import RunOptionsPort, RunProfile


def _profile(dataset="jp225_m1"):
    # settlement_currency は既定値を持たない必須フィールド（N-11 の判定データ源・D-10 同型の
    # Fail-Stop）。本検定は「翻訳が値を素通しするか」だけを見るため、権威値の出典突合は
    # integration/test_run_options_mt5_gate.py が fixture 直参照で担う（値の二重記述をしない）。
    # 銘柄仕様 8 項目は供給元スナップショットだけを権威とする（ISSUE-445 段階 C）。
    # ここにリテラルを書かない＝人が値を選べない。
    return RunProfile(
        dataset=dataset, data_path="/x/jp225_m1.csv", symbol="JP225", period="M1",
        **load_spec_fields(OANDA_JAPAN_MT5_LIVE, "JP225"),
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
    # ⚠ ISSUE-445 段階 C: ここは `_profile()` が持つ値の**写し**であり、
    # 「翻訳が値を素通しするか」だけを見ている（権威との突合は
    # `sim_ui/tests/integration/test_run_options_mt5_gate.py` が持つ）。
    # 段階 B まではここに `10.0` を人が書いており、`_profile()` を供給元へ寄せると
    # 赤に転じる**更新が要るピン**だった。段階 C で `_profile()` と同じ供給元から引く形に
    # 改めたので、人が数字を書き換えて緑に戻す余地は無くなった。
    # ただし依然として「素通し」の検定であり、緑は銘柄仕様の正しさの証拠にならない。
    assert resp.payload["datasets"][0]["contract_size"] == _profile().contract_size
    assert resp.payload["datasets"][0]["dataset"] == "jp225_m1"


def test_controller_response_is_json_serializable():
    port = _FakePort([_profile()], ["TC24051901"])
    ctrl = RunOptionsApiController(options=ListRunOptionsInteractor(port=port))
    raw = ctrl.list().to_bytes()
    import json

    assert json.loads(raw)["datasets"][0]["point_size"] == 0.1
