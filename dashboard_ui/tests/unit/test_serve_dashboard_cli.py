"""`python -m dashboard_ui.framework.serve_dashboard` の引数契約（ISSUE-449 レビュー 🟡-1）。

なぜ配信元をコマンド行に載せるのか（ISSUE-348 / ISSUE-355 の再発防止）:
    8481 を**別チェックアウトの** dashboard core が握っていると、こちらの core は bind に
    失敗して死に、router は `/dashboard/*` を別ツリーへ proxy する。自分のコードが 1 行も
    入っていない画面を、自分のものとして検証することになる。
    起動側（unified_ui/serve.sh）が「どのツリーの core か」を止める前に判定できるのは、
    **argv に絶対パスが載っている**ときだけである（PYTHONPATH は argv に現れない）。
    sim core が `-c` の中に web 根の絶対パスを持つのと同じ役割を、本引数が果たす。

構造は AAA。テスト名は「対象_条件_期待結果」。
"""
from __future__ import annotations

from dashboard_ui.framework import serve_dashboard
from dashboard_ui.main import composition_root


class _Calls:
    def __init__(self) -> None:
        self.built: "list[dict]" = []
        self.served: "list[tuple]" = []


def _patch(monkeypatch) -> _Calls:
    calls = _Calls()

    def build(**kwargs):
        calls.built.append(dict(kwargs))
        return "APP"

    def serve(app, port=None):
        calls.served.append((app, port))

    monkeypatch.setattr(composition_root, "build_dashboard_app", build)
    monkeypatch.setattr(serve_dashboard, "serve", serve)
    return calls


def test_the_repo_root_option_is_handed_to_the_composition_root(monkeypatch) -> None:
    # Arrange
    calls = _patch(monkeypatch)

    # Act
    serve_dashboard.main(["18481", "--repo-root", "/tmp/some-worktree"])

    # Assert: 配信元は起動側が決める（core 側が自分で推測しない）。
    assert calls.built == [{"repo_root": "/tmp/some-worktree"}]
    assert calls.served == [("APP", 18481)]


def test_without_the_option_the_composition_root_resolves_the_tree_itself(
    monkeypatch,
) -> None:
    """既定の挙動は不変（引数を足しても既存の起動形が壊れない）。"""
    # Arrange
    calls = _patch(monkeypatch)

    # Act
    serve_dashboard.main(["18481"])

    # Assert
    assert calls.built == [{"repo_root": None}]
    assert calls.served == [("APP", 18481)]


def test_without_any_argument_the_default_port_is_used(monkeypatch) -> None:
    # Arrange
    calls = _patch(monkeypatch)

    # Act
    serve_dashboard.main([])

    # Assert
    assert calls.served == [("APP", serve_dashboard.DEFAULT_PORT)]
