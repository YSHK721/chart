"""spread_point — 系列の spread 列を数える point の読み口（ISSUE-511 段階 3 前提 (a)）。

point を呼出ごとの引数ではなく**系列ごとの台帳属性**にする。どのスナップショットの point で数えるかは
台帳（``marketdata.dataset_registry`` の記述子欄 ``spread_point_snapshot``）が、値はスナップショット
（``marketdata.symbol_spec_snapshot``）が持つ。本モジュールは両者をつなぐだけで値を持たない。

書き手（M1 の spread 列）と読み手（simulator の約定 ask = bid + spread × point_size）の point は
同じ源でなければならない。読み手の point_size はスナップショット由来なので、書き手も同じ表の同じ
項目（``marketdata.symbol_spec_snapshot.SYMBOL_FIELD_SOURCES`` の point_size）を読む。

依存方向: ``marketdata.dataset_registry`` と ``marketdata.symbol_spec_snapshot`` のみ（pandas も
tick_m1 も知らない）。宣言は marketdata/tests/test_module_dependency_declarations.py が AST 走査で
強制する。スナップショットの読込は ``marketdata.symbol_spec_snapshot`` の関数をモジュール属性経由で
呼ぶ（計算量検定の Test Spy の継ぎ目）。
"""
from __future__ import annotations

from functools import lru_cache

from marketdata import dataset_registry as _dataset_registry
from marketdata import symbol_spec_snapshot as _symbol_spec_snapshot

#: 銘柄仕様側の項目名（MT5 のフィールド名ではない。MT5 名は SYMBOL_FIELD_SOURCES の中にだけ在る）。
_POINT_FIELD = "point_size"


def spread_point_of(ref: "str | None") -> "float | None":
    """``ref`` の spread 列を数える point を返す。宣言の無い ref・台帳に無い ref は ``None``。

    宣言の無い ref ではスナップショットを読まない（使わない point を読まない）。

    Raises:
        marketdata.symbol_spec_snapshot.SnapshotError: 宣言したスナップショットが読めない・point が
            引けない。**既定値へ落とさない**（落とすと別銘柄の point で数えた spread が形式上正しい
            値として CSV に残り、状態検証では検出できない）。
    """
    pair = _dataset_registry.spread_point_snapshot_of(ref)
    if pair is None:
        return None
    return _point_size_of_snapshot(*pair)


@lru_cache(maxsize=None)
def _point_size_of_snapshot(server: str, symbol: str) -> float:
    """スナップショット ``(server, symbol)`` の point_size（同じ組は 1 回だけ読む）。

    読込に失敗した組はキャッシュされない（lru_cache は例外を記憶しない）。
    """
    return _symbol_spec_snapshot.load_symbol_field(server, symbol, _POINT_FIELD)


__all__ = ["spread_point_of"]
