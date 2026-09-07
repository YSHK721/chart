"""8 択ソース解決手続きの単一実装ガードと計算量検定（ISSUE-502 段階 2 D-6）。

固定するのは「UI のソース値（close/open/high/low/hl2/hlc3/ohlc4/hlcc4）を表から価格系列へ
解決する**手続き**が repo に 1 つしかない」ことである。写像表 SOURCE_TO_APPLIED は
ISSUE-179 で共有済みだったが、手続き（列名の小文字照合・欠落時の例外・抽出順）は共有されて
おらず、btlm_trail / ma_marod / moving_averages の 3 箇所へ AST 一致の逐語複製が残っていた。

  R1 SRP/OCP : 解決手続きの実装が repo に 1 件だけ（第 2 実装＝取り残しの温床）。
  R2 自己検定: 検出器が正典実装そのものを捕まえている（空振りでない）。
  R3 OCP     : 表引きで解決する関数の所在が固定されている（新たな写しを無音で増やさせない）。
  R4 計算量  : 発行した列材料化 − 出力に使った列 = 0（重複抽出の不在）。
  R5 計算量  : 入力長を変えても発行回数が変わらない（オーダーの表明・2 点固定）。

様式は indigators/indicator_ui/api/tests/test_call_binding_open_closed.py の
nice_step 単一実装検定（AST 走査・offender を file:line で提示）を踏襲する。
"""

from __future__ import annotations

import ast
from pathlib import Path

import numpy as np
import pytest

from common.applied_price import (
    OHLC_COLUMNS,
    SOURCE_TO_APPLIED,
    SYNTHETIC_SOURCE_TO_APPLIED,
    resolve_source_prices,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]

#: 走査から外す木（第三者コード・生成物・仮想環境・使い捨てスパイク）。
_EXCLUDED_PARTS = {
    ".venv", "venv", "node_modules", "__pycache__", ".git", "out", "site-packages",
    ".claude", "lightweight-charts-python-main",
}

#: 解決手続きの指紋。両方の文言を持つ関数＝「未知ソースの拒否」と「必須列の抽出」を
#: 自前で行っている関数。関数の外（モジュール直下の定数）に置くので、本ファイル自身は
#: 検出器に引っかからない（検出器は FunctionDef のみを走査する）。
_UNKNOWN_SOURCE_MSG = "未知のソースです: "
_MISSING_COLUMN_MSG = "ソース計算に必要な列がありません: "

#: R3 の許可リスト。表引き＋合成を関数内で行う実装の所在（相対パス片）。
#: - common/applied_price.py           : 正典（本件で 1 本化した手続き）
#: - incremental/moving_averages.py    : 増分器 _prepare。ISSUE-502 段階 2 の実測で新たに
#:   検出した 5 件目だが、契約が別物（列欠落・未知ソースを例外でなく ``return None`` で
#:   参照実装経路へ落とす早期脱出）。委譲の可否は別途裁定するため本 Wave では改変しない。
_TABLE_LOOKUP_ALLOWED = (
    "common/applied_price.py",
    "adapter/compute/incremental/moving_averages.py",
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


def _functions() -> list[tuple[str, ast.FunctionDef | ast.AsyncFunctionDef]]:
    out = []
    for path in _python_sources():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):  # pragma: no cover - 壊れた木は走査対象外
            continue
        rel = path.relative_to(_REPO_ROOT).as_posix()
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                out.append((rel, node))
    return out


def _string_constants(fn: ast.AST) -> set[str]:
    return {
        n.value for n in ast.walk(fn)
        if isinstance(n, ast.Constant) and isinstance(n.value, str)
    }


def _called_names(fn: ast.AST) -> set[str]:
    out: set[str] = set()
    for n in ast.walk(fn):
        if isinstance(n, ast.Call):
            f = n.func
            out.add(f.id if isinstance(f, ast.Name) else getattr(f, "attr", ""))
    return out


