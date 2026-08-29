"""検出ゲート: 銘柄仕様が**期待値リテラル**として書き写された箇所を検出する。

由来: ISSUE-445 RC-1（人が書いた値が権威のように振る舞う）。姉妹ゲート
``test_jp225_spec_literals_in_tests.py`` は「JP225 を名乗る**組み立て**の Call kwargs」を
走査するが、**期待値側を見ない**。段階 C（2026-08-26）でこの穴が実際に刺さった——
``test_ma_slope_normalize_lot.py`` は別ファイルの kwargs ビルダ ``_mt5_kwargs`` を import して
使い、その戻り値に含まれる ``volume_min=0.1`` 等を **assert の期待値として書き写して**いた。
組み立て側を供給元参照へ是正した瞬間に赤になった。当該ファイルは段階 A の台帳にも段階 B の
3 分類表にも現れていない——**ゲートの射程外だったからである**。本ファイルはその射程を埋める。

## 判定の定義（何をもって「写し」とみなすか）

**値ではなく形で判定する。** 供給元の値と一致するかは見ない（``1.0`` / ``0.1`` /
``1`` はあらゆる文脈に現れるため、値一致では判定できない——実測: tracked 全 ``*.py`` の
``assert`` 内で供給元 8 値のいずれかと一致する数値リテラルは **1611 件 / 345 ファイル**。
これを赤にするゲートは無価値である）。写しの本質は「供給元から来た値を、人が書いた数値と
突き合わせていること」であり、それは**綴りの形**で判る。

ある数値リテラルを「銘柄仕様の期待値の写し」と判定するのは、次を**すべて**満たすとき:

    1. **母集団**: そのファイルが供給元に触れている——(A) 自分で ``load_spec_fields`` /
       ``spec_fields`` を呼ぶ関数を持つ、または (B) そういう関数を**他ファイルから
       import している**（実例が (B)）。:func:`_supplier_touching_sources` が同定する。
    2. **位置**: そのリテラルが ``assert`` 文の中にある（＝期待値側）。
    3. **相手**: 同じ ``assert`` の中で、銘柄仕様のフィールドの**読み出し**
       （``x.volume_min`` / ``x["volume_min"]``）と突き合わされている。まとめ書き
       （``assert cfg == {"volume_min": 0.1}``）も同じ写しとして扱う。
       ``pytest.approx(...)`` は剥がす。
    4. **往復でない**: 同じ ``def`` の中の ``assert`` の**外**に同じ値が現れない。
       現れるならその数値はテスト自身が注入した入力であり（``fake[...] = 12.5`` を読み戻す／
       CLI 引数 ``["--contract-size", "3.0"]`` を読み戻す）、供給元が変わっても陳腐化しない。

## 射程（実測 2026-08-27・何が検出でき、何ができないか）

検出できる（母集団 **32 ファイル** / tracked ``*.py`` **1311 件**）:

    * 実例（``99af6f0`` の是正前の形）を食わせて **3 件検出**。是正後の同ファイルは **0 件**。
    * 現行ツリーの母集団内ヒットは **3 ファイル 3 件**——うち 1 件が本ゲートが新たに
      見つけた違反（``_KNOWN``）、2 件は意図的な仕掛け線（``_EXCLUDED_BY_INTENT``）。
      偽陽性 0 件（3 件すべて人手で分類し、根拠を各エントリに書いた）。
    * 母集団の絞り込みが消している偽陽性は **6 件 / 5 ファイル**（``test_usecase_models.py``
      の合成 EURUSD 相当ほか）。往復除外が消している偽陽性は **2 件**。

**検出できない（＝この穴は完全には塞がっていない）**:

    * 期待値を**中間の名前**に置いた形（``_EXPECTED_MIN = 0.1`` … ``== _EXPECTED_MIN``）。
      比較の相手が ``Name`` でありリテラルではないため掛からない。
    * 期待値に**演算**が入る形（``== 0.1 * 2``）。
    * ``assert`` の外に置いた期待値（``@pytest.mark.parametrize`` の expected 列など）。
    * 銘柄仕様の読み出しと**同じ式に現れない**写し（別の変数へ移してから比較する形）。
    * ``.py`` 以外（JSON fixture 等）。姉妹ゲートが別入口で 1 件だけ見ている。

これらは「値の流れを追う」実装（データフロー解析）を要し、AST の局所形では判らない。
**塞げていない範囲があることを承知の上で、実例が属する形は塞いだ**——という状態である。

## 姉妹ゲートと分けた理由

判定・走査母集団・台帳のすべてが別である（あちらは「JP225 を名乗るか」＋「供給元と値が
食い違うか」、こちらは「供給元に触れるか」＋「期待値の綴りか」で、共有できるロジックが無い）。
あちらは段階 C 以降**恒久の緑**であり、そこへ新しい ``xfail(strict)`` を持ち込むと役割が濁る。

判定は :func:`_expectation_literals` 1 つに集約し、正の対照・負の対照・実ソース走査が
同じ関数を呼ぶ（判定を 2 度書かない）。
"""
from __future__ import annotations

