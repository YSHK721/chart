"""指標ごとの増分器レジストリ（ISSUE-233・内部設計_latest増分計算.md §5.4）。

``LatestMeta.incremental`` が持つ名前 → 増分器実体の解決だけを行う。増分器の追加は
**2 箇所**の宣言で完結する（``latest_dispatch`` / ``incremental_state`` は改変しない＝OCP）:

  1. 本モジュールの ``_FACTORIES`` へ 1 行（名前 → factory）
  2. ``call_binding._TABLE`` の当該指標の ``latest_meta`` へ増分器名

かつて本 docstring は「``_FACTORIES`` へ 1 行足すだけ」と述べていたが、2 を忘れると
``resolve()`` が ``None`` を返し、**例外を出さずに従来の重い経路へ黙って縮退**する
（無言の性能退行・テストは緑のまま）。この 2 宣言の集合一致は
``tests/test_incremental_registry_declaration.py`` が強制する（ISSUE-262）。
実体の import は遅延（指標 src のロードを起動時に走らせない）。

各増分器は指標 src の **公開関数のみ**を呼ぶ。計算式を写した時点で参照実装との二重定義に
なり、ISSUE-233 の再発源になる。
"""

from __future__ import annotations

from typing import Any, Callable


def _moving_averages() -> Any:
    from adapter.compute.incremental.moving_averages import MovingAveragesIncrementer

    return MovingAveragesIncrementer()


def _btlm_trail() -> Any:
    from adapter.compute.incremental.btlm_trail import BtlmTrailIncrementer

    return BtlmTrailIncrementer()


def _ma_marod() -> Any:
    from adapter.compute.incremental.marod import MarodIncrementer, _MovingAverageBaseline

    return MarodIncrementer(_MovingAverageBaseline(), "ma_marod")


def _btlm_trail_marod() -> Any:
    from adapter.compute.incremental.marod import MarodIncrementer, _TrendLineBaseline

    return MarodIncrementer(_TrendLineBaseline(), "btlm_trail_marod")


def _profit_rsi() -> Any:
    from adapter.compute.incremental.profit_rsi import ProfitRsiIncrementer

    return ProfitRsiIncrementer()


def _tickvol() -> Any:
    from adapter.compute.incremental.tickvol import TickvolIncrementer

    return TickvolIncrementer()


# 名前 → 増分器 factory（遅延生成・生成後はプロセス内で使い回す）。
_FACTORIES: dict[str, Callable[[], Any]] = {
    "moving_averages": _moving_averages,
    "btlm_trail": _btlm_trail,
    "ma_marod": _ma_marod,
    "btlm_trail_marod": _btlm_trail_marod,
    "tickvol": _tickvol,
    "profit_rsi": _profit_rsi,
}

_INSTANCES: dict[str, Any] = {}


def resolve(name: "str | None") -> Any:
    """名前から増分器を解決する。未登録・None は ``None``（従来経路へ落ちる）。"""
    if not name:
        return None
    if name in _INSTANCES:
        return _INSTANCES[name]
    factory = _FACTORIES.get(name)
    if factory is None:
        return None
    instance = factory()
    _INSTANCES[name] = instance
    return instance
