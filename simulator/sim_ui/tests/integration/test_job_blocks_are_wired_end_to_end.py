"""投入ブロックが端から端まで結線されていること（RUN_TRACE_BASIC_DESIGN §6.6.1・Blocker）。

なぜこのゲートが要るか（実測した壊れ方・ISSUE-291 の再発）:
    `JobSubmission` にブロックを 1 本足しても、`file_job_ledger.create` の**手書き dict
    列挙**を触らなければ、そのブロックは子プロセスへ 1 バイトも届かない。HTTP は 202 を
    返し、run は成功し、**その機能だけが無言で出ない**。実測した全ホップ:

        front → POST /sim/jobs
          → job_api_controller.submit    body.get("<block>") を JobSubmission へ
          → job_models.JobSubmission     dataclass フィールド
          → submit_job.execute           受付検証 → ledger.create(submission)
          → file_job_ledger.create       ★ spec.json を書く唯一の場所
          → 子プロセス run_job.main      spec.get("<block>") を読む

    今回（trace）が 5 本目で、実際に取り残された。**1 キー足すだけで終わらせない**——
    手書き dict 列挙そのものが欠陥源なので、`dataclasses.fields(JobSubmission)` からの
    機械導出へ置換したうえで、本ゲートが「6 本目でも同じ事故が起きない」ことを固定する。

**期待値の名前一覧をテストへ書かない**: 表を書けば、それ自体が 6 本目で取り残される
    7 つ目の写しになる。名前は必ず `fields(JobSubmission)` から採る。

**恒真にしない**: `fields()` が空・走査対象を取り違えた・構文木に何も現れない、の
    いずれでも表明が緑になりうる。正の対照と**検出器の自己検査**（合成ソースを実際に
    検出できること）を対で置く。
"""
from __future__ import annotations

import ast
import json
import pathlib
from dataclasses import fields

import pytest

from simulator.sim_ui.usecase.job_models import JobSubmission

#: 走査対象（モジュール, 関数の所在, 読み出しに使う変数名）。
#: 「どの関数の中を見るか」を宣言で持ち、検定本文には書かない。
_HOPS = (
    ("simulator.sim_ui.adapter.job_api_controller", ("JobApiController", "submit"), "body"),
    ("simulator.sim_ui.main.run_job", (None, "main"), "spec"),
)

_BLOCK_NAMES = tuple(f.name for f in fields(JobSubmission))


# ---- 検出器 ----

def _function_node(module_name: str, where: "tuple[str | None, str]") -> ast.AST:
    """`(クラス名 | None, 関数名)` で指す定義ノードを構文木から取り出す。"""
    import importlib

    module = importlib.import_module(module_name)
    tree = ast.parse(pathlib.Path(module.__file__).read_text(encoding="utf-8"))
    class_name, function_name = where
    scope = tree
    if class_name is not None:
        scope = next(
            n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == class_name
        )
    return next(
        n
        for n in scope.body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        and n.name == function_name
    )


def _derivation_shape(tree: ast.AST) -> "tuple[list, set[str]]":
    """`(fields(...) 呼出の列, dict リテラルの文字列鍵の集合)` を返す。

    「機械導出が在る」と「手書き列挙が無い」を**同じ 1 つの検出器**で測る。2 つの
    走査に分けると、片方だけが走査対象を取り違えても気づけない。
    """
    derives = [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "fields"
    ]
    literal_keys = {
        key.value
        for d in ast.walk(tree)
        if isinstance(d, ast.Dict)
        for key in d.keys
        if isinstance(key, ast.Constant) and isinstance(key.value, str)
    }
    return derives, literal_keys


def _string_keys_read_from(node: ast.AST, variable: str) -> "set[str]":
    """`<variable>.get("...")` で読まれている文字列鍵の集合を返す。"""
    keys: "set[str]" = set()
    for child in ast.walk(node):
        if not isinstance(child, ast.Call):
            continue
        func = child.func
        if not (isinstance(func, ast.Attribute) and func.attr == "get"):
            continue
        if not (isinstance(func.value, ast.Name) and func.value.id == variable):
            continue
        if child.args and isinstance(child.args[0], ast.Constant):
            value = child.args[0].value
            if isinstance(value, str):
                keys.add(value)
    return keys


