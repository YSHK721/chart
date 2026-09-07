"""σ スパン供給の**計算量テスト**（CLAUDE.md 絶対命令 2026-08-28・ISSUE-502 D-5）。

測るのは時間ではなく**回数**である。状態検証（出力の正しさ）は姉妹テストが担い、本
テストは「作ってから捨てる」欠陥＝**出力に使われない計算の発行**を Test Spy で数えて
遮断する（インタラクションテスト／behaviour verification）。

固定する 2 点（いずれも回数そのものは期待値に焼き込まない）:
    1. 無駄の不在: ``発行したスパン − 出力に使ったスパン = 0``。
       * 配列レベル: ``rolling_span`` / ``oscillator_span`` が返した配列は 1 つ残らず
         採点ループに読まれる（作って捨てる配列が 0）。
       * 要素レベル: 算出された（有限な）スパン値は 1 つ残らず読まれる（末尾 1 点しか
         使わないのに全長を算出する、といった浪費が 0）。
    2. オーダーの表明: 入力（バー数 n）を増やしても、また窓長 W を変えても、発行数
       （スパン配列の本数・累積掃引の回数）は**増えない**。窓・バー数の 2 点以上で固定する。

複製解消（span_stats への集約）が発行回数を増やしていないことも、同じ検定で担保される。
"""

from __future__ import annotations

import numpy as np

# import 解決は台帳（tools/dev_paths.txt）由来の pythonpath が担う。テスト側で sys.path を
# 改変しない（改変するとプロダクトとモジュール同一性が食い違う）。``indigators/`` は
# pyproject.toml ``[tool.pytest.ini_options] pythonpath`` と venv の
# ``jp225_chart_paths.pth`` の双方に登録済み。
from profit_rmm_macd.src import core
from profit_system import span_stats


# ---------------------------------------------------------------------------
# Test Spy: 「発行」と「使用」を数える装置
# ---------------------------------------------------------------------------
class _TrackedSpan(np.ndarray):
    """読まれた index を記録するスパン配列（使用側の計測）。"""

    def __new__(cls, values: np.ndarray) -> "_TrackedSpan":
        obj = np.asarray(values, dtype=np.float64).view(cls)
        obj.read_indices = set()
        return obj

    def __array_finalize__(self, obj) -> None:
        if obj is None:
            return
        self.read_indices = getattr(obj, "read_indices", set())

    def __getitem__(self, key):
        if isinstance(key, (int, np.integer)):
            self.read_indices.add(int(key))
        return super().__getitem__(key)


class _SpanSpy:
    """``rolling_span`` / ``oscillator_span`` の発行を数える Spy（委譲は本物へ）。"""

    def __init__(self) -> None:
        self.rolling_calls: list[dict] = []
        self.scalar_calls: int = 0

    def rolling(self, x, window, *, clamp, freeze_last=False):
        out = span_stats.rolling_span(
            x, window, clamp=clamp, freeze_last=freeze_last
        )
        tracked = _TrackedSpan(out)
        self.rolling_calls.append({"window": window, "array": tracked})
        return tracked

    def scalar(self, x, *, clamp):
        self.scalar_calls += 1
        return span_stats.oscillator_span(x, clamp=clamp)


def _ohlcv(n: int, seed: int = 20260906):
    rng = np.random.default_rng(seed)
    close = 100.0 + np.cumsum(rng.normal(0.0, 1.0, n))
    high = close + np.abs(rng.normal(0.0, 0.5, n))
    low = close - np.abs(rng.normal(0.0, 0.5, n))
    volume = np.abs(rng.normal(1000.0, 100.0, n))
    return high, low, close, volume


def _run_with_spy(monkeypatch, n: int, window: int | None) -> _SpanSpy:
    spy = _SpanSpy()
    monkeypatch.setattr(core, "rolling_span", spy.rolling)
    monkeypatch.setattr(core, "oscillator_span", spy.scalar)
    core.compute_rmmmacd(*_ohlcv(n), window=window)
    return spy


# ---------------------------------------------------------------------------
# 1. 無駄の不在（発行 − 使用 = 0）
# ---------------------------------------------------------------------------
def test_every_issued_span_array_is_consumed(monkeypatch) -> None:
    """発行したスパン配列 − 採点に使った配列 = 0（作って捨てる配列が無い）。"""
    # Arrange / Act
    spy = _run_with_spy(monkeypatch, n=400, window=120)

    # Assert
    issued = len(spy.rolling_calls)
    assert issued > 0, "スパンが 1 本も発行されていない（Spy 未結線）"
    used = sum(1 for c in spy.rolling_calls if c["array"].read_indices)
    assert issued - used == 0, f"発行 {issued} 本 − 使用 {used} 本 != 0（作って捨てている）"


