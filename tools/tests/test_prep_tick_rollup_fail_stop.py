"""試作 prep_tick_rollup が本番 M1 CSV を上書きできないこと（ISSUE-479 Wave2 フェーズ 1-E）。

固定する仕様:
    `prototype_260626-01/prep_tick_rollup.py` は読み込まれた時点で SystemExit を送出し、
    後継 `tools/build_tick_rollup.py` を案内する。以降の定義・書き込みは到達不能である。

なぜ削除ではなく fail-stop か:
    ファイルの削除は不可逆であり、試作の記録（当時どう作ったか）を失う。一方、
    残したまま「実行できる」状態にしておくと、本番 M1 CSV を絶対パスで無条件に
    上書きする経路が生き続ける。fail-stop は記録を残したまま実行経路だけを塞ぐ。

なぜ「到達不能」を構文木で測るか:
    実行して確かめる方法（走らせて何も起きないことを見る）は、ガードが効いていない
    場合に本番データを壊す。壊れてから分かる検査は検査ではない。だから静的に測る
    （`test_prototype_write_isolation.py` が保護領域への書き込み 0 件を固定し、本ファイルは
    ガードそのものの存在と作用を固定する）。
"""
from __future__ import annotations

import ast
import runpy
from pathlib import Path

import pytest

#: リポジトリ根（このファイル: <repo>/tools/tests/ → parents[2]）。
_REPO = Path(__file__).resolve().parents[2]

_PROTOTYPE = _REPO / "prototype_260626-01" / "prep_tick_rollup.py"


def _module_constant(path: Path, name: str):
    """モジュール直下の定数代入の値を構文木から取り出す。"""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == name for t in node.targets
        ):
            return ast.literal_eval(node.value)
    raise AssertionError(f"{path} に定数 {name} が無い")


class TestThePrototypeCannotRun:
    """読み込んだ時点で止まること。"""

    def test_loading_the_module_raises_system_exit(self):
        with pytest.raises(SystemExit):
            runpy.run_path(str(_PROTOTYPE), run_name="__main__")

    def test_the_message_names_the_successor(self):
        with pytest.raises(SystemExit) as excinfo:
            runpy.run_path(str(_PROTOTYPE), run_name="__main__")
        assert _module_constant(_PROTOTYPE, "SUCCESSOR") in str(excinfo.value)

    def test_the_successor_exists(self):
        successor = _module_constant(_PROTOTYPE, "SUCCESSOR")
        assert (_REPO / successor).is_file(), successor


class TestTheGuardPrecedesEveryWrite:
    """ガードより後ろにしか書き込みが無いこと（到達不能性）。"""

    def test_no_reachable_write_remains(self):
        from tools.tests.test_prototype_write_isolation import _writes_in_source

        source = _PROTOTYPE.read_text(encoding="utf-8")
        assert _writes_in_source(source, "prototype_260626-01/prep_tick_rollup.py") == []

    def test_the_guard_is_an_unconditional_module_level_exit(self):
        """条件付きの exit では無効化と認めない（分岐次第で通り抜けるため）。"""
        from tools.tests.test_prototype_write_isolation import _fail_stop_index

        tree = ast.parse(_PROTOTYPE.read_text(encoding="utf-8"), filename=str(_PROTOTYPE))
        stop = _fail_stop_index(tree)
        assert stop is not None
        # ガードより前の本体には import と定数宣言しか無い（書き込みが先に来ていない）。
        assert all(
            isinstance(stmt, (ast.Import, ast.ImportFrom, ast.Assign, ast.AnnAssign, ast.Expr))
            for stmt in tree.body[:stop]
        )


class TestTheGuardDoesNotWasteWork:
    """計算量検定（発行 − 使用 = 0）。測るのは時間ではなく回数。"""

    @pytest.mark.parametrize("attempts", [1, 4], ids=["attempts_1", "attempts_4"])
    def test_every_attempt_stops_without_touching_the_filesystem(
        self, attempts, monkeypatch
    ):
        """試行 1 / 4 の 2 点で「ファイル書き込みの発行 0」（回数は試行数に非比例）。"""
        writes: "list[str]" = []
        original_open = Path.open

        def counting_open(self, mode="r", *args, **kwargs):
            if any(ch in mode for ch in ("w", "a", "x", "+")):
                writes.append(str(self))
            return original_open(self, mode, *args, **kwargs)

        monkeypatch.setattr(Path, "open", counting_open)
        for _ in range(attempts):
            with pytest.raises(SystemExit):
                runpy.run_path(str(_PROTOTYPE), run_name="__main__")
        # 発行（書き込み）− 使用（0）= 0。何度試しても 1 バイトも書かない。
        assert len(writes) - 0 == 0
