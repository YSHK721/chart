"""``marketdata.symbol_spec_snapshot`` の検定（ISSUE-445 恒久策・段階 2 / D1）。

固定する不変条件（憶測禁止・すべて実スナップショットまたはモジュール自身から機械的に取る）:

    1. **対応表が唯一源**。MT5 のフィールド名（``trade_contract_size`` 等）から
       ``SymbolSpec`` の 8 フィールド名（``contract_size`` 等）への対応は
       :data:`SPEC_FIELD_SOURCES` ／ :data:`SETTLEMENT_CURRENCY_SOURCE` の
       **1 箇所にしかない**。モジュール本文の非 docstring 文字列定数を AST で数えて施行する。
    2. **実スナップショットで全キーが解決する**。表の全エントリが
       ``marketdata/symbol_specs/OANDA-Japan-MT5-Live/JP225.json`` 上で引ける。
       期待値はテスト側にリテラルで持たず、JSON から独立に読んで突き合わせる。
    3. ``leverage`` の供給元は ``account`` セクションである（``mt5.symbol_info`` に
       ``leverage`` は無い・設計書 §3.4）。これも実スナップショットで実証する。
    4. **捏造しない**。表のキーがスナップショットに無ければ既定値で埋めず Fail-Stop する。
    5. **表は「銘柄仕様」と「口座属性」に分かれている**（設計書 §3.4・依頼時の呼称「段階 3-D1」。
       呼称は設計書に無い会話上のものであり、権威は §3.4 である）。
       :data:`SYMBOL_FIELD_SOURCES` ∪ :data:`ACCOUNT_FIELD_SOURCES` == :data:`SPEC_FIELD_SOURCES`
       （キー集合・並び・``FieldSource`` の中身まで一致）であり、各表の供給セクションは
       単一である。前者は「分割しても呼出側の観測が 1 バイトも変わらない」ことの機械的固定、
       後者は段階 3-D2（``SymbolSpec`` からの ``leverage`` 分離）の前提になる。
"""
from __future__ import annotations

import ast
import copy
import json
from pathlib import Path

import pytest

from marketdata import symbol_spec_snapshot as sss

_SERVER = sss.OANDA_JAPAN_MT5_LIVE
_SYMBOL = "JP225"


@pytest.fixture(scope="module")
def raw() -> dict:
    """スナップショット JSON を**モジュールを経由せず**素で読む（突合の独立性を保つ）。"""
    path = (
        Path(sss.__file__).resolve().parent
        / "symbol_specs"
        / _SERVER
        / f"{_SYMBOL}.json"
    )
    return json.loads(path.read_text(encoding="utf-8"))


# --- 1. 対応表が唯一源 ---------------------------------------------------------------


def test_spec_fields_keys_are_exactly_the_mapping_table_keys(raw):
    """出力キー集合は表のキー集合と一致する（表を削れば出力も減る＝表が唯一源）。"""
    assert set(sss.spec_fields(raw)) == set(sss.SPEC_FIELD_SOURCES)


#: 対応表を保持する変数名（この代入文の外に MT5 フィールド名が現れてはならない）。
#:
#: 段階 3-D1 で表を「銘柄仕様 / 口座属性」に分割したため、対応を持つ表は 3 つになった。
#: **合成ビュー ``SPEC_FIELD_SOURCES`` はここに載せない**——あれは 2 表の合成であって対応を
#: 新たに持たない。載せなければ合成ビューの代入文は走査上「外」になり、そこへ対応を書き足す
#: （＝第 2 の対応が生える）と赤になる。ゲートの趣旨（対応は 1 箇所に限る）は変えていない。
_TABLE_NAMES = (
    "SYMBOL_FIELD_SOURCES",
    "ACCOUNT_FIELD_SOURCES",
    "SETTLEMENT_CURRENCY_SOURCE",
)


