"""PRO!fitRMMMACD 因果ローリング窓化（look-ahead/repaint 除去）の検証。

依頼仕様（feature/rmm-macd-causal-window）の Red フェーズを固定する。忠実移植は
放棄済み（元 MQL 全期間挙動からの乖離は承認済み）。本指標は level_count を
fast/slow EMA → macd=slow-fast → signal EMA → histogram の **EMA連鎖の入力**に
する。共有 EMA 実装（exponential_ma_on_buffer）は種 buffer[0]=price[0] かつ NaN
ガード無しのため、level_count 先頭 warm-up NaN をそのまま入れると EMA が全期間
NaN 汚染する。

固定する観点::

    1. 因果 no-repaint: 過去バー(#250)の出力が、データ末尾を足しても不変
       （window=120, n>=400 の合成 OHLCV）。
    2. warm-up NaN: level_count・histogram の先頭 window-1 が NaN、その後有限。
    3. EMA 非汚染（最重要）: 因果 histogram が「全 NaN ではなく」warm-up 後に
       有限値を持つ（NaN伝播していないことの回帰固定）。
    4. 全期間版（window=None）で従来の数学的性質（level_count=sister 一致）を保つ。
    5. 全NaN 境界（n<window）で全出力 NaN（argmax 誤判定で start=0→汚染しない）。
"""

import importlib.util
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # = profit_rmm_macd/

from src import core  # noqa: E402

# profit_rmm の正準 level_count（全期間版・window=None）を別名ロードして取り込む。
_rmm_core_path = (
    Path(__file__).resolve().parents[2] / "profit_rmm" / "src" / "core.py"
)
_spec = importlib.util.spec_from_file_location("profit_rmm_core_cw", _rmm_core_path)
rmm_core = importlib.util.module_from_spec(_spec)
sys.modules["profit_rmm_core_cw"] = rmm_core
_spec.loader.exec_module(rmm_core)


def _synthetic_ohlcv(n: int, seed: int = 7):
    """昇順合成 OHLCV（ランダムウォーク）。因果性検証用に十分長い系列。"""
    rng = np.random.default_rng(seed)
    close = 100 + np.cumsum(rng.normal(0, 1.0, n))
    high = close + np.abs(rng.normal(0, 0.5, n))
    low = close - np.abs(rng.normal(0, 0.5, n))
    volume = rng.integers(1, 100, n).astype(float)
    return high, low, close, volume


# ===========================================================================
# 観点 1: 因果 no-repaint（過去バーの出力がデータ末尾追加で不変）
# ===========================================================================
class TestCausalNoRepaint:
    def test_past_bar_histogram_unchanged_when_appending_future_bars(self) -> None:
        # Arrange: window=120, n=420。bar=250 を末尾追加前後で比較。
        high, low, close, volume = _synthetic_ohlcv(420)
        W, bar = 120, 250
        # Act: 全長と #300 までの短縮系列でそれぞれ因果計算。
        full = core.compute_rmmmacd(
            high, low, close, volume, window=W
        )
        short = core.compute_rmmmacd(
            high[:300], low[:300], close[:300], volume[:300], window=W
        )
        # Assert: bar=250 の histogram/macd/signal/level_count が repaint しない。
        np.testing.assert_allclose(
            full.level_count[bar], short.level_count[bar],
            rtol=1e-12, atol=1e-12,
        )
        np.testing.assert_allclose(
            full.histogram[bar], short.histogram[bar],
            rtol=1e-12, atol=1e-12,
        )
        np.testing.assert_allclose(
            full.macd[bar], short.macd[bar], rtol=1e-12, atol=1e-12
        )
        np.testing.assert_allclose(
            full.signal[bar], short.signal[bar], rtol=1e-12, atol=1e-12
        )

    def test_level_count_past_bar_unchanged_on_append(self) -> None:
        high, low, close, volume = _synthetic_ohlcv(420)
        W, bar = 120, 250
        full = core.compute_rmm_level_count(
            high, low, close, volume, window=W
        )
        short = core.compute_rmm_level_count(
            high[:300], low[:300], close[:300], volume[:300], window=W
        )
        np.testing.assert_allclose(
            full[bar], short[bar], rtol=1e-12, atol=1e-12
        )


