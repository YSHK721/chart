"""call_binding / indicator_compute_adapter の SRP・OCP 構造ガード（ISSUE-479 Wave2 I-1）。

固定するのは「指標を 1 件足すときに本体を改変しなくてよい」構造そのものである。
分岐（``if`` / ``==``）は「指標名を知っている場所」の痕跡なので、**分岐の不在**を数える。

  R1 SRP  : price_range_power 固有の定数が call_binding に代入されていない（協働子へ移設）。
  R2 SRP  : nice_step の丸め規則の実装が repo に 1 件だけ（第 2 実装＝取り残しの温床）。
  R3 OCP-1: ``_fitter_factory`` 本体に比較（``==``/``is``）が 0 件（fitter は表引き）。
  R4 OCP-2: invoke に if が 0 件（kind は表引き）＋表の鍵集合＝_TABLE の kind 集合。
  R5 OCP-3: indicator_compute_adapter の文字列リテラルに compute_id が 0 件（宣言は _TABLE 側）。

様式は同ディレクトリの構造ガード（``test_mp_worker_io_separation.py`` / ``test_no_usecase_dependency.py``）
を踏襲する（AST 走査・offender を file:line で提示）。
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from adapter.compute import call_binding
from adapter.compute import indicator_compute_adapter as ica
from adapter.compute.call_binding import _TABLE

_CALL_BINDING_PY = Path(call_binding.__file__).resolve()
_ADAPTER_PY = Path(ica.__file__).resolve()
_REPO_ROOT = Path(__file__).resolve().parents[4]

#: 走査から外す木（第三者コード・生成物・仮想環境）。
_EXCLUDED_PARTS = {".venv", "venv", "node_modules", "__pycache__", ".git", "out", "site-packages"}


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"))


def _docstring_ids(tree: ast.AST) -> set[int]:
    """docstring として置かれた文字列定数の id 集合（リテラル走査から除く）。"""
    out: set[int] = set()
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if not isinstance(body, list) or not body:
            continue
        first = body[0]
        if (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
        ):
            out.add(id(first.value))
    return out


# --------------------------------------------------------------------------- #
# R1: price_range_power 固有の定数は call_binding に代入されない（SRP 分離の実証）
# --------------------------------------------------------------------------- #
def _assigned_names(tree: ast.AST) -> list[tuple[int, str]]:
    """モジュール内で代入されている Name（Assign / AnnAssign の左辺）を行番号付きで列挙する。"""
    out: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        targets: list[ast.expr] = []
        if isinstance(node, ast.Assign):
            targets = list(node.targets)
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        out.extend((node.lineno, t.id) for t in targets if isinstance(t, ast.Name))
    return out


def test_price_range_power_constants_are_not_assigned_in_call_binding():
    offenders = [
        f"{_CALL_BINDING_PY.name}:{lineno}: {name}"
        for lineno, name in _assigned_names(_tree(_CALL_BINDING_PY))
        if name.startswith("_PRP_")
    ]
    assert not offenders, (
        "price_range_power 固有の定数が call_binding に残っている（協働子 bindings/"
        "price_range_power.py へ移設し再エクスポートすること）:\n" + "\n".join(offenders)
    )


def test_call_binding_reexports_price_range_power_hooks():
    """再エクスポート面（既存参照面）は維持される。"""
    assert callable(call_binding._adapt_prp_interval)
    assert callable(call_binding._nice_step)
    assert callable(call_binding._prp_preprocess)


# --------------------------------------------------------------------------- #
# R2: nice_step の丸め規則の実装は repo に 1 件（逐語第 2 実装の禁止）
# --------------------------------------------------------------------------- #
#: 1/2/5/10 ×10^n へ丸める規則の指紋（この定数集合を持つ IfExp 連鎖）。
_NICE_STEP_LADDER = {1.0, 2.0, 5.0, 10.0}


def _has_nice_step_ladder(fn: ast.AST) -> bool:
    consts: set[float] = set()
    for node in ast.walk(fn):
        if isinstance(node, ast.IfExp):
            for sub in ast.walk(node):
                if isinstance(sub, ast.Constant) and isinstance(sub.value, float):
                    consts.add(sub.value)
    return _NICE_STEP_LADDER <= consts


def _python_sources() -> list[Path]:
    out = []
    for p in _REPO_ROOT.rglob("*.py"):
        if _EXCLUDED_PARTS & set(p.parts):
            continue
        out.append(p)
    return out


def _nice_step_implementations() -> list[str]:
    sites = []
    for path in _python_sources():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):  # pragma: no cover - 走査対象外の壊れた木
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and _has_nice_step_ladder(node):
                sites.append(f"{path.relative_to(_REPO_ROOT)}:{node.lineno}:{node.name}")
    return sorted(sites)


def test_nice_step_rounding_rule_is_implemented_exactly_once_in_repo():
    sites = _nice_step_implementations()
    assert len(sites) == 1, (
        "1/2/5×10^n 丸め規則の実装が複数ある（逐語複製は必ず取り残しを生む・単一ソース化せよ）:\n"
        + "\n".join(sites)
    )


def test_nice_step_detector_finds_the_canonical_implementation():
    """検出器の自己検定: 正典実装そのものを検出できている（空振りでない）。"""
    sites = _nice_step_implementations()
    assert any("price_range_power.py" in s for s in sites), sites


# --------------------------------------------------------------------------- #
# R3: _fitter_factory は表引き（本体に比較 0 件）
# --------------------------------------------------------------------------- #
def _module_function(tree: ast.Module, name: str) -> ast.FunctionDef:
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    raise AssertionError(f"module 関数 {name} が見つからない（テストの前提崩壊）")


def test_fitter_factory_body_has_no_comparison():
    fn = _module_function(_tree(_CALL_BINDING_PY), "_fitter_factory")
    offenders = [
        f"{_CALL_BINDING_PY.name}:{n.lineno}: {ast.unparse(n)}"
        for n in ast.walk(fn)
        if isinstance(n, ast.Compare)
    ]
    assert not offenders, (
        "_fitter_factory が fitter 名を比較で分岐している（_FITTERS 表引きにすること）:\n"
        + "\n".join(offenders)
    )


def test_fitters_table_is_the_single_declaration_of_known_fitters():
    assert set(call_binding._FITTERS) == {"ols", "tgp"}


def test_fitter_factory_still_raises_value_error_with_same_message_for_unknown():
    with pytest.raises(ValueError, match="未知の fitter です: nope"):
        call_binding._fitter_factory("nope")


def test_fitter_factory_returns_declared_fitter_instances():
    src = call_binding._load_src_package("tgp_btlm")
    assert isinstance(call_binding._fitter_factory("ols"), src.OlsBtlmFitter)
    assert isinstance(call_binding._fitter_factory("tgp"), src.TgpBtlmFitter)


# --------------------------------------------------------------------------- #
# R4: invoke は表引き（if 0 件）／kind 表と _TABLE の kind 集合が一致
# --------------------------------------------------------------------------- #
def _method(tree: ast.Module, class_name: str, method: str) -> ast.FunctionDef:
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            for sub in node.body:
                if isinstance(sub, ast.FunctionDef) and sub.name == method:
                    return sub
    raise AssertionError(f"{class_name}.{method} が見つからない（テストの前提崩壊）")


def test_invoke_has_no_branches():
    fn = _method(_tree(_CALL_BINDING_PY), "CallBinding", "invoke")
    offenders = [
        f"{_CALL_BINDING_PY.name}:{n.lineno}: {ast.unparse(n).splitlines()[0]}"
        for n in ast.walk(fn)
        if isinstance(n, (ast.If, ast.IfExp))
    ]
    assert not offenders, (
        "invoke が kind / 指標を分岐している（_INVOKERS 表引きにすること）:\n" + "\n".join(offenders)
    )


def test_invokers_table_covers_exactly_the_declared_kinds():
    assert set(call_binding._INVOKERS) == {spec["kind"] for spec in _TABLE.values()}


def test_kind_consumed_params_is_derived_from_the_invokers_table():
    """``_KIND_CONSUMED_PARAMS`` は表からの導出値（二重宣言を作らない）。"""
    assert call_binding._KIND_CONSUMED_PARAMS == {
        kind: inv.consumes for kind, inv in call_binding._INVOKERS.items()
    }
    assert call_binding._KIND_CONSUMED_PARAMS["btlm"] == frozenset({"fitter", "mcmc_samples"})
    assert call_binding._KIND_CONSUMED_PARAMS["kw"] == frozenset()


# --------------------------------------------------------------------------- #
# R5: adapter に compute_id の文字列リテラルが無い（宣言は _TABLE 側 value_error_types）
# --------------------------------------------------------------------------- #
def _string_literals(path: Path) -> list[tuple[int, str]]:
    tree = _tree(path)
    skip = _docstring_ids(tree)
    return [
        (n.lineno, n.value)
        for n in ast.walk(tree)
        if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in skip
    ]


def test_adapter_contains_no_compute_id_string_literal():
    compute_ids = {cid for cid, _variant in _TABLE}
    offenders = [
        f"{_ADAPTER_PY.name}:{lineno}: {value!r}"
        for lineno, value in _string_literals(_ADAPTER_PY)
        if value in compute_ids
    ]
    assert not offenders, (
        "adapter が指標名リテラルを持っている（value_error_types 宣言を _TABLE 側へ移し、"
        "adapter は宣言の有無だけを見ること）:\n" + "\n".join(offenders)
    )


def test_value_error_types_declared_on_both_profit_band_variants():
    for variant in ("global", "robust"):
        declared = _TABLE[("profit_band", variant)].get("value_error_types")
        assert declared is not None, variant
        assert set(declared) == {"empty_series"}


def test_value_error_types_accessor_returns_declaration_union():
    declared = call_binding.value_error_types("profit_band")
    assert set(declared) == {"empty_series"}
    assert declared["empty_series"]() is call_binding.profit_band_empty_bucket_error()
    # 未宣言指標は空（汎用 validation へ一様翻訳される）。
    assert call_binding.value_error_types("tgp_btlm") == {}
    assert call_binding.value_error_types("does_not_exist") == {}


def test_value_error_translators_is_derived_from_declarations():
    assert set(ica._VALUE_ERROR_TRANSLATORS) == set(call_binding.value_error_declarations())
    assert "profit_band" in ica._VALUE_ERROR_TRANSLATORS