def test_mt5_field_names_appear_only_inside_the_mapping_table():
    """MT5 のフィールド名リテラルは対応表の代入文の**外に出ない**（AST による機械的施行）。

    「対応表を明示的に 1 箇所へ置け」を宣言ではなくソース走査で固定する。第 2 の対応が
    どこかに生えたら（例: 関数内で ``snapshot["symbol"]["point"]`` を直に引く）赤になる。

    表の中で同じ綴りが 2 回出るのは正当である（``volume_min`` 等は MT5 名と ``SymbolSpec``
     名が同綴りのため ``{"volume_min": FieldSource(..., "volume_min", ...)}`` になる）。
    よって「回数」ではなく「表の部分木の内か外か」で判定する。
    """
    source = Path(sss.__file__).resolve().with_suffix(".py").read_text(encoding="utf-8")
    tree = ast.parse(source)

    # 対応表の代入文（部分木）を特定する。
    table_nodes = [
        node
        for node in tree.body
        if isinstance(node, (ast.Assign, ast.AnnAssign))
        and any(
            isinstance(t, ast.Name) and t.id in _TABLE_NAMES
            for t in ([node.target] if isinstance(node, ast.AnnAssign) else node.targets)
        )
    ]
    assert {
        t.id
        for node in table_nodes
        for t in ([node.target] if isinstance(node, ast.AnnAssign) else node.targets)
        if isinstance(t, ast.Name)
    } == set(_TABLE_NAMES), "対応表の変数名が見つからない（テストの前提が崩れている）"

    inside = {id(n) for node in table_nodes for n in ast.walk(node)}

    docstrings = set()
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if isinstance(body, list) and body:
            first = body[0]
            if (
                isinstance(first, ast.Expr)
                and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)
            ):
                docstrings.add(id(first.value))

    outside_literals = [
        n.value
        for n in ast.walk(tree)
        if isinstance(n, ast.Constant)
        and isinstance(n.value, str)
        and id(n) not in inside
        and id(n) not in docstrings
    ]

    mt5_keys = {s.key for s in sss.SPEC_FIELD_SOURCES.values()}
    mt5_keys.add(sss.SETTLEMENT_CURRENCY_SOURCE.key)
    leaked = sorted(mt5_keys.intersection(outside_literals))
    assert not leaked, f"MT5 フィールド名が対応表の外に現れている: {leaked}（対応表は 1 箇所に限る）"


def test_mapping_table_is_actually_used(raw):
    """表を差し替えると出力が変わる（表が飾りでないことの実証）。"""
    fake = copy.deepcopy(raw)
    fake["symbol"][sss.SPEC_FIELD_SOURCES["contract_size"].key] = 12.5
    assert sss.spec_fields(fake)["contract_size"] == 12.5


# --- 2. 実スナップショットで全キーが解決する ------------------------------------------


def test_every_mapped_key_resolves_on_the_real_snapshot(raw):
    """表の全エントリが実スナップショット上で引け、値が JSON と一致する。"""
    fields = sss.spec_fields(raw)
    assert len(fields) == len(sss.SPEC_FIELD_SOURCES)
    for name, source in sss.SPEC_FIELD_SOURCES.items():
        assert source.key in raw[source.section], f"{name}: {source.section}.{source.key} が無い"
        assert fields[name] == pytest.approx(raw[source.section][source.key])


def test_load_spec_fields_reads_the_committed_snapshot(raw):
    """``load_spec_fields`` はコミット済みスナップショットを読む（経路の実証）。"""
    assert sss.load_spec_fields(_SERVER, _SYMBOL) == sss.spec_fields(raw)


def test_settlement_currency_comes_from_the_symbol_profit_currency(raw):
    src = sss.SETTLEMENT_CURRENCY_SOURCE
    assert sss.settlement_currency(raw) == raw[src.section][src.key]


def test_casts_match_the_symbol_spec_types(raw):
    """``stops_level`` / ``digits`` は int、他は float（``SymbolSpec`` の型注釈と同じ）。"""
    fields = sss.spec_fields(raw)
    assert isinstance(fields["stops_level"], int)
    assert isinstance(fields["digits"], int)
    for name in ("contract_size", "volume_min", "volume_max", "volume_step", "point_size", "leverage"):
        assert isinstance(fields[name], float), name


# --- 3. leverage は口座属性である（設計書 §3.4）---------------------------------------


def test_leverage_is_sourced_from_the_account_section(raw):
    assert sss.SPEC_FIELD_SOURCES["leverage"].section == "account"
    # 実測の裏付け: 供給元の symbol セクション（96 フィールド）に leverage は無い。
    assert "leverage" not in raw["symbol"]
    assert sss.spec_fields(raw)["leverage"] == float(raw["account"]["leverage"])


def test_all_other_fields_are_sourced_from_the_symbol_section():
    others = {n: s for n, s in sss.SPEC_FIELD_SOURCES.items() if n != "leverage"}
    assert {s.section for s in others.values()} == {"symbol"}


# --- 4. 捏造しない（Fail-Stop）---------------------------------------------------------


def test_missing_mapped_key_is_fail_stop(raw):
    """表のキーがスナップショットに無ければ既定値で埋めず中断する。"""
    broken = copy.deepcopy(raw)
    del broken["symbol"][sss.SPEC_FIELD_SOURCES["point_size"].key]
    with pytest.raises(sss.SnapshotError) as exc:
        sss.spec_fields(broken)
    assert sss.SPEC_FIELD_SOURCES["point_size"].key in str(exc.value)


