"""因果性・非リペイント・数値再現性の検証（仕様 §4 柱書・§6・§9 段階 1）。

正本仕様: indigators/cvfe/CVFE_spec_v1.0.md §4 柱書・§6・§9 段階 1

検証観点（3 つは別の性質であり、いずれか 1 つでは不足する）:
    1. 数値再現性  — 同一入力の 2 回実行で全出力配列が bit 一致（§6）
    2. 一括 = 逐次 — ``compute_cvfe`` と ``CvfeSequential`` の ``sigma_hat`` が bit 一致（§6）
    3. 切詰め不変  — 入力を ``bar_edges[T]`` で切り詰めて再計算しても ``sigma_hat[:T]``
                     が bit 一致（＝将来ティックが過去の出力へ影響しない）

観点 2 は本実装では構成上ほぼ自明（一括経路は逐次経路の合成）であり、
**将来情報の混入を検出する力を持たない**。それを担うのは観点 3 である
（内部設計 §4 / アーキテクチャ査読 🔴-1）。
"""

import sys
from pathlib import Path

import numpy as np

_PKG_DIR = Path(__file__).resolve().parents[1]
if str(_PKG_DIR) not in sys.path:
    sys.path.insert(0, str(_PKG_DIR))
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from cvfe_synthetic import make_dataset  # noqa: E402
from src import (  # noqa: E402
    CvfeSequential,
    compute_cvfe,
    diagnose_quality,
    fit_state,
    measure_all_bars,
)
from src.dto import CvfeParams  # noqa: E402
from src.sampling import validate_edges, validate_ticks  # noqa: E402

N_HAR = 500
N_BARS = 560
BAR_SEC = 3_600
SESSION_SEC = 1_800          # 場間を空けてギャップ保有バーを生成する（§4.7 を経路に含める）
TICK_SEC = 5


def _dataset(n_bars: int = N_BARS, **kw):
    return make_dataset(n_bars, bar_sec=BAR_SEC, tick_sec=TICK_SEC,
                        session_sec=SESSION_SEC, seed=31, gap_sigma=0.002, **kw)


def _run(ticks, edges, **kw):
    kw.setdefault("n_har", N_HAR)
    return compute_cvfe(ticks, edges, BAR_SEC, **kw)


def _assert_bitwise(a: np.ndarray, b: np.ndarray, name: str) -> None:
    """NaN を含む配列の bit 一致（``np.array_equal`` の equal_nan 版）。"""
    assert a.shape == b.shape, name
    assert a.dtype == b.dtype, name
    assert a.tobytes() == b.tobytes(), f"{name} が bit 一致しない"


def test_gap_component_is_exercised_by_this_fixture():
    """本テストのデータがギャップ成分・ジャンプ経路を実際に通ることを確認する。

    （すべて 0 の経路を「一致」と判定してしまう空虚な検証を防ぐ。）
    """
    ticks, edges = _dataset()
    res = _run(ticks, edges)
    assert res.available.any()
    assert np.any(res.sigma_co[res.available] > 0.0), "ギャップ成分が全て 0 では検証にならない"
    assert res.measure_id in ("RV", "TSRV")


def test_repeated_run_is_bitwise_identical():
    """同一入力で 2 回実行し全出力配列が bit 一致する（§6 数値再現性）。"""
    ticks, edges = _dataset()
    a = _run(ticks, edges)
    b = _run(ticks, edges)
    for name in ("sigma_hat", "sigma_oc", "sigma_co", "jump_flag", "har_coef", "available"):
        _assert_bitwise(getattr(a, name), getattr(b, name), name)
    assert a.measure_id == b.measure_id and a.delta_star_sec == b.delta_star_sec
    assert a.har_resid_var == b.har_resid_var


