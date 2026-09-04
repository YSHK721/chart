"""``common.watch_loop`` — 汎用ポーリングループ（中立核）の契約（ISSUE-479 F-3）。

run_watch は stdlib のみで書かれ、運用スクリプト層（tools）にもチャート UI（indigators の
indicator_ui）にも属さない汎用抽象である。旧所在 tools/watch_loop.py に置いたままだと
export_jp225_m1 から運用スクリプト層への依存辺が生まれ、運用スクリプト層側からの参照と
合わせて循環（C-2）になる。実体を中立核である common へ移し、両アクターがそこを参照する。

本モジュールは (1) 移設が byte 等価であること、(2) ループの副作用契約、(3) 計算量（無駄な待機の
不在）を固定する。
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from common.watch_loop import run_watch

_REPO_ROOT = Path(__file__).resolve().parents[2]
_NEW = _REPO_ROOT / "common" / "watch_loop.py"
_OLD = _REPO_ROOT / "tools" / "watch_loop.py"


def test_the_old_location_is_gone() -> None:
    """旧所在 tools/watch_loop.py は削除済み（ISSUE-479 F-3 段階 2・承認済み）。

    移設が byte 等価であることは、旧所在が孤児として残っていた段階 1 で関数定義の
    ソース断片どうしの突合により確認済みである。両者が並存する限り「どちらが実体か」が
    曖昧なままなので、参照ゼロを確かめたうえで旧所在を消した。以降ここが固定するのは
    **入口が 1 つであること**であり、旧所在を復活させると Red になる。
    """
    assert not _OLD.exists(), (
        "旧所在 tools/watch_loop.py が復活しています。run_watch の実体は common.watch_loop の"
        " 1 箇所だけです（運用スクリプト層に置くと循環 C-2 が戻ります）。"
    )


def _imported_roots(path: Path) -> set[str]:
    """``path`` の絶対 import 文から、パッケージ根の集合を返す（走査本体・分岐はここに閉じる）。"""
    roots: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            roots |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            roots.add(node.module.split(".")[0])
    return roots


def test_module_imports_only_stdlib() -> None:
    """中立核の条件: import は stdlib のみ（アクター・偶有的技術に依存しない）。"""
    outside = _imported_roots(_NEW) - {"__future__", "logging", "time", "typing"}
    assert not outside, f"stdlib 以外に依存: {sorted(outside)}"


def test_stop_after_bounds_the_number_of_updates() -> None:
    # Arrange
    done: list[int] = []

    # Act
    rc = run_watch(lambda: done.append(len(done)), interval=7, sleep_fn=lambda _s: None, stop_after=3)

    # Assert
    assert rc == 0
    assert done == [0, 1, 2]


def test_transient_update_failure_does_not_stop_the_loop() -> None:
    # Arrange
    attempts: list[int] = []

    def _flaky() -> None:
        attempts.append(len(attempts))
        if len(attempts) == 1:
            raise RuntimeError("一過性障害")

    # Act
    rc = run_watch(_flaky, interval=1, sleep_fn=lambda _s: None, stop_after=3)

    # Assert: 例外はログして次インターバルへ継続する（無人ポーリングの可用性）。
    assert rc == 0
    assert attempts == [0, 1, 2]


def test_keyboard_interrupt_from_update_terminates_normally() -> None:
    # Arrange
    calls: list[int] = []

    def _interrupt() -> None:
        calls.append(len(calls))
        raise KeyboardInterrupt

    # Act
    rc = run_watch(_interrupt, interval=1, sleep_fn=lambda _s: None, stop_after=5)

    # Assert
    assert rc == 0
    assert calls == [0]


@pytest.mark.parametrize("stop_after", [3, 5])
def test_loop_issues_no_work_and_no_wait_beyond_what_the_run_produces(stop_after: int) -> None:
    """計算量テスト: 発行した更新・待機が、実際に消化した周期の分をちょうど 1 つも超えない。

    - 更新: 発行（更新関数の呼出回数）− 使用（消化した周期＝出力に現れた成果物）= 0。
    - 待機: 周期の**間**にだけ入る。最後の周期のあとに待つのは捨てられる待機（無駄）なので、
      発行は使用 − 1 に一致する（末尾スリープの不在＝無駄の不在を固定する）。

    2 点（stop_after=3 / 5）で表明するのは「発行が消化周期数だけで決まる」というオーダーであり、
    回数そのもの（N 回呼ばれること）は焼き込まない（期待値は出力から導出する）。
    """
    # Arrange: Test Spy（更新の発行と待機の発行をそれぞれ数える）。
    produced: list[int] = []
    updates_issued: list[None] = []
    sleeps_issued: list[int] = []

    def _update() -> None:
        updates_issued.append(None)
        produced.append(len(produced))          # 出力（実際に消化した周期の成果物）

    def _sleep(seconds) -> None:                # noqa: ANN001
        sleeps_issued.append(seconds)

    # Act
    rc = run_watch(_update, interval=11, sleep_fn=_sleep, stop_after=stop_after)

    # Assert
    assert rc == 0
    used = len(produced)                        # 期待値は出力から導出する
    assert len(updates_issued) - used == 0, "出力に使われない更新を発行しています"
    assert len(sleeps_issued) - (used - 1) == 0, (
        "捨てられる待機を発行しています（最後の周期のあとに sleep している）"
    )
    assert set(sleeps_issued) <= {11}, "注入した interval 以外の待機を発行しています"
