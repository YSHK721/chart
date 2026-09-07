"""分位系列名の綴り規則の単一実装ガード（ISSUE-502 段階 2 D-12）。

固定するのは「分位 q を百分率の整数へ丸めて系列名を綴る規則の実装が、指標パッケージの
表示アダプタ（src/lwc_chart.py）に 1 件も無い」ことである。btlm_trail / btlm_trail_marod /
ma_marod / tickvol の 4 パッケージが同一式を prefix 違いで逐語複製していた。

  R1 OCP : 表示アダプタに自前実装が 0 件（prefix は引数、規則は共有側）。
  R2 OCP : 実装の所在が許可リストに固定されている（新たな写しを無音で増やさせない）。
  R3 自己検定: 検出器が正典実装を捕まえている（空振りでない）。
  R4 契約: 委譲済み 4 パッケージの出力が prefix 束縛のみで説明できる。
  R5 丸め挙動: 移設元と同一（台帳が主張する q=0.995/0.99 の同名衝突は実測で棄却。
     99.5 は偶数丸めで 100 になるため別名であり、実使用分位に衝突は無い）。

様式は indigators/indicator_ui/api/tests/test_call_binding_open_closed.py の
nice_step 単一実装検定を踏襲する。
"""

from __future__ import annotations

import ast
from pathlib import Path

from common_view.lwc_adapter import quantile_series_name

_REPO_ROOT = Path(__file__).resolve().parents[2]

_EXCLUDED_PARTS = {
    ".venv", "venv", "node_modules", "__pycache__", ".git", "out", "site-packages",
    ".claude", "lightweight-charts-python-main",
}

#: R2 の許可リスト。同一式を持ってよい所在（相対パス片）。
#: - common_view/lwc_adapter.py : 正典（本件で 1 本化した綴り規則）
#: - tgp_btlm/src/core.py       : 成果物**列**名 btlm_q{pct}。系列名ではなく計算成果物の
#:   列名で、参照実装の出力契約に属する。tgp_btlm は本 Wave の書込範囲外。
#: - profit_rsi/src/rsi.py      : 同上（正常帯の列名 rsi_q{pct}・float() 明示キャスト付き）。
#:   profit_rsi も本 Wave の書込範囲外。
_ALLOWED_SITES = (
    "common_view/lwc_adapter.py",
    "indigators/tgp_btlm/src/core.py",
    "indigators/profit_rsi/src/rsi.py",
)

#: 委譲済みパッケージと、その系列名 prefix。
_MIGRATED = (
    ("btlm_trail", "btlm_trail"),
    ("btlm_trail_marod", "btlm_trail_marod"),
    ("ma_marod", "ma_marod"),
    ("tickvol", "tickvol"),
)


def _python_sources() -> list[Path]:
    out: list[Path] = []
    for p in _REPO_ROOT.rglob("*.py"):
        rel = p.relative_to(_REPO_ROOT)
        if _EXCLUDED_PARTS & set(rel.parts):
            continue
        if rel.parts[0].startswith("prototype_"):
            continue
        out.append(p)
    return out


def _has_percent_rounding_name(fn: ast.AST) -> bool:
    """``f"..._q{int(round(<expr> * 100))}"`` 形の綴りを持つか。"""
    for node in ast.walk(fn):
        if not isinstance(node, ast.JoinedStr):
            continue
        parts = [v.value for v in node.values if isinstance(v, ast.Constant)]
        if not any(str(p).endswith("_q") for p in parts):
            continue
        for value in node.values:
            if not isinstance(value, ast.FormattedValue):
                continue
            expr = value.value
            if (
                isinstance(expr, ast.Call)
                and getattr(expr.func, "id", "") == "int"
                and expr.args
                and isinstance(expr.args[0], ast.Call)
                and getattr(expr.args[0].func, "id", "") == "round"
                and expr.args[0].args
                and isinstance(expr.args[0].args[0], ast.BinOp)
                and isinstance(expr.args[0].args[0].op, ast.Mult)
            ):
                return True
    return False


def _naming_implementations() -> list[str]:
    sites: list[str] = []
    for path in _python_sources():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):  # pragma: no cover - 壊れた木は対象外
            continue
        rel = path.relative_to(_REPO_ROOT).as_posix()
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if _has_percent_rounding_name(node):
                    sites.append(f"{rel}:{node.lineno}:{node.name}")
    return sorted(sites)


# --------------------------------------------------------------------------- #
# R1 / R2 / R3
# --------------------------------------------------------------------------- #
def test_no_lwc_chart_adapter_spells_the_quantile_name_itself():
    offenders = [s for s in _naming_implementations() if "/src/lwc_chart.py" in s]
    assert not offenders, (
        "表示アダプタが分位系列名の綴りを自前実装している"
        "（common_view.lwc_adapter.quantile_series_name へ委譲せよ）:\n" + "\n".join(offenders)
    )