import ast
import subprocess
from pathlib import Path

import pytest

from marketdata.symbol_spec_snapshot import SPEC_FIELD_SOURCES

#: 銘柄仕様のキー集合は対応表が唯一源（ここに列挙を書き写さない）。
_SPEC_KEYS = frozenset(SPEC_FIELD_SOURCES)

#: リポジトリ根（``simulator/tests/unit/`` から 3 つ上）。
_REPO_ROOT = Path(__file__).resolve().parents[3]

#: 供給元を読む関数名（``marketdata/symbol_spec_snapshot.py`` の公開 API）。
#: ここを起点に「供給元に触れる」を 1 段だけ辿る。
_SUPPLIER_ENTRY_POINTS = frozenset({"load_spec_fields", "spec_fields"})


def _numeric(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Constant)
        and isinstance(node.value, (int, float))
        and not isinstance(node.value, bool)
    )


def _spec_field_read(node: ast.AST) -> "str | None":
    """「銘柄仕様のフィールドを読む式」ならそのキー名を返す。"""
    if isinstance(node, ast.Attribute) and node.attr in _SPEC_KEYS:
        return node.attr
    if isinstance(node, ast.Subscript):
        index = node.slice
        if isinstance(index, ast.Constant) and index.value in _SPEC_KEYS:
            return index.value
    return None


def _unwrap_approx(node: ast.AST) -> ast.AST:
    """``pytest.approx(x)`` / ``approx(x)`` を剥がす。"""
    if isinstance(node, ast.Call):
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
        if name == "approx" and node.args:
            return node.args[0]
    return node


def _injected_by_the_test_itself(scope: ast.AST, value) -> bool:
    """同じ ``def`` の中で、``assert`` の**外**に同じ値が現れるか。

    現れるならその数値はテスト自身が**注入した入力**であり、供給元の値の写しではない
    （読み戻しの往復であって、供給元が変わっても陳腐化しない）。
    """
    stack = [scope]
    while stack:
        node = stack.pop()
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.Assert):
                continue  # 期待値側は「注入」ではない
            if isinstance(child, ast.Constant) and not isinstance(child.value, bool):
                if isinstance(child.value, (int, float)) and child.value == value:
                    return True
                # CLI 引数のように**文字列で綴られた**注入も往復である。
                if isinstance(child.value, str) and child.value == str(value):
                    return True
            stack.append(child)
    return False


def _expectation_literals(source: str) -> "list[str]":
    """``assert`` の中で銘柄仕様の読み出しと突き合わされている数値リテラルを列挙する。"""
    found: "list[str]" = []

    def visit(node: ast.AST, scope: ast.AST) -> None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            scope = node
        if isinstance(node, ast.Assert):
            for compare in [c for c in ast.walk(node.test) if isinstance(c, ast.Compare)]:
                sides = [compare.left, *compare.comparators]
                for i, side in enumerate(sides):
                    key = _spec_field_read(side)
                    if key is None:
                        continue
                    for j, other in enumerate(sides):
                        literal = _unwrap_approx(other)
                        if i == j or not _numeric(literal):
                            continue
                        if _injected_by_the_test_itself(scope, literal.value):
                            continue
                        found.append(f"L{compare.lineno}: {key} == {literal.value!r}")
            for mapping in [d for d in ast.walk(node.test) if isinstance(d, ast.Dict)]:
                for name, value in zip(mapping.keys, mapping.values):
                    if not (isinstance(name, ast.Constant) and name.value in _SPEC_KEYS):
                        continue
                    literal = _unwrap_approx(value)
                    if not _numeric(literal):
                        continue
                    if _injected_by_the_test_itself(scope, literal.value):
                        continue
                    found.append(
                        f"L{mapping.lineno}: {name.value} == {literal.value!r}"
                    )
            return
        for child in ast.iter_child_nodes(node):
            visit(child, scope)

    tree = ast.parse(source)
    visit(tree, tree)
    return found


