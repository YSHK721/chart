"""MemoizedCausalComputePort（計算源ロードの記憶・adapter）の単体検定。

固定する規則（契約改訂裁定 B）:
    1. ``load_source`` は (ref, timeframe, csv_mtime) を鍵に記憶する。
    2. **鍵が変われば読み直す**（mtime が動いた＝源が更新された、を見落とさない）。
    3. **式（compute）にも因果規約（truncate）にも触れない**。他のメソッドはすべて委譲する。
    4. **キャッシュ汚染が起きない**: 消費側（`causal_compute`）は必ず
       `reveal_clock.truncate` を通し、truncate は毎回**新しい dict の新しい list** を
       返す（reveal_clock.py:20-22）。その複製を書き換えても記憶した列は変わらない。
    5. `CausalComputePort` として差し替え可能（LSP）。

方式: フェイクの内側 Port と参照実装 `reveal_clock.truncate`。実データも
indicator_ui も触らない。
"""
from __future__ import annotations

from simulator.replay_ui.domain.reveal_clock import truncate
from simulator.sim_ui.adapter.memoized_causal_compute_port import (
    MemoizedCausalComputePort,
)

_BARS = [
    {"time": 100, "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0},
    {"time": 160, "open": 2.0, "high": 2.0, "low": 2.0, "close": 2.0},
]


class _FakeInner:
    """内側の `CausalComputePort`（呼ばれ方だけを記録する）。"""

    def __init__(self) -> None:
        self.load_calls: "list[tuple[str, str | None]]" = []
        self.calls: "list[str]" = []

    def load_source(self, ref, timeframe):
        self.load_calls.append((ref, timeframe))
        return [dict(b) for b in _BARS]

    def bar_time(self, timeframe, unix_sec):
        self.calls.append("bar_time")
        return int(unix_sec)

    def period_start(self, timeframe, unix_sec):
        self.calls.append("period_start")
        return int(unix_sec) - 1

    def causal_series(self, indicator, variant, chart_bars, source_bars, compute_tf,
                      window_bars, params):
        self.calls.append("causal_series")
        return [{"name": indicator}]

    def compute(self, indicator, variant, mode, bars, params):
        self.calls.append("compute")
        return [{"name": indicator, "mode": mode, "bars": len(bars)}]

    def compute_latest_seq(self, indicator, variant, prefix_bars, tails, params):
        self.calls.append("compute_latest_seq")
        return [[{"name": indicator}] for _ in tails]

    def extra_face(self):
        self.calls.append("extra_face")
        return "ok"


def _port(inner=None, mtime=1.0) -> MemoizedCausalComputePort:
    return MemoizedCausalComputePort(
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
    port = MemoizedCausalComputePort(inner=inner, mtime_of=lambda _ref: clock["mtime"])
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
    port = MemoizedCausalComputePort(inner=inner, mtime_of=lambda _ref: None)
    # Act
    port.load_source("jp225", "5m")
    port.load_source("jp225", "5m")
    # Assert
    assert len(inner.load_calls) == 1


# --- 3. 全メソッド委譲（規則 3・5）----------------------------------------

def test_computeは委譲される() -> None:
    """式には触れない（記憶するのは源のロードだけ）。"""
    # Arrange
    inner = _FakeInner()
    port = _port(inner)
    # Act
    first = port.compute("ma", "default", "full", _BARS, {})
    second = port.compute("ma", "default", "full", _BARS, {})
    # Assert
    assert inner.calls == ["compute", "compute"]   # compute は記憶しない
    assert first == second


def test_その他のPort面も委譲される() -> None:
    # Arrange
    inner = _FakeInner()
    port = _port(inner)
    # Act
    port.bar_time("1D", 100)
    port.period_start("1D", 100)
    port.causal_series("ma", "default", [], [], "1D", [], {})
    port.compute_latest_seq("ma", "default", [], [[]], {})
    # Assert
    assert inner.calls == [
        "bar_time", "period_start", "causal_series", "compute_latest_seq"
    ]


def test_明示していない面も内側へ委譲される() -> None:
    """Port が増えても穴を空けない。"""
    # Arrange
    inner = _FakeInner()
    port = _port(inner)
    # Act / Assert
    assert port.extra_face() == "ok"


def test_CausalComputePortとして通る() -> None:
    """LSP: 呼び出し側は記憶の有無を知らない。"""
    # Arrange
    from simulator.replay_ui.usecase.replay_ports import CausalComputePort

    # Act / Assert
    assert isinstance(_port(), CausalComputePort)


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
