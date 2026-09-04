"""POST /compute のモード表と例外分類の一本化（ISSUE-479 Wave2 3-5 / S-3）。

分割前の do_POST は 3 つのモード（既定 / latest_seq / latest_seq_multi）それぞれに
``except MemoryError / except ValueError / except Exception`` を書き下しており、
**同一の分類が 9 ブロックに複製**されていた。分類を 1 つ直すには 3 箇所を同じ理由で
触ることになり、片方だけ直す形の壊れ方（ISSUE-097 で実際に起きたもの）を招く。

固定する規則:
    1. モードごとの差は「呼ぶメソッド」と「応答のキー」だけである。それを表で宣言する。
    2. 例外の分類は 1 組だけ書く（3 ブロック）。分類の中身は中央翻訳器が持つ。
    3. モード名のリテラルが表の外に現れない（第 2 の宣言を作らない）。

応答 byte のパリティは
`replay_ui/tests/integration/test_replay_route_parity.py` が 3 モード × 正常/異常で固定する。
本ファイルは「表がどうなっているか」と「表から外すと何が壊れるか」を見る。

計算量検定（絶対命令 2026-08-28）: 1 リクエストにつき App のメソッド発行は 1 回
    （発行 − 応答に使った結果の数 = 0）。モードを変えた 2 点で、発行が増えない。
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

from simulator.replay_ui.framework import serve_replay
from simulator.replay_ui.framework.serve_replay_compute import (
    _COMPUTE_POST_MODES,
    _DEFAULT_COMPUTE_MODE,
    ReplayComputeApp,
)


class _App:
    """3 モードぶんの入口を持つ App のフェイク（呼ばれ方だけを記録する）。"""

    def __init__(self, *, raises: "Exception | None" = None) -> None:
        self.calls: "list[str]" = []
        self._raises = raises

    def _result(self, name: str):
        self.calls.append(name)
        if self._raises is not None:
            raise self._raises
        return [{"name": name}]

    def compute(self, body):
        return self._result("compute")

    def compute_seq(self, body):
        return self._result("compute_seq")

    def compute_seq_multi(self, body):
        return self._result("compute_seq_multi")


def _respond(app, body):
    return ReplayComputeApp(inner=app).respond(body)


# --------------------------------------------------------------------------------------
# 1. 表の宣言（モード → メソッドと応答キー）
# --------------------------------------------------------------------------------------
def test_the_table_declares_the_only_difference_between_the_modes() -> None:
    """モードごとの差は「呼ぶメソッド」と「応答のキー」だけ（分類も直列化も共通）。"""
    assert _COMPUTE_POST_MODES == {
        "latest_seq_multi": ("compute_seq_multi", "results"),
        "latest_seq": ("compute_seq", "steps"),
    }
    assert _DEFAULT_COMPUTE_MODE == ("compute", "series")


def test_every_declared_method_exists_on_the_app() -> None:
    """表が実在しない入口を指していない（宣言と実装の乖離を作らない）。"""
    app = _App()
    for method, _key in [*_COMPUTE_POST_MODES.values(), _DEFAULT_COMPUTE_MODE]:
        assert callable(getattr(app, method, None)), method


def test_the_response_keys_are_distinct() -> None:
    """応答キーが重なるとクライアントはモードを見分けられない。"""
    keys = [k for _m, k in [*_COMPUTE_POST_MODES.values(), _DEFAULT_COMPUTE_MODE]]
    assert len(keys) - len(set(keys)) == 0, keys


# --------------------------------------------------------------------------------------
# 2. 表に従って呼び分ける
# --------------------------------------------------------------------------------------
@pytest.mark.parametrize(
    "mode,method,key",
    [
        (None, "compute", "series"),
        ("latest", "compute", "series"),
        ("latest_seq", "compute_seq", "steps"),
        ("latest_seq_multi", "compute_seq_multi", "results"),
    ],
    ids=["default", "latest", "seq", "seq_multi"],
)
def test_the_mode_selects_the_method_and_the_key(mode, method, key) -> None:
    app = _App()
    status, payload = _respond(app, {"mode": mode, "generation": 7})
    assert app.calls == [method]
    assert status == 200
    assert list(payload) == ["ok", "generation", key], payload
    assert payload["generation"] == 7


def test_an_unknown_mode_falls_back_to_the_default() -> None:
    """境界: 表に無いモードは既定へ落ちる（分割前と同じ＝未知モードを弾かない）。"""
    app = _App()
    status, payload = _respond(app, {"mode": "no-such-mode"})
    assert app.calls == ["compute"]
    assert (status, list(payload)) == (200, ["ok", "generation", "series"])


# --------------------------------------------------------------------------------------
# 3. 例外分類は 1 組だけ
# --------------------------------------------------------------------------------------
@pytest.mark.parametrize("mode", [None, "latest_seq", "latest_seq_multi"])
@pytest.mark.parametrize(
    "exc,status,message",
    [
        (MemoryError(), 500, "memory limit"),
        (ValueError("bad"), 400, "bad"),
        (RuntimeError("boom"), 500, "RuntimeError: boom"),
    ],
    ids=["memory", "value", "generic"],
)
def test_every_mode_classifies_exceptions_the_same_way(mode, exc, status, message) -> None:
    """モードごとに分類を書き下すと、片方だけ直す形の壊れ方が生まれる（ISSUE-097 の再来）。"""
    got_status, payload = _respond(_App(raises=exc), {"mode": mode, "generation": 4})
    assert got_status == status
    assert payload["generation"] == 4
    assert payload["error"]["message"] == message


# --------------------------------------------------------------------------------------
# 4. 表の外に第 2 の宣言を作らない
# --------------------------------------------------------------------------------------
def _do_post_node() -> ast.FunctionDef:
    source = Path(inspect.getsourcefile(serve_replay)).read_text(encoding="utf-8")
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.FunctionDef) and node.name == "do_POST":
            return node
    raise AssertionError("do_POST が見つかりません（走査が空振りしています）")


def test_the_handler_no_longer_repeats_the_exception_classification() -> None:
    """分割前は 9 ブロック（3 モード × 3 分類）。分類は 1 組へ寄せる。"""
    handlers = [n for n in ast.walk(_do_post_node()) if isinstance(n, ast.ExceptHandler)]
    assert handlers == [], ast.unparse(_do_post_node())


def test_the_classification_is_written_exactly_once() -> None:
    """モード表の持ち主に分類が 1 組だけある（3 ブロック）。"""
    source = Path(inspect.getsourcefile(ReplayComputeApp)).read_text(encoding="utf-8")
    handlers = [n for n in ast.walk(ast.parse(source)) if isinstance(n, ast.ExceptHandler)]
    kinds = sorted(ast.unparse(h.type) for h in handlers if h.type is not None)
    assert kinds == ["Exception", "MemoryError", "ValueError"], kinds


def _code_string_constants(source_path: Path) -> "set[str]":
    """ソースの**実コード**に現れる文字列リテラル（docstring は除く）。

    散文での言及（「POST /compute mode=... は …」という説明）を違反と誤判定しないため、
    docstring を除く。見たいのは「分岐や比較にモード名が書かれていないか」であって、
    説明を禁じることではない。
    """
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    docstrings = {
        node.body[0].value
        for node in ast.walk(tree)
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
        and node.body
        and isinstance(node.body[0], ast.Expr)
        and isinstance(node.body[0].value, ast.Constant)
        and isinstance(node.body[0].value.value, str)
    }
    return {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and node not in docstrings
    }


def test_the_detector_ignores_prose_but_sees_code(tmp_path: Path) -> None:
    """検出力の自己検査: docstring の言及は見逃し、実コードのリテラルは見る。"""
    sample = tmp_path / "sample.py"
    sample.write_text(
        '"""mode=in-prose の説明。"""\n'
        "x = 1\n"
        "if x == 'in-code':\n"
        "    pass\n",
        encoding="utf-8",
    )
    found = _code_string_constants(sample)
    assert "in-code" in found
    assert "in-prose" not in found


def test_no_mode_literal_appears_outside_the_table() -> None:
    """モード名は表だけが持つ（Handler にも他の App にも分岐として書かない）。"""
    source = Path(inspect.getsourcefile(serve_replay))
    leaked = sorted(_COMPUTE_POST_MODES.keys() & _code_string_constants(source))
    assert leaked == []


# --------------------------------------------------------------------------------------
# 5. 計算量検定（Test Spy・発行 − 使用 = 0）
# --------------------------------------------------------------------------------------
@pytest.mark.parametrize("mode", [None, "latest_seq_multi"], ids=["default", "seq_multi"])
def test_one_request_issues_one_call(mode) -> None:
    """モードを変えた 2 点で「App のメソッド発行 − 応答に使った結果の数 = 0」。

    表引きに失敗して 2 つ試す、という形になっていないことを固定する。
    """
    app = _App()
    _status, payload = _respond(app, {"mode": mode})
    results_used = len([k for k in payload if k not in ("ok", "generation")])
    assert len(app.calls) - results_used == 0, app.calls


def test_a_failing_request_issues_no_extra_call() -> None:
    """異常時に別モードで再試行しない（捨てる計算を作らない）。"""
    app = _App(raises=ValueError("bad"))
    _respond(app, {"mode": "latest_seq"})
    assert len(app.calls) - 1 == 0, app.calls