def test_every_computed_span_value_is_consumed(monkeypatch) -> None:
    """算出した（有限な）スパン値 − 読まれた値 = 0（要素レベルの浪費が無い）。"""
    # Arrange / Act
    spy = _run_with_spy(monkeypatch, n=400, window=120)

    # Assert
    for call in spy.rolling_calls:
        arr = np.asarray(call["array"], dtype=np.float64)
        finite_idx = set(np.flatnonzero(np.isfinite(arr)).tolist())
        read_idx = call["array"].read_indices
        assert finite_idx, "有限なスパン値が 0 件（前提の崩れ）"
        assert not (finite_idx - read_idx), (
            "算出したが読まれなかったスパン値がある: "
            f"{sorted(finite_idx - read_idx)[:10]}"
        )
        # 非有限（warm-up）スロットは「算出していない」ため使用の対象外。読まれ方は
        # 採点分岐の短絡に依存するので固定しない（実装詳細を焼き込まない）。


def test_batch_path_issues_no_more_spans_than_the_causal_path(monkeypatch) -> None:
    """全期間バッチ（window=None）でもスパンの発行 − 使用 = 0。

    使用本数は「無駄の不在を別途実測済み」の因果経路（発行＝使用）の本数を基準にする
    （リテラルの回数を期待値に焼き込まない）。
    """
    # Arrange
    causal = _run_with_spy(monkeypatch, n=400, window=120)
    used = sum(1 for c in causal.rolling_calls if c["array"].read_indices)

    # Act
    batch = _run_with_spy(monkeypatch, n=400, window=None)

    # Assert: 因果版のローリング発行は 0、スカラ発行は採点に使われた本数と一致
    assert batch.rolling_calls == []
    assert batch.scalar_calls - used == 0


# ---------------------------------------------------------------------------
# 2. オーダーの表明（入力・窓を変えても発行が増えない）
# ---------------------------------------------------------------------------
def test_span_issuance_does_not_grow_with_bar_count(monkeypatch) -> None:
    """バー数を倍にしてもスパンの発行本数は増えない（n に比例しない）。"""
    # Arrange / Act
    small = _run_with_spy(monkeypatch, n=200, window=120)
    large = _run_with_spy(monkeypatch, n=400, window=120)

    # Assert（回数の絶対値ではなく「増えないこと」を固定する）
    assert len(large.rolling_calls) == len(small.rolling_calls)


def test_span_issuance_does_not_grow_with_window(monkeypatch) -> None:
    """窓長を変えてもスパンの発行本数は増えない（W に比例しない）。"""
    # Arrange / Act
    narrow = _run_with_spy(monkeypatch, n=400, window=30)
    wide = _run_with_spy(monkeypatch, n=400, window=120)

    # Assert
    assert len(wide.rolling_calls) == len(narrow.rolling_calls)


class _CountingNumpy:
    """``span_stats`` が使う numpy への薄い委譲。累積掃引の発行回数だけ数える。"""

    def __init__(self) -> None:
        self.cumsum_calls = 0

    def cumsum(self, *args, **kwargs):
        self.cumsum_calls += 1
        return np.cumsum(*args, **kwargs)

    def __getattr__(self, name: str):
        return getattr(np, name)


def _count_sweeps(monkeypatch, n: int, window: int) -> int:
    counter = _CountingNumpy()
    monkeypatch.setattr(span_stats, "np", counter)
    span_stats.rolling_span(np.arange(n, dtype=np.float64), window, clamp=True)
    return counter.cumsum_calls


def test_rolling_span_sweeps_do_not_grow_with_window(monkeypatch) -> None:
    """累積掃引の発行回数は窓長に依存しない（窓ごとの再走査 = O(n·W) を禁止）。"""
    # Arrange / Act
    narrow = _count_sweeps(monkeypatch, n=1000, window=10)
    wide = _count_sweeps(monkeypatch, n=1000, window=500)

    # Assert
    assert narrow > 0, "掃引が数えられていない（Spy 未結線）"
    assert wide == narrow


def test_rolling_span_sweeps_do_not_grow_with_bar_count(monkeypatch) -> None:
    """累積掃引の発行回数はバー数にも依存しない（掃引は入力長で増えない）。"""
    # Arrange / Act
    small = _count_sweeps(monkeypatch, n=500, window=60)
    large = _count_sweeps(monkeypatch, n=2000, window=60)

    # Assert
    assert large == small


def test_rolling_span_emits_exactly_the_causal_values(monkeypatch) -> None:
    """出力の有限要素数 = 因果的に算出可能なバー数（余剰に作らない・足りなくもない）。"""
    # Arrange
    n, window = 500, 60
    x = np.arange(n, dtype=np.float64)

    # Act
    out = span_stats.rolling_span(x, window, clamp=False)

    # Assert（warm-up を除く全バーちょうど。多くも少なくもない）
    assert int(np.isfinite(out).sum()) == n - window + 1
