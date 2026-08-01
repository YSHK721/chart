"""指標ごとの増分器レジストリ（ISSUE-233・内部設計_latest増分計算.md §5.4）。

``LatestMeta.incremental`` が持つ名前 → 増分器実体の解決だけを行う。増分器の追加は
``_FACTORIES`` へ 1 行足すだけで、``latest_dispatch`` / ``incremental_state`` は改変しない
（OCP）。実体の import は遅延（指標 src のロードを起動時に走らせない）。

各増分器は指標 src の **公開関数のみ**を呼ぶ。計算式を写した時点で参照実装との二重定義に
なり、ISSUE-233 の再発源になる。
"""

from __future__ import annotations

from typing import Any, Callable


# 名前 → 増分器 factory（遅延生成・生成後はプロセス内で使い回す）。
#   指標ごとの増分器は本表への 1 行追加だけで載る（latest_dispatch / incremental_state は不変）。
_FACTORIES: dict[str, Callable[[], Any]] = {}

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