def test_bulk_equals_sequential_bitwise():
    """一括計算と 1 バーずつの逐次計算の sigma_hat が bit 一致する（§6・§9 段階 1）。"""
    ticks, edges = _dataset()
    bulk = _run(ticks, edges)

    times, logp = validate_ticks(ticks)
    e = validate_edges(edges)
    params = CvfeParams(bar_interval_sec=BAR_SEC, n_har=N_HAR)
    quality = diagnose_quality(times, logp, e, params.n_har, params.freeze_thresh)
    measures = measure_all_bars(times, logp, e, quality, params)
    state = fit_state(measures, quality, params)

    seq = CvfeSequential(state, params)
    got = np.full(len(measures), np.nan, dtype=np.float64)
    for m in measures:                       # 1 バーずつ供給する
        got[m.index] = seq.push(m)[0]

    _assert_bitwise(bulk.sigma_hat, got, "sigma_hat（一括 vs 逐次）")


def test_truncated_input_reproduces_prefix_bitwise():
    """入力を bar_edges[T] で切り詰めても sigma_hat[:T] が bit 一致する（look-ahead 不在）。

    将来のティック・将来のバーが過去の出力へ影響しないことを直接示す。
    構成上の同値ではなく、**入力の情報量を実際に減らして**比較する。
    """
    ticks, edges = _dataset()
    full = _run(ticks, edges)

    for cut in (530, 545):
        cut_time = edges[cut]
        keep = ticks[:, 0] < cut_time
        part = _run(ticks[keep], edges[:cut + 1])
        _assert_bitwise(full.sigma_hat[:cut], part.sigma_hat[:cut],
                        f"sigma_hat[:{cut}]（全期間 vs 切詰め）")
        _assert_bitwise(full.sigma_oc[:cut], part.sigma_oc[:cut], f"sigma_oc[:{cut}]")
        _assert_bitwise(full.sigma_co[:cut], part.sigma_co[:cut], f"sigma_co[:{cut}]")
        _assert_bitwise(full.jump_flag[:cut], part.jump_flag[:cut], f"jump_flag[:{cut}]")
        assert full.har_coef.tobytes() == part.har_coef.tobytes()
        assert full.delta_star_sec == part.delta_star_sec
        assert full.measure_id == part.measure_id


def test_appending_future_bars_does_not_repaint_past():
    """後続データの追加で確定済みバーの値が変化しない（非リペイント）。"""
    ticks, edges = _dataset(n_bars=N_BARS)
    short_cut = 540
    short = _run(ticks[ticks[:, 0] < edges[short_cut]], edges[:short_cut + 1])
    longer = _run(ticks, edges)
    _assert_bitwise(short.sigma_hat, longer.sigma_hat[:short_cut], "確定バーの非リペイント")


def test_refit_every_produces_causal_forecasts():
    """refit_every > 0 でも切詰め不変が成立する（§4.5-6 の再学習が t−1 以前のみを使う）。"""
    ticks, edges = _dataset()
    full = _run(ticks, edges, refit_every=5)
    cut = 545
    part = _run(ticks[ticks[:, 0] < edges[cut]], edges[:cut + 1], refit_every=5)
    _assert_bitwise(full.sigma_hat[:cut], part.sigma_hat[:cut],
                    "sigma_hat[:cut]（refit_every=5）")


def test_refit_changes_coefficients_relative_to_frozen():
    """refit_every > 0 が実際に係数を更新している（再学習が空回りしていない）。"""
    ticks, edges = _dataset()
    frozen = _run(ticks, edges, refit_every=0)
    refit = _run(ticks, edges, refit_every=5)
    assert not np.array_equal(frozen.sigma_hat, refit.sigma_hat), "再学習が結果に反映されていない"