def test_missing_section_is_fail_stop(raw):
    broken = copy.deepcopy(raw)
    del broken["account"]
    with pytest.raises(sss.SnapshotError):
        sss.spec_fields(broken)


def test_missing_snapshot_file_is_fail_stop():
    with pytest.raises(sss.SnapshotError) as exc:
        sss.load_snapshot(_SERVER, "NO_SUCH_SYMBOL")
    assert "NO_SUCH_SYMBOL" in str(exc.value)


def test_snapshot_root_is_inside_the_marketdata_package():
    """出力先の起点は**スクリプト位置からの推測ではなく**パッケージ内の実在ディレクトリ。"""
    assert sss.SNAPSHOT_ROOT == Path(sss.__file__).resolve().parent / "symbol_specs"
    assert sss.SNAPSHOT_ROOT.is_dir()
    assert sss.snapshot_path(_SERVER, _SYMBOL).is_file()


# --- 5. 表は「銘柄仕様」と「口座属性」に分かれている（SRP・段階 3-D1）------------------
#
# 分割しても**呼出側の観測が変わらない**こと（合成ビュー :data:`SPEC_FIELD_SOURCES` が
# 従来どおり 8 エントリを同じ並びで返すこと）と、各表の供給セクションが単一であることを
# 機械的に固定する。判定は下の純関数 2 つに集約し、実データ側と負の対照が**同じ関数**を
# 呼ぶ（判定を 2 度書かない）。期待値（キー名・セクション名）はここにリテラルで書かず、
# 表とスナップショットから導く。


def composition_disagreements(parts, composed) -> "list[str]":
    """``parts`` を順に合成した表と ``composed`` の食い違いを列挙する（空なら一致）。

    キーの**並び**まで見る: 合成ビューの並びは ``simulator/tools/symbol_spec_args.py`` の
    ``SPEC_KEYS``（argparse の宣言順）として呼出側に観測されるため、並びが変われば
    「呼出側 0 改変」が崩れる。分割どうしのキー重複も検出する——重複は後勝ちで黙って
    片方を消すため、合成が一致していても分割の意味が失われている。
    """
    merged: "dict[str, sss.FieldSource]" = {}
    problems: "list[str]" = []
    for part in parts:
        for name, source in part.items():
            if name in merged:
                problems.append(f"分割どうしでキーが重複している: {name}")
            merged[name] = source
    if list(merged) != list(composed):
        problems.append(f"キーの並びが違う: {list(merged)} != {list(composed)}")
    for name in sorted(set(merged) | set(composed)):
        if merged.get(name) != composed.get(name):
            problems.append(f"{name}: {merged.get(name)!r} != {composed.get(name)!r}")
    return problems


def tables_not_drawing_from_a_single_section(tables) -> "list[str]":
    """``{表名: 表}`` のうち供給セクションが 1 つに定まらない表を挙げる（空なら合格）。

    空の表も該当させる（空の分割は分割になっておらず、合成一致が自明に成立してしまう）。
    """
    return [
        name
        for name, table in tables.items()
        if len({source.section for source in table.values()}) != 1
    ]


def _split_tables() -> dict:
    """分割後の 2 表（この 2 つの合成が :data:`SPEC_FIELD_SOURCES` である）。"""
    return {
        "SYMBOL_FIELD_SOURCES": sss.SYMBOL_FIELD_SOURCES,
        "ACCOUNT_FIELD_SOURCES": sss.ACCOUNT_FIELD_SOURCES,
    }


def test_the_composed_view_is_exactly_the_two_tables_merged():
    """合成ビューは 2 表の合成そのもの（対応を新たに持たない）。"""
    assert (
        composition_disagreements(
            (sss.SYMBOL_FIELD_SOURCES, sss.ACCOUNT_FIELD_SOURCES), sss.SPEC_FIELD_SOURCES
        )
        == []
    )


def test_both_tables_are_non_empty():
    """片方が空なら「分割した」ことにならない（上の一致検定が空虚になる）。"""
    assert sss.SYMBOL_FIELD_SOURCES
    assert sss.ACCOUNT_FIELD_SOURCES


def test_each_table_draws_from_a_single_section():
    """各表の供給セクションは単一である（段階 3-D2 の前提）。"""
    assert tables_not_drawing_from_a_single_section(_split_tables()) == []