#: ISSUE-445 段階 C で穴から出た唯一の実例（``99af6f0`` の是正**前**の形）。
_THE_REAL_EXAMPLE = '''
def test_build_interactor_supplies_volume_spec_to_strategy_params(tmp_path):
    from simulator.main import build_interactor
    from simulator.tests.unit.test_ea_factory_registry import _mt5_kwargs, _write_mt5_csv

    csv = _write_mt5_csv(tmp_path / "mt5.csv")
    _controller, request = build_interactor(**_mt5_kwargs(csv, "MA_Slope_EA"))

    assert request.config["volume_min"] == pytest.approx(0.1)
    assert request.config["volume_max"] == pytest.approx(100.0)
    assert request.config["volume_step"] == pytest.approx(0.1)
'''


def _source(rel: str) -> str:
    return (_REPO_ROOT / rel).read_text(encoding="utf-8")


def _tracked_python() -> "dict[str, str]":
    """git の index に載っている ``*.py`` の本文（``rel -> source``）。

    ``rglob`` ではなく index を引くのは、untracked な機械生成物・手元の作業記録を
    走査から外すためである（姉妹ゲート ``test_jp225_spec_literals_in_tests.py`` と同じ規律）。
    """
    out = subprocess.run(
        ["git", "-C", str(_REPO_ROOT), "ls-files", "*.py"],
        capture_output=True,
        text=True,
        check=True,
    )
    sources: "dict[str, str]" = {}
    for rel in out.stdout.split():
        try:
            sources[rel] = (_REPO_ROOT / rel).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
    return sources


def _names_used(node: ast.AST) -> "set[str]":
    return {n.id for n in ast.walk(node) if isinstance(n, ast.Name)} | {
        n.attr for n in ast.walk(node) if isinstance(n, ast.Attribute)
    }


def _supplier_derived_builders(sources: "dict[str, str]") -> "dict[str, set[str]]":
    """本文で供給元 API を呼ぶ関数（``rel -> 関数名の集合``）。

    ``load_spec_fields`` 自身（``marketdata/symbol_spec_snapshot.py``）もここに入るため、
    「供給元を直に import しているファイル」と「``_mt5_kwargs`` のような**二次のビルダ**を
    import しているファイル」が同じ規則で母集団に入る。
    """
    builders: "dict[str, set[str]]" = {}
    for rel, source in sources.items():
        if not any(name in source for name in _SUPPLIER_ENTRY_POINTS):
            continue  # 本文に名前が無ければ AST にも現れない（parse を省く）
        try:
            tree = ast.parse(source)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if _names_used(node) & _SUPPLIER_ENTRY_POINTS:
                    builders.setdefault(rel, set()).add(node.name)
    return builders


def _imported_module(rel: str, node: ast.ImportFrom) -> str:
    """``from X import ...`` の X を**絶対のモジュール名**にして返す。

    ``node.level`` は先頭のドットの数（相対 import）。絶対名として扱うと、パッケージ内で
    相対 import された二次ビルダの消費者が母集団から静かに漏れる。
    """
    if not node.level:
        return node.module or ""
    package = rel.rsplit("/", 1)[0].split("/") if "/" in rel else []
    base = package[: len(package) - (node.level - 1)] if node.level > 1 else package
    return ".".join([*base, *([node.module] if node.module else [])])


