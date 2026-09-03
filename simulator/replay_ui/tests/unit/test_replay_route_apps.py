"""機能別ルート App の構造検定（ISSUE-479 Wave2 3-4 / S-3）。

固定する規則:
    1. 重い処理のワーカーとロックは**全 App で単一インスタンス**（絶対条件）。
       rpy2/R はスレッド親和で、「常に同一スレッドから呼ぶ」ことが安全性の前提である。
       App ごとにワーカーを持つとリクエストごとに実行スレッドが変わり、前提が崩れる。
       同値ではなく**同一性**（is）で固定する——等しいだけの別インスタンスでは意味が無い。
    2. Handler は GET の分岐を 1 つも持たない（ルートの宣言は組み立て 1 箇所）。
    3. どのルートが存在するかは Port の注入で決まる（未注入なら静的配信へ落ちる）。

応答そのもののパリティは `replay_ui/tests/integration/test_replay_route_parity.py` が
byte 単位で固定する。本ファイルは「誰が何を持つか」だけを見る。

計算量検定（絶対命令 2026-08-28）: ルート表の組み立ては App の生成 1 回につき 1 回
    （発行 − 使用 = 0）。App を 1 個 / 4 個作る 2 点で、生成あたりの組み立てが増えない。
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

from simulator.replay_ui.framework import serve_replay
from simulator.replay_ui.framework.serve_replay import ReplayApp, build_replay_routes
from simulator.replay_ui.framework.serve_replay_candles import ReplayCandlesApp
from simulator.replay_ui.framework.serve_replay_catalog import ReplayCatalogApp
from simulator.replay_ui.framework.serve_replay_intraday import ReplayIntradayApp
from simulator.replay_ui.framework.serve_replay_profiles import ReplayProfilesApp

_ROUTE_APPS = (ReplayCandlesApp, ReplayIntradayApp, ReplayProfilesApp, ReplayCatalogApp)


class _Port:
    """全 Port の面を satisfy する最小のフェイク（構造だけを見るので中身は空でよい）。"""

    def load_candles(self, *a, **k):
        return []

    def load_candles_from(self, *a, **k):
        return []

    def load_days(self, *a, **k):
        return []

    def load_source(self, *a, **k):
        return []

    def bar_time(self, tf, s):
        return int(s)

    def period_start(self, tf, s):
        return int(s)

    def causal_series(self, *a, **k):
        return []

    def compute(self, *a, **k):
        return []

    def compute_latest_seq(self, *a, **k):
        return []

    def load_m1_rows(self, *a, **k):
        return []

    def load_raw_ticks(self, *a, **k):
        return []

    def forming(self, *a, **k):
        return (200, {})

    def profile(self, *a, **k):
        return (200, {})

    def catalog(self):
        return (200, {})


def _core(tmp_path, **over):
    kw = dict(
        candle_port=_Port(), compute_port=_Port(), window_port=_Port(),
        web_dir=tmp_path, days_port=_Port(), forming_port=_Port(),
        market_profile_port=_Port(), tickvol_profile_port=_Port(), catalog_port=_Port(),
    )
    kw.update(over)
    return ReplayApp(**kw)


# --------------------------------------------------------------------------------------
# 1. 重い処理のワーカーとロックは単一インスタンス（絶対条件）
# --------------------------------------------------------------------------------------
@pytest.mark.parametrize("app_class", _ROUTE_APPS, ids=lambda c: c.__name__)
def test_every_route_app_shares_the_one_heavy_worker(tmp_path, app_class) -> None:
    """App ごとにワーカーを持つと「常に同一スレッド」という前提が壊れる（rpy2 スレッド親和）。"""
    core = _core(tmp_path)
    app = app_class(inner=core)
    assert app._heavy_worker is core._heavy_worker


@pytest.mark.parametrize("app_class", _ROUTE_APPS, ids=lambda c: c.__name__)
def test_every_route_app_shares_the_one_heavy_lock(tmp_path, app_class) -> None:
    """直列化ロックも同一実体でなければ、直列化はルートごとに分裂する。"""
    core = _core(tmp_path)
    app = app_class(inner=core)
    assert app._lock is core._lock


def test_the_whole_chain_keeps_a_single_heavy_worker(tmp_path) -> None:
    """4 本を連結しても、末端まで同じワーカー 1 つを見ている。"""
    core = _core(tmp_path)
    chain = build_replay_routes(core)
    assert chain._heavy_worker is core._heavy_worker
    assert chain._lock is core._lock


def test_the_core_builds_exactly_one_heavy_worker(tmp_path) -> None:
    """ルート表を据えても、ワーカーが増えていない（1 App = 1 ワーカー）。"""
    workers = []
    original = serve_replay._HeavyWorker

    class _Spy(original):  # type: ignore[misc,valid-type]
        def __init__(self) -> None:
            workers.append(self)
            super().__init__()

    serve_replay._HeavyWorker = _Spy
    try:
        apps_built = 1
        _core(tmp_path)
    finally:
        serve_replay._HeavyWorker = original
    assert len(workers) - apps_built == 0, workers


# --------------------------------------------------------------------------------------
# 2. Handler は GET の分岐を持たない
# --------------------------------------------------------------------------------------
def _do_get_source() -> str:
    source = Path(inspect.getsourcefile(serve_replay)).read_text(encoding="utf-8")
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.FunctionDef) and node.name == "do_GET":
            return ast.unparse(node)
    raise AssertionError("do_GET が見つかりません（走査が空振りしています）")


def test_the_handler_has_no_get_routing_branch() -> None:
    """分割前は 7 ルートぶんの分岐を持っていた。ルーティングの宣言は組み立て 1 箇所に置く。"""
    tree = ast.parse(_do_get_source())
    branches = [n for n in ast.walk(tree) if isinstance(n, (ast.If, ast.Try))]
    assert branches == [], ast.unparse(tree)


def test_the_handler_does_not_name_any_route_literal() -> None:
    """ルート文字列が Handler に 1 つも現れない（表の外に第 2 の宣言を作らない）。"""
    source = _do_get_source()
    named = [r for r in ("/candles", "/intraday", "/catalog", "/market_profile",
                         "/tickvol_profile", "/available_days") if r in source]
    assert named == []


# --------------------------------------------------------------------------------------
# 3. ルートの有無は Port の注入で決まる
# --------------------------------------------------------------------------------------
@pytest.mark.parametrize(
    "port_kw,path",
    [
        ("days_port", "/available_days"),
        ("catalog_port", "/catalog"),
        ("market_profile_port", "/market_profile"),
        ("forming_port", "/market_profile_forming"),
        ("tickvol_profile_port", "/tickvol_profile"),
    ],
)
def test_a_route_is_absent_when_its_port_is_not_injected(tmp_path, port_kw, path) -> None:
    """未注入なら静的配信へ落ちる（分割前の ``and app.*_enabled`` と同値）。"""
    with_port = set(_declared_routes(_core(tmp_path)))
    without = set(_declared_routes(_core(tmp_path, **{port_kw: None})))
    assert path in with_port
    assert path not in without


def _declared_routes(core) -> "list[str]":
    """App に**据え付け済み**のルート表を辿って prefix を順に集める。

    ここで build_replay_routes を呼び直さないこと: 既に据えた表の上へ二重に組み上がり、
    すべての prefix が 2 回現れる（本検定の初版が実際にそうなった）。見るべきは
    本番の App がいま持っている表である。
    """
    declared: "list[str]" = []
    node = core.static_server
    while hasattr(node, "_routes"):
        declared.extend(node._routes)
        node = node._fallback
    return declared


def test_the_chain_declares_every_route_exactly_once(tmp_path) -> None:
    """同じ prefix を 2 つの App が宣言していない（どちらが勝つか読めなくなる）。"""
    declared = _declared_routes(_core(tmp_path))
    assert len(declared) - len(set(declared)) == 0, sorted(declared)


# --------------------------------------------------------------------------------------
# 4. 計算量検定（Test Spy・発行 − 使用 = 0）
# --------------------------------------------------------------------------------------
@pytest.mark.parametrize("apps_requested", [1, 4], ids=["build_1", "build_4"])
def test_the_route_table_is_built_once_per_app(tmp_path, apps_requested: int) -> None:
    """App 1 個 / 4 個の 2 点で「ルート表の組み立て − App の生成数 = 0」。

    生成のたびに表を作り直して捨てる、という形になっていないことだけを固定する
    （回数リテラルは焼き込まず、生成数から導出する）。
    """
    # Arrange
    built: "list[object]" = []
    original = serve_replay.build_replay_routes

    def _spy(inner):
        built.append(inner)
        return original(inner)

    serve_replay.build_replay_routes = _spy
    try:
        # Act
        apps = [_core(tmp_path / f"w{i}") for i in range(apps_requested)]
    finally:
        serve_replay.build_replay_routes = original
    # Assert
    assert len(apps) == apps_requested
    assert len(built) - apps_requested == 0, built
