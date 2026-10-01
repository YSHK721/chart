"""共有 fixture の書き手ヘルパが価格基準を注入しないことを AST で固定する（ISSUE-511 段階 3 の段階 6）。

なぜ在るか（実測に基づく欠陥・2026-09-17）:
    ``marketdata.tests.spread_series_fixture.run_writer`` は、かつて全呼出へ無条件に
    price_basis="bid" を渡していた。一方 :mod:`marketdata.tick_m1` は登録済み ref への
    point 明示と基準明示の**どちらも** ValueError で拒否し、両方の文面が「台帳に登録済みです」で
    始まる。しかも書き手は基準を先に解決する（``marketdata.tick_m1.build_m1_from_ticks`` /
    ``marketdata.tick_m1.append_m1_from_ticks`` とも ``marketdata.tick_m1._resolved_basis`` →
    ``marketdata.tick_m1._checked_series`` の順）。よって注入が戻ると、point の明示拒否を測る
    6 検定（``marketdata/tests/test_tick_m1_spread_schema_guard.py`` の
    test_an_explicit_point_for_a_registered_ref_is_refused_before_any_io）は**基準**の拒否を
    掴んで緑のまま通り、point 拒否を撤去しても落ちなくなる（＝主張の空洞化）。
    注入が「point 付き呼出にだけ」戻る形（半分だけ旧）でも同じことが起きる。

本検定が固定するもの:
    走査した ``marketdata/tests`` の中の ``_HELPER`` 定義が、その引数と本体（docstring は除く）で
    ``price_basis`` に一切触れないこと。基準が要る呼出は呼出側が kw で渡す（唯一源は呼出側）。
    CX-A 計算量: ファイルの parse は「``_HELPER`` を含むファイル」の数だけで決まり、
    発行 − 使用 = 0（parse して捨てない・無関係ファイルは parse しない）。規模 2 点で固定する。

本検定が固定しないもの（射程の明示）:
    - 他のヘルパ・他ディレクトリの基準注入（走査対象は ``marketdata/tests`` のみ）。
    - 実行時の振る舞い（それは guard 側の 6 検定が測る）。ここで見るのは注入の不在だけである。

構造: Arrange-Act-Assert（AAA）。ファイルは読むだけで、1 バイトも書かない（合成例は tmp_path）。
"""
from __future__ import annotations

import ast
from pathlib import Path
from typing import NamedTuple

#: 走査対象（本ファイルが置かれているディレクトリ＝共有 fixture のある場所）。
_TESTS_DIR = Path(__file__).resolve().parent
#: 注入を探す関数名（build / append を同じ引数で呼ぶ共有の口）。
_HELPER = "run_writer"
#: 唯一源であるべき語（価格基準の引数名）。
_BASIS = "price_basis"


class _Scan(NamedTuple):
    """走査の結果。``inspected``＝AST を歩いたファイル数（＝parse を使った数）。"""

    inspected: int
    definitions: int
    injections: "list[str]"


def _scan(paths: "list[Path]") -> _Scan:
    """``paths`` の ``_HELPER`` 定義を調べ、``_BASIS`` に触れる箇所を返す。

    parse するのは ``_HELPER`` の名前を含むファイルだけである（含まないファイルを parse して
    捨てない）。docstring は本体から除く（``_HELPER`` の説明は基準の語を含むが、注入ではない）。
    """
    inspected = definitions = 0
    injections: "list[str]" = []
    for path in sorted(paths):
        text = path.read_text(encoding="utf-8")
        if _HELPER not in text:
            continue
        tree = ast.parse(text, filename=str(path))
        inspected += 1
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef) or node.name != _HELPER:
                continue
            definitions += 1
            injections.extend(_basis_mentions(node, path))
    return _Scan(inspected, definitions, injections)


def _basis_mentions(func: ast.FunctionDef, path: Path) -> "list[str]":
    """``func`` の引数と本体（docstring を除く）で ``_BASIS`` に触れる箇所を列挙する。"""
    found: "list[str]" = []
    for arg in [*func.args.args, *func.args.kwonlyargs]:
        if arg.arg == _BASIS:
            found.append(f"{path.name}:{arg.lineno}: 引数 {_BASIS}")
    body = func.body[1:] if ast.get_docstring(func) is not None else func.body
    for stmt in body:
        for node in ast.walk(stmt):
            what = _mention_of(node)
            if what is not None:
                found.append(f"{path.name}:{node.lineno}: {what}")
    return found


def _mention_of(node: ast.AST) -> "str | None":
    """``node`` が ``_BASIS`` に触れていれば、その触れ方を表す語を返す。"""
    if isinstance(node, ast.keyword) and node.arg == _BASIS:
        return f"キーワード引数 {_BASIS}="
    if isinstance(node, ast.Constant) and node.value == _BASIS:
        return f"文字列キー {_BASIS!r}"
    if isinstance(node, ast.Name) and node.id == _BASIS:
        return f"名前 {_BASIS}"
    return None


def _py_files(directory: Path) -> "list[Path]":
    """``directory`` 直下の Python ファイル（再帰しない＝走査範囲を一意にする）。"""
    return sorted(directory.glob("*.py"))


def _write(directory: Path, name: str, source: str) -> Path:
    """合成例を ``directory`` へ置く（変異と誤検出を測るための材料）。"""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text(source, encoding="utf-8")
    return path


