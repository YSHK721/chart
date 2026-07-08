"""profit_oscillator2 core 層テスト（Red→Green・元 MQL 1:1 固定）。

discriminating 観点:
    TC-DUP    : 複製一致（rsi/mfi/wpr/marod/stochastic/level_count_score = 複製元数値一致）。
    TC-STOCH  : iStochastic main/signal 二段 EMA（rawK→EMA(slowing)→main→EMA(d)→signal）。
    TC-RSIOV  : RSI 上書きバグ（RSI_Typical/High 極端値でも結果は RSI_Low 基底のみ）。
    TC-WEIGHT : 加重 1/2/2/10/10/10/1/1（各サブ単独活性化で係数固定）。
    TC-LEVELS : σ6 ＋ sub_min/max(×1.5)・クランプ無し（母σ÷N）。
    TC-RCI    : RCI int 切り捨て（int 化で同値タイ化・direction T/F・warm-up 0）。
    TC-EXC    : 例外（osc_period<2/ma_period<2/OHLCV 長不一致→ValueError）。
    TC-DTO    : 不変性（writeable=False、frozen）。
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

# 共有層（複製元との数値一致確認に使用）。
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # = indicators/
from moving_averages import exponential_ma_on_buffer  # noqa: E402

# テスト対象（src パッケージ）。parents[1] = profit_oscillator2/。
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src import core  # noqa: E402

# 複製元（STC 生 %K の二段 EMA 照合用）。
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "profit_stc"))
from profit_stc.src import core as stc_core  # type: ignore  # noqa: E402


# --------------------------------------------------------------------------- #
# 共通フィクスチャ: 再現性のある OHLCV（昇順 古→新）。
# --------------------------------------------------------------------------- #
def _make_ohlcv(n: int = 40, seed: int = 7):
    rng = np.random.default_rng(seed)
    close = np.cumsum(rng.normal(0.0, 1.0, n)) + 100.0
    high = close + rng.uniform(0.2, 1.5, n)
    low = close - rng.uniform(0.2, 1.5, n)
    open_ = close + rng.normal(0.0, 0.3, n)
    volume = rng.uniform(100.0, 1000.0, n)
    return open_, high, low, close, volume


# =========================================================================== #
# TC-STOCH: iStochastic main/signal 二段 EMA（手計算で固定）
# =========================================================================== #
def test_stc_main_signal_is_two_stage_ema_of_raw_k():
    """STC main=EMA(rawK,slowing=6) / signal=EMA(main,d=6) の二段 EMA を共有 EMA で固定。

    手計算具体値（rawK→main→signal）は実装の compute_oscillator2_full ではなく、
    iStochastic main/signal を直接構築するヘルパ compute_istoch_main_signal で固定する。
    """
    _, high, low, close, _ = _make_ohlcv()
    raw_k = stc_core.compute_stochastic(high, low, close, period=6)

    n = raw_k.shape[0]
    main_exp = np.zeros(n)
    exponential_ma_on_buffer(n, 0, 0, 6, raw_k, main_exp)
    signal_exp = np.zeros(n)
    exponential_ma_on_buffer(n, 0, 0, 6, main_exp, signal_exp)

    main_got, signal_got = core.compute_istoch_main_signal(
        high, low, close, osc_period=6, stc_slow=6
    )
    np.testing.assert_array_equal(main_got, main_exp)
    np.testing.assert_array_equal(signal_got, signal_exp)


# =========================================================================== #
# TC-RSIOV: RSI 上書きバグ（RSI_Typical/High は消え RSI_Low が基底）
# =========================================================================== #
def test_level_count_uses_only_rsi_low_base_due_to_overwrite_bug():
    """RSI_Typical/High を極端化しても level_count が変化しない（上書きバグ 1:1）ことを固定。

    high/low を不変に保ち typical(=価格合成) の RSI が変わるよう close のみを差し替えても、
    RSI 基底は RSI_Low（low 価格の RSI）のみのため、low 不変なら RSI 寄与は不変。
    ここでは「RSI_High も寄与しない」ことを示すため high を変えずに別経路（typical）を変える。
    """
    o, high, low, close, volume = _make_ohlcv()

    # ベースの level_count。
    base = core.compute_level_count(o, high, low, close, volume,
                                    osc_period=6, stc_slow=6, ma_period=60)

    # RSI 寄与のみを取り出すヘルパ compute_level_count_rsi_term で固定する。
    # RSI_High / RSI_Typical を「採点が打ち消し合わない」非対称な極端値に設定し、
    # それを別の非対称極端値に差し替えても結果が不変であることを確認する
    # （accumulation 実装なら値が変化するため、上書きバグの discriminating 固定になる）。
    rsi_term_extreme_a = core.compute_level_count_rsi_term(
        rsi_low=np.array([30.0, 70.0]),
        rsi_high=np.array([90.0, 90.0]),   # 同側（>50）で打ち消さない
        rsi_typical=np.array([80.0, 80.0]),
    )
    rsi_term_extreme_b = core.compute_level_count_rsi_term(
        rsi_low=np.array([30.0, 70.0]),
        rsi_high=np.array([10.0, 5.0]),    # 逆側（<50）の別極端値
        rsi_typical=np.array([20.0, 15.0]),
    )
    # RSI_High / RSI_Typical をどう変えても結果は RSI_Low 基底のみで不変。
    np.testing.assert_array_equal(rsi_term_extreme_a, rsi_term_extreme_b)
    # かつ RSI_Low 基底のみと一致する（accumulation でないことを確定）。
    rsi_low_only = np.array([
        core.level_count_score(30.0, 100.0, 1),
        core.level_count_score(70.0, 100.0, 0),
    ])
    np.testing.assert_array_equal(rsi_term_extreme_a, rsi_low_only)
    assert base.shape == (o.shape[0],)


def test_level_count_rsi_term_equals_rsi_low_score_only():
    """RSI 項が score(RSI_Low, 50pivot) のみであることを手計算で固定。"""
    # RSI_High / RSI_Typical は採点が打ち消し合わない非対称極端値（同側 >50）に設定し、
    # accumulation 実装なら期待値と乖離するようにする。
    rsi_low = np.array([30.0, 70.0, 50.0])
    rsi_high = np.array([90.0, 90.0, 90.0])
    rsi_typical = np.array([80.0, 80.0, 80.0])
    got = core.compute_level_count_rsi_term(
        rsi_low=rsi_low, rsi_high=rsi_high, rsi_typical=rsi_typical
    )
    expected = np.array([
        core.level_count_score(30.0, 100.0, 1),  # <50 -> case1
        core.level_count_score(70.0, 100.0, 0),  # >50 -> case0
        0.0,                                      # ==50 -> 0（RSI_Low==50）
    ])
    np.testing.assert_array_equal(got, expected)


def test_compute_level_count_wiring_matches_independent_assembly():
    """compute_level_count の配線（RSI=Low 基底・MAROD=Typ/High/Low・STC main/signal・span=100）を独立再構成で固定。

    各サブを独立に組み立てた期待系列と一致することを確認し、価格ソース取り違え
    （例: RSI に Typical、MAROD ソース入替）や加重位置ズレを検出する。
    """
    o, high, low, close, volume = _make_ohlcv(80)
    got = core.compute_level_count(
        o, high, low, close, volume, osc_period=6, stc_slow=6, ma_period=60
    )

    typ = (high + low + close) / 3.0
    rsi_low = core.compute_rsi(low, period=6)            # RSI は Low 価格基底（上書き後）
    wpr = core.compute_wpr(high, low, close, period=6) + 100.0
    mfi = core.compute_mfi(high, low, close, volume, period=6)
    n = close.shape[0]
    ma_t = np.zeros(n); exponential_ma_on_buffer(n, 0, 0, 60, typ, ma_t)
    ma_h = np.zeros(n); exponential_ma_on_buffer(n, 0, 0, 60, high, ma_h)
    ma_l = np.zeros(n); exponential_ma_on_buffer(n, 0, 0, 60, low, ma_l)
    marod_t = core.compute_marod(typ, ma_t)
    marod_h = core.compute_marod(high, ma_h)
    marod_l = core.compute_marod(low, ma_l)
    stc_main, stc_signal = core.compute_istoch_main_signal(
        high, low, close, osc_period=6, stc_slow=6
    )

    def s50(v):
        return (core.level_count_score(v, 100.0, 1) if v < 50
                else core.level_count_score(v, 100.0, 0) if v > 50 else 0.0)

    def s0(v):
        return (core.level_count_score(v, 100.0, 2) if v < 0
                else core.level_count_score(v, 100.0, 3) if v > 0 else 0.0)

    expected = np.array([
        s50(rsi_low[i]) + 2 * s50(wpr[i]) + 2 * s50(mfi[i])
        + 10 * s0(marod_t[i]) + 10 * s0(marod_h[i]) + 10 * s0(marod_l[i])
        + s50(stc_signal[i]) + s50(stc_main[i])
        for i in range(n)
    ])
    np.testing.assert_array_equal(got, expected)


# =========================================================================== #
# TC-WEIGHT: 加重 1/2/2/10/10/10/1/1（各サブ単独活性化で係数固定）
# =========================================================================== #
def test_level_count_weights_per_subterm():
    """各サブオシレーター寄与の加重係数（1/2/2/10/10/10/1/1）を直接合算ヘルパで固定。"""
    # サブ採点値を既知の単一値に固定し、加重合算式を検証する。
    terms = core.combine_level_count_terms(
        rsi_low_score=1.0,
        wpr_score=1.0,
        mfi_score=1.0,
        marod_typical_score=1.0,
        marod_high_score=1.0,
        marod_low_score=1.0,
        stc_signal_score=1.0,
        stc_main_score=1.0,
    )
    # 1*1 + 2*1 + 2*1 + 10*1 + 10*1 + 10*1 + 1*1 + 1*1 = 37
    assert terms == pytest.approx(37.0)


def test_level_count_weights_isolated_marod_high_is_ten():
    """MAROD_High のみ活性で寄与が ×10 であることを単独固定。"""
    terms = core.combine_level_count_terms(
        rsi_low_score=0.0, wpr_score=0.0, mfi_score=0.0,
        marod_typical_score=0.0, marod_high_score=0.5, marod_low_score=0.0,
        stc_signal_score=0.0, stc_main_score=0.0,
    )
    assert terms == pytest.approx(5.0)


def test_level_count_weights_isolated_wpr_is_two():
    """WPR のみ活性で寄与が ×2 であることを単独固定。"""
    terms = core.combine_level_count_terms(
        rsi_low_score=0.0, wpr_score=0.3, mfi_score=0.0,
        marod_typical_score=0.0, marod_high_score=0.0, marod_low_score=0.0,
        stc_signal_score=0.0, stc_main_score=0.0,
    )
    assert terms == pytest.approx(0.6)


def test_pivot_equality_branches_return_zero():
    """50/0 ピボットの境界（==50 / ==0）が 0 を返すことを固定（境界値網羅）。"""
    assert core._score_50pivot(50.0) == 0.0   # ==50 -> 0
    assert core._score_0pivot(0.0) == 0.0     # ==0 -> 0
    # 各サイド（<,>）の符号も確認（pivot ロジックの方向固定）。
    assert core._score_50pivot(40.0) < 0.0    # <50 -> case1（負）
    assert core._score_50pivot(60.0) > 0.0    # >50 -> case0（正）
    assert core._score_0pivot(-5.0) < 0.0     # <0 -> case2（負）
    assert core._score_0pivot(5.0) > 0.0      # >0 -> case3（正）


@pytest.mark.parametrize(
    "term_name,weight",
    [
        ("rsi_low_score", 1.0),
        ("wpr_score", 2.0),
        ("mfi_score", 2.0),
        ("marod_typical_score", 10.0),
        ("marod_high_score", 10.0),
        ("marod_low_score", 10.0),
        ("stc_signal_score", 1.0),
        ("stc_main_score", 1.0),
    ],
)
def test_level_count_each_term_weight_isolated(term_name, weight):
    """8 サブ項を 1 つずつ単独活性化し、各係数（1/2/2/10/10/10/1/1）を個別固定。

    全項を 0 にし対象 1 項のみ既知値 0.7 を与え、寄与が weight*0.7 であることを確認する。
    係数の置換（例: mfi↔marod_typical 入替）を検出する discriminating 固定。
    """
    args = dict(
        rsi_low_score=0.0, wpr_score=0.0, mfi_score=0.0,
        marod_typical_score=0.0, marod_high_score=0.0, marod_low_score=0.0,
        stc_signal_score=0.0, stc_main_score=0.0,
    )
    args[term_name] = 0.7
    got = core.combine_level_count_terms(**args)
    assert got == pytest.approx(weight * 0.7)


# =========================================================================== #
# TC-LEVELS: σ6 ＋ sub_min/max(×1.5)・クランプ無し（母σ÷N）
# =========================================================================== #
def test_compute_levels2_sigma6_population_std_no_clamp():
    """compute_levels2 が母σ（÷N）で σ6 水準・sub_min/max(×1.5) を返すことを手計算で固定。"""
    lc = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
    avg = float(np.mean(lc))                 # 3.0
    dev = float(np.sqrt(np.mean((lc - avg) ** 2)))  # 母σ = sqrt(2) ≈ 1.41421356
    out = core.compute_levels2(lc)
    assert out["up_165"] == pytest.approx(avg + 1.65 * dev)
    assert out["up_196"] == pytest.approx(avg + 1.96 * dev)
    assert out["up_258"] == pytest.approx(avg + 2.58 * dev)
    assert out["dn_165"] == pytest.approx(avg - 1.65 * dev)
    assert out["dn_196"] == pytest.approx(avg - 1.96 * dev)
    assert out["dn_258"] == pytest.approx(avg - 2.58 * dev)
    assert out["sub_min"] == pytest.approx((avg - 1.96 * dev) * 1.5)
    assert out["sub_max"] == pytest.approx((avg + 1.96 * dev) * 1.5)


# =========================================================================== #
# TC-RCI: RCI int 切り捨て（同値タイ化・direction T/F・warm-up 0）
# =========================================================================== #
def _rci_reference(level_count, period, direction, sigma_ref):
    """元 RankPrices/SpearmanRankCorrelation を Python で 1:1 再現した参照実装（int 切り捨て）。"""
    n = level_count.shape[0]
    rci = np.zeros(n)
    for a in range(n):
        if a < period - 1:
            rci[a] = 0.0
            continue
        # w[k] = level_count[a-k]、int 切り捨て（0 方向）。
        w = [int(level_count[a - k]) for k in range(period)]
        sort_int = sorted(w, reverse=direction)
        true_ranks = [i + 1 for i in range(period)]
        i = 0
        while i < period - 1:
            if sort_int[i] != sort_int[i + 1]:
                i += 1
                continue
            dublicat = sort_int[i]
            k = i + 1
            counter = 1
            average_rank = i + 1
            while k < period:
                if sort_int[k] == dublicat:
                    counter += 1
                    average_rank += k + 1
                    k += 1
                else:
                    break
            average_rank = average_rank / counter
            for m in range(i, k):
                true_ranks[m] = average_rank
            i = k
        r2 = [0.0] * period
        for idx in range(period):
            etalon = int(w[idx])
            for k in range(period):
                if etalon == sort_int[k]:
                    r2[idx] = true_ranks[k]
                    break
        z2 = sum((r2[idx] - idx - 1) ** 2 for idx in range(period))
        spearman = 1.0 - 6.0 * z2 / (period ** 3 - period)
        rci[a] = spearman * sigma_ref
    return rci


def test_compute_rci_int_truncation_creates_ties_direction_false():
    """LC が int 化で同値タイ化する入力で int 版 Spearman を固定（direction=False）。

    float 値が僅差でも int 化で同値になるケース（[2.7, 2.3, ...]）を構成し、
    int 切り捨て版 RCI が参照実装と一致することを固定する。
    """
    period = 4
    # int 化すると [2,2,3,1,...] のようにタイが発生する系列。
    lc = np.array([1.2, 3.8, 2.3, 2.7, 1.9, 3.1, 2.2, 2.9])
    sigma_ref = 1.5
    got = core.compute_rci(lc, period=period, direction=False, sigma_ref=sigma_ref)
    expected = _rci_reference(lc, period, False, sigma_ref)
    np.testing.assert_allclose(got, expected, rtol=0, atol=1e-12)
    # warm-up: a < period-1 は 0。
    assert got[0] == 0.0 and got[1] == 0.0 and got[2] == 0.0


def test_compute_rci_int_truncation_direction_true():
    """direction=True（降順ソート）でも int 切り捨て版 Spearman が参照実装と一致することを固定。"""
    period = 4
    lc = np.array([1.2, 3.8, 2.3, 2.7, 1.9, 3.1, 2.2, 2.9])
    sigma_ref = 2.0
    got = core.compute_rci(lc, period=period, direction=True, sigma_ref=sigma_ref)
    expected = _rci_reference(lc, period, True, sigma_ref)
    np.testing.assert_allclose(got, expected, rtol=0, atol=1e-12)


def test_compute_rci_int_truncation_differs_from_float_ranking():
    """int 切り捨て版と float 版で RCI 値が異なる discriminating 入力を固定。

    [2.7, 2.3] は float では別順位、int では同値（2,2）でタイ平均ランクになる。
    int 版（実装）と float 版（誤実装相当）の結果が異なることを示す。
    """
    period = 4
    lc = np.array([2.7, 2.3, 2.1, 2.9, 2.4, 2.8, 2.2, 2.6])
    sigma_ref = 1.0
    got_int = core.compute_rci(lc, period=period, direction=False, sigma_ref=sigma_ref)

    # float 版（int 切り捨てをしない誤実装）を参照として構築。
    def _rci_float(level_count, period, direction, sigma_ref):
        n = level_count.shape[0]
        rci = np.zeros(n)
        for a in range(n):
            if a < period - 1:
                continue
            w = [float(level_count[a - k]) for k in range(period)]
            sort_v = sorted(w, reverse=direction)
            true_ranks = [i + 1 for i in range(period)]
            i = 0
            while i < period - 1:
                if sort_v[i] != sort_v[i + 1]:
                    i += 1
                    continue
                dublicat = sort_v[i]
                k = i + 1
                counter = 1
                average_rank = i + 1
                while k < period and sort_v[k] == dublicat:
                    counter += 1
                    average_rank += k + 1
                    k += 1
                average_rank /= counter
                for m in range(i, k):
                    true_ranks[m] = average_rank
                i = k
            r2 = [0.0] * period
            for idx in range(period):
                for k in range(period):
                    if w[idx] == sort_v[k]:
                        r2[idx] = true_ranks[k]
                        break
            z2 = sum((r2[idx] - idx - 1) ** 2 for idx in range(period))
            rci[a] = (1 - 6 * z2 / (period ** 3 - period)) * sigma_ref
        return rci

    got_float = _rci_float(lc, period, False, sigma_ref)
    # 少なくとも 1 バーで int 版 != float 版（int 切り捨ての discriminating 証明）。
    assert not np.allclose(got_int, got_float)


# =========================================================================== #
# TC-EXC: 例外
# =========================================================================== #
def test_compute_oscillator2_full_osc_period_below_two_raises():
    """osc_period<2 で ValueError を投げることを固定。"""
    o, h, l, c, v = _make_ohlcv(20)
    with pytest.raises(ValueError):
        core.compute_oscillator2_full(o, h, l, c, v, osc_period=1)


def test_compute_oscillator2_full_ma_period_below_two_raises():
    """ma_period<2 で ValueError を投げることを固定。"""
    o, h, l, c, v = _make_ohlcv(20)
    with pytest.raises(ValueError):
        core.compute_oscillator2_full(o, h, l, c, v, ma_period=1)


def test_compute_oscillator2_full_length_mismatch_raises():
    """OHLCV 長不一致で ValueError を投げることを固定。"""
    o, h, l, c, v = _make_ohlcv(20)
    with pytest.raises(ValueError):
        core.compute_oscillator2_full(o, h, l, c[:-1], v, osc_period=6)


# =========================================================================== #
# TC-DTO: 不変性（writeable=False、frozen）
# =========================================================================== #
def test_oscillator2_result_arrays_are_readonly():
    """Oscillator2Result の ndarray が writeable=False であることを固定。"""
    o, h, l, c, v = _make_ohlcv(80)
    res = core.compute_oscillator2_full(o, h, l, c, v)
    assert res.level_count.flags.writeable is False
    assert res.rci.flags.writeable is False


def test_oscillator2_result_is_frozen():
    """Oscillator2Result が frozen（属性再代入不可）であることを固定。"""
    o, h, l, c, v = _make_ohlcv(80)
    res = core.compute_oscillator2_full(o, h, l, c, v)
    with pytest.raises(Exception):
        res.sub_min = 0.0  # type: ignore[misc]


def test_oscillator2_result_exposes_levels_and_sub_bounds():
    """compute_oscillator2_full が level_count/rci/levels/sub_min/sub_max を持つことを固定。"""
    o, h, l, c, v = _make_ohlcv(80)
    res = core.compute_oscillator2_full(o, h, l, c, v)
    n = c.shape[0]
    assert res.level_count.shape == (n,)
    assert res.rci.shape == (n,)
    for key in ("up_165", "up_196", "up_258", "dn_165", "dn_196", "dn_258"):
        assert key in res.levels
    # sigma_ref（RCI 倍率）= dn_196。levels との整合。
    levels2 = core.compute_levels2(res.level_count)
    assert res.sub_min == pytest.approx(levels2["sub_min"])
    assert res.sub_max == pytest.approx(levels2["sub_max"])


def test_compute_oscillator2_full_rci_uses_dn196_as_sigma_ref():
    """統合の RCI が sigma_ref=dn_196（元 StcLCStdDevArray[5]）で計算されることを固定。

    dn_196 を使った RCI と一致し、up_196 を使った RCI とは異なることを示す
    discriminating 固定（sigma_ref の取り違えを検出）。
    """
    o, h, l, c, v = _make_ohlcv(80)
    res = core.compute_oscillator2_full(o, h, l, c, v)
    levels2 = core.compute_levels2(res.level_count)
    rci_dn196 = core.compute_rci(
        res.level_count, period=12, direction=False, sigma_ref=levels2["dn_196"]
    )
    rci_up196 = core.compute_rci(
        res.level_count, period=12, direction=False, sigma_ref=levels2["up_196"]
    )
    np.testing.assert_array_equal(res.rci, rci_dn196)
    # dn_196 と up_196 が異なる前提で、取り違えを検出できることを保証。
    assert not np.isclose(levels2["dn_196"], levels2["up_196"])
    assert not np.allclose(res.rci, rci_up196)