# ---- 検出器の自己検査（走査の空振りを塞ぐ）----

class TestTheDetectorActuallyDetects:
    """検出器が「在る」も「無い」も正しく言えること。"""

    def test_it_finds_the_keys_in_a_synthetic_source(self):
        # Arrange
        source = (
            "class C:\n"
            "    def submit(self, raw):\n"
            "        body = load(raw)\n"
            "        return D(a=body.get('alpha'), b=body.get('beta'), c=other.get('nope'))\n"
        )
        tree = ast.parse(source)
        node = next(
            n
            for n in next(c for c in tree.body if isinstance(c, ast.ClassDef)).body
            if isinstance(n, ast.FunctionDef)
        )

        # Act
        keys = _string_keys_read_from(node, "body")

        # Assert: 対象変数の読み出しだけを拾う（別変数は拾わない）。
        assert keys == {"alpha", "beta"}

    def test_it_reports_nothing_for_a_source_that_reads_nothing(self):
        # Arrange
        node = ast.parse("def main():\n    return 0\n").body[0]

        # Act / Assert
        assert _string_keys_read_from(node, "spec") == set()

    def test_the_scope_lookup_is_confined_to_the_named_function(self):
        """別の関数で読まれている鍵を取り違えて拾わないこと。"""
        # Arrange
        source = (
            "def other():\n    return spec.get('elsewhere')\n"
            "def main():\n    return spec.get('here')\n"
        )
        tree = ast.parse(source)
        node = next(
            n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "main"
        )

        # Act / Assert
        assert _string_keys_read_from(node, "spec") == {"here"}

    def test_the_block_names_are_not_empty(self):
        """正の対照: `fields(JobSubmission)` が空なら全表明が恒真になる。"""
        # Arrange / Act / Assert
        assert len(_BLOCK_NAMES) >= 4, _BLOCK_NAMES
        assert "backtest" in _BLOCK_NAMES


# ---- 表明 1・3: 受付口と子プロセスが各ブロックを読む ----

@pytest.mark.parametrize("block", _BLOCK_NAMES, ids=_BLOCK_NAMES)
@pytest.mark.parametrize(
    "module_name,where,variable", _HOPS, ids=[h[0].rsplit(".", 1)[-1] for h in _HOPS]
)
class TestEveryDeclaredBlockIsReadAtEveryHop:
    """`fields(JobSubmission)` の各名が、各ホップで実際に読まれていること。"""

    def test_the_hop_reads_the_block(self, block, module_name, where, variable):
        # Arrange
        node = _function_node(module_name, where)

        # Act
        keys = _string_keys_read_from(node, variable)

        # Assert
        assert block in keys, (
            f"{module_name}.{where[1]} が {variable}.get({block!r}) を読んでいない"
            f"（読んでいる鍵: {sorted(keys)}）。このブロックは端から端まで結線されておらず、"
            "HTTP は 202・run は成功したまま、この機能だけが無言で出ない"
        )


# ---- 表明 2: 台帳が書く spec.json の鍵 ----

