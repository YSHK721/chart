"""call_binding / indicator_compute_adapter の SRP・OCP 構造ガード（ISSUE-479 Wave2 I-1
／ISSUE-502 段階 4B）。

固定するのは「指標を 1 件足すときに本体を改変しなくてよい」構造そのものである。
分岐（``if`` / ``==``）は「指標名を知っている場所」の痕跡なので、**分岐の不在**を数える。

  R1 SRP  : price_range_power 固有の定数が call_binding に代入されていない（協働子へ移設）。
  R2 SRP  : nice_step の丸め規則の実装が repo に 1 件だけ（第 2 実装＝取り残しの温床）。
  R3 OCP-1: ``fitter_factory`` 本体に比較（``==``/``is``）が 0 件（fitter は表引き）。
  R4 OCP-2: invoke に if が 0 件（kind は表引き）＋表の鍵集合＝_TABLE の kind 集合。
  R5 OCP-3: indicator_compute_adapter の文字列リテラルに compute_id が 0 件（宣言は _TABLE 側）。

ISSUE-502 段階 4B で 4 本を追加した。R1〜R3 は「特定の 1 指標について移設済み」を固定していた
（＝次の指標で同じ違反が再発しても緑）。G1〜G4 は **指標名を名指ししない不変条件**であり、
どの指標の固有ロジックが call_binding へ現れても Red になる:

  G1 SRP  : call_binding.py が定義する名前（def / class / 代入 / import 別名）に compute_id が
            現れない（``profit_band_empty_bucket_error`` 型の関数・``_prp_preprocess`` 型の
            再エクスポート別名を再発させない）。
  G2 OCP  : compute_id の文字列リテラルは ``_TABLE`` 代入の**内側**にしか現れない。
  G3 SRP  : ``_TABLE`` / ``_INVOKERS`` が宣言する hook の実装モジュールが call_binding ではない
            （preprocess・value_error_types の型ローダ・名前付き latest_meta resolver・Invoker）。
  G4 DIP  : 協働子（bindings/*）と汎用機構（src_packages / param_binding / kind_invokers）が
            call_binding を import しない（依存は表 → 協働子の一方向）。

様式は同ディレクトリの構造ガード（``test_mp_worker_io_separation.py`` / ``test_no_usecase_dependency.py``）
を踏襲する（AST 走査・offender を file:line で提示）。
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from adapter.compute import call_binding, kind_invokers, param_binding, src_packages
from adapter.compute import indicator_compute_adapter as ica
from adapter.compute.bindings import price_range_power as prp
from adapter.compute.bindings import profit_band as profit_band_binding
from adapter.compute.bindings import tgp_btlm
from adapter.compute.call_binding import _TABLE

_CALL_BINDING_PY = Path(call_binding.__file__).resolve()
_TGP_BTLM_PY = Path(tgp_btlm.__file__).resolve()
_ADAPTER_PY = Path(ica.__file__).resolve()
_REPO_ROOT = Path(__file__).resolve().parents[4]
_BINDINGS_DIR = Path(prp.__file__).resolve().parent
_COMPUTE_DIR = _CALL_BINDING_PY.parent

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


def test_price_range_power_hooks_live_in_the_collaborator_not_in_call_binding():
    """フックの参照面は協働子ただ 1 つ（ISSUE-502 段階 4B で再エクスポート別名を撤去）。

    従来は ``call_binding._nice_step`` 等の別名 3 行を共有ファイルへ置いて参照面を維持していた。
    これは「指標を 1 件足すたびに共有ファイルへ行を足す」構造そのものであり OCP 違反なので、
    別名を撤去して参照面を協働子へ一本化した。ここでは (a) 協働子側に在ること
    (b) call_binding 側に別名が**無い**ことの両方を固定する（G1 が一般形を担保する）。
    """
    assert callable(prp.adapt_interval)
    assert callable(prp.nice_step)
    assert callable(prp.preprocess)
    for alias in ("_adapt_prp_interval", "_nice_step", "_prp_preprocess"):
        assert not hasattr(call_binding, alias), alias


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
    # 所有者は協働子 bindings/tgp_btlm.py（ISSUE-502 段階 4B）。検査内容は不変。
    fn = _module_function(_tree(_TGP_BTLM_PY), "fitter_factory")
    offenders = [
        f"{_TGP_BTLM_PY.name}:{n.lineno}: {ast.unparse(n)}"
        for n in ast.walk(fn)
        if isinstance(n, ast.Compare)
    ]
    assert not offenders, (
        "fitter_factory が fitter 名を比較で分岐している（FITTERS 表引きにすること）:\n"
        + "\n".join(offenders)
    )


def test_fitters_table_is_the_single_declaration_of_known_fitters():
    assert set(tgp_btlm.FITTERS) == {"ols", "tgp"}


def test_fitter_factory_still_raises_value_error_with_same_message_for_unknown():
    with pytest.raises(ValueError, match="未知の fitter です: nope"):
        tgp_btlm.fitter_factory("nope")


def test_fitter_factory_returns_declared_fitter_instances():
    src = src_packages.load_src_package("tgp_btlm")
    assert isinstance(tgp_btlm.fitter_factory("ols"), src.OlsBtlmFitter)
    assert isinstance(tgp_btlm.fitter_factory("tgp"), src.TgpBtlmFitter)


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
    assert declared["empty_series"]() is profit_band_binding.empty_bucket_error()
    # 未宣言指標は空（汎用 validation へ一様翻訳される）。
    assert call_binding.value_error_types("tgp_btlm") == {}
    assert call_binding.value_error_types("does_not_exist") == {}


def test_value_error_translators_is_derived_from_declarations():
    assert set(ica._VALUE_ERROR_TRANSLATORS) == set(call_binding.value_error_declarations())
    assert "profit_band" in ica._VALUE_ERROR_TRANSLATORS


# =========================================================================== #
# G1〜G4（ISSUE-502 段階 4B）— 指標名を名指ししない一般形の構造ガード
#
# R1〜R3 は「price_range_power / profit_band / tgp_btlm について移設済み」を固定していた。
# それでは **次の指標**が同じやり方（call_binding へ固有関数・固有定数・再エクスポート別名を
# 足す）で入っても検査は緑のままである。以下は不変条件そのものを固定する。
# =========================================================================== #
_COMPUTE_IDS = sorted({cid for cid, _variant in _TABLE})


def _table_assign_node(tree: ast.Module) -> ast.AST:
    for node in tree.body:
        targets = (
            list(node.targets) if isinstance(node, ast.Assign)
            else [node.target] if isinstance(node, ast.AnnAssign) else []
        )
        if any(isinstance(t, ast.Name) and t.id == "_TABLE" for t in targets):
            return node
    raise AssertionError("_TABLE の代入が見つからない（テストの前提崩壊）")


def _defined_names(tree: ast.Module) -> list[tuple[int, str]]:
    """モジュール直下で **名前を導入する** 構文（def / class / 代入 / import 別名）を列挙する。"""
    out: list[tuple[int, str]] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            out.append((node.lineno, node.name))
        elif isinstance(node, ast.Assign):
            out.extend((node.lineno, t.id) for t in node.targets if isinstance(t, ast.Name))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            out.append((node.lineno, node.target.id))
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            out.extend((node.lineno, a.asname or a.name.split(".")[0]) for a in node.names)
    return out


def _per_indicator_imports(tree: ast.Module) -> list[tuple[int, str]]:
    """指標を名指しする import（``...bindings.<compute_id>`` 等）を行番号付きで列挙する。

    別名（``import ... as _prp_preprocess``）で指標名を隠せるため、**導入された名前だけでなく
    import 元のモジュール経路**も見る。協働子へは遅延解決するパッケージ ``bindings`` 経由でしか
    触らない（＝指標を足しても import 行は増えない）ことを課す。
    """
    out: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        paths: list[str] = []
        if isinstance(node, ast.ImportFrom) and node.module:
            paths = [node.module] + [f"{node.module}.{a.name}" for a in node.names]
        elif isinstance(node, ast.Import):
            paths = [a.name for a in node.names]
        for path in paths:
            segments = set(path.split("."))
            out.extend((node.lineno, f"{path} ({cid})") for cid in _COMPUTE_IDS if cid in segments)
    return out


def _g1_offenders(tree: ast.Module) -> list[str]:
    """G1 の offender（指標を名指しする定義名・import 経路）を列挙する。"""
    out = [
        f"{lineno}: {name}  <- {cid}"
        for lineno, name in _defined_names(tree)
        for cid in _COMPUTE_IDS
        if cid in name.lower()
    ]
    out += [f"{lineno}: import {label}" for lineno, label in _per_indicator_imports(tree)]
    return out


def test_g1_call_binding_names_no_indicator_specific_symbol_or_module():
    """G1: call_binding.py が導入する名前にも import 経路にも compute_id が現れない。

    ``profit_band_empty_bucket_error`` / ``_moving_averages_latest_meta``（固有関数）と
    ``from ...bindings.price_range_power import preprocess as _prp_preprocess``（別名で指標名を
    隠した再エクスポート）はいずれもこの形だった。どちらも「指標を足すとき本ファイルを触る」
    ことを意味するので、名前と import 経路の両水準で禁ずる。
    """
    offenders = [f"{_CALL_BINDING_PY.name}:{o}" for o in _g1_offenders(_tree(_CALL_BINDING_PY))]
    assert not offenders, (
        "call_binding が指標を名指ししている（協働子 adapter/compute/bindings/<compute_id>.py へ移し、"
        "_TABLE の宣言から bindings 経由で参照すること）:\n" + "\n".join(offenders)
    )


def test_g1_detector_catches_a_reintroduced_indicator_specific_definition():
    """G1 の検出力（負の対照 1）: 指標名を含む定義を足した木は offender として挙がる。"""
    hits = _g1_offenders(ast.parse("def profit_band_empty_bucket_error():\n    return None\n"))
    assert hits == ["1: profit_band_empty_bucket_error  <- profit_band"]


def test_g1_detector_catches_an_alias_that_hides_the_indicator_name():
    """G1 の検出力（負の対照 2）: 別名で指標名を隠した再エクスポートも挙がる。

    ``_prp_preprocess`` という別名自体には compute_id が含まれないため、定義名だけを見る検査は
    これを見逃す（実測で確認した穴）。import 経路まで見て初めて Red になる。
    """
    src = (
        "from adapter.compute.bindings.price_range_power import preprocess as _prp_preprocess\n"
    )
    hits = _g1_offenders(ast.parse(src))
    assert hits and all("price_range_power" in h for h in hits), hits


def test_g2_compute_id_string_literals_appear_only_inside_the_table():
    """G2: compute_id の文字列リテラルは ``_TABLE`` 代入の内側にしか無い。

    ``_DEFAULT_SAMPLES = _TABLE[("tgp_btlm", "default")][...]`` や
    ``_load_src_package("profit_band")`` のように、表の外で指標名を書く箇所を禁ずる
    （表の外に指標名がある＝そこが指標ごとの改変点になる）。
    """
    tree = _tree(_CALL_BINDING_PY)
    inside = {id(n) for n in ast.walk(_table_assign_node(tree))}
    skip = _docstring_ids(tree)
    offenders = [
        f"{_CALL_BINDING_PY.name}:{n.lineno}: {n.value!r}"
        for n in ast.walk(tree)
        if isinstance(n, ast.Constant)
        and isinstance(n.value, str)
        and n.value in _COMPUTE_IDS
        and id(n) not in inside
        and id(n) not in skip
    ]
    assert not offenders, (
        "call_binding が _TABLE の外で指標名リテラルを持っている:\n" + "\n".join(offenders)
    )


def test_g2_detector_sees_the_table_keys_as_the_only_legitimate_site():
    """G2 の自己検定: 表の内側には指標名リテラルが実在する（検査が空振りでない）。"""
    tree = _tree(_CALL_BINDING_PY)
    inside = [
        n.value for n in ast.walk(_table_assign_node(tree))
        if isinstance(n, ast.Constant) and isinstance(n.value, str) and n.value in _COMPUTE_IDS
    ]
    assert set(inside) == set(_COMPUTE_IDS)


def _declared_hooks() -> list[tuple[str, object]]:
    """_TABLE / _INVOKERS が宣言する hook 実体（呼出可能）を (ラベル, 実体) で列挙する。"""
    out: list[tuple[str, object]] = []
    for (compute_id, variant), spec in _TABLE.items():
        label = f"{compute_id}/{variant}"
        hook = spec.get("preprocess")
        if hook is not None:
            out.append((f"{label}.preprocess", hook))
        resolver = spec.get("latest_meta")
        # lambda（宣言そのもの）は表に置いてよい。名前つき関数＝手続きは協働子が持つ。
        if resolver is not None and getattr(resolver, "__name__", "<lambda>") != "<lambda>":
            out.append((f"{label}.latest_meta", resolver))
        for error_type, loader in (spec.get("value_error_types") or {}).items():
            out.append((f"{label}.value_error_types[{error_type}]", loader))
    for kind, invoker in call_binding._INVOKERS.items():
        out.append((f"_INVOKERS[{kind}].call", invoker.call))
    return out


def test_g3_declared_hooks_are_implemented_outside_call_binding():
    """G3: 宣言された hook の実装は協働子（bindings.*）か汎用機構であり call_binding ではない。"""
    allowed = ("adapter.compute.bindings.", "adapter.compute.kind_invokers")
    offenders = [
        f"{label}: {getattr(hook, '__module__', '?')}"
        for label, hook in _declared_hooks()
        if not str(getattr(hook, "__module__", "")).startswith(allowed)
    ]
    assert not offenders, (
        "hook の実装が call_binding（表）側にある。指標固有 hook は bindings/<compute_id>.py、"
        "汎用の引数渡し規約は kind_invokers が所有すること:\n" + "\n".join(offenders)
    )


def test_g3_detector_actually_inspects_hooks():
    """G3 の自己検定: 検査対象の hook が 1 件以上ある（空集合を通していない）。"""
    labels = [label for label, _hook in _declared_hooks()]
    assert len(labels) >= 5, labels


def _imported_modules(path: Path) -> set[str]:
    """モジュールが import する経路（``from pkg import mod`` の mod も含む）を集める。

    ``from adapter.compute import call_binding`` は ImportFrom の module が ``adapter.compute``
    であり、後方参照の実体は alias 側にある。module だけを見る検査はこの形を見逃す
    （実測で確認した穴）ため、``module.alias`` も経路として数える。
    """
    out: set[str] = set()
    for node in ast.walk(_tree(path)):
        if isinstance(node, ast.Import):
            out |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            out.add(node.module)
            out |= {f"{node.module}.{a.name}" for a in node.names}
    return out


def _collaborator_and_mechanism_sources() -> list[Path]:
    files = [p for p in _BINDINGS_DIR.glob("*.py")]
    files += [Path(m.__file__).resolve() for m in (src_packages, param_binding, kind_invokers)]
    return sorted(files)


def test_g4_collaborators_and_mechanisms_do_not_import_call_binding():
    """G4: 依存は「表 → 協働子・汎用機構」の一方向（逆流すると循環し、移設が骨抜きになる）。

    call_binding へ後方参照（``from adapter.compute import call_binding``）を許すと、協働子は
    表側に置き去りにした定数・関数を参照でき、G1/G2 を満たしたまま実質の同居が続いてしまう。
    """
    offenders = [
        f"{path.relative_to(_COMPUTE_DIR)}: imports {mod}"
        for path in _collaborator_and_mechanism_sources()
        for mod in sorted(_imported_modules(path))
        if mod.endswith("call_binding")
    ]
    assert not offenders, (
        "協働子・汎用機構が call_binding を import している（依存方向は表 → 協働子の一方向）:\n"
        + "\n".join(offenders)
    )


def test_g4_detector_catches_a_back_reference_written_as_from_package_import():
    """G4 の検出力（負の対照）: ``from adapter.compute import call_binding`` 形も挙がる。"""
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        leaky = Path(tmp) / "leaky.py"
        leaky.write_text("from adapter.compute import call_binding\n", encoding="utf-8")
        hits = [m for m in _imported_modules(leaky) if m.endswith("call_binding")]
    assert hits == ["adapter.compute.call_binding"], hits


def test_g4_detector_covers_every_collaborator_file():
    """G4 の自己検定: 走査対象に全協働子と 3 つの汎用機構が入っている。"""
    names = {p.stem for p in _collaborator_and_mechanism_sources()}
    assert {"price_range_power", "tgp_btlm", "profit_band", "moving_averages", "tickvol"} <= names
    assert {"src_packages", "param_binding", "kind_invokers"} <= names
