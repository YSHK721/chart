"""sim コアの Composition Root（DI 結線）の検証。

対象: `simulator/sim_ui/main/composition_root.py` の `build_sim_app`。
検証点: web_dir / shared_js_root / repo_root の**結線**（どこを配信面にするかが引数と
既定値から一意に決まり、cwd に依存しない）。replay_ui の同名テストと同じ観点。

構造は AAA。テスト名は「対象_条件_期待結果」。
"""
from __future__ import annotations

from pathlib import Path

from simulator.sim_ui.framework.serve_sim import SimApp
from simulator.sim_ui.main.composition_root import build_sim_app

# repo 根 = simulator/sim_ui/tests/integration/test_composition_root.py の parents[4]。
_REPO_ROOT = Path(__file__).resolve().parents[4]


def test_build_sim_app_returns_a_sim_app():
    # Arrange / Act
    app = build_sim_app()
    # Assert
    assert isinstance(app, SimApp)


def test_web_dir_is_resolved_to_an_absolute_path(tmp_path):
    # Arrange
    web = tmp_path / "sim_web"
    web.mkdir()
    # Act
    app = build_sim_app(web_dir=web)
    # Assert: cwd 非依存の絶対パスで持つ（相対のまま持つと起動場所で配信面が変わる）。
    assert app.web_dir == web.resolve()
    assert app.web_dir.is_absolute()


def test_web_dir_omitted_disables_static_serving():
    # Arrange / Act
    app = build_sim_app()
    # Assert: 未指定なら静的配信は無効（replay と同一規約）。
    assert app.web_dir is None


def test_shared_js_root_defaults_to_indicator_ui_web():
    """共有 JS の既定フォールバック根は indicator_ui/web（単一ソース共有・§11.1 裁定 5）。"""
    # Arrange / Act
    app = build_sim_app()
    # Assert
    assert app.shared_js_root == (_REPO_ROOT / "indigators" / "indicator_ui" / "web").resolve()


def test_shared_js_root_can_be_overridden(tmp_path):
    # Arrange
    shared = tmp_path / "other_web"
    shared.mkdir()
    # Act
    app = build_sim_app(shared_js_root=shared)
    # Assert
    assert app.shared_js_root == shared.resolve()


def test_repo_root_override_moves_the_shared_js_default(tmp_path):
    """repo_root を差し替えると共有 JS の既定もそれに追随する（既定値の導出元が 1 つ）。"""
    # Arrange / Act
    app = build_sim_app(repo_root=tmp_path)
    # Assert
    assert app.shared_js_root == (tmp_path / "indigators" / "indicator_ui" / "web").resolve()


def test_sim_web_directory_ships_an_index_html():
    """sim フロントの実体は simulator/sim_ui/web に置く（§11.1 裁定 5 = D-2）。"""
    # Arrange / Act
    index = _REPO_ROOT / "simulator" / "sim_ui" / "web" / "index.html"
    # Assert
    assert index.is_file(), "sim コアが自分の web を所有する（/sim 経由で配信する実体）"
