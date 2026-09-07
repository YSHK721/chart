"""ルート App の「要求面の宣言 ＋ 生成時検査」を固定する（ISSUE-502 段階 5B・ISP/LSP）。

## 何を是正したか

ルート App は以前 __getattr__ で内側 App へ全属性を透過させていた。透過は
「この App が何を必要としているか」を宣言しないまま、**委譲の欠落をリクエスト時まで隠す**。
受け口はあるのに結線が死ぬ（ISSUE-291 の形）。同型の壊れ方は
``sim_ui/adapter/causal_compute_ports.py`` が対照実験で実測済みである。

現在は各 App が REQUIRED_CORE_MEMBERS で要求面を宣言し、require_core_members が
**生成時に**照合する。欠落は組み立てのその場で ``TypeError``（欠落名つき）＝ fail-stop。

## 本ファイルが固定する規則

1. 欠落は生成時に落ち、欠落名がメッセージに出る（リクエストを 1 本も送らずに分かる）。
2. 宣言した面が揃っていれば生成できる（検査が過剰に厳しくない）。
3. **計算量**: 照合は App の生成 1 回につき 1 回。リクエストを何回送っても増えない。

計算量検定（絶対命令 2026-08-28）は Test Spy で「発行した検査 − 生成した App 数 = 0」を
表明し、リクエスト数を変えた 2 点で「入力を増やしても発行が増えない」を固定する。
回数リテラルは期待値に焼き込まない（要求数から導出する）。
"""

from __future__ import annotations

import http.client
import json
import threading

import pytest

from simulator.replay_ui.framework import serve_replay
from simulator.replay_ui.framework.serve_replay import ReplayApp, make_server
from simulator.replay_ui.framework.serve_replay_candles import ReplayCandlesApp
from simulator.replay_ui.framework.serve_replay_catalog import ReplayCatalogApp
from simulator.replay_ui.framework.serve_replay_compute import ReplayComputeApp
from simulator.replay_ui.framework.serve_replay_intraday import ReplayIntradayApp
from simulator.replay_ui.framework.serve_replay_profiles import ReplayProfilesApp
from simulator.replay_ui.usecase.port_result import PortResult

#: 生成時検査を持つ App の全数。App を増やしたらここへ 1 行足す。
_APPS_WITH_CORE_CONTRACT = (
    ReplayCandlesApp,
    ReplayIntradayApp,
    ReplayProfilesApp,
    ReplayCatalogApp,
    ReplayComputeApp,
)

#: 連鎖に組まれるルート App（ルート表の組み立て関数が並べる順）。
_CHAINED_APPS = (ReplayCandlesApp, ReplayIntradayApp, ReplayProfilesApp, ReplayCatalogApp)


class _Port:
    """全 Port の面を満たす最小のフェイク（構造だけを見るので中身は空でよい）。"""

    def load_candles(self, *a, **k): return []
    def load_candles_from(self, *a, **k): return []
    def load_days(self, *a, **k): return []
    def load_source(self, *a, **k): return []
    def bar_time(self, tf, s): return int(s)
    def period_start(self, tf, s): return int(s)
    def causal_series(self, *a, **k): return []
    def compute(self, *a, **k): return []
    def compute_latest_seq(self, *a, **k): return []
    def load_m1_rows(self, *a, **k): return []
    def load_raw_ticks(self, *a, **k): return []
    def forming(self, *a, **k): return PortResult.success({"ok": True})
    def profile(self, *a, **k): return PortResult.success({"ok": True})
    def catalog(self): return PortResult.success({"ok": True})


def _core(tmp_path, **over) -> ReplayApp:
    kw = dict(
        candle_port=_Port(), compute_port=_Port(), window_port=_Port(),
        web_dir=tmp_path, days_port=_Port(), forming_port=_Port(),
        market_profile_port=_Port(), tickvol_profile_port=_Port(), catalog_port=_Port(),
    )
    kw.update(over)
    return ReplayApp(**kw)


def _build(app_class, core):
    """App を組む（連鎖に入るものだけ fallback を要る）。"""
    if app_class in _CHAINED_APPS:
        return app_class(core=core, fallback=core.static_server)
    return app_class(core=core)


# --------------------------------------------------------------------------------------
# 1. 欠落は生成時に落ちる
# --------------------------------------------------------------------------------------
@pytest.mark.parametrize("app_class", _APPS_WITH_CORE_CONTRACT, ids=lambda c: c.__name__)
@pytest.mark.parametrize("member_index", [0, -1], ids=["first_member", "last_member"])
def test_a_missing_declared_member_fails_at_construction(tmp_path, app_class, member_index) -> None:
    """宣言面が 1 つ欠けたら、リクエストを 1 本も送る前に落ちる（欠落名つき）。"""
    core = _core(tmp_path)
    missing = app_class.REQUIRED_CORE_MEMBERS[member_index]

    class _Crippled:
        """宣言面を 1 つだけ持たない core（他はすべて本物へ委ねる）。"""

        def __getattribute__(self, name):
            if name == missing:
                raise AttributeError(name)
            return getattr(core, name)

    with pytest.raises(TypeError) as excinfo:
        _build(app_class, _Crippled())
    message = str(excinfo.value)
    assert missing in message, message
    assert app_class.__name__ in message, message