def _supplier_touching_sources(
    sources: "dict[str, str] | None" = None,
) -> "dict[str, str]":
    """走査母集団（``rel -> 母集団に入る理由``）。

    (A) 自分で供給元 API を呼ぶ関数を持つファイル、または
    (B) そういう関数を**他ファイルから import している**ファイル。

    (B) が射程の要である。ISSUE-445 段階 C の実例はこの形で、値は供給元から来るのに
    ファイル自身は ``load_spec_fields`` を 1 度も書いていなかった。

    ``sources`` を渡さなければ tracked な ``*.py`` 全件を対象にする。渡せるのは、
    (B) の規則そのものを合成ソースで実証するためである（実ツリーの実例は是正の副作用で
    (A) にも該当するようになり、単独では (B) の証拠にならない）。
    """
    if sources is None:
        sources = _tracked_python()
    builders = _supplier_derived_builders(sources)
    scope = {rel: "供給元 API を直に呼ぶ" for rel in builders}

    module_of = {rel[:-3].replace("/", "."): rel for rel in sources}
    every_builder = {name for names in builders.values() for name in names}
    for rel, source in sources.items():
        if rel in scope or not any(name in source for name in every_builder):
            continue
        try:
            tree = ast.parse(source)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom):
                continue
            origin = module_of.get(_imported_module(rel, node))
            if origin is None or origin == rel or origin not in builders:
                continue
            imported = sorted(a.name for a in node.names if a.name in builders[origin])
            if imported:
                scope[rel] = f"{origin} から {', '.join(imported)} を import"
    return scope


def test_scanner_detects_the_real_example_of_a_written_down_expectation():
    """正: 実例（是正前の形）の 3 行を検出する。"""
    assert _expectation_literals(_THE_REAL_EXAMPLE) == [
        "L9: volume_min == 0.1",
        "L10: volume_max == 100.0",
        "L11: volume_step == 0.1",
    ]


def test_scanner_ignores_a_literal_that_the_same_test_injected_itself():
    """**負の対照（実ソース）**: テストが自分で注入した値の往復は「写し」ではない。

    ``marketdata/tests/test_symbol_spec_snapshot.py`` の
    ``test_mapping_table_is_actually_used`` は ``fake[...] = 12.5`` と注入し
    ``assert spec_fields(fake)["contract_size"] == 12.5`` で読み戻す。この 12.5 は
    供給元の値ではなくテストの入力であり、供給元が変わっても陳腐化しない。
    """
    assert _expectation_literals(
        _source("marketdata/tests/test_symbol_spec_snapshot.py")
    ) == []


def test_scanner_ignores_a_round_trip_whose_input_was_written_as_text():
    """**負の対照（実ソース）**: 注入が文字列でも往復は往復である。

    ``simulator/tests/unit/test_cli_symbol_spec_args.py`` の
    ``test_explicit_value_overrides_the_snapshot`` は CLI 引数
    ``["--contract-size", "3.0"]``（**文字列**）で注入し ``== 3.0``（数値）で読み戻す。
    注入の綴りが文字列であることは往復の性質を変えない。
    同ファイルは負の対照 ``_REMOVED_DEFAULTS``（撤去済み既定値の記録）も持つ。
    """
    assert _expectation_literals(
        _source("simulator/tests/unit/test_cli_symbol_spec_args.py")
    ) == []


def test_scanner_detects_a_spec_expectation_written_as_a_dict_literal():
    """正: 期待値を dict リテラルで並べる形も同じ写しである。

    比較形（``cfg["volume_min"] == 0.1``）だけを見ると、まとめ書き
    （``cfg == {"volume_min": 0.1}``）で同じ写しが素通りする。**同じ誤りの別の綴り**を
    通す穴を残さない。現行ツリーにこの形の実例は無い（実測 0 件）が、判定の射程には入れる。
    """
    assert _expectation_literals(
        'def test_x():\n'
        '    assert request.config == {"volume_min": 0.1, "digits": 1}\n'
    ) == [
        "L2: volume_min == 0.1",
        "L2: digits == 1",
    ]


# --- 走査母集団 -------------------------------------------------------------------------


