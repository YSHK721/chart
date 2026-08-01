"""S1 検証 — moving_averages の増分計算が full と bit 一致することを固定する（ISSUE-233）。

通過条件（内部設計_latest増分計算.md §6.1 / B-5）:
  1. **全系列 max_dev = 0**（浮動小数の完全一致）。latest（増分）の応答が full の末尾 K 点と
     time・value とも完全一致すること。
  2. **足内更新の非破壊性**: 同一の確定状態から形成中バーを変えて 10 回計算し、毎回その
     形成中バーで full 計算した結果と一致すること（§5.3.2 の不変条件を固定する）。
  3. **バー確定の前進**: 窓を 1 本ずつ伸ばして（リプレイの左端固定窓＝limit=bar+1 と同型）
     繰り返し計算し、各時点で full と一致すること。
  4. 増分器が扱えないパラメータ（平滑化あり等）は従来経路へ落ち、挙動が変わらないこと。

参照実装は現行 ``full_compute``（ISSUE-158 で確立した方式）。

import 規約: api/tests/conftest.py が api/ を sys.path へ追加済み。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from adapter.compute import IndicatorComputeAdapter
from adapter.compute import incremental_state
from adapter.compute.latest_dispatch import full_compute, latest_compute
from adapter.compute.latest_meta import latest_meta

_MA_TYPES = ["sma", "ema", "smma", "lwma"]


@pytest.fixture(autouse=True)
def _clean_state():
    """テストごとに状態キャッシュを空にする（テスト間の状態持ち越しを排除）。"""
    incremental_state.reset()
    yield
    incremental_state.reset()


def _ohlcv(n: int = 400, seed: int = 0) -> pd.DataFrame:
    """指数水準の擬似 OHLCV（丸め蓄積が観測できる価格帯・昇順）。"""
    rng = np.random.default_rng(seed)
    close = 39000.0 + np.cumsum(rng.normal(0.0, 12.0, n))
    open_ = np.concatenate([[close[0]], close[:-1]])
    high = np.maximum(open_, close) + np.abs(rng.normal(0.0, 4.0, n))
    low = np.minimum(open_, close) - np.abs(rng.normal(0.0, 4.0, n))
    return pd.DataFrame(
        {
            "open": open_, "high": high, "low": low, "close": close,
            "volume": np.full(n, 1000.0),
        },
        index=pd.date_range("2024-01-01", periods=n, freq="h", name="time"),
    )


def _params(ma_type: str, **overrides) -> dict:
    p = {
        "ma_type": ma_type,
        "length": 24,
        "source": "close",
        "offset": 0,
        "smoothing_type": "none",
        "smoothing_length": 9,
        "bb_stddev": 2.0,
        "wait_for_close": False,
    }
    p.update(overrides)
    return p


def _assert_tail_matches_full(adapter, df, params, k=1, *, min_tail=None, incremental=True):
    """latest（増分）と full の末尾 K 点が完全一致することを確認する。

    ``incremental=True`` は「増分経路が実際に使われたこと」も確認する。増分器が無言で従来経路へ
    落ちると一致検証が素通りしてしまうため、経路の実証を一致検証と同じ場所で固定する。
    """
    full = full_compute(adapter, "moving_averages", "default", df, dict(params))
    latest = latest_compute(
        adapter, "moving_averages", "default", df, dict(params), min_tail=min_tail
    )
    if incremental:
        assert incremental_state.stats()["states"] >= 1, "増分経路が使われていること"
    assert [s["name"] for s in latest] == [s["name"] for s in full]
    for got, want in zip(latest, full):
        assert {kk: vv for kk, vv in got.items() if kk != "data"} == {
            kk: vv for kk, vv in want.items() if kk != "data"
        }, "系列 metadata（名前・色・描画ヒント）が full と一致すること"
        assert got["data"] == want["data"][-k:], (
            f"末尾 {k} 点が full と完全一致すること（系列 {got['name']}）"
        )
    return latest


# =========================================================================== #
# 宣言（archetype / 状態器）
# =========================================================================== #
@pytest.mark.parametrize("ma_type", _MA_TYPES)
def test_declared_as_incremental(ma_type):
    meta = latest_meta("moving_averages", "default", _params(ma_type))
    assert meta.archetype == "incremental"
    assert meta.incremental == "moving_averages"
    # 増分器が扱えないパラメータで落ちる従来経路は厳密一致設計（tail しない）を維持する。
    assert meta.min_window is None
    assert meta.trailing_k == 1


# =========================================================================== #
# 1. 全系列 max_dev = 0（パラメータ行列）
# =========================================================================== #
@pytest.mark.parametrize("ma_type", _MA_TYPES)
@pytest.mark.parametrize("length", [2, 9, 24, 50])
def test_latest_equals_full_exactly(ma_type, length):
    adapter = IndicatorComputeAdapter()
    _assert_tail_matches_full(adapter, _ohlcv(400), _params(ma_type, length=length))


@pytest.mark.parametrize("ma_type", _MA_TYPES)
@pytest.mark.parametrize("offset", [-3, -1, 0, 1, 3])
def test_latest_equals_full_with_offset(ma_type, offset):
    adapter = IndicatorComputeAdapter()
    _assert_tail_matches_full(adapter, _ohlcv(400), _params(ma_type, offset=offset))


@pytest.mark.parametrize("ma_type", _MA_TYPES)
@pytest.mark.parametrize("source", ["close", "open", "high", "low", "hl2", "hlc3", "ohlc4", "hlcc4"])
def test_latest_equals_full_with_source(ma_type, source):
    adapter = IndicatorComputeAdapter()
    _assert_tail_matches_full(adapter, _ohlcv(400), _params(ma_type, source=source))


@pytest.mark.parametrize("ma_type", _MA_TYPES)
def test_latest_equals_full_with_wait_for_close(ma_type):
    adapter = IndicatorComputeAdapter()
    _assert_tail_matches_full(adapter, _ohlcv(400), _params(ma_type, wait_for_close=True))


@pytest.mark.parametrize("ma_type", _MA_TYPES)
@pytest.mark.parametrize("min_tail", [2, 5, 30])
def test_latest_equals_full_with_min_tail(ma_type, min_tail):
    # ISSUE-162: 欠落閉周期の合成で末尾切り点数が K を超える場合も full と一致すること。
    adapter = IndicatorComputeAdapter()
    _assert_tail_matches_full(
        adapter, _ohlcv(400), _params(ma_type), k=min_tail, min_tail=min_tail
    )


# =========================================================================== #
# 2. 足内更新の非破壊性（同一確定状態 × 形成中バー 10 通り）
# =========================================================================== #
@pytest.mark.parametrize("ma_type", _MA_TYPES)
@pytest.mark.parametrize("wait_for_close", [False, True])
def test_intrabar_steps_are_non_destructive(ma_type, wait_for_close):
    adapter = IndicatorComputeAdapter()
    base = _ohlcv(400)
    params = _params(ma_type, wait_for_close=wait_for_close)

    # 確定状態を作る（1 回目の呼出でキャッシュへ載る）。
    _assert_tail_matches_full(adapter, base, params)

    # 同じ確定バー列のまま、形成中バー（末尾足）の OHLC を 10 通りに差し替える。
    for i in range(10):
        df = base.copy()
        delta = (i - 5) * 7.5
        for col in ("open", "high", "low", "close"):
            df.iloc[-1, df.columns.get_loc(col)] = base.iloc[-1][col] + delta
        _assert_tail_matches_full(adapter, df, params)


# =========================================================================== #
# 3. バー確定の前進（左端固定・窓が 1 本ずつ伸びる＝リプレイの limit=bar+1 と同型）
# =========================================================================== #
@pytest.mark.parametrize("ma_type", _MA_TYPES)
def test_bar_advance_keeps_exact_match(ma_type):
    adapter = IndicatorComputeAdapter()
    base = _ohlcv(400)
    params = _params(ma_type)
    for n in range(300, 320):
        _assert_tail_matches_full(adapter, base.iloc[:n], params)
    # 状態は 1 指標 1 パラメータで 1 エントリ（窓が伸びても増殖しない）。
    assert incremental_state.stats()["states"] == 1


@pytest.mark.parametrize("ma_type", _MA_TYPES)
def test_window_shrink_and_regrow_keeps_exact_match(ma_type):
    # 基底構築（全レンジ）→ per-step（短い窓）→ 再び伸長、の順でも常に full と一致する。
    adapter = IndicatorComputeAdapter()
    base = _ohlcv(400)
    params = _params(ma_type)
    _assert_tail_matches_full(adapter, base, params)
    for n in (120, 121, 122, 400):
        _assert_tail_matches_full(adapter, base.iloc[:n], params)


def test_left_edge_shift_rebuilds_and_stays_exact():
    # 左端が動く窓（ライブの df.tail(limit)）でも値は full と一致する（状態は再構築される）。
    adapter = IndicatorComputeAdapter()
    base = _ohlcv(400)
    params = _params("ema")
    for start in range(0, 5):
        _assert_tail_matches_full(adapter, base.iloc[start:start + 300], params)


# =========================================================================== #
# 4. 増分器が扱えない入力は従来経路（挙動不変）
# =========================================================================== #
@pytest.mark.parametrize("smoothing_type", ["sma", "ema", "smma", "wma", "sma_bb"])
def test_smoothing_falls_back_to_full_path_and_matches(smoothing_type):
    adapter = IndicatorComputeAdapter()
    df = _ohlcv(400)
    params = _params("ema", smoothing_type=smoothing_type, smoothing_length=9)
    _assert_tail_matches_full(adapter, df, params, incremental=False)
    # 増分状態を作らない（従来経路であることの実証）。
    assert incremental_state.stats()["states"] == 0


def test_short_window_falls_back_to_full_path_and_matches():
    # warm-up 直後（本数が length+3 未満）は従来経路。値は full と一致する。
    adapter = IndicatorComputeAdapter()
    params = _params("ema", length=24)
    _assert_tail_matches_full(adapter, _ohlcv(26), params, incremental=False)
    assert incremental_state.stats()["states"] == 0


def test_length_over_bar_count_returns_empty_like_full():
    # length > 本数 は add_moving_averages が系列を出さない（full も latest も空）。
    adapter = IndicatorComputeAdapter()
    df = _ohlcv(20)
    params = _params("ema", length=50)
    assert full_compute(adapter, "moving_averages", "default", df, dict(params)) == []
    assert latest_compute(adapter, "moving_averages", "default", df, dict(params)) == []


def test_unknown_source_raises_same_error_as_full_path():
    # 入力不正のエラー翻訳は従来どおり adapter が担う（増分器は判定を持たない）。
    from adapter.compute.indicator_compute_adapter import ComputeError

    adapter = IndicatorComputeAdapter()
    df = _ohlcv(400)
    params = _params("ema", source="unknown_src")
    with pytest.raises(ComputeError) as full_exc:
        full_compute(adapter, "moving_averages", "default", df, dict(params))
    with pytest.raises(ComputeError) as latest_exc:
        latest_compute(adapter, "moving_averages", "default", df, dict(params))
    assert latest_exc.value.error_type == full_exc.value.error_type
