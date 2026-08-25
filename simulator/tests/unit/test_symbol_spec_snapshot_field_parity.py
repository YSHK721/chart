"""スナップショット対応表と ``SymbolSpec`` のフィールド名が一致することを固定する。

由来: ISSUE-445 恒久策 **段階 2**（``.doc/SYMBOL_SPEC_SUPPLY_BASIC_DESIGN.md`` §3.2）。

なぜ ``marketdata`` 側ではなくここに置くか（依存方向）:
    ``marketdata`` は最下層（依存ゼロ）であり ``simulator`` を知らない。よって
    「対応表のキー集合 == ``simulator.usecase.models.SymbolSpec`` のフィールド名集合」という
    突合は ``simulator`` 側の検定が持つ（依存の向きは simulator → marketdata の 1 方向のまま）。

何を防ぐか:
    ``SymbolSpec`` にフィールドが増減したとき、対応表が追随しないまま
    ``build_interactor(**load_spec_fields(...))`` が ``TypeError`` になる（あるいは供給漏れが
    既定値で埋まる）事態を、実行時ではなく検定時に赤にする。
"""
from __future__ import annotations

import dataclasses

from marketdata.symbol_spec_snapshot import SPEC_FIELD_SOURCES
from simulator.usecase.models import SymbolSpec


def test_mapping_table_covers_exactly_the_symbol_spec_fields():
    expected = {f.name for f in dataclasses.fields(SymbolSpec)}
    assert set(SPEC_FIELD_SOURCES) == expected


def test_build_interactor_accepts_the_mapping_table_keys():
    """8 キーがそのまま ``build_interactor`` の引数名である（``**spec`` 展開の前提）。"""
    import inspect

    from simulator.main import build_interactor

    parameters = set(inspect.signature(build_interactor).parameters)
    missing = sorted(set(SPEC_FIELD_SOURCES) - parameters)
    assert not missing, f"build_interactor が受け取らないキー: {missing}"
