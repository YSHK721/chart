"""``marketdata.mt5_ticks`` の依存宣言を **検定で強制する**（ISSUE-447 段階 1 / 検定 A-7）。

方式は ``marketdata/tests/test_module_dependency_declarations.py``（ISSUE-262）と同じである。
宣言は施行されているように読めるが、施行する仕組みが無ければ次の編集で静かに破れる。

この規則を ``tools`` 側の検定に置かない理由（設計 §3）:
    ``tools/tests/test_tools_composition_declaration.py`` は ``tools/*.py`` を**非再帰**で走査
    する。パッケージ配下へ潜れば検査を免れる穴があるため、本パッケージの依存規則は
    本パッケージ側で施行する（穴を突かない）。
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

_PKG = Path(__file__).resolve().parents[1] / "mt5_ticks"

#: 同パッケージ配下は 1 つの名前へ丸める（``marketdata.mt5_ticks.port`` 等を区別しない）。
_SELF = "marketdata.mt5_ticks"

#: モジュール → 許可する外部 import（stdlib と ``__future__`` は常に許可）。
#:
#: 値は **各モジュールの docstring および ``__init__.py`` の表と一致していなければならない**。
#: 依存を足すなら docstring と本表を同時に更新する（片方だけの更新を許さない）。
_ALLOWED: "dict[str, set[str]]" = {
    # domain / usecase 境界: **依存ゼロ**。pandas も numpy も持ち込まない。
    # ここが破れると「純粋な規則」を pandas 無しで検定できなくなる。
    "server_clock.py": set(),
    "cursor.py": set(),
    "wire.py": set(),
    "port.py": set(),
    # adapter: 台帳の権威へ委譲する。
    "journal.py": {"pandas", "marketdata.tick_m1", _SELF},
    # ``tools.capture_mt5_symbol_spec`` はパス成分変換の**唯一の実装**であり、
    # 複製を作らないために層の向きを曲げて import する（設計 §4・検定 M-1）。
    "ingest.py": {"pandas", "marketdata.tick_m1", "tools.capture_mt5_symbol_spec", _SELF},
    "m1_chain.py": {"pandas", "marketdata.tick_m1", "marketdata.rollup", _SELF},
    # usecase: 同パッケージのみ。
    "usecases.py": {_SELF},
    # test 支援: 同パッケージのみ（本番経路から import されない）。
    "fakes.py": {_SELF},
}

_STDLIB_PREFIXES = {
    "__future__", "typing", "pathlib", "datetime", "os", "sys", "re", "json", "csv",
    "time", "math", "logging", "tempfile", "collections", "dataclasses", "functools",
    "itertools", "hashlib", "hmac", "struct", "secrets", "base64", "binascii",
    "urllib", "http", "socketserver", "socket", "argparse", "contextlib", "abc",
    "enum", "threading", "queue", "shutil", "glob", "zlib", "traceback", "uuid",
}


def _external_imports(path: Path) -> "set[str]":
    """モジュール内の全 import（関数内の遅延 import を含む）から外部依存名を集める。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out: "set[str]" = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                out.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level:            # 相対 import は自パッケージ内＝対象外
                continue
            module = node.module or ""
            if module == "marketdata":
                # `from marketdata import X` は marketdata.X として数える（粒度を揃える）。
                for alias in node.names:
                    out.add(f"marketdata.{alias.name}")
            else:
                out.add(module)
    normalized = {
        _SELF if n == _SELF or n.startswith(_SELF + ".") else n
        for n in out
    }
    return {n for n in normalized if n.split(".")[0] not in _STDLIB_PREFIXES}


def test_the_declaration_table_covers_every_module_in_the_package():
    """新しいモジュールを足したら本表も足す（宣言の外側を作らない）。"""
    actual = {p.name for p in _PKG.glob("*.py") if p.name != "__init__.py"}
    assert actual == set(_ALLOWED), (
        f"宣言表とモジュール構成が食い違っています。表にない: {sorted(actual - set(_ALLOWED))}"
        f" / 実体がない: {sorted(set(_ALLOWED) - actual)}"
    )


@pytest.mark.parametrize("filename", sorted(_ALLOWED))
def test_module_imports_match_the_declared_dependency_set(filename):
    """A-7: 実 import が宣言（本表）を超えていない。"""
    got = _external_imports(_PKG / filename)
    extra = got - _ALLOWED[filename]
    assert not extra, (
        f"{filename} が宣言外の依存を持っています: {sorted(extra)}。"
        " docstring の依存宣言と本表を同時に更新するか、import を撤去してください。"
    )


@pytest.mark.parametrize("filename", sorted(_ALLOWED))
def test_declared_dependency_set_has_no_stale_entries(filename):
    """本表に、実際には使われていない許可エントリが残っていない（宣言の陳腐化を防ぐ）。"""
    got = _external_imports(_PKG / filename)
    stale = _ALLOWED[filename] - got
    assert not stale, (
        f"{filename} の許可表に未使用のエントリが残っています: {sorted(stale)}。"
        " 依存が消えたら宣言側も狭めてください。"
    )


@pytest.mark.parametrize("filename", ["server_clock.py", "cursor.py", "wire.py", "port.py"])
def test_the_pure_layer_never_reaches_for_pandas_or_numpy(filename):
    """純層に重い依存を持ち込まない（規則だけを pandas 無しで検定できる状態を保つ）。"""
    got = _external_imports(_PKG / filename)
    assert not (got & {"pandas", "numpy"}), f"{filename} が {sorted(got)} を import しています。"


def test_only_ingest_reaches_into_tools():
    """``tools`` への逆向き依存は sanitize 1 点に閉じる（無制限に広げない）。"""
    offenders = {
        name for name in _ALLOWED
        if any(dep.startswith("tools") for dep in _external_imports(_PKG / name))
    }
    assert offenders == {"ingest.py"}, (
        f"``tools`` へ依存してよいのは ingest.py だけです: {sorted(offenders)}"
    )
