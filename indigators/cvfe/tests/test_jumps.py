"""CVFE 仕様 §4.4 ジャンプ分離の検証。

正本仕様: indigators/cvfe/CVFE_spec_v1.0.md §4.4・§8 K5・§9 段階 1

検証観点:
    - μ1 / μ_{4/3} / BPV / TQ / z が仕様の式そのものであること
    - ジャンプを含まない合成 GBM の誤検出率が 3 × (1 − jump_alpha) 以内（§9 段階 1）
    - 既知の 5σ ジャンプの検出率が 90% 以上（§9 段階 1）
    - n < 50 のバーは jump_flag=False に固定（§8 K5）
    - BPV ≤ 0 のとき C_t = V_t / J_t = 0 とし WARN を残す（§4.4）

`5σ` の解釈: 仕様は基準となる σ の尺度を明示しない。本テストは
「バー全体の σ（＝バー内積分ボラティリティの平方根）の 5 倍」と解釈する
（ジャンプ検定文献の慣行と一致）。1 サンプル収益の σ の 5 倍という解釈での
実測値の解釈差は ISSUE-211 に記録した。
"""

import io
import json
import math
import sys
from pathlib import Path

import numpy as np
import pytest

_PKG_DIR = Path(__file__).resolve().parents[1]
if str(_PKG_DIR) not in sys.path:
    sys.path.insert(0, str(_PKG_DIR))

from src import JsonlLogger  # noqa: E402
from src.jumps import (  # noqa: E402
    JUMP_MIN_N,
    MU_1,
    MU_4_3,
    Z_VARIANCE_CONST,
    bipower_variation,
    jump_test,
    tri_power_quarticity,
)
from src.measures import realized_variance  # noqa: E402


def test_constants_match_specification():
    """μ1 = sqrt(2/π)、μ_{4/3} = 2^(2/3)·Γ(7/6)/Γ(1/2)、分散定数 = π²/4 + π − 5（§4.4）。"""
    assert MU_1 == pytest.approx(math.sqrt(2.0 / math.pi), rel=1e-15)
    assert MU_4_3 == pytest.approx(2.0 ** (2.0 / 3.0) * math.gamma(7.0 / 6.0) / math.gamma(0.5), rel=1e-15)
    assert Z_VARIANCE_CONST == pytest.approx(math.pi ** 2 / 4.0 + math.pi - 5.0, rel=1e-15)
    assert JUMP_MIN_N == 50


def test_bipower_variation_matches_specification_formula():
    """BPV_t = μ1^(-2)·(n/(n−1))·Σ_{i=2}^{n} |r_i||r_{i−1}|（§4.4）。"""
    rng = np.random.default_rng(1)
    r = rng.standard_normal(60) * 0.001
    n = r.size
    expect = MU_1 ** -2 * (n / (n - 1)) * float((np.abs(r[1:]) * np.abs(r[:-1])).sum())
    assert bipower_variation(r) == pytest.approx(expect, rel=1e-13)


def test_tri_power_quarticity_matches_specification_formula():
    """TQ_t = n·μ_{4/3}^(-3)·(n/(n−2))·Σ_{i=3}^{n} |r_i|^{4/3}|r_{i−1}|^{4/3}|r_{i−2}|^{4/3}（§4.4）。"""
    rng = np.random.default_rng(2)
    r = rng.standard_normal(60) * 0.001
    n = r.size
    a = np.abs(r) ** (4.0 / 3.0)
    expect = n * MU_4_3 ** -3 * (n / (n - 2)) * float((a[2:] * a[1:-1] * a[:-2]).sum())
    assert tri_power_quarticity(r) == pytest.approx(expect, rel=1e-13)


def test_no_jump_leaves_continuous_component_equal_to_measure():
    """z ≤ c のとき C_t = V_t、J_t = 0、jump_flag=False（§4.4）。"""
    rng = np.random.default_rng(4)
    r = rng.standard_normal(400) * (1.0 / np.sqrt(400))
    v = float((r ** 2).sum())
    res = jump_test(v, r, 0.999)
    assert res.flag is False
    assert res.c == v
    assert res.j == 0.0


def test_short_bar_is_never_flagged_as_jump():
    """n < 50 のバーは jump_flag=False に固定する（§8 K5）。"""
    rng = np.random.default_rng(5)
    r = rng.standard_normal(40) * 0.001
    r[20] += 0.5                                  # 巨大なジャンプを注入しても
    v = float((r ** 2).sum())
    res = jump_test(v, r, 0.999)
    assert res.flag is False and res.c == v and res.j == 0.0


def test_nonpositive_bipower_falls_back_and_logs():
    """BPV ≤ 0 のとき C_t = V_t、J_t = 0 とし WARN を残す（§4.4）。"""
    from src.errors import W02_BPV_NONPOSITIVE

    r = np.zeros(100)
    r[0] = 0.01                                   # 隣接積がすべて 0 → BPV = 0
    v = float((r ** 2).sum())
    stream = io.StringIO()
    res = jump_test(v, r, 0.999, logger=JsonlLogger(stream), bar_index=9)
    assert res.flag is False and res.c == v and res.j == 0.0

    records = [json.loads(line) for line in stream.getvalue().splitlines() if line]
    warn = [rec for rec in records if rec["code"] == W02_BPV_NONPOSITIVE]
    assert len(warn) == 1 and warn[0]["bar_index"] == 9