def test_the_scope_admits_a_consumer_that_only_imports_a_supplier_derived_builder():
    """射程の要 (B): **import しかしていない**消費者が母集団に入ること。

    ISSUE-445 段階 C の実例は、供給元を自分では 1 度も呼ばず ``_mt5_kwargs`` を別ファイルから
    import して期待値を写していた。実測（是正前 ``99af6f0^`` の AST）: 当該ファイルに
    ``load_spec_fields`` / ``spec_fields`` を参照する関数は **0 個**であり、(A) では母集団に
    入らない。母集団を「自分で供給元を呼ぶファイル」に限ると**この形が丸ごと外れる**——
    それが段階 A/B が見落とした経路そのものである。

    実ツリーの是正後は当該ファイルが (A) にも該当してしまい（是正で ``load_spec_fields`` の
    呼び出しが入ったため）(B) の単独実証にならない。よって合成ソースで (B) だけを見る。
    """
    scope = _supplier_touching_sources(
        {
            "pkg/builders.py": (
                "def _mt5_kwargs():\n"
                "    return dict(**load_spec_fields('S', 'JP225'))\n"
            ),
            "pkg/consumer.py": (
                "from pkg.builders import _mt5_kwargs\n"
                "\n"
                "def test_x():\n"
                "    assert _mt5_kwargs()['volume_min'] == 0.1\n"
            ),
            "pkg/unrelated.py": "def test_y():\n    assert 1 == 1\n",
        }
    )
    assert "pkg/consumer.py" in scope
    assert "import" in scope["pkg/consumer.py"]
    assert "pkg/unrelated.py" not in scope


def test_the_scope_resolves_a_relative_import_of_a_supplier_derived_builder():
    """(B) は**相対 import** でも成り立つこと。

    tracked な ``*.py`` には相対 ``from . import`` が **312 件**ある（実測 2026-08-27）。
    ``node.module`` を絶対名として扱うと、パッケージ内で相対 import された二次ビルダの
    消費者が母集団から静かに漏れる（＝この穴と同型の見落とし）。
    """
    scope = _supplier_touching_sources(
        {
            "pkg/sub/builders.py": (
                "def _kw():\n    return load_spec_fields('S', 'JP225')\n"
            ),
            "pkg/sub/consumer.py": (
                "from .builders import _kw\n"
                "\n"
                "def test_x():\n"
                "    assert _kw()['digits'] == 1\n"
            ),
            "pkg/other.py": (
                "from .sub.builders import _kw\n"
                "\n"
                "def test_y():\n"
                "    assert _kw()['digits'] == 1\n"
            ),
        }
    )
    assert "pkg/sub/consumer.py" in scope
    assert "pkg/other.py" in scope


def test_the_scope_covers_the_file_that_the_hole_produced():
    """実ツリーで、実例のファイルと供給元由来ビルダの定義元がどちらも母集団に入ること。"""
    scope = _supplier_touching_sources()
    assert "simulator/tests/unit/test_ma_slope_normalize_lot.py" in scope
    assert "simulator/tests/unit/test_ea_factory_registry.py" in scope


def test_the_scope_excludes_a_source_that_never_touches_the_supplier():
    """**負の対照**: 供給元に触れないファイルは母集団に入らない。

    ``test_usecase_models.py`` は合成仕様（``contract_size=100000.0`` ほか）を組んで
    そのまま読み戻すだけであり、そこの数値は供給元の写しではない。母集団を絞らないと
    この種の合成データが全部偽陽性になる（実測: 同ファイルだけで 8 件）。
    """
    assert "simulator/tests/unit/test_usecase_models.py" not in _supplier_touching_sources()


# --- 走査（母集団 × 判定）と台帳 --------------------------------------------------------


def _scan() -> "dict[str, list[str]]":
    found: "dict[str, list[str]]" = {}
    for rel in _supplier_touching_sources():
        try:
            hits = _expectation_literals(_source(rel))
        except (SyntaxError, UnicodeDecodeError, OSError):
            continue
        if hits:
            found[rel] = hits
    return found


#: 走査結果は collection 時に 1 回だけ取る（xfail の reason に実測を載せるため）。
_FOUND = _scan()

