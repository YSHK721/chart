"""因果ローリング分位（増分ソート窓 sweep）の計算量テスト（CLAUDE.md 絶対命令 2026-08-28）。

固定するのは**無駄の不在**であって実装詳細ではない（回数そのものを期待値に焼き込まない）:
    1. 発行した分位評価 − 出力に使った分位評価 = 0（作ってから捨てる評価が無い）。
    2. numpy の行単位分位（nanquantile / apply_along_axis 退行の入口）の発行が 0 回。
       従来は系列に NaN が 1 つでもあると全行が行単位 Python 経路へ落ち、隣接窓が
       499/500 本を共有しているのに毎行を一から並べ直していた（コールド実測 12 秒の律速）。
    3. 窓長を増やしても発行が増えない（オーダーの表明・2 点で固定）。
    4. バンド上下 2 本（quantile_bands）が窓前進を共有する＝掃引の発行は分位数に比例しない。

状態検証（出力の bit 等価）は test_marod_bands_kind_table.py の digest 凍結が持つ。
本ファイルは出力が正しいままでも落ちない型の欠陥（浪費）だけを対象にする。
"""
from __future__ import annotations

import numpy as np
import pytest

from common import marod_bands


def _nan_mixed_series(n: int) -> np.ndarray:
    rng = np.random.default_rng(20260907)
    values = rng.normal(0.0, 1.0, n)
    values[: min(11, n)] = np.nan          # 先頭 warm-up 欠損（実系列と同型）
    values[n // 2: n // 2 + 3] = np.nan    # 中間欠損
    return values


def _spy_counter(monkeypatch: pytest.MonkeyPatch, owner, name: str) -> "list[int]":
    """`owner.name` の呼び出し回数を数える Test Spy（挙動は素通し）。"""
    count = [0]
    original = getattr(owner, name)

    def counting(*args, **kwargs):
        count[0] += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(owner, name, counting)
    return count


@pytest.mark.parametrize(("n", "window_n"), [(120, 25), (900, 200)])
def test_issued_quantile_evaluations_equal_the_outputs_used(
    monkeypatch: pytest.MonkeyPatch, n: int, window_n: int
) -> None:
    """発行した分位評価 − 出力に使った分位評価 = 0（入力長・窓長 2 点で不変）。"""
    # Arrange
    evals = _spy_counter(monkeypatch, marod_bands, "_sorted_linear_quantile")
    values = _nan_mixed_series(n)

    # Act
    out = marod_bands.rolling_causal_fast(values, window_n, "quantile", 0.95)

    # Assert: 出力に載った点（非 NaN）だけが評価されている（±inf の無い系列では 1:1）。
    used = int(np.count_nonzero(~np.isnan(out)))
    assert evals[0] - used == 0


@pytest.mark.parametrize("n", [120, 900])
def test_the_sweep_issues_no_row_wise_numpy_quantile(
    monkeypatch: pytest.MonkeyPatch, n: int
) -> None:
    """numpy の nanquantile / quantile の発行が 0 回（行単位 Python 退行の入口を通らない）。

    入力長 2 点で固定する（入力を増やしても発行が 0 のまま＝オーダーの表明）。
    """
    # Arrange
    nanq = _spy_counter(monkeypatch, marod_bands.np, "nanquantile")
    plainq = _spy_counter(monkeypatch, marod_bands.np, "quantile")
    values = _nan_mixed_series(n)

    # Act
    marod_bands.rolling_causal_fast(values, 50, "quantile", 0.9)
    marod_bands.quantile_bands(values, window_n=50, q_low=0.05, q_high=0.95)

    # Assert
    assert nanq[0] == 0
    assert plainq[0] == 0


@pytest.mark.parametrize("window_n", [30, 300])
def test_issued_evaluations_do_not_grow_with_the_window(
    monkeypatch: pytest.MonkeyPatch, window_n: int
) -> None:
    """窓長を 10 倍にしても発行は出力と同数のまま（窓内の再並べ替えを発行しない）。"""
    # Arrange
    evals = _spy_counter(monkeypatch, marod_bands, "_sorted_linear_quantile")
    values = _nan_mixed_series(600)

    # Act
    out = marod_bands.rolling_causal_fast(values, window_n, "quantile", 0.95)

    # Assert
    assert evals[0] - int(np.count_nonzero(~np.isnan(out))) == 0


def test_quantile_bands_share_a_single_window_sweep(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """バンド上下 2 本で掃引（窓前進）は 1 回＝分位数に比例して窓を維持し直さない。"""
    # Arrange
    sweeps = _spy_counter(monkeypatch, marod_bands, "_rolling_quantile_sweep")
    values = _nan_mixed_series(400)

    # Act
    marod_bands.quantile_bands(values, window_n=50, q_low=0.05, q_high=0.95)

    # Assert: 発行した掃引 − 必要な掃引（1 要求に 1 本）= 0。
    assert sweeps[0] - 1 == 0