# ===========================================================================
# 観点 2: warm-up NaN（先頭 window-1 が NaN、その後有限）
# ===========================================================================
class TestWarmupNan:
    def test_level_count_warmup_is_nan_then_finite(self) -> None:
        high, low, close, volume = _synthetic_ohlcv(420)
        W = 120
        lc = core.compute_rmm_level_count(high, low, close, volume, window=W)
        # Assert: 先頭 W-1 が全 NaN、W-1 以降に有限値が出現する。
        assert np.all(np.isnan(lc[: W - 1]))
        assert np.isfinite(lc[W - 1])
        assert np.any(np.isfinite(lc[W - 1:]))

    def test_histogram_warmup_is_nan(self) -> None:
        high, low, close, volume = _synthetic_ohlcv(420)
        W = 120
        res = core.compute_rmmmacd(high, low, close, volume, window=W)
        # Assert: histogram の先頭 window-1 は非描画（NaN）。
        assert np.all(np.isnan(res.histogram[: W - 1]))


# ===========================================================================
# 観点 3: EMA 非汚染（最重要・NaN伝播していないことの回帰固定）
# ===========================================================================
class TestEmaNotContaminated:
    def test_causal_histogram_is_not_all_nan_and_has_finite_after_warmup(
        self,
    ) -> None:
        # Arrange
        high, low, close, volume = _synthetic_ohlcv(420)
        W = 120
        # Act
        res = core.compute_rmmmacd(high, low, close, volume, window=W)
        # Assert: histogram が全 NaN でない（共有 EMA の NaN 汚染が起きていない）。
        assert not np.all(np.isnan(res.histogram))
        # warm-up 後に有限値が複数存在する。
        finite = np.isfinite(res.histogram[W - 1:])
        assert finite.sum() > 0

    def test_all_chain_buffers_have_finite_values_after_warmup(self) -> None:
        high, low, close, volume = _synthetic_ohlcv(420)
        W = 120
        res = core.compute_rmmmacd(high, low, close, volume, window=W)
        for name in ("fast", "slow", "macd", "signal", "histogram"):
            arr = getattr(res, name)
            assert np.any(np.isfinite(arr[W - 1:])), f"{name} all-NaN after warmup"

    def test_warmup_region_of_chain_buffers_is_nan(self) -> None:
        # warm-up 区間（level_count が NaN の区間）は fast/slow/macd/signal/histogram
        # すべて NaN（非描画）であること。
        high, low, close, volume = _synthetic_ohlcv(420)
        W = 120
        res = core.compute_rmmmacd(high, low, close, volume, window=W)
        for name in ("fast", "slow", "macd", "signal", "histogram"):
            arr = getattr(res, name)
            assert np.all(np.isnan(arr[: W - 1])), f"{name} warmup not NaN"


# ===========================================================================
# 観点 4: 全期間版（window=None）で従来の数学的性質を保つ
# ===========================================================================
class TestAllPeriodInvariant:
    def test_window_none_level_count_matches_sister_all_period(self) -> None:
        # Arrange: window=None かつ sister も window=None（全期間スカラ span）。
        high, low, close, volume = _synthetic_ohlcv(200)
        # Act
        got = core.compute_rmm_level_count(
            high, low, close, volume, window=None
        )
        expected = rmm_core.compute_rmm(
            high, low, close, volume, window=None
        ).level_count
        # Assert: 全期間版の数学的同一性（bit-for-bit）。
        np.testing.assert_allclose(got, expected, rtol=0, atol=0)

    def test_window_none_has_no_nan_for_full_length(self) -> None:
        # 全期間版は warm-up NaN を持たない（従来の全バー採点）。
        high, low, close, volume = _synthetic_ohlcv(200)
        lc = core.compute_rmm_level_count(high, low, close, volume, window=None)
        assert np.all(np.isfinite(lc))