def test_forecast_does_not_depend_on_own_bar_prices():
    """バー t の価格を変えても σ̂_t は変わらない（自バー情報の不使用・仕様 §4 柱書）。

    切詰め不変性テストはこの欠陥を検出できない。バー ``t`` 自身のティックは
    ``bar_edges[T]``（``T > t``）での切詰めでも残るため、``x_{t−1}`` の代わりに
    ``x_t`` を使う実装でも切詰め前後で一致してしまう。よって別テストが要る。

    摂動はバー内**対数収益のスケーリング**で行う（価格の一律倍率では対数収益が
    変わらず ``RV`` も ``C_t`` も不変になり、検定が空虚になる）。時刻は変えない。
    仕様 §4.7-1 のギャップ判定は当該バーの**最初のティック時刻**を参照するため、
    時刻を摂動すると「仕様どおりの挙動」まで差分に現れてしまうためである。
    なお、この参照自体が §4 柱書（``bar_edges[t]`` より厳密に前の情報のみ）と
    整合しない（``t_first ∈ [edge_t, edge_{t+1})``）ことは ISSUE-210 に記録した。

    検出力は変異注入で確認済み: ``CvfeSequential._forecast_oc`` で ``x1`` に
    当該バーの ``C_t`` を混入させると本テストが失敗する。
    """
    ticks, edges = _dataset()
    base = _run(ticks, edges)

    target = 545
    assert base.available[target], "検証対象バーが available でなければ意味がない"

    perturbed = ticks.copy()
    in_bar = (perturbed[:, 0] >= edges[target]) & (perturbed[:, 0] < edges[target + 1])
    assert in_bar.sum() > 2
    logp_bar = np.log(perturbed[in_bar, 1])
    # バー内対数収益を 1.5 倍する（始値は保存し、RV は 2.25 倍になる）。
    perturbed[in_bar, 1] = np.exp(logp_bar[0] + 1.5 * (logp_bar - logp_bar[0]))
    got = _run(perturbed, edges)

    # 摂動が当該バーの測定量を実際に変えていること（空虚な検定でないことの確認）。
    from src.engine import measure_all_bars as _mab
    from src.quality import diagnose_quality as _dq
    _t, _lp = validate_ticks(perturbed)
    _e = validate_edges(edges)
    _p = CvfeParams(bar_interval_sec=BAR_SEC, n_har=N_HAR)
    _q = _dq(_t, _lp, _e, _p.n_har, _p.freeze_thresh)
    v_pert = _mab(_t, _lp, _e, _q, _p)[target].v
    _t0, _lp0 = validate_ticks(ticks)
    v_base = _mab(_t0, _lp0, _e, _q, _p)[target].v
    assert v_pert != v_base, "摂動が当該バーの V_t を変えていない＝検定が空虚"

    assert got.sigma_hat[target] == base.sigma_hat[target], "σ̂_t が自バーの価格に依存している"
    assert got.sigma_oc[target] == base.sigma_oc[target]
    # 摂動が後続バーには効いていること（テストが空虚でないことの確認）。
    assert got.sigma_hat[target + 1] != base.sigma_hat[target + 1]


def test_gap_ewma_init_is_causal_when_fewer_than_200_gap_bars():
    """ギャップ保有バーが 200 本未満でも EWMA 初期値が将来バーを参照しない（§4 柱書）。

    仕様 §4.7-3 は初期値を「先頭 200 本のギャップ保有バーの g² の平均」とだけ定め、
    対象を予測開始バー ``t0`` より前に限定していない。総数が 200 本未満のとき、
    素直に実装すると ``t >= t0`` のギャップが初期値に混入し、将来情報が過去の
    ``σ̂`` へ漏れる（ISSUE-208）。

    ``SESSION_SEC < BAR_SEC`` の通常フィクスチャは**全バーがギャップ保有**になるため
    この経路を構造的に通れない。ここでは寄り付きを遅らせたバーだけをギャップ保有に
    して、`t0` 前後にまたがる 120 本の構成を作る。
    """
    n_bars = 560
    t0 = N_HAR + 22
    # t0 の前に 100 本、後に 20 本のギャップ保有バーを置く（合計 120 < 200）。
    late = tuple(range(100, 600, 5))[:100] + tuple(range(t0 + 1, t0 + 21))
    ticks, edges = make_dataset(n_bars, bar_sec=BAR_SEC, tick_sec=TICK_SEC,
                                seed=57, late_open_bars=late)
    full = _run(ticks, edges)

    n_gap = int((full.sigma_co > 0.0).sum())
    assert 0 < n_gap < 200, f"ギャップ保有バー {n_gap} 本ではこの経路を検証できない"

    for cut in (530, 545):
        part = _run(ticks[ticks[:, 0] < edges[cut]], edges[:cut + 1])
        _assert_bitwise(full.sigma_hat[:cut], part.sigma_hat[:cut],
                        f"sigma_hat[:{cut}]（ギャップ保有 {n_gap} 本・切詰め）")
        _assert_bitwise(full.sigma_co[:cut], part.sigma_co[:cut], f"sigma_co[:{cut}]")
