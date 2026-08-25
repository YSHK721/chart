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
_TABLE_NAMES = ("SPEC_FIELD_SOURCES", "SETTLEMENT_CURRENCY_SOURCE")


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