def _loaded_names(fn: ast.AST) -> set[str]:
    return {n.id for n in ast.walk(fn) if isinstance(n, ast.Name)}


def _resolution_implementations() -> list[str]:
    """解決手続きの実装（未知ソース拒否＋必須列抽出を自前で持つ関数）を列挙する。"""
    sites = [
        f"{rel}:{fn.lineno}:{fn.name}"
        for rel, fn in _functions()
        if {_UNKNOWN_SOURCE_MSG, _MISSING_COLUMN_MSG} <= _string_constants(fn)
    ]
    return sorted(sites)


def _table_lookup_sites() -> list[str]:
    """8 択表を関数内で引いて合成価格を作る関数を列挙する。"""
    sites = [
        f"{rel}:{fn.lineno}:{fn.name}"
        for rel, fn in _functions()
        if "SOURCE_TO_APPLIED" in _loaded_names(fn) and "applied_price" in _called_names(fn)
    ]
    return sorted(sites)


# --------------------------------------------------------------------------- #
# R1 / R2: 解決手続きの実装は repo に 1 件（逐語複製の禁止）
# --------------------------------------------------------------------------- #
def test_source_resolution_procedure_is_implemented_exactly_once_in_repo():
    sites = _resolution_implementations()
    assert len(sites) == 1, (
        "8 択ソース解決手続きの実装が複数ある（逐語複製は必ず取り残しを生む・"
        "common.applied_price.resolve_source_prices へ委譲せよ）:\n" + "\n".join(sites)
    )


def test_source_resolution_detector_finds_the_canonical_implementation():
    """検出器の自己検定: 正典実装そのものを検出できている（空振りでない）。"""
    sites = _resolution_implementations()
    assert any("common/applied_price.py" in s for s in sites), sites


# --------------------------------------------------------------------------- #
# R3: 表引き＋合成を行う関数の所在は固定（新たな写しを無音で増やさせない）
# --------------------------------------------------------------------------- #
def test_table_lookup_sites_are_pinned_to_the_declared_allowlist():
    offenders = [
        s for s in _table_lookup_sites()
        if not any(allowed in s for allowed in _TABLE_LOOKUP_ALLOWED)
    ]
    assert not offenders, (
        "8 択表を自前で引いて合成価格を作る関数が許可外の場所にある"
        "（common.applied_price.resolve_source_prices へ委譲せよ）:\n" + "\n".join(offenders)
    )


def test_delegating_packages_do_not_look_up_the_table_themselves():
    """委譲済み 3 パッケージが表引きへ戻っていない（本件の是正が生きている）。"""
    regressed = [
        s for s in _table_lookup_sites()
        if "/btlm_trail/" in s or "/ma_marod/" in s or "/moving_averages/src/" in s
    ]
    assert not regressed, ("委譲済みパッケージに表引きが復活している:\n" + "\n".join(regressed))


# --------------------------------------------------------------------------- #
# 語彙の単一ソース（部分写しの禁止）
# --------------------------------------------------------------------------- #
def test_synthetic_source_vocabulary_is_derived_from_the_single_table():
    """合成 source の語彙は 8 択表からの導出であり、独立した列挙ではない。"""
    derived = {s: k for s, k in SOURCE_TO_APPLIED.items() if s not in OHLC_COLUMNS}
    assert SYNTHETIC_SOURCE_TO_APPLIED == derived
    assert set(SYNTHETIC_SOURCE_TO_APPLIED) == {"hl2", "hlc3", "hlcc4", "ohlc4"}


def test_ohlc_columns_declares_the_four_required_columns_in_argument_order():
    assert OHLC_COLUMNS == ("open", "high", "low", "close")


# --------------------------------------------------------------------------- #
# 契約（移設元と同一であることの固定）
# --------------------------------------------------------------------------- #
class _CountingFrame:
    """列材料化の発行回数を数える Test Spy（``columns`` と ``__getitem__`` だけを持つ）。"""

    def __init__(self, data: dict[str, np.ndarray]) -> None:
        self._data = data
        self.columns = list(data)
        self.materialized: list[str] = []

    def __getitem__(self, key: str) -> "_CountingColumn":
        return _CountingColumn(self, key)