#: 現行と同じ形（基準を渡さない。docstring だけが基準の語を含む）。
_CLEAN = '''
def run_writer(entry, ref, data_dir, start, end, **kw):
    """価格基準は渡さない（price_basis が要る呼出は kw で渡す）。"""
    return entry(start, end, ref=ref, data_dir=data_dir, **kw)
'''

#: 旧（全呼出へ無条件に注入）。
_OLD = '''
def run_writer(entry, ref, data_dir, start, end, **kw):
    """基準は bid。"""
    return entry(start, end, ref=ref, data_dir=data_dir, price_basis="bid", **kw)
'''

#: 半分だけ旧（point 付き呼出にだけ注入）。文字列キー経由なのでキーワード引数では現れない。
_HALF_OLD = '''
def run_writer(entry, ref, data_dir, start, end, **kw):
    """point を渡す呼出にだけ基準を添える。"""
    if "point" in kw:
        kw = {**kw, "price_basis": "bid"}
    return entry(start, end, ref=ref, data_dir=data_dir, **kw)
'''


# =====================================================================
# 注入の不在（現物の走査）
# =====================================================================
def test_the_shared_writer_helper_injects_no_basis():
    """``marketdata/tests`` の ``_HELPER`` は価格基準を注入しない（唯一源は呼出側）。"""
    # Arrange / Act
    scan = _scan(_py_files(_TESTS_DIR))

    # Assert
    assert scan.definitions == 1, (
        f"{_HELPER} の定義が {scan.definitions} 件あります（走査: {_TESTS_DIR}）。"
        " 2 件以上あると、どちらが唯一源かを検定が答えられません（複製の再発）。"
    )
    assert scan.injections == [], (
        f"{_HELPER} が {_BASIS} を注入しています: {scan.injections}。"
        " 登録済み ref では基準の拒否が point の拒否より先に上がるため、point の明示拒否を測る"
        " 検定が基準の拒否を掴んで緑のまま空洞化します。"
    )


# =====================================================================
# 検出力と誤検出（合成例で測る）
# =====================================================================
def test_the_scan_catches_a_helper_that_injects_a_basis(tmp_path):
    """変異: 旧（無条件注入）と半分だけ旧（point 付きにだけ注入）の両方を検出する。"""
    # Arrange
    _write(tmp_path, "old_helper.py", _OLD)
    _write(tmp_path, "half_old_helper.py", _HALF_OLD)

    # Act
    scan = _scan(_py_files(tmp_path))

    # Assert
    assert scan.definitions == 2  # 空振り防止（両方の定義を見ている）
    caught = {line.split(":")[0] for line in scan.injections}
    assert caught == {"old_helper.py", "half_old_helper.py"}, scan.injections


def test_the_scan_does_not_flag_a_helper_that_only_documents_the_basis(tmp_path):
    """誤検出 0: 現行と同じ形（docstring だけが基準の語を含む）は検出しない。"""
    # Arrange
    _write(tmp_path, "clean_helper.py", _CLEAN)

    # Act
    scan = _scan(_py_files(tmp_path))

    # Assert
    assert scan.definitions == 1  # 空振り防止（定義を見た上で 0 件）
    assert scan.injections == []


# =====================================================================
# CX-A 計算量（発行 − 使用 = 0・規模 2 点・回数は焼き込まない）
# =====================================================================
def _parses(monkeypatch) -> "list[str]":
    """``ast.parse`` を包み、発行ごとに対象ファイル名を記録する Test Spy。"""
    real = ast.parse
    issued: "list[str]" = []

    def recorded(source, filename="<unknown>", *args, **kwargs):
        issued.append(str(filename))
        return real(source, filename, *args, **kwargs)

    monkeypatch.setattr(ast, "parse", recorded)
    return issued


def _scan_with_noise(monkeypatch, tmp_path: Path, n_noise: int) -> "tuple[int, _Scan]":
    """``_HELPER`` を持つ 1 ファイルと、持たない ``n_noise`` ファイルを走査する。"""
    directory = tmp_path / f"noise{n_noise}"
    _write(directory, "clean_helper.py", _CLEAN)
    for k in range(n_noise):
        _write(directory, f"noise{k}.py", "def unrelated(x):\n    return x\n")
    issued = _parses(monkeypatch)
    scan = _scan(_py_files(directory))
    return len(issued), scan


def test_cxa_every_parse_is_used_and_the_count_does_not_grow_with_unrelated_files(
    tmp_path, monkeypatch
):
    """CX-A: parse の発行 − 使用 = 0 で、無関係ファイルを 1 → 4 に増やしても発行は増えない。

    parse してから「``_HELPER`` が無い」と捨てる実装だと、走査対象が増えるほど発行が増える
    （結果は同じなので状態検証では落ちない）。固定するのは回数そのものではなく無駄の不在である。
    """
    # Arrange / Act
    small_issued, small = _scan_with_noise(monkeypatch, tmp_path, 1)
    large_issued, large = _scan_with_noise(monkeypatch, tmp_path, 4)

    # Assert
    assert small.definitions == large.definitions == 1  # 空振り防止（どちらも定義を見た）
    assert small_issued - small.inspected == 0, f"発行 {small_issued} / 使用 {small.inspected}"
    assert large_issued - large.inspected == 0, f"発行 {large_issued} / 使用 {large.inspected}"
    assert small_issued == large_issued, (
        f"無関係ファイルを 1 → 4 に増やしたら parse が {small_issued} → {large_issued} へ"
        " 増えました（走査対象の数だけ parse しています）。"
    )
