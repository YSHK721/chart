"""CVFE 仕様 §3.3 エラーレスポンスの検証（§9 段階 1 の第 1 項目）。

正本仕様: indigators/cvfe/CVFE_spec_v1.0.md §3.1（制約）・§3.3（E01〜E09）

検証観点:
    - E01〜E05・E08 は ``ValueError``（``CvfeError``）を送出する
    - E06・E09 は例外を送出せず当該バーを ``available=False`` / ``sigma_hat=nan`` にする
    - E07 は例外を送出せず ``quality_gate="FAIL"`` / ``measure_id="PARK"`` へ縮退する
    - E06・E07 の発生時に JSON Lines ログ 1 行（``{ts, level, code, bar_index, detail}``）を出す

各テストは 1 つの違反条件のみを成立させる（他の条件はすべて満たす）。
"""

import io
import json
import sys
from pathlib import Path

import numpy as np
import pytest

_PKG_DIR = Path(__file__).resolve().parents[1]
if str(_PKG_DIR) not in sys.path:
    sys.path.insert(0, str(_PKG_DIR))
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from cvfe_synthetic import make_dataset  # noqa: E402
from src import (  # noqa: E402
    E01_INSUFFICIENT_BARS,
    E02_TICKS_NOT_MONOTONIC,
    E03_NONPOSITIVE_PRICE,
    E04_EDGES_NOT_MONOTONIC,
    E05_PARAM_RANGE,
    E06_EMPTY_BAR,
    E07_QUALITY_FAIL,
    E08_HAR_SINGULAR,
    CvfeError,
    JsonlLogger,
    compute_cvfe,
    har_fit,
)

# 試験用の縮小構成（n_har は仕様 §3.1 の下限 500。必要バー数 = 500 + 22 = 522）。
N_HAR = 500
N_BARS = 540
BAR_SEC = 3_600
TICK_SEC = 5


def _dataset(n_bars: int = N_BARS, **kw):
    return make_dataset(n_bars, bar_sec=BAR_SEC, tick_sec=TICK_SEC, seed=7, **kw)


def _run(ticks, edges, **kw):
    kw.setdefault("n_har", N_HAR)
    return compute_cvfe(ticks, edges, BAR_SEC, **kw)


def test_e01_insufficient_bars_raises():
    """バー数が n_har + 22 未満なら E01 を送出する（§3.3）。"""
    ticks, edges = _dataset(n_bars=520)
    assert len(edges) - 1 < N_HAR + 22
    with pytest.raises(CvfeError) as ei:
        _run(ticks, edges)
    assert ei.value.code == E01_INSUFFICIENT_BARS
    assert isinstance(ei.value, ValueError)


def test_e02_ticks_not_monotonic_raises():
    """ticks[:,0] が狭義単調増加でなければ E02 を送出する（§3.3）。"""
    ticks, edges = _dataset()
    ticks = ticks.copy()
    ticks[10, 0] = ticks[9, 0]          # 等値 ＝ 狭義単調増加の違反
    with pytest.raises(CvfeError) as ei:
        _run(ticks, edges)
    assert ei.value.code == E02_TICKS_NOT_MONOTONIC


def test_e03_nonpositive_price_raises():
    """ticks[:,1] に 0 以下が含まれれば E03 を送出する（§3.3）。"""
    ticks, edges = _dataset()
    ticks = ticks.copy()
    ticks[123, 1] = 0.0
    with pytest.raises(CvfeError) as ei:
        _run(ticks, edges)
    assert ei.value.code == E03_NONPOSITIVE_PRICE


def test_e04_edges_not_monotonic_raises():
    """bar_edges が狭義単調増加でなければ E04 を送出する（§3.3）。"""
    ticks, edges = _dataset()
    edges = edges.copy()
    edges[5], edges[6] = edges[6], edges[5]
    with pytest.raises(CvfeError) as ei:
        _run(ticks, edges)
    assert ei.value.code == E04_EDGES_NOT_MONOTONIC


@pytest.mark.parametrize(
    "kw",
    [
        {"n_har": 499},            # n_har >= 500
        {"lam_gap": 0.89},         # 0.90 <= lam_gap < 1.0
        {"lam_gap": 1.0},
        {"jump_alpha": 0.98},      # 0.99 <= jump_alpha <= 0.9999
        {"jump_alpha": 0.99991},
        {"refit_every": -1},       # >= 0
        {"bar_interval_sec": 59},  # >= 60
    ],
)
def test_e05_param_range_raises(kw):
    """§3.1 の制約に違反するパラメータは E05 を送出する（§3.3）。"""
    ticks, edges = _dataset()
    kw = dict(kw)
    bar_sec = kw.pop("bar_interval_sec", BAR_SEC)
    kw.setdefault("n_har", N_HAR)
    with pytest.raises(CvfeError) as ei:
        compute_cvfe(ticks, edges, bar_sec, **kw)
    assert ei.value.code == E05_PARAM_RANGE


def test_e06_empty_bar_marks_unavailable_and_logs():
    """ティック < 2 のバーは例外を出さず available=False / sigma_hat=nan にし WARN を残す（§3.3）。"""
    ticks, edges = _dataset(empty_bars=(530,))
    stream = io.StringIO()
    res = _run(ticks, edges, logger=JsonlLogger(stream))

    assert not bool(res.available[530])
    assert np.isnan(res.sigma_hat[530])

    records = [json.loads(line) for line in stream.getvalue().splitlines() if line]
    e06 = [r for r in records if r["code"] == E06_EMPTY_BAR]
    assert len(e06) >= 1
    assert set(e06[0]) == {"ts", "level", "code", "bar_index", "detail"}
    assert e06[0]["level"] == "WARN"
    assert 530 in {r["bar_index"] for r in e06}


