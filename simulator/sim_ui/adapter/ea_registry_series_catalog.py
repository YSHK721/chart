"""A-EaRegistrySeriesCatalog: ea_name → 指標レジストリの登録系列名（E-3・§12.5）。

**単一ソース**: 注入された指標構築関数（束縛は `simulator.main.build_ea_indicators`）を
**実際に呼んで**登録系列を得る。ea_name → 系列名の対応表を本モジュールに書き写さない。
§12.1 が「戦略ごとの明示指定リストのハードコード」を禁じているのに加え、書き写した表は
登録表が増えた時に必ず取り残される（本リポジトリで繰り返し起きている壊れ方）。

依存の向き（ISSUE-405 の是正）: 構築関数は**注入**で受ける。以前は Composition Root の
私有な登録表とその既定フォールバック用ファクトリを越境 import し、「表を ea_name で引き、
未登録なら既定 TC 経路へ落とす」という**選択規則そのものを書き写して**いた。規則の所有者は
simulator/main/ea_bindings（EA ごとの宣言モジュールを登録した宣言駆動の束縛表）であり、
選択は select_ea_binding の 1 箇所にしか無い。公開アクセサ build_ea_indicators がそこへ
委譲する。束ねるのは Composition Root（R-4 と同型・ISSUE-502 段階 4A で宣言駆動へ移行）。

探索用データセットの用意は :class:`EaBuildProbe`（同 adapter）が持つ。SL 設定カタログと
同じ段であり、ここに書くと 2 箇所に写る。

系列名の取り出しは `names()`（宣言は `simulator/usecase/indicator_catalog_ports.py`・
RUN_TRACE_BASIC_DESIGN §6.3）へ委譲する。是正前は未登録名を get して
IndicatorBufferError（`simulator/domain/exceptions.py`）を組み立て、その ``context["available"]`` を読んで例外を
捨てていた——**名前を列挙するために例外を 1 つ作って捨てる**形であり、同じ一覧を作る規則の
第 2 実装でもあった（設計書 実測 9）。出力（系列名の集合）は正しいままなので状態検証では
原理的に落ちない種類の無駄である（`simulator/tests/unit/test_indicator_series_names.py`
の計算量検定が発行 0 を機械的に固定する）。

既存 `IndicatorSeriesCatalogPort`（`series_for(ea_name)`）は残す: ea_name → registry という
**別の問い**であり、`names()` はその registry に対する問いだからである。

fail-safe: 探索に失敗したら空集合を返す（＝sizing 不可として受付時に拒否される）。
黙って通して誤った発注量で走らせるより、拒否して気付かせる。
"""
from __future__ import annotations

from simulator.sim_ui.adapter.ea_build_probe import EaBuildProbe
from simulator.sim_ui.usecase.job_ports import IndicatorSeriesCatalogPort


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
            series = frozenset(self._probe.for_ea(ea_name).names())
        except Exception:
            series = frozenset()
        self._cache[ea_name] = series
        return series
