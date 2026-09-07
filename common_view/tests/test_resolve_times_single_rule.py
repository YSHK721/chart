"""時刻解決規則の単一実装ガード（ISSUE-502 段階 2 D-7）。

固定するのは「``明示指定 > time 列 > date 列 > DatetimeIndex`` という規則の実装が、指標
パッケージ側に 1 件も無い」ことである。ISSUE-179 の 1 本化は未完で、指標 3 パッケージ
（profit_hl_band / profit_hlband / moving_averages）が自前実装を保持していた。

  R1 OCP : 指標パッケージ（indigators 配下）に自前実装が 0 件。
  R2 OCP : 実装の所在が共有層 2 件に固定されている（新たな所有者を無音で増やさせない）。
  R3 自己検定: 検出器が正典実装を捕まえている（空振りでない）。
  R4 契約: 委譲した 3 パッケージが共有実装と同一オブジェクトを束縛している。
  R5 計算量: 発行した時刻変換 − 出力に使った時刻変換 = 0（入力長に依らず 1 回）。

**共有層 2 件は到達点ではない**。marketdata.time_column.resolve_times（c.lower() 版）と
common_view.lwc_adapter.resolve_times（str(c).lower() 版）は同一規則の 2 所有者であり、
一本化には marketdata 側の改変が要る＝ISSUE-502 段階 2 の作業範囲外（未収束・別途裁定）。
本検定は「2 件から増えない」ことだけを機械的に保証する。

様式は indigators/indicator_ui/api/tests/test_call_binding_open_closed.py の
nice_step 単一実装検定を踏襲する。
"""

from __future__ import annotations

import ast
from pathlib import Path

import pandas as pd

_REPO_ROOT = Path(__file__).resolve().parents[2]

_EXCLUDED_PARTS = {
    ".venv", "venv", "node_modules", "__pycache__", ".git", "out", "site-packages",
    ".claude", "lightweight-charts-python-main",
}

#: 規則の指紋（解決不能時の文言）。関数の外に置くので本ファイル自身は検出されない
#: （検出器は FunctionDef のみを走査する）。
_UNRESOLVABLE_MSG = "時刻を解決できません（time/date 列、または DatetimeIndex が必要）。"

#: R2 の許可リスト。同一規則を実装してよい共有層の所在。
_SHARED_OWNERS = ("common_view/lwc_adapter.py", "marketdata/time_column.py")


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


def _resolve_times_implementations() -> list[str]:
    """時刻解決規則を自前で実装している関数を列挙する。"""
    sites: list[str] = []
    for path in _python_sources():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):  # pragma: no cover - 壊れた木は対象外
            continue
        rel = path.relative_to(_REPO_ROOT).as_posix()
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            literals = {
                n.value for n in ast.walk(node)
                if isinstance(n, ast.Constant) and isinstance(n.value, str)
            }
            if _UNRESOLVABLE_MSG in literals:
                sites.append(f"{rel}:{node.lineno}:{node.name}")
    return sorted(sites)


# --------------------------------------------------------------------------- #
# R1: 指標パッケージに自前実装が 0 件
# --------------------------------------------------------------------------- #
def test_no_indicator_package_implements_the_time_resolution_rule():
    offenders = [s for s in _resolve_times_implementations() if s.startswith("indigators/")]
    assert not offenders, (
        "指標パッケージが時刻解決規則を自前実装している"
        "（common_view.lwc_adapter.resolve_times へ委譲せよ）:\n" + "\n".join(offenders)
    )


# --------------------------------------------------------------------------- #
# R2 / R3: 所在は共有層 2 件に固定（増加の禁止・空振りでないことの自己検定）
# --------------------------------------------------------------------------- #
def test_the_rule_is_owned_only_by_the_declared_shared_modules():
    offenders = [
        s for s in _resolve_times_implementations()
        if not any(owner in s for owner in _SHARED_OWNERS)
    ]
    assert not offenders, (
        "時刻解決規則の所有者が共有層の外に増えている:\n" + "\n".join(offenders)
    )


def test_shared_owner_count_does_not_grow():
    """2 件は未収束の到達点。増えたら Red（減るぶんには本検定は通す）。"""
    sites = _resolve_times_implementations()
    assert len(sites) <= len(_SHARED_OWNERS), (
        "同一規則の所有者が増えた（marketdata 側との一本化が未了なのは既知・増加は不可）:\n"
        + "\n".join(sites)
    )


def test_detector_finds_the_canonical_shared_implementation():
    """検出器の自己検定: 正典実装そのものを検出できている（空振りでない）。"""
    sites = _resolve_times_implementations()
    assert any("common_view/lwc_adapter.py" in s for s in sites), sites


# --------------------------------------------------------------------------- #
# R4: 委譲済みパッケージは共有実装を import で束縛している
#     （静的検査で行う。indigators の src は同名パッケージ衝突を避けるため sys.path 操作を
#      伴う動的ロードが要り、それはテストとプロダクトのモジュール同一性を崩すため採らない）
# --------------------------------------------------------------------------- #
#: 本 Wave（ISSUE-502 段階 2 D-7）で自前実装を撤去し共有版へ委譲したパッケージ。
_MIGRATED = ("profit_hl_band", "profit_hlband", "moving_averages")


def _binds_shared_resolve_times(package: str) -> bool:
    path = _REPO_ROOT / "indigators" / package / "src" / "lwc_chart.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return any(
        isinstance(n, ast.ImportFrom)
        and n.module == "common_view.lwc_adapter"
        and any(a.name == "resolve_times" and a.asname == "_resolve_times" for a in n.names)
        for n in ast.walk(tree)
    )


def test_migrated_packages_bind_the_shared_implementation():
    offenders = [pkg for pkg in _MIGRATED if not _binds_shared_resolve_times(pkg)]
    assert not offenders, f"共有実装を束縛していないパッケージ: {offenders}"


# --------------------------------------------------------------------------- #
# R5: 計算量テスト（絶対命令）
# --------------------------------------------------------------------------- #
def _frame(rows: int) -> pd.DataFrame:
    return pd.DataFrame({
        "Time": pd.date_range("2024-01-01", periods=rows, freq="1min"),
        "Close": range(rows),
    })


def test_time_conversion_is_issued_once_regardless_of_input_length(monkeypatch):
    """発行した時刻変換 − 出力に使った時刻変換 = 0、かつ入力長に依らず不変（2 点固定）。

    識別力: 解決が「探索のたびに to_datetime する」実装へ退化すると Red になる。
    """
    from common_view import lwc_adapter

    calls: list[int] = []
    real = pd.to_datetime

    def _spy(arg, *args, **kwargs):
        calls.append(len(arg))
        return real(arg, *args, **kwargs)

    monkeypatch.setattr(lwc_adapter.pd, "to_datetime", _spy)

    lwc_adapter.resolve_times(_frame(8), None)
    issued_short = len(calls)
    calls.clear()
    lwc_adapter.resolve_times(_frame(2048), None)
    issued_long = len(calls)

    assert issued_short == 1
    assert issued_long == issued_short
