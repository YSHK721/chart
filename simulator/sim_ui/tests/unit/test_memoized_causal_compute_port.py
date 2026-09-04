"""計算源ロードの記憶（`MemoizedSourceLoadPort`・adapter）の単体検定。

対象の移行（ISSUE-479 Wave2b 削除2）:
    本ファイルは元々 ``MemoizedCausalComputePort``（全メソッド委譲 Decorator）を対象に
    していた。旧名クラスは承認済み削除で消えたため、**記憶の振る舞いそのもの**を
    記憶規則の唯一の実体である `MemoizedSourceLoadPort` へ移した。規則 1・2・4 は
    そのまま移行できる（記憶は元々こちらが持っており、旧名クラスは委譲していただけ）。

    移行できず撤去した検定と、その性質の行き先:
      - 「明示していない面も内側へ委譲される」（catch-all 委譲）: 対象消滅。
        **むしろ逆の性質**が正しい仕様であり、
        test_causal_compute_ports_composition.py が
        「宣言していない面は AttributeError で落ちる」として固定する。
      - 「compute / bar_time 等が委譲される」「CausalComputePort として通る」: 対象消滅。
        6 面の結線と LSP は同ファイルの合成（CausalComputePorts）側で固定済みである。

固定する規則（契約改訂裁定 B）:
    1. ``load_source`` は (ref, timeframe, csv_mtime) を鍵に記憶する。
    2. **鍵が変われば読み直す**（mtime が動いた＝源が更新された、を見落とさない）。
    4. **キャッシュ汚染が起きない**: 消費側（`causal_compute`）は必ず
       `reveal_clock.truncate` を通し、truncate は毎回**新しい dict の新しい list** を
       返す（reveal_clock.py:20-22）。その複製を書き換えても記憶した列は変わらない。

方式: フェイクの内側 Port と参照実装 `reveal_clock.truncate`。実データも
indicator_ui も触らない。
"""
from __future__ import annotations

import importlib

import pytest

from simulator.replay_ui.domain.reveal_clock import truncate
from simulator.sim_ui.adapter.causal_compute_ports import MemoizedSourceLoadPort

_BARS = [
    {"time": 100, "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0},
    {"time": 160, "open": 2.0, "high": 2.0, "low": 2.0, "close": 2.0},
]


class _FakeInner:
    """内側のロード面（呼ばれ方だけを記録する）。"""

    def __init__(self) -> None:
        self.load_calls: "list[tuple[str, str | None]]" = []

    def load_source(self, ref, timeframe):
        self.load_calls.append((ref, timeframe))
        return [dict(b) for b in _BARS]


def _port(inner=None, mtime=1.0) -> MemoizedSourceLoadPort:
    return MemoizedSourceLoadPort(
        inner=inner or _FakeInner(), mtime_of=lambda _ref: mtime
    )


# --- 1. 記憶（規則 1）------------------------------------------------------

def test_同じ鍵なら2回目は読み直さない() -> None:
    # Arrange
    inner = _FakeInner()
    port = _port(inner)
    # Act
    first = port.load_source("jp225", "5m")
    second = port.load_source("jp225", "5m")
    # Assert
    assert inner.load_calls == [("jp225", "5m")]
    assert second is first
    assert (port.hits, port.misses) == (1, 1)


def test_値は内側の返り値と同じ() -> None:
    # Arrange
    port = _port()
    # Act
    bars = port.load_source("jp225", "5m")
    # Assert
    assert [b["time"] for b in bars] == [100, 160]


def test_timeframeが違えば別に読む() -> None:
    # Arrange
    inner = _FakeInner()
    port = _port(inner)
    # Act
    port.load_source("jp225", "5m")
    port.load_source("jp225", "1m")
    # Assert
    assert inner.load_calls == [("jp225", "5m"), ("jp225", "1m")]


def test_refが違えば別に読む() -> None:
    # Arrange
    inner = _FakeInner()
    port = _port(inner)
    # Act
    port.load_source("jp225", "5m")
    port.load_source("jp225_tick", "5m")
    # Assert
    assert len(inner.load_calls) == 2


# --- 2. 源の更新を見落とさない（規則 2）------------------------------------

def test_mtimeが変われば読み直す() -> None:
    """源 CSV が更新されたのに古い列を配ると、供給が黙って過去の値になる。"""
    # Arrange
    inner = _FakeInner()
    clock = {"mtime": 1.0}
    port = MemoizedSourceLoadPort(inner=inner, mtime_of=lambda _ref: clock["mtime"])
    # Act
    port.load_source("jp225", "5m")
    clock["mtime"] = 2.0
    port.load_source("jp225", "5m")
    # Assert
    assert len(inner.load_calls) == 2


def test_mtimeが不明でも記憶はする() -> None:
    """境界値: mtime 解決不能（None）。鍵の一部として「不明」を保持する。"""
    # Arrange
    inner = _FakeInner()
    port = MemoizedSourceLoadPort(inner=inner, mtime_of=lambda _ref: None)
    # Act
    port.load_source("jp225", "5m")
    port.load_source("jp225", "5m")
    # Assert
    assert len(inner.load_calls) == 1


# --- 3. 旧名クラスは存在しない（ISSUE-479 Wave2b 削除2）---------------------

def test_旧名クラスのモジュールは解決しない() -> None:
    """記憶規則の実体は 1 つ。第 2 の入口が復活していないことを固定する。"""
    # Arrange
    module = "simulator.sim_ui.adapter.memoized_causal_compute_port"
    # Act / Assert
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module(module)


# --- 4. キャッシュ汚染なし（規則 4）---------------------------------------

def test_truncateの結果を書き換えても記憶は汚れない() -> None:
    """裁定 B の根拠そのもの。消費側は必ず truncate を通り、truncate は新 dict を返す。"""
    # Arrange
    port = _port()
    bars = truncate(port.load_source("jp225", "5m"), None)
    # Act（消費側が窓を加工する＝バーの中身も列も書き換える）
    bars[0]["close"] = 999.0
    bars.append({"time": 999})
    # Assert
    again = port.load_source("jp225", "5m")
    assert [b["time"] for b in again] == [100, 160]
    assert again[0]["close"] == 1.0


def test_truncateは新しいdictを返す() -> None:
    """参照実装（reveal_clock.py:20-22）の性質を検定として固定する。"""
    # Arrange
    port = _port()
    source = port.load_source("jp225", "5m")
    # Act
    copied = truncate(source, None)
    # Assert
    assert copied == source
    assert all(a is not b for a, b in zip(copied, source))


def test_案iの窓は毎回truncateを通るので記憶を共有できる() -> None:
    """until を変えても、記憶した列そのものは書き換わらない。"""
    # Arrange
    port = _port()
    # Act
    first = truncate(port.load_source("jp225", "5m"), 100)
    first[0]["close"] = 999.0
    second = truncate(port.load_source("jp225", "5m"), 160)
    # Assert
    assert [b["close"] for b in second] == [1.0, 2.0]