#: **検出されるが是正対象でない**もの＝供給元が変わったら赤にするために人が置いた合図。
#: 姉妹ゲートと同じ語彙（``_EXCLUDED_BY_INTENT``）を使う。
#:
#: ここに入る条件は「その数値が**権威として振る舞っていない**こと」である。RC-1 の害は
#: 人が書いた値が権威になって沈黙で食い違うことであり、下の 2 件はどちらも逆に
#: 「供給元が変わったら赤くする」ためだけに置かれた仕掛け線であって、後続の検定の**前提を
#: 宣言している**。機械では役割を判別できないため明示除外する。
_EXCLUDED_BY_INTENT: "dict[str, str]" = {
    "simulator/tests/unit/test_jp225_spec_literals_in_tests.py": (
        "L190 `assert _TRUTH['contract_size'] == 1.0`。直後の負の対照が食わせる合成ソース"
        " `contract_size=1.0` が**真値と一致していること**が当該検定の成立条件であり、"
        "この 1 行はその前提の宣言（原文コメント「前提の明示（真値は供給元が決める）」）。"
        "供給元が変われば赤になって合成ソースの更新を促す＝仕掛け線として働く。"
    ),
    "simulator/tests/unit/test_order_admission.py": (
        "L144 `assert jp225.volume_min == 1.0`。原文コメント「以下 2 検定の前提"
        "（供給元が変われば赤にする）」のとおり、続く"
        " `test_the_pre_stage1_lot_is_rejected`（lot=0.1 が棄却される）が意味を持つのは"
        " volume_min > 0.1 のときだけであり、この 1 行がその前提を宣言している。"
    ),
}

#: 既知の違反＝**所在の台帳**（値の期待値ではない）。**走査結果から導出してはならない。**
#:
#: **現在 0 件**（2026-08-27）。本ゲートが新設時に検出した唯一の違反
#: `test_run_options_api_controller.py` L74 `assert … ["point_size"] == 0.1` は、
#: 同ファイル L64 が段階 C で `== _profile().contract_size` へ是正されたのと同じ形の
#: **取り残し**であった。是正（`== _profile().point_size`）を入れた結果、本台帳に基づく
#: xfail が **XPASS(strict) で赤に転じ**、マーカーと台帳エントリの撤去を機械的に促した
#: ——機構が設計どおり働いた実例である。
#:
#: 空のままでよい。新たな違反が混入すれば
#: `test_the_ledger_agrees_with_the_scan_in_both_directions` が「台帳に無い違反」として
#: 赤にする（空であることと導出であることは別＝下の自己検査を参照）。
_KNOWN: "tuple[str, ...]" = ()


def test_the_ledger_agrees_with_the_scan_in_both_directions():
    """台帳と走査結果が**完全一致**すること。

    片側包含では足りない。左が増える＝新規混入（台帳に足す前に赤で気付く）、
    左が減る＝是正済み（台帳から外す合図）。どちらも赤にする。
    """
    assert set(_FOUND) == set(_KNOWN) | set(_EXCLUDED_BY_INTENT), (
        "台帳と走査結果が食い違う。\n"
        f"  走査にあって台帳・除外に無い（新規混入）: "
        f"{sorted(set(_FOUND) - set(_KNOWN) - set(_EXCLUDED_BY_INTENT))}\n"
        f"  台帳にあって走査に無い（是正済み・台帳から外す）: "
        f"{sorted(set(_KNOWN) - set(_FOUND))}"
    )


def test_the_ledger_is_written_down_and_not_derived_from_the_scan():
    """``_KNOWN`` が**走査結果から導出されていない**こと（自己検査・最重要）。

    ``_KNOWN = tuple(set(_FOUND) - set(_EXCLUDED_BY_INTENT))`` のように導出すると、
    新しい違反ファイルは collection 時に自動で台帳へ入り、**赤にならずに吸収される**。
    それは ISSUE-445 の失敗モード（誤りが 2 か月誰にも気付かれない）そのものである。
    台帳は人が書き下し、走査結果との**一致**を検定で見る。
    """
    module = ast.parse(Path(__file__).read_text(encoding="utf-8"))
    assigned = [
        node.value
        for node in module.body
        if isinstance(node, (ast.Assign, ast.AnnAssign))
        for target in ([*node.targets] if isinstance(node, ast.Assign) else [node.target])
        if isinstance(target, ast.Name) and target.id == "_KNOWN"
    ]
    assert len(assigned) == 1, "_KNOWN の代入が 1 つでない"
    value = assigned[0]
    assert isinstance(value, ast.Tuple), "_KNOWN は tuple リテラルでなければならない"
    assert all(
        isinstance(e, ast.Constant) and isinstance(e.value, str) for e in value.elts
    ), "_KNOWN の要素は文字列リテラルでなければならない（式で導出しない）"