@pytest.mark.parametrize("app_class", _APPS_WITH_CORE_CONTRACT, ids=lambda c: c.__name__)
def test_a_core_that_satisfies_the_declaration_is_accepted(tmp_path, app_class) -> None:
    """宣言面が揃っていれば組める（検査が過剰に厳しくないこと）。"""
    core = _core(tmp_path)
    app = _build(app_class, core)
    assert app.core is core


# --------------------------------------------------------------------------------------
# 2. 計算量検定（Test Spy・発行 − 使用 = 0）
# --------------------------------------------------------------------------------------
def _count_membership_probes(core, names):
    """宣言面の読み取り回数を数える薄い包み（hasattr は ``__getattribute__`` を通る）。"""
    probes: "list[str]" = []

    class _Counting:
        def __getattribute__(self, name):
            if name in names:
                probes.append(name)
            return getattr(core, name)

    return _Counting(), probes


@pytest.mark.parametrize("apps_requested", [1, 4], ids=["build_1", "build_4"])
def test_the_core_contract_is_checked_once_per_construction(tmp_path, apps_requested: int) -> None:
    """App 1 個 / 4 個の 2 点で「照合の発行 − 生成数 = 0」（宣言面 1 つあたり）。

    生成のたびに何度も照合し直す形になっていないことだけを固定する。回数リテラルは
    焼き込まず、生成数と宣言面の数から導出する。
    """
    # Arrange
    core = _core(tmp_path)
    names = set(ReplayCatalogApp.REQUIRED_CORE_MEMBERS)
    counting, probes = _count_membership_probes(core, names)
    # Act
    apps = [ReplayCatalogApp(core=counting, fallback=core.static_server)
            for _ in range(apps_requested)]
    # Assert
    assert len(apps) == apps_requested
    expected = apps_requested * len(ReplayCatalogApp.REQUIRED_CORE_MEMBERS)
    # catalog_enabled は照合のあとルート表の組み立てでもう 1 度読まれる（1 生成につき 1 回）。
    expected += apps_requested
    assert len(probes) - expected == 0, sorted(probes)


@pytest.mark.parametrize("requests_made", [1, 4], ids=["req_1", "req_4"])
def test_the_core_contract_is_never_checked_per_request(tmp_path, requests_made: int) -> None:
    """リクエスト 1 回 / 4 回の 2 点で「リクエスト中の照合発行 = 0」。

    照合は起動時の 1 回だけであり、**入力（リクエスト数）を増やしても増えない**——
    これが「リクエスト毎に契約検査を走らせない」というオーダーの表明である。
    """
    # Arrange: 各 App は require_core_members を自分のモジュールへ束縛して import する。
    #   Spy は束縛先すべてに差し込む（1 つでも漏らすと「呼ばれていない」と誤読する）。
    checked: "list[str]" = []
    original = serve_replay.require_core_members

    def _spy(core, members, *, owner):
        checked.append(owner)
        return original(core, members, owner=owner)

    patched = [serve_replay] + [
        __import__(cls.__module__, fromlist=["require_core_members"])
        for cls in _APPS_WITH_CORE_CONTRACT
    ]
    for module in patched:
        module.require_core_members = _spy
    try:
        # ルート App は ReplayApp の組み立てで、POST の App は Handler の生成で組まれる。
        #   どちらも**起動時**であり、リクエスト処理の外である。
        app = _core(tmp_path / f"web_{requests_made}")
        server = make_server(app, port=0)
        built = len(checked)
        checked.clear()          # ここから先はリクエスト処理だけを数える
        # Act
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        port = server.server_address[1]
        try:
            for _ in range(requests_made):
                conn = http.client.HTTPConnection("127.0.0.1", port, timeout=20)
                conn.request("GET", "/catalog")
                conn.getresponse().read()
                conn.close()
                conn = http.client.HTTPConnection("127.0.0.1", port, timeout=20)
                conn.request("POST", "/compute", body=json.dumps({
                    "indicatorId": "ma", "variant": "d", "datasetRef": "jp225_m1",
                    "timeframe": "5m", "params": {}, "generation": 1,
                }).encode(), headers={"Content-Type": "application/json"})
                conn.getresponse().read()
                conn.close()
        finally:
            server.shutdown()
            server.server_close()
    finally:
        for module in patched:
            module.require_core_members = original
    # Assert
    assert built > 0, "組み立て時の照合が 1 度も走っていない（Spy が空振り）"
    assert len(checked) == 0, checked


def test_the_probe_counter_is_not_vacuous(tmp_path) -> None:
    """Spy の空振り検定: 照合そのものは確かに発行されている。"""
    core = _core(tmp_path)
    names = set(ReplayIntradayApp.REQUIRED_CORE_MEMBERS)
    counting, probes = _count_membership_probes(core, names)
    ReplayIntradayApp(core=counting, fallback=core.static_server)
    assert sorted(set(probes)) == sorted(names), probes
