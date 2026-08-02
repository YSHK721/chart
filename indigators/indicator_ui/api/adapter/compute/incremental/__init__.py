"""指標ごとの増分器レジストリ（ISSUE-233・内部設計_latest増分計算.md §5.4）。

``LatestMeta.incremental`` が持つ名前 → 増分器実体の解決だけを行う。増分器の追加は
``_FACTORIES`` へ 1 行足すだけで、``latest_dispatch`` / ``incremental_state`` は改変しない
（OCP）。実体の import は遅延（指標 src のロードを起動時に走らせない）。

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
