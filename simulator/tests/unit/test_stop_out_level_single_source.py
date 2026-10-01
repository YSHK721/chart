"""ストップアウト水準の出所は台帳ただ 1 つである（ISSUE-546）。

何を固定するか:
    sim 画面の run はストップアウト水準を渡さず、「`build_interactor`」 / 「`EngineBinding`」 の既定
    0.0 が入っていた。判定は `margin_level < stop_out_level` なので、強制決済は有効証拠金が
    負になるまで起きず、残高が −55.2 になるまで取引が続いた（実測・ジョブ 1d08008a）。
    口座の水準は MT5 端末の `account_info().margin_so_so`（台帳
    `marketdata/symbol_specs/<server>/<symbol>.json`）にあり、「`spec_fields`」 が
    「`stop_out_level`」 として配る。

    人が書いた水準（既定値・CLI の既定・呼出側のリテラル）が 1 つでも残ると、その入口だけ
    黙って別の水準で走る。出力は形式上正しいので状態検証では落ちない。よって本番コードの
    ソースを AST で走査し、**水準を値で書いた箇所が 0 件**であることを固定する。

    走査の対象は「本番コード」＝ `simulator/` と `marketdata/` と `tools/` の `.py` のうち
    検定（`tests/` 配下・`test_*.py`）を除いたもの。散文（docstring・コメント）は数えない
    （AST の数値定数だけを見る）。
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[3]
_ROOTS = ("simulator", "marketdata", "tools")
_NAME = "stop_out_level"
_OPTION = "--stop-out-level"


def _production_files() -> "list[Path]":
    files = []
    for root in _ROOTS:
        for path in sorted((_REPO / root).rglob("*.py")):
            rel = path.relative_to(_REPO).parts
            if "tests" in rel or path.name.startswith("test_") or "sim_ui" in rel and "data" in rel:
                continue
            if ".venv" in rel or "node_modules" in rel or "__pycache__" in rel:
                continue
            files.append(path)
    return files


def _is_written_value(node: "ast.AST | None") -> bool:
    """人が書いた値か（数値の定数・その符号反転）。``None`` は「値なし」なので数えない。"""
    if isinstance(node, ast.UnaryOp):
        node = node.operand
    return isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(
        node.value, bool
    )


def written_stop_out_levels(source: str) -> "list[str]":
    """ソース 1 つの中で、ストップアウト水準を値で書いた箇所を挙げる（空なら合格）。"""
    found: "list[str]" = []
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            args = node.args
            positional = args.posonlyargs + args.args
            defaults = [None] * (len(positional) - len(args.defaults)) + list(args.defaults)
            pairs = list(zip(positional, defaults)) + list(zip(args.kwonlyargs, args.kw_defaults))
            for arg, default in pairs:
                if arg.arg == _NAME and default is not None:
                    found.append(f"{node.name}:{node.lineno} 引数 {_NAME} に既定値")
        elif isinstance(node, ast.AnnAssign):
            target = node.target
            if isinstance(target, ast.Name) and target.id == _NAME and node.value is not None:
                found.append(f"{node.lineno} 欄 {_NAME} に既定値")
        elif isinstance(node, ast.Call):
            for kw in node.keywords:
                if kw.arg == _NAME and _is_written_value(kw.value):
                    found.append(f"{node.lineno} 呼出の {_NAME}= に値")
            literal_args = [a.value for a in node.args if isinstance(a, ast.Constant)]
            if _OPTION in literal_args:
                for kw in node.keywords:
                    if kw.arg == "default" and not (
                        isinstance(kw.value, ast.Constant) and kw.value.value is None
                    ):
                        found.append(f"{node.lineno} {_OPTION} に既定値")
        elif isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values):
                if isinstance(key, ast.Constant) and key.value == _NAME and _is_written_value(value):
                    found.append(f"{node.lineno} 辞書の {_NAME} に値")
    return found


def test_no_production_entry_writes_a_stop_out_level():
    """本番コードに水準を値で書いた箇所が無い（出所は台帳ただ 1 つ）。"""
    files = _production_files()
    assert files, "走査対象が空（前提が崩れている）"
    offenders = {
        str(path.relative_to(_REPO)): hits
        for path in files
        if (hits := written_stop_out_levels(path.read_text(encoding="utf-8")))
    }
    assert offenders == {}


def test_the_ledger_supplies_the_stop_out_level():
    """台帳の配り口に水準がある（上の検定が「どこにも無い」で空虚に緑にならない）。"""
    from marketdata.symbol_spec_snapshot import OANDA_JAPAN_MT5_LIVE, load_spec_fields

    assert _NAME in load_spec_fields(OANDA_JAPAN_MT5_LIVE, "JP225")


@pytest.mark.parametrize(
    "source",
    [
        "def f(*, stop_out_level: float = 0.0): ...",
        "def f(stop_out_level=100.0): ...",
        "class C:\n    stop_out_level: float = 0.0\n",
        "g(stop_out_level=99.95)",
        "g(stop_out_level=-1)",
        "p.add_argument('--stop-out-level', type=float, default=100.0)",
        "x = {'stop_out_level': 100.0}",
    ],
)
def test_the_scan_flags_every_written_form(source):
    """負の対照: 値を書いた形はどれも検出する（検査が何も測らずに緑にならない）。"""
    assert written_stop_out_levels(source)


@pytest.mark.parametrize(
    "source",
    [
        "def f(*, stop_out_level: float): ...",
        "g(stop_out_level=spec['stop_out_level'])",
        "g(**spec)",
        "p.add_argument('--stop-out-level', type=float, default=None)",
        "class C:\n    stop_out_level: float\n",
    ],
)
def test_the_scan_stays_silent_on_sourced_forms(source):
    """正の対照: 台帳から渡す形は検出しない。"""
    assert written_stop_out_levels(source) == []