def test_the_scan_reaches_a_real_population_of_tracked_sources():
    """走査が実際に木を舐めていること。空振りする走査で「違反 0」を主張しない。"""
    tracked = _tracked_python()
    scope = _supplier_touching_sources()
    assert len(tracked) > 1_000
    assert all(rel.endswith(".py") for rel in tracked)
    # 母集団は空でなく、かつ tracked 全件より真に小さい（絞り込みが働いている）。
    assert 0 < len(scope) < len(tracked)
    # (B) 経路（import だけで母集団に入る）が実在すること＝この穴の入口が覆われている。
    assert any("import" in reason for reason in scope.values())


def test_the_excluded_sources_are_actually_detected_by_the_scanner():
    """除外が**意図的**であること。検出されないから外れているのではない。

    これが落ちるときは、当該ファイルが是正されたか形が変わったかであり、
    ``_EXCLUDED_BY_INTENT`` の記述ごと見直す合図である。
    """
    for rel in _EXCLUDED_BY_INTENT:
        assert _FOUND.get(rel), f"{rel} は走査に掛からない。除外の記述が古い。"


#: 走査に掛かってはならない**実ファイル**と、掛からない**構造上の**理由。
#: 「走査対象に入れていない」ではなく、判定関数に**実ソースを食わせて 0 件**で示す。
#: （往復除外そのものの振る舞いは上の 2 検定が個別に持つ。ここは別の理由で外れるもの。）
_MUST_NOT_DETECT = {
    "simulator/tests/unit/test_ma_slope_normalize_lot.py": (
        "**是正後の姿**（99af6f0）。期待値を書かず `load_spec_fields` と突き合わせる。"
        "次段階が到達すべき形を検出すると「是正したのに赤が消えない」ゲートになる"
    ),
    "simulator/tests/unit/test_ea_factory_registry.py": (
        "供給元由来ビルダ `_mt5_kwargs` の**定義元**。組み立てはするが銘柄仕様を"
        "期待値に持つ assert が無い"
    ),
    "simulator/tests/unit/test_tool_symbol_specs_from_snapshot.py": (
        "負の対照 `_REMOVED_LITERALS`（撤去済み旧値の記録）を持つが、それは module 直下の"
        "dict であって assert の期待値ではない。意図的な記録を赤にしない"
    ),
}


@pytest.mark.parametrize("rel", sorted(_MUST_NOT_DETECT))
def test_scanner_does_not_detect_the_sources_that_must_stay_out(rel):
    assert _expectation_literals(_source(rel)) == [], (
        f"{rel} を検出した（偽陽性）。除外理由: {_MUST_NOT_DETECT[rel]}"
    )


# 注記（2026-08-27・撤去の記録）: ここには `_KNOWN` を parametrize して既知違反の所在を
# `xfail(strict=True)` で固定する `test_known_written_down_expectation_is_still_there` を
# 置いていた。本ゲートが新設時に検出した唯一の違反
# （`test_run_options_api_controller.py` L74 の `== 0.1`）を是正した結果、その検定は
# **XPASS(strict) で赤に転じ**、自らの reason に書いたとおり「マーカーごと撤去し `_KNOWN`
# から外せ」と機械的に知らせた。指示どおり撤去した（機構が設計どおり働いた実例）。
#
# 台帳が空になっても検出力は落ちない: `test_the_ledger_agrees_with_the_scan_in_both_directions`
# が「台帳にも除外にも無い違反」を赤にするため、新たな混入はそこで捕まる。
# 既知違反が再び現れたら `_KNOWN` へ書き下し、本検定を同じ形で復活させればよい。