# --------------------------------------------------------------------------------------
# §9 段階 1：誤検出率と検出力
# --------------------------------------------------------------------------------------

_N_STEPS = 1_440
_ALPHA = 0.999


def _gbm_returns(n_bars: int, seed: int, jump_size: float = 0.0):
    rng = np.random.default_rng(seed)
    sd = 1.0 / np.sqrt(_N_STEPS)                  # バー分散 = 1（σ = 1）
    r = rng.standard_normal((n_bars, _N_STEPS)) * sd
    if jump_size != 0.0:
        r[:, _N_STEPS // 2] += jump_size
    return r


def _flag_rate(r_mat):
    flags = 0
    for r in r_mat:
        v = realized_variance(np.concatenate([[0.0], np.cumsum(r)]))
        flags += int(jump_test(v, r, _ALPHA).flag)
    return flags / r_mat.shape[0]


@pytest.fixture(scope="module")
def false_positive_rate():
    return _flag_rate(_gbm_returns(20_000, seed=21))


def test_stage1_false_positive_rate_within_three_times_alpha(false_positive_rate):
    """ジャンプ無し GBM の誤検出率が 3 × (1 − jump_alpha) 以内（既定 0.3%）（§9 段階 1）。"""
    limit = 3.0 * (1.0 - _ALPHA)
    assert false_positive_rate <= limit, f"false positive rate = {false_positive_rate!r} > {limit}"


@pytest.fixture(scope="module")
def detection_rate_bar_sigma():
    """バー σ（= 1）の 5 倍のジャンプに対する検出率。"""
    return _flag_rate(_gbm_returns(2_000, seed=22, jump_size=5.0))


def test_stage1_five_sigma_jump_detection_rate(detection_rate_bar_sigma):
    """既知の 5σ ジャンプの検出率が 90% 以上（§9 段階 1）。"""
    assert detection_rate_bar_sigma >= 0.90, f"detection rate = {detection_rate_bar_sigma!r}"


# --------------------------------------------------------------------------------------
# TSRV 経路（quality_gate = DEGRADED）のジャンプ検定
# --------------------------------------------------------------------------------------

def _tsrv_path_flag_rate(bar_sec: int):
    """ノイズ注入で DEGRADED（TSRV）へ落ちる構成の end-to-end ジャンプ検出率を返す。"""
    import sys as _sys
    from pathlib import Path as _Path
    _here = str(_Path(__file__).resolve().parent)
    if _here not in _sys.path:
        _sys.path.insert(0, _here)
    from cvfe_synthetic import make_dataset

    from src.dto import CvfeParams
    from src.engine import measure_all_bars
    from src.quality import diagnose_quality
    from src.sampling import validate_edges, validate_ticks

    ticks, edges = make_dataset(560, bar_sec=bar_sec, tick_sec=5, seed=77,
                                noise_omega_ratio=1.0)          # ジャンプ無し DGP
    times, logp = validate_ticks(ticks)
    e = validate_edges(edges)
    params = CvfeParams(bar_interval_sec=bar_sec, n_har=500)
    quality = diagnose_quality(times, logp, e, params.n_har, params.freeze_thresh)
    measures = measure_all_bars(times, logp, e, quality, params)
    flags = np.array([m.jump_flag for m in measures])
    return quality, float(flags.mean()), int(np.median([m.n for m in measures]))


def test_tsrv_path_is_actually_exercised():
    """本テスト群が TSRV 分岐を実際に通ること（RV 分岐だけを測る空虚な検定を防ぐ）。"""
    quality, _rate, n_med = _tsrv_path_flag_rate(21_600)
    assert quality.quality_gate == "DEGRADED"
    assert quality.measure_id == "TSRV"
    assert n_med >= JUMP_MIN_N, "n < 50 では K5 により検定が無効化され測定にならない"


@pytest.mark.xfail(strict=True, reason=(
    "仕様の欠陥（ISSUE-209）。§4.4 の z 統計量の分散 (π²/4+π−5)/n·max(1,TQ/BPV²) は "
    "Barndorff-Nielsen & Shephard (2006) が RV/BPV 比について導いた漸近分布であり、"
    "ノイズ補正済みの TSRV を分子に据えた場合には妥当しない。ジャンプ無し DGP の "
    "end-to-end 実測で誤検出率 13.57%（bar_sec=21600）／16.79%（43200）となり、"
    "§9 段階 1 の許容 0.3% を 45〜56 倍超過する。式の変更は裁定後に行う。"))
def test_stage1_false_positive_rate_on_tsrv_path():
    """TSRV 経路でも誤検出率が 3 × (1 − jump_alpha) 以内であること（§9 段階 1）。"""
    _quality, rate, _n = _tsrv_path_flag_rate(21_600)
    limit = 3.0 * (1.0 - _ALPHA)
    assert rate <= limit, f"TSRV 経路の誤検出率 = {rate:.4%} > {limit:.2%}"
