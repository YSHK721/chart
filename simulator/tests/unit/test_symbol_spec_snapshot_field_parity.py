"""スナップショット対応表と、それを消費する型の**フィールド名の一致**を固定する。

由来: ISSUE-445 恒久策 **段階 2**（``.doc/SYMBOL_SPEC_SUPPLY_BASIC_DESIGN.md`` §3.2）、
段階 3-D2 で対応表 2 分割（§3.4）に追随。

なぜ ``marketdata`` 側ではなくここに置くか（依存方向）:
    ``marketdata`` は最下層（依存ゼロ）であり ``simulator`` を知らない。よって
    「対応表のキー集合 == ``simulator`` 側の型のフィールド名集合」という突合は
    ``simulator`` 側の検定が持つ（依存の向きは simulator → marketdata の 1 方向のまま）。

何を防ぐか:
    ``SymbolSpec`` にフィールドが増減したとき、対応表が追随しないまま
    ``build_interactor(**load_spec_fields(...))`` が ``TypeError`` になる（あるいは供給漏れが
    既定値で埋まる）事態を、実行時ではなく検定時に赤にする。

**段階 3-D2 で突き合わせ先を替えた（何が守れて何が守れなくなるか）**:
    以前は「合成ビュー :data:`SPEC_FIELD_SOURCES`（8 キー）== ``SymbolSpec`` のフィールド」
    を施行しており、``SymbolSpec(**load_spec_fields(...))`` が成立することまで保証していた。
    段階 3-D2 で ``leverage`` が ``SymbolSpec`` を出た（口座属性・``RunBacktestRequest`` が
    持つ）ため、この等式は**成立しなくなった**。突合先を供給セクションごとの表へ移す:

    * :data:`SYMBOL_FIELD_SOURCES`（銘柄仕様）== ``SymbolSpec`` のフィールド（厳密一致）
    * :data:`ACCOUNT_FIELD_SOURCES`（口座属性）⊆ ``RunBacktestRequest`` のフィールド
      （行き先が実在すること。``initial_deposit`` / ``stop_out_level`` のように供給元
      スナップショットから引かない口座属性もあるため**包含**で押さえる）
    * 合成ビュー（``load_spec_fields`` が返す 8 キー）⊆ ``build_interactor`` の引数名
      （``**`` 展開の前提。ここは従来どおり合成ビューが対象）

    **守れるもの**: 供給元の各エントリに消費側の受け口が実在すること（供給漏れ・綴り違いは
    従来どおり検定時に赤）。``build_interactor(**load_spec_fields(...))`` の成立。
    **守れなくなるもの**: ``SymbolSpec(**load_spec_fields(...))``（合成ビューをそのまま
    ``SymbolSpec`` へ展開する形）。これは**意図した喪失**である——口座属性を銘柄仕様の型へ
    流し込む形そのものが段階 3-D2 で消した SRP 違反であり、呼出側は銘柄仕様の表
    （:data:`SYMBOL_FIELD_SOURCES`）でキーを絞る。
    なお「合成ビュー == 2 表の和（並び・``FieldSource`` の中身まで）」は
    ``marketdata/tests/test_symbol_spec_snapshot.py`` が固定済みであり、ここには書かない
    （判定を 2 度持たない）。
"""
from __future__ import annotations

import dataclasses

from marketdata.symbol_spec_snapshot import (
    ACCOUNT_FIELD_SOURCES,
    SPEC_FIELD_SOURCES,
    SYMBOL_FIELD_SOURCES,
)
from simulator.usecase.models import SymbolSpec
from simulator.usecase.run_backtest import RunBacktestRequest


def _field_names(dataclass_type) -> "set[str]":
    return {f.name for f in dataclasses.fields(dataclass_type)}


def test_symbol_table_covers_exactly_the_symbol_spec_fields():
    assert set(SYMBOL_FIELD_SOURCES) == _field_names(SymbolSpec)


def test_account_table_fields_have_a_destination_on_the_run_request():
    """口座属性の供給には、それを受ける口座属性の面（``RunBacktestRequest``）がある。

    行き先が無い供給は「引けるが誰にも届かない」状態であり、消費側が既定値で埋める形へ
    静かに戻り得る（ISSUE-445 RC-1）。**空の表で自明に成立させない**ため非空も要求する。
    """
    assert ACCOUNT_FIELD_SOURCES
    missing = sorted(set(ACCOUNT_FIELD_SOURCES) - _field_names(RunBacktestRequest))
    assert not missing, f"RunBacktestRequest が受け取らない口座属性: {missing}"


def test_the_two_tables_do_not_both_claim_the_same_field():
    """銘柄仕様と口座属性の受け口が重ならない（同じ名前が 2 つの契約に属さない）。"""
    assert set(SYMBOL_FIELD_SOURCES).isdisjoint(set(ACCOUNT_FIELD_SOURCES))
    assert _field_names(SymbolSpec).isdisjoint(set(ACCOUNT_FIELD_SOURCES))


def test_build_interactor_accepts_the_mapping_table_keys():
    """合成ビューの全キーがそのまま ``build_interactor`` の引数名である（``**spec`` 展開の前提）。

    ここだけは**合成ビュー**が対象である。``load_spec_fields`` はこの 8 キーを返し、
    本番・検定の 20 か所超が ``build_interactor(**load_spec_fields(...))`` で展開する。
    """
    import inspect

    from simulator.main import build_interactor

    parameters = set(inspect.signature(build_interactor).parameters)
    missing = sorted(set(SPEC_FIELD_SOURCES) - parameters)
    assert not missing, f"build_interactor が受け取らないキー: {missing}"
