"""LWMA 増分 API（走行和の授受）の検証 — 現行実装を凍結オラクルとして固定する。

背景（ISSUE-233 / 内部設計_latest増分計算.md S1）:
    ``latest`` の真の増分化には「前回までの状態から 1 点だけ進めて **full と bit 一致**」が要る。
    sma / ema / smma は既存の ``prev_calculated`` 契約がそのまま full の漸化を継続するため
    bit 一致する（実測済み）。一方 LWMA は ``prev_calculated>0`` 分岐が走行和 ``total`` /
    ``lsum`` を **窓から再構築** するため、full 側の長い漸化で蓄積した丸めが消え末尾値が
    ズレる（実測 max_dev 2.1e-09 @ n=1400）。``buffer[i] = total_i / weight`` は丸め済みで
    ``total_i`` を復元できないため、src の外側からは継続不能である。

対策（真因の除去）:
    走行和を授受する ``linear_weighted_ma_on_buffer_stateful`` を追加し、**既存
    ``linear_weighted_ma_on_buffer`` はその共有部品（seed / advance）へ委譲する**。
    漸化式の定義は 1 箇所だけになり、新旧へ写した二重定義を作らない。

本テストが固定する不変条件:
    1. 既存 ``linear_weighted_ma_on_buffer`` の出力が委譲前（凍結オラクル）と **bit 一致**。
       全 4 引数軸（rates_total / prev_calculated / begin / period）を格子で走査する。
    2. ``*_stateful`` を state=None で呼んだ結果が full（凍結オラクル）と bit 一致。
    3. state を継承して 1 点ずつ進めた結果が、その本数で full を計算した結果と bit 一致
       （＝増分計算の通過条件 max_dev = 0）。
    4. state は不変（``step`` 相当の再呼出で壊れない＝足内更新で何度でも呼べる）。
"""

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src import (  # noqa: E402
    linear_weighted_ma_on_buffer,
    linear_weighted_ma_on_buffer_stateful,
)


# --------------------------------------------------------------------------- #
# 凍結オラクル（委譲前の linear_weighted_ma_on_buffer 実体・2026-08-01 時点の写し）
#
# ISSUE-158 で確立した方式（現行実装を参照実装として固定）。本関数は **テスト専用の
# 参照**であり、製品コードは委譲版のみを持つ（二重定義にしない）。
# --------------------------------------------------------------------------- #
def _oracle_lwma_on_buffer(rates_total, prev_calculated, begin, period, price, buffer):
    if period <= 1 or period > (rates_total - begin):
        return 0
    if prev_calculated <= period + begin + 2:
        start_position = period + begin
        for i in range(start_position):
            buffer[i] = 0.0
    else:
        start_position = prev_calculated - 2
    total = 0.0
    lsum = 0.0
    weight = 0
    weight_idx = 1
    for i in range(start_position - period, start_position):
        total += price[i] * weight_idx
        lsum += price[i]
        weight += weight_idx
        weight_idx += 1
    buffer[start_position - 1] = total / weight
    for i in range(start_position, rates_total):
        total = total - lsum + price[i] * period
        lsum = lsum - price[i - period] + price[i]
        buffer[i] = total / weight
    return rates_total


def _prices(n: int, seed: int = 0) -> np.ndarray:
    """再現可能な擬似価格系列（指数水準・丸め蓄積が出る大きさ）。"""
    rng = np.random.default_rng(seed)
    return 39000.0 + np.cumsum(rng.normal(0.0, 12.0, n))