def test_the_two_tables_draw_from_different_real_sections(raw):
    """2 表のセクションは互いに異なり、どちらも実スナップショットに実在する。

    銘柄仕様側のセクション名は :data:`SETTLEMENT_CURRENCY_SOURCE` から同定する
    （``currency_profit`` は ``mt5.symbol_info()`` のフィールドであるため、その供給元
    セクションが ``symbol_info`` のセクションである）。口座側は名前を書かず「銘柄側と
    異なる実在セクション」であることで押さえる（セクション名をここに書き写さない）。
    """
    symbol_section = sss.SETTLEMENT_CURRENCY_SOURCE.section
    sections = {
        name: sorted({source.section for source in table.values()})
        for name, table in _split_tables().items()
    }
    assert sections["SYMBOL_FIELD_SOURCES"] == [symbol_section]
    (account_section,) = sections["ACCOUNT_FIELD_SOURCES"]
    assert account_section != symbol_section
    for section in (symbol_section, account_section):
        assert isinstance(raw.get(section), dict), section
    for table in _split_tables().values():
        for name, source in table.items():
            assert source.key in raw[source.section], f"{name}: {source.section}.{source.key}"


def test_spec_fields_preserves_the_composed_key_order(raw):
    """出力の並びは合成ビューの並び（＝分割の順序がそのまま呼出側の観測になる）。"""
    assert list(sss.spec_fields(raw)) == list(sss.SPEC_FIELD_SOURCES)


class TestTheSplitChecksDetectAndOnlyDetect:
    """判定 2 つが両方向に効くこと（検出する／余計に検出しない）を合成の表で固定する。

    実データではなく合成の表を食わせるのは、**本番の値を 1 つも変えずに**「崩れたら赤に
    なる」側を実証するためである。実データ側の緑と対で読むこと。
    """

    def test_composition_flags_a_dropped_entry(self):
        # Arrange: 銘柄側から 1 件落とした分割。
        symbol = dict(sss.SYMBOL_FIELD_SOURCES)
        symbol.popitem()
        # Act / Assert
        assert composition_disagreements(
            (symbol, sss.ACCOUNT_FIELD_SOURCES), sss.SPEC_FIELD_SOURCES
        )

    def test_composition_flags_a_changed_cast(self):
        # Arrange: セクションもキーも同じで ``cast`` だけ差し替えた分割。
        symbol = dict(sss.SYMBOL_FIELD_SOURCES)
        name, source = next(iter(symbol.items()))
        symbol[name] = sss.FieldSource(source.section, source.key, str)
        # Act / Assert
        assert composition_disagreements(
            (symbol, sss.ACCOUNT_FIELD_SOURCES), sss.SPEC_FIELD_SOURCES
        )

    def test_composition_flags_a_reordered_split(self):
        """並びが変われば赤（キー集合が同じでも呼出側の観測が変わるため）。"""
        assert composition_disagreements(
            (sss.ACCOUNT_FIELD_SOURCES, sss.SYMBOL_FIELD_SOURCES), sss.SPEC_FIELD_SOURCES
        )

    def test_composition_flags_a_key_present_in_both_parts(self):
        # Arrange: 合成結果は一致するが、同じキーを 2 表が持つ分割。
        account = dict(sss.ACCOUNT_FIELD_SOURCES)
        duplicated = next(iter(sss.SYMBOL_FIELD_SOURCES))
        account[duplicated] = sss.SPEC_FIELD_SOURCES[duplicated]
        # Act
        problems = composition_disagreements(
            (sss.SYMBOL_FIELD_SOURCES, account), sss.SPEC_FIELD_SOURCES
        )
        # Assert: 重複だけが問題として挙がる（合成一致は保たれている）。
        assert problems == [f"分割どうしでキーが重複している: {duplicated}"]

    def test_composition_stays_silent_on_an_agreeing_split(self):
        """余計に検出しない: 素直に分けた表は問題ゼロ（偽陽性を作らない）。"""
        names = list(sss.SPEC_FIELD_SOURCES)
        head = {name: sss.SPEC_FIELD_SOURCES[name] for name in names[:1]}
        tail = {name: sss.SPEC_FIELD_SOURCES[name] for name in names[1:]}
        assert composition_disagreements((head, tail), sss.SPEC_FIELD_SOURCES) == []

    def test_single_section_flags_a_mixed_table(self):
        # Arrange: 分割前の姿（銘柄仕様と口座属性が 1 表に同居している）。
        mixed = {**dict(sss.SYMBOL_FIELD_SOURCES), **dict(sss.ACCOUNT_FIELD_SOURCES)}
        # Act / Assert
        assert tables_not_drawing_from_a_single_section({"mixed": mixed}) == ["mixed"]

    def test_single_section_flags_an_empty_table(self):
        assert tables_not_drawing_from_a_single_section({"empty": {}}) == ["empty"]

    def test_single_section_stays_silent_on_a_uniform_table(self):
        assert (
            tables_not_drawing_from_a_single_section(
                {"uniform": dict(sss.SYMBOL_FIELD_SOURCES)}
            )
            == []
        )