# ===========================================================================
# 観点 4b: 退化帯（finite_len < EMA period）— 共有 EMA の偽 0.0 混入回帰固定
#
# window=120 のとき start=window-1=119。n∈{121,123,125} で有限スライス長
# m=n-119∈{2,4,6} となり slow period=8 を下回る（n=121 では fast=4 も下回る）。
# 共有 EMA（exponential_ma_on_buffer）は period > m のとき buffer へ何も書かず
# 0 を返すため、修正前は seg_out=np.zeros(m) のままの偽 0.0 が活性区間に混入する
# （本来は EMA 不能＝非描画 NaN であるべき）。修正後は当該 EMA 不能列が NaN に
# なることを固定する。Red（修正前は活性区間に 0.0 が出て失敗）→ Green。
# ===========================================================================
class TestDegenerateBandNoFalseZero:
    """finite_len < EMA period の退化帯で偽 0.0（非 EMA 結果）が混入しない。"""

    DEGENERATE_NS = (121, 123, 125)

    def _res(self, n: int):
        high, low, close, volume = _synthetic_ohlcv(n)
        return core.compute_rmmmacd(high, low, close, volume, window=120)

    def _start(self, lc: np.ndarray) -> int:
        # level_count の最初の有限 index（= EMA 開始位置）。
        finite = np.isfinite(lc)
        return int(np.argmax(finite)) if finite.any() else lc.size

    def test_level_count_activated_region_is_finite_in_degenerate_band(self) -> None:
        # 前提固定: 退化帯でも level_count[start:] 自体は有限（描画対象）である。
        # よって活性区間に出る 0.0 は level_count 由来ではなく EMA 由来の偽値。
        for n in self.DEGENERATE_NS:
            res = self._res(n)
            start = self._start(res.level_count)
            assert start < n, f"n={n}: level_count に有限区間が無い（前提崩壊）"
            assert np.all(
                np.isfinite(res.level_count[start:])
            ), f"n={n}: level_count 活性区間に非有限が混入"

    def test_chain_buffers_have_no_false_zero_in_activated_region(self) -> None:
        # 本体回帰: fast/slow/macd/signal/histogram の活性区間（level_count が
        # 有限な区間）に、EMA 不能（period > m）由来の偽 0.0 が出ない。
        # 修正後は EMA 不能列が NaN になる（少なくとも「偽の 0.0」ではない）。
        periods = {"fast": 4, "slow": 8, "signal": 4}
        for n in self.DEGENERATE_NS:
            res = self._res(n)
            start = self._start(res.level_count)
            m = n - start  # 有限スライス長
            for name in ("fast", "slow", "macd", "signal", "histogram"):
                seg = getattr(res, name)[start:]
                # EMA 不能（period > m）な系列は活性区間が全 NaN（非描画）。
                if name in periods and periods[name] > m:
                    assert np.all(np.isnan(seg)), (
                        f"n={n} {name}: period={periods[name]}>m={m} は EMA 不能。"
                        f" 偽 0.0 でなく NaN であるべき。got={seg}"
                    )
                # macd=slow-fast / histogram=macd-signal は構成 EMA のいずれかが
                # 不能なら NaN 伝播する（slow が常に不能＝m<8 のため）。
                if name in ("macd", "histogram"):
                    assert np.all(np.isnan(seg)), (
                        f"n={n} {name}: slow(8)>m={m} 不能のため NaN 伝播すべき。"
                        f" got={seg}"
                    )

    def test_no_zero_value_leaks_into_activated_region(self) -> None:
        # discriminating: 修正前の症状は「活性区間に 0.0 が立つ」。活性区間に
        # 厳密な 0.0 が 1 つでも存在したら（EMA 不能列なのに）失敗とする。
        # 退化帯では slow(8)>m が常に成立し macd/histogram/slow が EMA 不能なので、
        # これらの活性区間に 0.0 は出てはならない（NaN であるべき）。
        for n in self.DEGENERATE_NS:
            res = self._res(n)
            start = self._start(res.level_count)
            for name in ("slow", "macd", "histogram"):
                seg = getattr(res, name)[start:]
                assert not np.any(seg == 0.0), (
                    f"n={n} {name}: 活性区間に偽 0.0 が混入（EMA 不能列）。got={seg}"
                )


# ===========================================================================
# 観点 5: 全NaN 境界（n<window→全出力 NaN・argmax 誤判定で汚染しない）
# ===========================================================================
class TestAllNanBoundary:
    def test_short_series_below_window_yields_all_nan_chain(self) -> None:
        # Arrange: n < window → level_count 全 NaN → start 検出が末尾相当になり
        # EMA は実行されず全出力 NaN（argmax の 0 返しで [0:0] 汚染が起きない）。
        high, low, close, volume = _synthetic_ohlcv(50)
        W = 120  # n=50 < W
        # Act
        res = core.compute_rmmmacd(high, low, close, volume, window=W)
        # Assert: 全チェーンバッファが全 NaN（有限値が混入しない）。
        for name in ("level_count", "fast", "slow", "macd", "signal", "histogram"):
            arr = getattr(res, name)
            assert np.all(np.isnan(arr)), f"{name} not all-NaN for n<window"
