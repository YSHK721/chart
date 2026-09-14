"""`python -m dashboard_ui.main.serve` の引数契約（ISSUE-449 レビュー 🟡-1）。

起動口の所在（ISSUE-502 段階 3・台帳 C-3 / F-11 / F-13）:
    argv 解釈・本番既定（持ち越し・温め）・Composition Root の呼出は **main 層**
    （`dashboard_ui/main/serve.py`）が持つ。以前は `framework/serve_dashboard.py` にあり、
    束縛のために framework が main を関数内 import していた＝循環だった。
    層の側の規則は `test_dashboard_import_direction.py` の R6 が固定する。本ファイルは
    起動口の**振る舞い**（何を読み、何を何回組み、どこへ渡すか）だけを見る。

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

import pytest

from dashboard_ui.framework import serve_dashboard
from dashboard_ui.main import composition_root
from dashboard_ui.main import serve


class _Calls:
    """Test Spy。組み立ての**発行**と、待受けへの**引き渡し**を別々に数える。"""

    def __init__(self) -> None:
        self.built: "list[dict]" = []   # build_dashboard_app に渡った引数
        self.apps: "list[object]" = []  # build_dashboard_app が返した実体
        self.served: "list[tuple]" = []  # serve に渡った (app, port)


def _patch(monkeypatch) -> _Calls:
    calls = _Calls()

    def build(**kwargs):
        calls.built.append(dict(kwargs))
        app = object()  # 呼び出しごとに別実体（作った物と渡した物の同一性を見るため）
        calls.apps.append(app)
        return app

    def serve_(app, port=None):
        calls.served.append((app, port))

    monkeypatch.setattr(composition_root, "build_dashboard_app", build)
    monkeypatch.setattr(serve_dashboard, "serve", serve_)
    return calls


def test_the_repo_root_option_is_handed_to_the_composition_root(monkeypatch) -> None:
    # Arrange
    calls = _patch(monkeypatch)

    # Act
    serve.main(["18481", "--repo-root", "/tmp/some-worktree"])

    # Assert: 配信元は起動側が決める（core 側が自分で推測しない）。本番の起動口だけが
    #   確定素材の持ち越しと温めを有効化する（ISSUE-501 段階 2）。
    assert calls.built == [
        {"repo_root": "/tmp/some-worktree", "persist": True, "warmup": True}
    ]
    assert calls.served == [(calls.apps[0], 18481)]


def test_without_the_option_the_composition_root_resolves_the_tree_itself(
    monkeypatch,
) -> None:
    """既定の挙動は不変（引数を足しても既存の起動形が壊れない）。"""
    # Arrange
    calls = _patch(monkeypatch)

    # Act
    serve.main(["18481"])

    # Assert
    assert calls.built == [{"repo_root": None, "persist": True, "warmup": True}]
    assert calls.served == [(calls.apps[0], 18481)]


def test_without_any_argument_the_default_port_is_used(monkeypatch) -> None:
    # Arrange
    calls = _patch(monkeypatch)

    # Act
    serve.main([])

    # Assert
    assert calls.served == [(calls.apps[0], serve.DEFAULT_PORT)]


# ------------------------------------------------------- 計算量（発行 − 使用 = 0）
#
# 起動口は「アプリを組む」という重い計算（素材の持ち越し読み込み・温めスレッドの起動）を
#   発行する。出力（待受けが始まる）は 1 個組めば正しく見えるため、**捨てる組み立てを
#   1 個混ぜても状態検証では落ちない**（CLAUDE.md 絶対命令 2026-08-28）。
#   固定するのは「無駄が無いこと」であって回数そのものではないので、
#   `発行した組み立て − 待受けへ渡した組み立て = 0` の形で表明する。

@pytest.mark.parametrize(
    "argv",
    [
        [],
        ["18481"],
        ["18481", "--repo-root", "/tmp/some-worktree"],
        ["18481", "--repo-root", "/tmp/some-worktree", "--repo-root", "/tmp/other"],
    ],
    ids=["no-args", "port", "port+root", "port+root-twice"],
)
def test_the_entry_point_discards_no_application_it_built(monkeypatch, argv) -> None:
    """組み立てた実体は 1 つで、それがそのまま待受けへ渡る（捨てる組み立てが無い）。

    argv を伸ばしても発行が増えないこと（オーダーの表明）を 4 点で固定する。
    引数の解釈は組み立ての**前**に完了していなければならず、`serve._parse` の途中で
    組み直す実装（読み直すたびに build する形）はここで落ちる。
    """
    # Arrange
    calls = _patch(monkeypatch)

    # Act
    serve.main(list(argv))

    # Assert
    handed_over = [app for app, _ in calls.served]
    assert len(calls.apps) == 1
    assert handed_over == calls.apps          # 発行 − 使用 = 0（捨てた実体が無い）
    assert len(calls.built) == len(calls.apps)