def test_naming_rule_sites_are_pinned_to_the_declared_allowlist():
    offenders = [
        s for s in _naming_implementations()
        if not any(allowed in s for allowed in _ALLOWED_SITES)
    ]
    assert not offenders, (
        "分位名の綴り規則が許可外の場所に増えている:\n" + "\n".join(offenders)
    )


def test_detector_finds_the_canonical_implementation():
    """検出器の自己検定: 正典実装そのものを検出できている（空振りでない）。"""
    sites = _naming_implementations()
    assert any("common_view/lwc_adapter.py" in s for s in sites), sites


# --------------------------------------------------------------------------- #
# R4: 委譲済みパッケージは共有規則を import し、prefix を束縛するだけ
#     （静的検査で行う。indigators の src は同名パッケージ衝突を避けるため sys.path 操作を
#      伴う動的ロードが要り、それはテストとプロダクトのモジュール同一性を崩すため採らない）
# --------------------------------------------------------------------------- #
def _lwc_chart_tree(package: str) -> ast.Module:
    path = _REPO_ROOT / "indigators" / package / "src" / "lwc_chart.py"
    return ast.parse(path.read_text(encoding="utf-8"))


def _imports_shared_rule(tree: ast.Module) -> bool:
    return any(
        isinstance(n, ast.ImportFrom)
        and n.module == "common_view.lwc_adapter"
        and any(a.name == "quantile_series_name" for a in n.names)
        for n in ast.walk(tree)
    )


def test_migrated_packages_import_the_shared_rule():
    offenders = [pkg for pkg, _ in _MIGRATED if not _imports_shared_rule(_lwc_chart_tree(pkg))]
    assert not offenders, f"共有規則を import していないパッケージ: {offenders}"


def test_migrated_packages_only_bind_their_prefix():
    """各パッケージの委譲関数の本体が「共有規則を prefix 付きで 1 回呼ぶ」だけである。"""
    offenders = []
    for package, _prefix in _MIGRATED:
        fn = next(
            n for n in ast.walk(_lwc_chart_tree(package))
            if isinstance(n, ast.FunctionDef) and n.name == "_quantile_series_name"
        )
        body = [s for s in fn.body if not isinstance(s, ast.Expr)]
        shape = ast.unparse(body[0]) if len(body) == 1 else f"<{len(body)} 文>"
        offenders.extend(
            [(package, shape)]
            if not (len(body) == 1 and isinstance(body[0], ast.Return)
                    and isinstance(body[0].value, ast.Call)
                    and len(body[0].value.args) == 2)
            else []
        )
    assert not offenders, f"prefix 束縛以外を行っている: {offenders}"


# --------------------------------------------------------------------------- #
# R5: 丸め挙動は移設元のまま（台帳の衝突主張は実測で棄却済み）
# --------------------------------------------------------------------------- #
#: 実使用分位（4 パッケージの既定・catalog の分位ペア）。
_USED_QUANTILES = (0.005, 0.01, 0.05, 0.1, 0.5, 0.9, 0.95, 0.99, 0.995)


def test_no_two_used_quantiles_share_a_series_name():
    """実使用分位に同名衝突は無い。

    台帳 .doc/solid_audit_20260906.md は D-12 で「q=0.995/0.99 の同名衝突」を主張したが、
    99.5 は偶数丸めで 100 になるため q100 / q99 と別名である＝主張は誤り。本検定はその
    実測結果（衝突なし）を固定し、丸めの変更で衝突が生まれたら Red にする。
    """
    names = [quantile_series_name("btlm_trail", q) for q in _USED_QUANTILES]
    assert len(names) == len(set(names)), sorted(names)


def test_rounding_is_bankers_rounding_exactly_as_before_the_move():
    """丸めの境界挙動を移設元のまま固定する（int(round(q * 100)) の同値性）。"""
    assert quantile_series_name("btlm_trail", 0.99) == "btlm_trail_q99"
    assert quantile_series_name("btlm_trail", 0.995) == "btlm_trail_q100"
    assert quantile_series_name("btlm_trail", 0.994) == "btlm_trail_q99"


def test_rule_is_lossy_below_one_percent_resolution():
    """1% 未満の分解能は落ちる（規則の射程を明示。是正は front 契約の変更を伴い範囲外）。"""
    assert quantile_series_name("tickvol", 0.0) == "tickvol_q0"
    assert quantile_series_name("tickvol", 0.004) == "tickvol_q0"
