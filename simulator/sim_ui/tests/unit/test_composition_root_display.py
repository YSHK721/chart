"""合成根（表示層つき sim core）の結線検定（Phase 8 スライス 2）.

固定する不変条件:
    1. `build_sim_display_app()` が schema 経路を**実結線**で持つ（fake ではなく実カタログ・
       実 EA 名・実非対象宣言表に到達する）。
    2. schema の外側事実は注入元と一致する（キー順＝字句層・必須キー＝検証層・
       対象接尾辞＝`main/tester_settings`）。
    3. wrapper は既存の面を置き換えず**包む**（内側に既存の run-options 面が残っている）。

期待値をリテラルで書かない（単一ソースから引く）。書けば合成根の結線が変わっても
この検定だけが古いまま緑になる。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from simulator.adapter.tester_settings.ini_codec import STANDARD_KEY_ORDER
from simulator.framework.tester_settings.validation import required_tester_keys
from simulator.main import known_ea_names
from simulator.main.tester_settings.ea_input_map import SUBJECT_SUFFIX
from simulator.main.tester_settings.unsupported import RULES
from simulator.sim_ui.framework.serve_sim_run_options import SimRunOptionsApp
from simulator.sim_ui.main.composition_root_display import build_sim_display_app
from simulator.usecase.tester_settings.enums import TIMEFRAME_INI_LABELS

_ROOT = Path(__file__).resolve().parents[4]
_SIM_WEB = _ROOT / "simulator" / "sim_ui" / "web"


@pytest.fixture
def schema(tmp_path: Path):
    app = build_sim_display_app(
        repo_root=_ROOT, web_dir=_SIM_WEB, data_root=tmp_path / "data"
    )
    return app, app.inner.settings_schema_controller.schema.list()


def test_the_schema_route_is_wired_to_the_real_catalog(schema) -> None:
    _app, result = schema
    assert {o.token for o in result.enum_options["Period"]} == set(
        TIMEFRAME_INI_LABELS.values()
    )
    assert {n.unsupported_id for n in result.unsupported} == set(RULES)


def test_the_injected_outside_facts_match_their_sources(schema) -> None:
    _app, result = schema
    assert result.key_order == tuple(STANDARD_KEY_ORDER)
    assert result.required_keys == required_tester_keys()
    assert result.required_keys  # 空の必須集合で条件が空振りしない
    tokens = [o.token for o in result.expert_options]
    assert {t.removesuffix(SUBJECT_SUFFIX) for t in tokens} == set(known_ea_names())
    assert all(t.endswith(SUBJECT_SUFFIX) for t in tokens)


def test_the_wrapper_wraps_and_does_not_replace_the_existing_face(schema) -> None:
    app, _result = schema
    assert isinstance(app.inner.inner, SimRunOptionsApp)
    # 既存の選択肢 controller は内側にそのまま残る（置き換えていない）。
    assert app.run_options_controller is app.inner.inner.run_options_controller