class TestTheLedgerWritesEveryDeclaredBlock:
    """`file_job_ledger.create` の出力 dict に、宣言された全ブロックの鍵が在ること。"""

    def test_the_written_spec_carries_exactly_the_declared_block_names(self, tmp_path):
        """**実際に書かせて読み返す**（構文木では機械導出の鍵を数えられない）。

        鍵を手書き列挙していた頃は「dict リテラルの鍵」を構文木で数えられたが、
        機械導出へ置換した後は列挙そのものが存在しない。ここで測るべきは
        「どう書いたか」ではなく「何が書かれたか」なので、書かせて読む。
        """
        # Arrange
        from simulator.sim_ui.adapter.file_job_ledger import FileJobLedger

        ledger = FileJobLedger(data_root=tmp_path)
        submission = JobSubmission(backtest={"ea_name": "X", "symbol": "S"})

        # Act
        job = ledger.create(submission)
        spec = json.loads(
            (tmp_path / job.job_id / "spec.json").read_text(encoding="utf-8")
        )

        # Assert: 過不足なく一致する（足りなければ届かない・余れば子が知らない鍵を読む）。
        assert set(spec) == set(_BLOCK_NAMES), (sorted(spec), sorted(_BLOCK_NAMES))
        assert _BLOCK_NAMES, "宣言が空（検定が何も測っていない）"

    def test_a_supplied_block_survives_the_round_trip(self, tmp_path):
        """値が素通りすること（鍵だけ在って中身が落ちる形を赤にする）。"""
        # Arrange
        from simulator.sim_ui.adapter.file_job_ledger import FileJobLedger

        ledger = FileJobLedger(data_root=tmp_path)
        supplied = {
            name: {"probe": name}
            for name in _BLOCK_NAMES
            if name != "backtest"
        }
        submission = JobSubmission(backtest={"ea_name": "X"}, **supplied)

        # Act
        job = ledger.create(submission)
        spec = json.loads(
            (tmp_path / job.job_id / "spec.json").read_text(encoding="utf-8")
        )

        # Assert
        for name, value in supplied.items():
            assert spec[name] == value, name
        assert spec["backtest"] == {"ea_name": "X"}
        # 正の対照: 任意ブロックが 0 本なら上のループは恒真になる。
        assert supplied, "任意ブロックが 0 本（検定が何も測っていない）"

    def test_an_absent_block_is_written_as_null_not_dropped(self, tmp_path):
        """不在は null で残すこと（鍵ごと消すと再投入で形が変わる）。"""
        # Arrange
        from simulator.sim_ui.adapter.file_job_ledger import FileJobLedger

        ledger = FileJobLedger(data_root=tmp_path)

        # Act
        job = ledger.create(JobSubmission(backtest={"ea_name": "X"}))
        spec = json.loads(
            (tmp_path / job.job_id / "spec.json").read_text(encoding="utf-8")
        )

        # Assert
        optional = [n for n in _BLOCK_NAMES if n != "backtest"]
        assert {spec[n] for n in optional} == {None}, spec
        assert optional, "任意ブロックが 0 本（検定が何も測っていない）"

    def test_the_ledger_derives_the_keys_instead_of_enumerating_them(self):
        """手書き dict 列挙が**構造として**戻っていないこと。

        読み返しの検定（上）は「今の 5 本」を守るが、6 本目が足された瞬間には
        まだ赤にならない（`fields()` から採るので同時に増える）。欠陥の源は
        「手書き列挙」という形そのものなので、その形の不在を別に固定する。
        """
        # Arrange: 走査はモジュール全体（導出を補助関数へ切り出しても追随する。
        #   `create` の中だけを見ると、切り出した瞬間に検定が空振りする）。
        import simulator.sim_ui.adapter.file_job_ledger as mod

        tree = ast.parse(pathlib.Path(mod.__file__).read_text(encoding="utf-8"))

        # Act
        derives, literal_keys = _derivation_shape(tree)

        # Assert
        assert derives, "`fields(...)` からの機械導出が無い（手書き列挙へ戻っている）"
        assert literal_keys & set(_BLOCK_NAMES) == set(), literal_keys

    def test_the_derivation_detector_would_catch_a_rewritten_enumeration(self):
        """検出器の自己検査: 手書き列挙へ戻した形を実際に赤にできること。"""
        # Arrange
        rewritten = ast.parse(
            "def _spec_of(s):\n"
            "    return {'backtest': dict(s.backtest), 'sizing': None}\n"
        )
        derived = ast.parse(
            "def _spec_of(s):\n"
            "    return {f.name: getattr(s, f.name) for f in fields(s)}\n"
        )

        # Act
        rewritten_shape = _derivation_shape(rewritten)
        derived_shape = _derivation_shape(derived)

        # Assert
        assert rewritten_shape[0] == []
        assert rewritten_shape[1] & set(_BLOCK_NAMES) == {"backtest", "sizing"}
        assert derived_shape[0] and derived_shape[1] == set()
