"""確定素材のディスク持ち越し（ISSUE-501 段階 2）の検定。

計算量テスト（CLAUDE.md 絶対命令）: 固定するのは**無駄の不在**——
「再起動（＝新しいストア実体）後、epoch 一致の素材に対する factory の発行 = 0」。
回数そのものは焼き込まず、発行 − 使用 = 0 の形で表明する。
"""
from __future__ import annotations

import pickle

import pytest

from dashboard_ui.adapter.gateway.material_store import MaterialStore
from dashboard_ui.adapter.gateway.persistent_material_store import (
    PersistentMaterialStore,
)


def _store(tmp_path) -> PersistentMaterialStore:
    return PersistentMaterialStore(MaterialStore(), spill_dir=tmp_path / "spill")


class _Factory:
    """Test Spy: 発行回数を数える素材工場。"""

    def __init__(self, value) -> None:
        self.value = value
        self.calls = 0

    def __call__(self):
        self.calls += 1
        return self.value


_KEY = ("jp225_tick", "1D")
_NAME = ("ma_marod", "default", '{"length": 50}')
_EPOCH = (1757116800, 2000, 1585353600, 66195.051)


@pytest.mark.parametrize("payload", [
    {"rsi": ((1, 2.5), (2, 3.5))},                      # 系列の実形
    {"a": [1, 2, 3], "b": None, "c": "x"},              # 入れ子プリミティブ
])
def test_a_restarted_store_issues_no_factory_for_an_unchanged_epoch(
    tmp_path, payload
) -> None:
    """発行した再計算 − 素材の変化（0 件）= 0（再起動を新実体で模擬・payload 2 形で不変）。"""
    # Arrange: 旧プロセスが素材を 1 回作る。
    before = _Factory(payload)
    assert _store(tmp_path).material(
        key=_KEY, epoch=_EPOCH, name=_NAME, factory=before
    ) == payload
    assert before.calls == 1

    # Act: 再起動（新しいストア実体・同じディスク）。
    after = _Factory(payload)
    recalled = _store(tmp_path).material(
        key=_KEY, epoch=_EPOCH, name=_NAME, factory=after
    )

    # Assert: 素材は同一・再計算の発行は 0。
    assert recalled == payload
    assert after.calls == 0


def test_an_advanced_epoch_recomputes_and_replaces_the_spill(tmp_path) -> None:
    """epoch が進んだら必ず再計算する（古い素材を配らない）——そして新版が持ち越される。"""
    # Arrange
    _store(tmp_path).material(
        key=_KEY, epoch=_EPOCH, name=_NAME, factory=_Factory({"v": 1})
    )

    # Act: 版が進んだ再起動。
    fresh = _Factory({"v": 2})
    first = _store(tmp_path).material(
        key=_KEY, epoch=(*_EPOCH[:-1], 66200.0), name=_NAME, factory=fresh
    )
    again = _Factory({"v": 2})
    second = _store(tmp_path).material(
        key=_KEY, epoch=(*_EPOCH[:-1], 66200.0), name=_NAME, factory=again
    )

    # Assert: 進んだ版は 1 回だけ作られ、次の再起動では発行 0。
    assert first == {"v": 2} and second == {"v": 2}
    assert fresh.calls == 1
    assert again.calls == 0


def test_a_corrupt_spill_falls_back_to_the_factory(tmp_path) -> None:
    store = _store(tmp_path)
    store.material(key=_KEY, epoch=_EPOCH, name=_NAME, factory=_Factory({"v": 1}))
    spill_file = next((tmp_path / "spill").glob("*.pkl"))
    spill_file.write_bytes(b"broken")

    fallback = _Factory({"v": 1})
    recalled = _store(tmp_path).material(
        key=_KEY, epoch=_EPOCH, name=_NAME, factory=fallback
    )

    assert recalled == {"v": 1}
    assert fallback.calls == 1


def test_a_non_plain_payload_stays_in_process_only(tmp_path) -> None:
    """クラス実体はディスクへ書かない（コード改版で形が黙ってずれる持ち越しを作らない）。"""
    import numpy as np

    store = _store(tmp_path)
    value = store.material(
        key=_KEY, epoch=_EPOCH, name=_NAME, factory=_Factory(np.array([1.0]))
    )
    assert value.tolist() == [1.0]
    assert list((tmp_path / "spill").glob("*.pkl")) == []

    # 同一プロセス内（内側ストア）は従来どおり共有される。
    inner_hit = _Factory(np.array([9.9]))
    shared = store.material(key=_KEY, epoch=_EPOCH, name=_NAME, factory=inner_hit)
    assert shared.tolist() == [1.0]
    assert inner_hit.calls == 0


def test_a_mismatched_envelope_is_ignored(tmp_path) -> None:
    """(key, name, epoch) のどれかが違う封筒は読まない（ハッシュ衝突・形式版ずれ耐性）。"""
    store = _store(tmp_path)
    store.material(key=_KEY, epoch=_EPOCH, name=_NAME, factory=_Factory({"v": 1}))
    spill_file = next((tmp_path / "spill").glob("*.pkl"))
    spill_file.write_bytes(
        pickle.dumps((999, _KEY, _NAME, _EPOCH, {"v": "偽"}))   # 形式版が違う
    )

    fallback = _Factory({"v": 1})
    recalled = _store(tmp_path).material(
        key=_KEY, epoch=_EPOCH, name=_NAME, factory=fallback
    )

    assert recalled == {"v": 1}
    assert fallback.calls == 1