# =========================================================================== #
# 1. 既存関数の出力が凍結オラクルと bit 一致（委譲リファクタの非改変性）
# =========================================================================== #
@pytest.mark.parametrize("n", [50, 200, 1400])
@pytest.mark.parametrize("period", [2, 9, 24, 50])
@pytest.mark.parametrize("begin", [0, 3])
@pytest.mark.parametrize("prev_calculated", [0, 1, 30, 199])
def test_on_buffer_is_bit_identical_to_frozen_oracle(n, period, begin, prev_calculated):
    price = _prices(n)
    if prev_calculated > n:
        pytest.skip("prev_calculated > rates_total は呼出契約外")
    got = np.zeros(n)
    want = np.zeros(n)
    r_got = linear_weighted_ma_on_buffer(n, prev_calculated, begin, period, price, got)
    r_want = _oracle_lwma_on_buffer(n, prev_calculated, begin, period, price, want)
    assert r_got == r_want
    assert got.tobytes() == want.tobytes(), "委譲後も既存関数の出力は bit 不変であること"


# =========================================================================== #
# 2. stateful(state=None) == full（凍結オラクル・prev_calculated=0）
# =========================================================================== #
@pytest.mark.parametrize("n", [50, 1400])
@pytest.mark.parametrize("period", [2, 9, 50])
@pytest.mark.parametrize("begin", [0, 3])
def test_stateful_seed_equals_full(n, period, begin):
    price = _prices(n)
    got = np.zeros(n)
    want = np.zeros(n)
    r_got, state = linear_weighted_ma_on_buffer_stateful(n, begin, period, price, got)
    r_want = _oracle_lwma_on_buffer(n, 0, begin, period, price, want)
    assert r_got == r_want
    assert got.tobytes() == want.tobytes()
    if r_want == 0:  # 期間チェック不成立（period > rates_total - begin）＝状態を作らない
        assert state is None
    else:
        assert state is not None and state.calculated == n


# =========================================================================== #
# 3. 状態を継承した 1 点前進が full と bit 一致（増分計算の通過条件 max_dev = 0）
# =========================================================================== #
@pytest.mark.parametrize("period", [2, 9, 24, 50])
def test_stateful_step_is_bit_identical_to_full(period):
    n = 1400
    price = _prices(n)
    m = n - 5  # 確定済み本数（ここまでを状態として保持する）

    buf = np.zeros(n)
    _, state = linear_weighted_ma_on_buffer_stateful(m, 0, period, price[:m], buf[:m])

    # 5 本ぶん 1 点ずつ前進し、各時点で full 計算と bit 一致することを確認する。
    for extra in range(1, 6):
        size = m + extra
        step_buf = np.zeros(size)
        step_buf[:size - 1] = buf[:size - 1]
        _, state = linear_weighted_ma_on_buffer_stateful(
            size, 0, period, price[:size], step_buf, state
        )
        buf[:size] = step_buf

        full = np.zeros(size)
        _oracle_lwma_on_buffer(size, 0, 0, period, price[:size], full)
        assert step_buf.tobytes() == full.tobytes(), (
            f"period={period} extra={extra}: 増分結果は full と bit 一致であること"
        )


# =========================================================================== #
# 4. state は不変（同一確定状態から形成中バーを差し替えて何度でも呼べる）
# =========================================================================== #
@pytest.mark.parametrize("period", [9, 50])
def test_state_is_not_mutated_by_step(period):
    n = 600
    price = _prices(n)
    m = n - 1
    buf = np.zeros(m)
    _, confirmed = linear_weighted_ma_on_buffer_stateful(m, 0, period, price[:m], buf)
    snapshot = (confirmed.total, confirmed.lsum, confirmed.weight, confirmed.calculated)

    # 形成中バーを 10 通りに差し替えて step。毎回 full と一致し、state は壊れない。
    for i in range(10):
        ext = np.concatenate([price[:m], [price[m - 1] + float(i) * 3.0]])
        step_buf = np.zeros(m + 1)
        step_buf[:m] = buf
        linear_weighted_ma_on_buffer_stateful(m + 1, 0, period, ext, step_buf, confirmed)

        full = np.zeros(m + 1)
        _oracle_lwma_on_buffer(m + 1, 0, 0, period, ext, full)
        assert step_buf.tobytes() == full.tobytes(), f"{i} 回目の step が full と不一致"
        assert (
            confirmed.total, confirmed.lsum, confirmed.weight, confirmed.calculated
        ) == snapshot, "step は確定状態を破壊してはならない"