class _CountingColumn:
    def __init__(self, frame: _CountingFrame, key: str) -> None:
        self._frame = frame
        self._key = key

    def to_numpy(self, dtype=None) -> np.ndarray:
        self._frame.materialized.append(self._key)
        return np.asarray(self._frame._data[self._key], dtype=dtype)


def _frame(rows: int) -> _CountingFrame:
    step = np.arange(rows, dtype=float)
    return _CountingFrame({
        "Open": 100.0 + step, "High": 102.0 + step,
        "Low": 98.0 + step, "Close": 101.0 + step,
    })


def test_resolve_source_prices_is_case_insensitive_on_source_and_columns():
    values = resolve_source_prices(_frame(3), "HL2")
    np.testing.assert_allclose(values, np.array([100.0, 101.0, 102.0]))


def test_resolve_source_prices_rejects_unknown_source_with_value_error():
    with pytest.raises(ValueError, match="未知のソースです: vwap"):
        resolve_source_prices(_frame(3), "vwap")


def test_resolve_source_prices_requires_every_ohlc_column_regardless_of_source():
    """契約: ソース種別によらず 4 列すべての存在を要求する（移設元と同一）。"""
    frame = _CountingFrame({"Close": np.zeros(3)})
    with pytest.raises(ValueError, match="ソース計算に必要な列がありません: open"):
        resolve_source_prices(frame, "close")


# --------------------------------------------------------------------------- #
# R4 / R5: 計算量テスト（絶対命令）
# --------------------------------------------------------------------------- #
def test_each_required_column_is_materialized_exactly_once():
    """発行した列材料化 − 出力に使った列 = 0（重複抽出の不在）。

    識別力: 委譲が「共有手続きを呼びつつ自前でも抽出する」実装へ退化すると Red になる。
    期待値に回数リテラルを焼き込まず、宣言 OHLC_COLUMNS から導く。
    """
    frame = _frame(64)
    resolve_source_prices(frame, "ohlc4")
    assert sorted(frame.materialized) == sorted(OHLC_COLUMNS_TITLED)
    assert len(frame.materialized) == len(set(frame.materialized))


def test_materialization_count_does_not_grow_with_input_length():
    """オーダーの表明: 入力長を変えても発行回数は不変（2 点固定）。"""
    short, long = _frame(16), _frame(4096)
    resolve_source_prices(short, "hlc3")
    resolve_source_prices(long, "hlc3")
    assert len(short.materialized) == len(long.materialized) == len(OHLC_COLUMNS)


def test_applied_price_is_issued_once_per_resolution(monkeypatch):
    """発行した合成計算 − 出力に使った合成計算 = 0（委譲で増えていない）。

    実装モジュールは sys.modules から取る。common パッケージは公開名 applied_price と同名の
    サブモジュールの衝突ガードを持ち、属性経由ではモジュールではなく関数が返るため
    （common/__init__.py の _CommonPackage が明示）。
    """
    import sys

    import common.applied_price  # noqa: F401  # sys.modules への登録を確定させる

    ap = sys.modules["common.applied_price"]

    calls: list[int] = []
    real = ap.applied_price

    def _spy(kind, *args, **kwargs):
        calls.append(int(kind))
        return real(kind, *args, **kwargs)

    monkeypatch.setattr(ap, "applied_price", _spy)
    ap.resolve_source_prices(_frame(32), "hlcc4")
    assert calls == [int(SOURCE_TO_APPLIED["hlcc4"])]


#: Spy が観測する列名は入力表の綴り（大文字始まり）。宣言との対応を明示する。
OHLC_COLUMNS_TITLED = tuple(c.title() for c in OHLC_COLUMNS)