def test_e07_quality_fail_degrades_to_park_and_logs():
    """気配凍結率が閾値超過なら FAIL へ縮退し PARK を採用する。例外は出さない（§3.3・§4.1）。"""
    ticks, edges = _dataset(freeze_fraction=0.5)   # 120 秒（>=60 秒）の不変帯を時間の 50% に注入
    stream = io.StringIO()
    res = _run(ticks, edges, logger=JsonlLogger(stream))

    assert res.quality_gate == "FAIL"
    assert res.measure_id == "PARK"
    assert res.delta_star_sec == 0
    assert res.freeze_ratio > 0.05

    records = [json.loads(line) for line in stream.getvalue().splitlines() if line]
    e07 = [r for r in records if r["code"] == E07_QUALITY_FAIL]
    assert len(e07) == 1
    assert e07[0]["level"] == "ERROR"


def test_e08_har_singular_raises():
    """HAR 設計行列が退化すれば E08 を送出する（§3.3・§4.5-4）。"""
    x = np.ones((100, 5))          # 全列が定数 → 切片と完全共線（ランク < 6）
    y = np.zeros(100)
    with pytest.raises(CvfeError) as ei:
        har_fit(x, y)
    assert ei.value.code == E08_HAR_SINGULAR


@pytest.mark.parametrize("empty_bar", [100, 300, 520, 521])
def test_e06_inside_training_window_continues(empty_bar):
    """学習窓の内側にある空バーでも例外を出さず処理を継続する（§3.3 E06）。

    仕様 §3.3 E06 は「当該バーの available=False / sigma_hat=nan。処理は継続」を
    明示する。無効バーの nan 行を学習標本に残すと §3.3 E08 が送出され、この保証が
    破れる（ISSUE-205）。学習窓の**外側**（bar 530）だけを置いた検証では検出できない。
    """
    from src.errors import W05_HAR_TRAINING_ROWS_DROPPED

    # 空バーの C_t = nan は 22 本の遡及窓を通じて後続 22 本の特徴量も無効化する
    # （仕様 §4.5 は欠測の補完方針を規定していない・ISSUE-206）。伝播分を超える
    # 長さの系列で「例外を出さず、伝播の外側は有効に残る」ことを確認する。
    ticks, edges = _dataset(n_bars=600, empty_bars=(empty_bar,))
    stream = io.StringIO()
    res = _run(ticks, edges, logger=JsonlLogger(stream))       # 例外を出さないこと

    assert not bool(res.available[empty_bar])
    assert res.available.any(), "1 本の空バーで全バーが無効化されている"

    codes = [json.loads(line)["code"] for line in stream.getvalue().splitlines() if line]
    assert W05_HAR_TRAINING_ROWS_DROPPED in codes
    assert E06_EMPTY_BAR in codes


def test_e09_nonfinite_sigma_is_marked_unavailable():
    """σ̂ が非有限になるバーを実際に生成し available=False / nan になることを見る（§3.3 E09）。

    対偶どうしを突き合わせるだけの検証は恒真であり検証力を持たない。ここでは
    HAR 係数を巨大値へ差し替えて ``exp(ŷ/2 + s²/8)`` をオーバーフローさせる。
    """
    import dataclasses

    from src import CvfeSequential, diagnose_quality, fit_state, measure_all_bars
    from src.dto import CvfeParams
    from src.sampling import validate_edges, validate_ticks

    ticks, edges = _dataset()
    times, logp = validate_ticks(ticks)
    e = validate_edges(edges)
    params = CvfeParams(bar_interval_sec=BAR_SEC, n_har=N_HAR)
    quality = diagnose_quality(times, logp, e, params.n_har, params.freeze_thresh)
    measures = measure_all_bars(times, logp, e, quality, params)
    state = fit_state(measures, quality, params)

    blown = dataclasses.replace(state, har_coef=np.array([1e5, 0.0, 0.0, 0.0, 0.0, 0.0]))
    seq = CvfeSequential(blown, params)
    sigmas, avails = [], []
    for m in measures:
        sigma, _oc, _co, ok = seq.push(m)
        sigmas.append(sigma)
        avails.append(ok)

    sigmas = np.array(sigmas)
    avails = np.array(avails)
    tail = slice(state.first_available_index, None)
    assert not np.any(np.isfinite(sigmas[tail]) & (sigmas[tail] > 0.0)), "σ̂ が発散していない"
    assert not avails[tail].any(), "非有限 σ̂ のバーが available のままになっている"
    assert np.all(np.isnan(sigmas[tail]))


def test_e09_available_implies_finite_positive_sigma():
    """σ̂ が非有限または 0 以下のバーは available=False とする（§3.3・§4.8）。"""
    ticks, edges = _dataset()
    res = _run(ticks, edges)
    finite_pos = np.isfinite(res.sigma_hat) & (res.sigma_hat > 0.0)
    assert np.all(finite_pos[res.available])          # available ⇒ 有限かつ正
    assert not np.any(res.available[~finite_pos])     # 非有限/非正 ⇒ available=False
    assert res.available.any()                        # 全滅していないこと


def test_result_arrays_are_read_only():
    """出力 DTO は不変（frozen）で、配列は書込不可であること（§4.8）。"""
    import dataclasses

    ticks, edges = _dataset()
    res = _run(ticks, edges)
    with pytest.raises(dataclasses.FrozenInstanceError):
        res.measure_id = "RV"          # type: ignore[misc]
    for name in ("sigma_hat", "sigma_oc", "sigma_co", "jump_flag", "har_coef", "available"):
        arr = getattr(res, name)
        assert not arr.flags.writeable, name
