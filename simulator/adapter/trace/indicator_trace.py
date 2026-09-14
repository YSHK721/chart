"""IndicatorTrace: 何を残すか（足粒度）（adapter 層・RUN_TRACE_BASIC_DESIGN §6.5.2）。

アクター（改訂の動機）: **指標系列をどう残すか**。行粒度（足）も協働相手（指標 registry）も
点粒度（`simulator/adapter/trace/columnar_run_trace.py`）と交わらないため、モジュールを分ける（是正 D-3）。

なぜ点ごとに写さないか（§6.2 の複製禁止・実測 4）:
    指標は足単位（`IndicatorPort.update(bar_index)`）でしか動かない。点ごとに写せば同じ値を
    ティック本数ぶん重複させるだけであり、1 ヶ月・実ティックなら 952,832 行に対して
    実際の異なりは足数（数万）にすぎない。

何を値とするか:
    「**エンジンが実際に読んだ値**」である。戦略は `series.iloc[bar_index]` で系列を位置参照
    する（`adapter/strategy/tc24051901.py:42`）。トレースは run の事実の記録なので、同じ
    引き方で得た値をそのまま残す（別の引き方に置き換えると、記録が run の事実でなくなる）。

**既知の不整合（段階 3 が作る欠陥ではない・§6.5.2）**:
    指標 registry は data_path の**全 CSV** から作られる（`ea_bindings/sources.py:30` に
    窓の適用が無い）一方、`bars` は `marketdata_window` で絞られる。したがって
    `marketdata_window` を伴う run では `bar_index` の指す先が両者で一致しない。これは
    エンジンに既存の性質であり、段階 3 が負う義務は**隠さないこと**だけである
    （成果物 trace_meta.json が `marketdata_window` の有無を記録する）。

列挙できない供給を明示エラーにする理由（§7）:
    空集合で黙って続行すると「指標が無い run だった」という**別の事実**に見える。
    系列 0 本の供給（NullIndicatorRegistry（`simulator/adapter/indicator/null_registry.py`））は正当な run の姿であり、こちらは
    エラーではない——両者を値で区別できる状態を保つ。

依存規律: pandas / pyarrow を import しない（D-5 の隔離）。列は素の `list` で持つ。
"""
from __future__ import annotations

from typing import Any

from simulator.domain.exceptions import ConfigError

#: 鍵の列名（trace_points.parquet の同名列と突き合わせる）。
BAR_INDEX_COLUMN = "bar_index"


class IndicatorTrace:
    """記録された足の並びに対して、登録系列の値を足単位で並べる。"""

    __slots__ = ("_registry",)

    def __init__(self, registry: Any) -> None:
        """``registry``: `names()`（IndicatorSeriesNamesPort（`simulator/usecase/indicator_catalog_ports.py`））と `get(name)`
        （IndicatorPort（`simulator/usecase/ports.py`））を持つ指標供給。

        2 つの Port を 1 つの実体で受けるのは、供給の実体が同一だからである
        （分けて注入すると「名前は A・値は B」という組み合わせが表現でき、
        列名と値の出所が食い違う経路が生まれる）。
        """
        self._registry = registry

    def columns_for(self, recorded_bar_indices: Any) -> "dict[str, list]":
        """記録された `bar_index` の並び → 足単位の列集合。

        事前条件: ``recorded_bar_indices`` は `ColumnarRunTrace.bar_indices()` の戻り
            （記録**順**・重複あり）。
        事後条件: 鍵列は重複を畳んだ**記録順**の `bar_index`。各系列は同じ行数を持つ。
        例外: 系列を列挙できない供給は `ConfigError`（空で黙って続行しない）。
        """
        names = self._names()
        # 重複を畳みつつ記録順を保つ（`dict` は挿入順を保つ＝並べ替え規則を書かない）。
        bars = list(dict.fromkeys(recorded_bar_indices))
        columns: "dict[str, list]" = {BAR_INDEX_COLUMN: bars}
        for name in names:
            # 系列は**1 本につき 1 回**引く。行ごとに引くと出力は同じまま発行だけが
            #   行数倍になる（作ってから捨てる形・ISSUE-450 と同型）。
            series = self._registry.get(name)
            iloc = series.iloc
            columns[name] = [iloc[bar_index] for bar_index in bars]
        return columns

    def _names(self) -> "tuple[str, ...]":
        """登録系列名（IndicatorSeriesNamesPort（`simulator/usecase/indicator_catalog_ports.py`））。列挙できない供給は明示エラー。"""
        names = getattr(self._registry, "names", None)
        if names is None:
            raise ConfigError(
                "指標系列を列挙できない供給です（IndicatorSeriesNamesPort 未実装）。"
                "空の指標トレースを書くと「指標が無い run」に見えるため実行を止めます",
                context={"registry": type(self._registry).__name__},
            )
        return tuple(names())
