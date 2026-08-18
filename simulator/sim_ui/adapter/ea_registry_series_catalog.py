"""A-EaRegistrySeriesCatalog: ea_name → 指標レジストリの登録系列名（E-3・§12.5）。

**単一ソース**: 注入された指標構築関数（束縛は `simulator.main.build_ea_indicators`）を
**実際に呼んで**登録系列を得る。ea_name → 系列名の対応表を本モジュールに書き写さない。
§12.1 が「戦略ごとの明示指定リストのハードコード」を禁じているのに加え、書き写した表は
登録表が増えた時に必ず取り残される（本リポジトリで繰り返し起きている壊れ方）。

依存の向き（ISSUE-405 の是正）: 構築関数は**注入**で受ける。以前は
``from simulator.main import _EA_FACTORIES, _factory_tc24051901`` で私有名を越境 import し、
``_EA_FACTORIES.get(ea_name, _factory_tc24051901)`` という**選択規則そのものを書き写して**
いた。規則の所有者は `simulator.main._select_ea_factory` の 1 箇所であり、公開アクセサ
（`build_ea_indicators`）がそこへ委譲する。束ねるのは Composition Root（R-4 と同型）。

探索用データセットの用意は :class:`EaBuildProbe`（同 adapter）が持つ。SL 設定カタログと
同じ段であり、ここに書くと 2 箇所に写る。

系列名の取り出しは `IndicatorPort` の**公開されたエラー契約**を使う。未登録名を `get` すると
`IndicatorBufferError` が ``context={"name": ..., "available": [...]}`` を伴って送出される。
私有属性（``_series``）を覗かずに済む。

fail-safe: 探索に失敗したら空集合を返す（＝sizing 不可として受付時に拒否される）。
黙って通して誤った発注量で走らせるより、拒否して気付かせる。
"""
from __future__ import annotations

from typing import Any

from simulator.sim_ui.adapter.ea_build_probe import EaBuildProbe
from simulator.sim_ui.usecase.job_ports import IndicatorSeriesCatalogPort

_PROBE_NAME = "__sim_ui_probe_missing_series__"


class EaRegistrySeriesCatalog(IndicatorSeriesCatalogPort):
    """EA の指標レジストリを実際に組み立てて登録系列名を得るカタログ。"""

    def __init__(self, probe: EaBuildProbe) -> None:
        """``probe``: 指標レジストリを組む :class:`EaBuildProbe`（**必須**）。

        既定値を置かないのは R-4 と同型（既定束縛があると adapter → main の外向き依存が
        復活する）。束縛は Composition Root が持つ。
        """
        self._probe = probe
        self._cache: "dict[str, frozenset[str]]" = {}

    def series_for(self, ea_name: str) -> "frozenset[str]":
        """登録系列名の集合を返す。探索できないときは空集合（fail-safe）。"""
        if ea_name in self._cache:
            return self._cache[ea_name]
        try:
            series = _series_names(self._probe.for_ea(ea_name))
        except Exception:
            series = frozenset()
        self._cache[ea_name] = series
        return series


def _series_names(registry: Any) -> "frozenset[str]":
    """registry の登録系列名を公開エラー契約（`available`）から取り出す。"""
    from simulator.domain.exceptions import IndicatorBufferError

    try:
        registry.get(_PROBE_NAME)
    except IndicatorBufferError as exc:
        return frozenset(exc.context.get("available", ()))
    raise RuntimeError("未登録系列の参照が IndicatorBufferError にならなかった")
