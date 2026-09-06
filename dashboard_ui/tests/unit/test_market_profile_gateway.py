"""MP 列の素材（依頼者承認 2026-09-06): 行の水準価格 → 直近 1m プロファイルの TPO 密度 norm。

固定する核心:
  - プロファイルは**シート共通の 1 本**（dataset_ref の 1m 確定足・直近 MP_WINDOW_BARS 本）。
  - ビン数は固定値ではなく**ビン幅から導出**する（`MP_BIN_WIDTH_POINTS` と窓の span）。
    上限は `MP_MAX_BINS` でクランプする（計算量の保護）。
  - 行価格 → bin は `price_min` と bin 幅の算術で引く（bins[].price は bin 中心・実測済み）。
  - 範囲外・素材なしは **None**（発明しない）。0.0 で埋めると「密度が最小」と読める。
  - 供給の末尾 1 本は**無条件に**窓から落とす（P-2 の契約: `bars()[-1]` は形成中でありうる）。
    時計（`now_unix`）を窓の決定に使わない——`now_unix` は表示足の末尾 time であり、
    表示足が 1m より粗いと形成中の 1m 足を「確定」と誤って窓へ入れてしまう。

計算量（絶対命令 §4.1）は `dashboard_ui/tests/complexity/test_market_profile_computed_once.py`
が別途固定する（本ファイルは状態検証＝出力の正しさだけを見る）。
"""
from __future__ import annotations

import pytest

from dashboard_ui.adapter.gateway import market_profile_gateway
from dashboard_ui.adapter.gateway.market_profile_gateway import (
    MP_BIN_WIDTH_POINTS,
    MP_MAX_BINS,
    MP_TIMEFRAME,
    MP_WINDOW_BARS,
    MarketProfileGateway,
    _bins_for,
    _norm_at,
    _reference_compute,
)
from dashboard_ui.adapter.gateway.material_store import MaterialStore
from dashboard_ui.domain.bar import Bar
from dashboard_ui.usecase.sheet_ports import MarketProfilePort

REF = "jp225_tick"
#: 2026-08-01 00:00:00 UTC（1m の境界に載る時刻）。
START = 1_785_542_400
MINUTE = 60


def _bars(count: int, *, base: float = 100.0) -> "tuple[Bar, ...]":
    return tuple(
        Bar(time=START + index * MINUTE, open=base + index, high=base + index + 5.0,
            low=base + index - 5.0, close=base + index)
        for index in range(count)
    )


def _flat_bars(count: int, *, low: float, high: float) -> "tuple[Bar, ...]":
    """価格域を `[low, high]` に固定した足。

    どの部分列を取っても span が `high - low` のままなので、**span がビン数へどう効くか**
    だけを見る検定の素材になる（本数や窓の切り出しが span を動かさない）。
    """
    return tuple(
        Bar(time=START + index * MINUTE, open=low, high=high, low=low, close=high)
        for index in range(count)
    )


class BarPortFake:
    """P-2 の代役。`forming` を立てると末尾の足を形成中として返す（bars の末尾と同一物）。

    どの時間足を訊かれたか・形成中足を訊かれたかを記録する。gateway が窓の決定に
    時計を使っていないことは「`forming_bar` を呼んでいない」で機械的に見える。
    """

    def __init__(self, bars_by_timeframe, *, forming: bool = False) -> None:
        self._bars_by_timeframe = dict(bars_by_timeframe)
        self._forming = bool(forming)
        self.asked_timeframes: "list[str]" = []
        self.forming_calls: "list[str]" = []

    def bars(self, *, dataset_ref, timeframe):
        self.asked_timeframes.append(timeframe)
        return self._bars_by_timeframe.get(timeframe, ())

    def forming_bar(self, *, dataset_ref, timeframe, now_unix):
        self.forming_calls.append(timeframe)
        supplied = self._bars_by_timeframe.get(timeframe) or ()
        return supplied[-1] if (self._forming and supplied) else None


class ComputeSpy:
    """MP 計算面（参照実装 `market_profile_api/compute/market_profile.py`）の Test Spy。

    返す形は実測済みの応答そのもの（bins[].price は bin 中心・norm は 0..1）。
    価格域 [100, 160] を 6 bin（幅 10）に割り、bin ごとに違う norm を持たせる
    ＝「どの bin を引いたか」が出力から一意に読める素材にする。
    """

    def __init__(self) -> None:
        self.calls: "list[tuple[tuple, int]]" = []

    def __call__(self, candles, *, n_bins):
        self.calls.append((tuple(int(candle["time"]) for candle in candles), int(n_bins)))
        return profile()


class PriceRangeSpy:
    """価格レンジ面（`market_profile_gateway._core_price_range`）の Test Spy。

    引いた事実と、返す値を握る。

    返り値は**素材と無関係に**呼び出し側が決める。`_bins_for` が「この面が返したレンジ」に
    追従するのか、それとも素材の高安を自分で読み直しているのかは、両者が食い違う入力を
    作らなければ区別できない——`max(high) - min(low)` を書き写した実装は縮退窓でも
    `max(1, …)` に吸収されて同じ 1 ビンを返すため、素材を工夫しても分かれ道が現れない。
    面を差し替えて初めて分かれる。
    """

    def __init__(self, *, low: float, high: float) -> None:
        self.calls: "list[int]" = []
        self._range = (float(low), float(high))

    def __call__(self, candles) -> "tuple[float, float]":
        self.calls.append(len(candles))
        return self._range


def profile() -> dict:
    return {
        "bins": [
            {"price": 105.0, "tpo": 1, "norm": 0.1},
            {"price": 115.0, "tpo": 2, "norm": 0.2},
            {"price": 125.0, "tpo": 3, "norm": 0.3},
            {"price": 135.0, "tpo": 4, "norm": 0.4},
            {"price": 145.0, "tpo": 5, "norm": 0.5},
            {"price": 155.0, "tpo": 6, "norm": 1.0},
        ],
        "poc": 155.0,
        "va_low": 125.0,
        "va_high": 155.0,
        "price_min": 100.0,
        "price_max": 160.0,
        "tpo_units": 60,
        "n_bins": 6,
    }


def gateway_of(bar_port, compute) -> MarketProfileGateway:
    return MarketProfileGateway(bar_port=bar_port, store=MaterialStore(), compute=compute)


class TestNorms:
    def test_a_price_inside_the_profile_takes_the_norm_of_the_bin_that_contains_it(
        self,
    ) -> None:
        # Arrange
        compute = ComputeSpy()
        port = gateway_of(BarPortFake({MP_TIMEFRAME: _bars(10)}), compute)

        # Act: bin 中心・bin 内の任意点・下端・上端（同値クラスの代表と境界）。
        norms = port.norms_at(
            dataset_ref=REF, prices=(105.0, 152.0, 100.0, 160.0), now_unix=START,
        )

        # Assert
        assert norms == (0.1, 1.0, 0.1, 1.0)

    def test_a_price_outside_the_profile_range_has_no_norm(self) -> None:
        # Arrange: 境界のすぐ外側（下端未満・上端超過）。
        compute = ComputeSpy()
        port = gateway_of(BarPortFake({MP_TIMEFRAME: _bars(10)}), compute)

        # Act
        norms = port.norms_at(dataset_ref=REF, prices=(99.9, 160.1), now_unix=START)

        # Assert: 0.0 で埋めない（「密度が最小」と読める・発明しない）。
        assert norms == (None, None)

    def test_a_dataset_without_minute_bars_has_no_norm_and_computes_nothing(self) -> None:
        # Arrange
        compute = ComputeSpy()
        port = gateway_of(BarPortFake({MP_TIMEFRAME: ()}), compute)

        # Act
        norms = port.norms_at(dataset_ref=REF, prices=(105.0, 145.0), now_unix=START)

        # Assert: 素材が無ければ全行 None。無い素材で計算を発行もしない。
        assert norms == (None, None)
        assert compute.calls == []

    def test_the_prices_and_the_norms_line_up_one_to_one(self) -> None:
        """行と密度の対応がずれない（順序も件数も要求どおり）。"""
        # Arrange
        port = gateway_of(BarPortFake({MP_TIMEFRAME: _bars(10)}), ComputeSpy())
        prices = (155.0, 99.0, 105.0)

        # Act
        norms = port.norms_at(dataset_ref=REF, prices=prices, now_unix=START)

        # Assert
        assert len(norms) == len(prices)
        assert norms == (1.0, None, 0.1)

    def test_no_prices_asks_for_nothing(self) -> None:
        """境界値: 行が 1 本も無いシート（要求 0 件）。"""
        # Arrange
        compute = ComputeSpy()
        port = gateway_of(BarPortFake({MP_TIMEFRAME: _bars(10)}), compute)

        # Act
        norms = port.norms_at(dataset_ref=REF, prices=(), now_unix=START)

        # Assert
        assert norms == ()
        assert compute.calls == []


class TestTheDeclaredMaterial:
    """素材の名乗り（依頼者承認 2026-09-06「a) 1m 足の短窓へ変更」）。

    数（本数・ビン幅）は依頼者が承認した値そのものなので、値として固定する。
    「1m」を読む口であることは版面ではなく**供給に何を訊いたか**で確かめる。
    """

    def test_the_material_is_read_from_the_one_minute_supply(self) -> None:
        # Arrange: 1m 以外も供給されている場（誤って粗い足を掴んでいたら見える）。
        bar_port = BarPortFake({"1m": _bars(10), "1D": _bars(10, base=900.0)})
        port = gateway_of(bar_port, ComputeSpy())

        # Act
        port.norms_at(dataset_ref=REF, prices=(105.0,), now_unix=START)

        # Assert
        assert bar_port.asked_timeframes == ["1m"]

    def test_the_declared_window_and_bin_width_are_the_approved_ones(self) -> None:
        """依頼者承認の実測選定値（1m×2000 本・幅 5pt・上限 2000 ビン）。"""
        # Arrange / Act / Assert
        assert (MP_WINDOW_BARS, MP_BIN_WIDTH_POINTS, MP_MAX_BINS) == (2000, 5.0, 2000)

    def test_the_supply_limit_covers_the_declared_window(self) -> None:
        """供給が窓より短ければ、名乗った本数を**決して**満たせない（無言で短い窓になる）。"""
        # Arrange
        from dashboard_ui.main.composition_root import BAR_LIMITS

        # Act / Assert
        assert BAR_LIMITS[MP_TIMEFRAME] >= MP_WINDOW_BARS


class TestWindow:
    @pytest.mark.parametrize("forming", [True, False])
    def test_the_supplied_tail_bar_is_dropped_whether_or_not_a_forming_bar_is_reported(
        self, forming: bool
    ) -> None:
        """末尾 1 本は**無条件に**落とす（P-2 の契約: 確定足を見たい側は `bars()[-2]`）。

        形成中判定に頼ると、表示足が 1m より粗いとき（`now_unix` は表示足の末尾 time）に
        形成中の 1m 足が窓へ残り、epoch がティックごとに動く。
        """
        # Arrange
        supplied = _bars(10)
        compute = ComputeSpy()
        port = gateway_of(BarPortFake({MP_TIMEFRAME: supplied}, forming=forming), compute)

        # Act
        port.norms_at(dataset_ref=REF, prices=(105.0,), now_unix=START)

        # Assert
        times, _n_bins = compute.calls[0]
        assert times == tuple(int(bar.time) for bar in supplied[:-1])

    def test_the_window_never_consults_the_forming_bar(self) -> None:
        """窓の決定に時計を使わない（決定的・休場でも周期でも同じ窓）。"""
        # Arrange
        bar_port = BarPortFake({MP_TIMEFRAME: _bars(10)}, forming=True)
        port = gateway_of(bar_port, ComputeSpy())

        # Act
        port.norms_at(dataset_ref=REF, prices=(105.0,), now_unix=START)

        # Assert
        assert bar_port.forming_calls == []

    def test_the_window_is_the_same_whatever_the_clock_says(self) -> None:
        """境界: `now_unix` が 1m の周期に載らない値（粗い表示足の末尾 time）でも窓は同じ。"""
        # Arrange: ストアを分けて 2 回とも実計算させる（共有で 2 回目が省かれない）。
        supplied = _bars(10)
        compute = ComputeSpy()
        gateway_of(BarPortFake({MP_TIMEFRAME: supplied}), compute).norms_at(
            dataset_ref=REF, prices=(105.0,), now_unix=START,
        )

        # Act: 1m の境界に載らない・遠い時刻。
        gateway_of(BarPortFake({MP_TIMEFRAME: supplied}), compute).norms_at(
            dataset_ref=REF, prices=(105.0,), now_unix=START + 37 * MINUTE + 11,
        )

        # Assert
        assert compute.calls[0][0] == compute.calls[1][0]

    def test_the_window_keeps_only_the_declared_number_of_the_latest_confirmed_bars(
        self,
    ) -> None:
        """窓は確定足（末尾 1 本を除いた残り）の末尾 MP_WINDOW_BARS 本。"""
        # Arrange
        supplied = _bars(MP_WINDOW_BARS + 7)
        compute = ComputeSpy()
        port = gateway_of(BarPortFake({MP_TIMEFRAME: supplied}), compute)

        # Act
        port.norms_at(dataset_ref=REF, prices=(105.0,), now_unix=START)

        # Assert
        times, _n_bins = compute.calls[0]
        assert times == tuple(int(bar.time) for bar in supplied[:-1][-MP_WINDOW_BARS:])

    def test_a_window_shorter_than_the_declared_length_is_used_as_it_is(self) -> None:
        """境界値: 素材が窓より短い（起動直後）。短いまま計算する（発明も切り捨てもしない）。"""
        # Arrange
        supplied = _bars(3)
        compute = ComputeSpy()
        port = gateway_of(BarPortFake({MP_TIMEFRAME: supplied}), compute)

        # Act
        port.norms_at(dataset_ref=REF, prices=(105.0,), now_unix=START)

        # Assert
        times, _n_bins = compute.calls[0]
        assert times == tuple(int(bar.time) for bar in supplied[:-1])

    def test_a_single_supplied_bar_leaves_no_confirmed_window(self) -> None:
        """境界値: 供給が 1 本だけ（＝確定足 0 本）。素材なしとして扱い、計算も発行しない。"""
        # Arrange
        compute = ComputeSpy()
        port = gateway_of(BarPortFake({MP_TIMEFRAME: _bars(1)}), compute)

        # Act
        norms = port.norms_at(dataset_ref=REF, prices=(105.0,), now_unix=START)

        # Assert
        assert norms == (None,)
        assert compute.calls == []


class TestTheBinWidthDrivesTheBinCount:
    """ビン数は固定値ではなく**窓の span とビン幅**から決まる（依頼者承認 2026-09-06）。

    固定ビン数（旧 `MP_BINS`）だと、素材が広いほどビンが粗くなり、可視ラダー域（±130pt）が
    1〜2 ビンへ潰れて行ごとの差が消える（実測 2026-09-06: norm 0.83〜0.96・distinct 2/7）。
    幅を固定すれば版面の分解能が素材の広さに依らない。
    """

    @pytest.mark.parametrize(
        ("span", "expected"),
        [
            (100.0, 20),    # 幅の整数倍（5pt × 20）
            (102.0, 20),    # 端数は切り捨て（20.4 → 20・幅より細いビンを作らない）
            (5.0, 1),       # 境界: 幅ちょうど
            (4.0, 1),       # 境界: 幅より狭い（0 ビンにはしない）
        ],
    )
    def test_the_bin_count_is_the_span_divided_by_the_declared_bin_width(
        self, span: float, expected: int
    ) -> None:
        # Arrange
        supplied = _flat_bars(4, low=30_000.0, high=30_000.0 + span)
        compute = ComputeSpy()
        port = gateway_of(BarPortFake({MP_TIMEFRAME: supplied}), compute)

        # Act
        port.norms_at(dataset_ref=REF, prices=(105.0,), now_unix=START)

        # Assert: 具体値と、幅の宣言（定数）の双方に紐付ける。
        _times, n_bins = compute.calls[0]
        assert n_bins == expected
        assert n_bins == max(1, int(span / MP_BIN_WIDTH_POINTS))

    def test_a_huge_span_is_clamped_to_the_declared_maximum(self) -> None:
        """上限クランプ（計算量の保護）。期待値は定数参照——裸の数を書かない。

        `MP_WINDOW_BARS` と `MP_MAX_BINS` は同じリテラルなので、span は「クランプが効く
        境目」より確実に大きく取り、クランプが実際に効く配置であることも併せて固定する。
        """
        # Arrange
        span = MP_MAX_BINS * MP_BIN_WIDTH_POINTS * 3.0
        supplied = _flat_bars(4, low=10_000.0, high=10_000.0 + span)
        compute = ComputeSpy()
        port = gateway_of(BarPortFake({MP_TIMEFRAME: supplied}), compute)

        # Act
        port.norms_at(dataset_ref=REF, prices=(105.0,), now_unix=START)

        # Assert
        _times, n_bins = compute.calls[0]
        assert int(span / MP_BIN_WIDTH_POINTS) > MP_MAX_BINS, "クランプが効かない配置です"
        assert n_bins == MP_MAX_BINS

    def test_a_degenerate_window_still_folds_into_one_bin(self) -> None:
        """境界: 高安が 1 点に潰れた窓（休場直後の同値足）でも 0 ビンを core へ渡さない。

        ここが固定するのは**下限クランプ**だけである（上限クランプの対称）。span の唯一源が
        core であることは固定できない: 手書きの max(high) - min(low) を注いでも同じ 1 ビンを
        返すため、この検定は緑のまま通る（実測 2026-09-06・変異注入で確認）。core の安全化
        （上端 = 下端 + 1）と写しの span = 0 の差 1pt は、どちらも `max(1, …)` に吸収されて
        出力に現れないからである。唯一源の固定は面を差し替える
        `test_the_bin_count_follows_the_core_range_definition_not_the_raw_high_and_low`
        が担う。
        """
        # Arrange
        supplied = _flat_bars(4, low=30_000.0, high=30_000.0)
        compute = ComputeSpy()
        port = gateway_of(BarPortFake({MP_TIMEFRAME: supplied}), compute)

        # Act
        port.norms_at(dataset_ref=REF, prices=(105.0,), now_unix=START)

        # Assert
        _times, n_bins = compute.calls[0]
        assert n_bins == 1

    @pytest.mark.parametrize("widths", [3, 17])
    def test_the_bin_count_follows_the_core_range_definition_not_the_raw_high_and_low(
        self, monkeypatch: pytest.MonkeyPatch, widths: int
    ) -> None:
        """span の唯一源が core の価格レンジ面であることを**機械的に**固定する。

        なぜ素材では固定できないか（実測 2026-09-06）: 高安を書き写した実装を注いでも、
        縮退窓（span 0）は `max(1, …)` に吸収されて core 由来と同じ 1 ビンを返す。出力に
        差が出ないので、素材だけを工夫する検定は手書きの写しを通してしまう（vacuous）。
        そこで面を差し替え、**返ってきたレンジに n_bins が追従するか**を見る。素材の高安は
        1 点に潰したままなので、写しを持つ実装はレンジを無視して 1 ビンに落ちる。

        追従は 2 点（3 幅 / 17 幅）で固定する——1 点だと定数を返すだけの実装も通る。
        """
        # Arrange: 素材の高安は潰す（写しを持つ実装なら span = 0）。面だけが広さを知る。
        degenerate = [{"time": 0, "open": 5.0, "high": 5.0, "low": 5.0, "close": 5.0}]
        spy = PriceRangeSpy(low=5.0, high=5.0 + widths * MP_BIN_WIDTH_POINTS)
        monkeypatch.setattr(market_profile_gateway, "_core_price_range", spy)

        # Act
        n_bins = _bins_for(degenerate)

        # Assert: 面を引いており（回数は焼き込まない）、返り値へ追従している。
        assert spy.calls != []
        assert n_bins == widths

    def test_the_span_comes_from_the_window_not_from_the_dropped_tail_bar(self) -> None:
        """span と窓は同じ素材から来る（落とした足が span に混ざらない）。

        混ざると、形成中の足が走るたびにビン数が動き、epoch が同じでも版面の分解能が
        揺れる。落とす 1 本だけを極端に広く取れば、混入は即座に見える。
        """
        # Arrange
        body = _flat_bars(4, low=30_000.0, high=30_100.0)
        tail = Bar(time=START + 4 * MINUTE, open=30_000.0, high=90_000.0,
                   low=1_000.0, close=30_000.0)
        compute = ComputeSpy()
        port = gateway_of(BarPortFake({MP_TIMEFRAME: (*body, tail)}), compute)

        # Act
        port.norms_at(dataset_ref=REF, prices=(105.0,), now_unix=START)

        # Assert: 窓の span は 100pt のまま（末尾を混ぜれば 89,000pt になる）。
        _times, n_bins = compute.calls[0]
        assert n_bins == int(100.0 / MP_BIN_WIDTH_POINTS)


def core_bin_index():
    """MP core の bin 帰属（参照実装）。写しを持たず、実物を読んで突き合わせる。

    探索パスの用意は `api_loader` が唯一源（本番経路と同じ順序で用意する）。
    """
    from indigators.indicator_ui import api_loader

    api_loader.load_compute()
    from market_profile_api.compute.market_profile import _bin_index

    return _bin_index


#: 帰属式の食い違いが実際に出る価格域（JP225 の実勢に近い値・実測 2026-09-06）。
#: 100..160 のような小さい域では 2 式が偶然一致してしまい、検定が空振りする。
DIVERGENT_MIN, DIVERGENT_MAX = 38_000.0, 42_000.0
#: 同じく実測で食い違いが出たビン数。gateway が使うビン数（幅から導出）とは無関係である
#: ——ここで見るのは `_norm_at` の**式**であって、素材の分解能ではない。
DIVERGENT_BINS = 60


def lattice_profile(price_min: float, price_max: float, n_bins: int) -> dict:
    """bin ごとに**相異なる** norm を持つプロファイル（norm から bin が一意に読める）。"""
    return {
        "bins": [
            {"price": price_min + (index + 0.5) * (price_max - price_min) / n_bins,
             "tpo": index + 1, "norm": index / (n_bins - 1)}
            for index in range(n_bins)
        ],
        "poc": price_max, "va_low": price_min, "va_high": price_max,
        "price_min": price_min, "price_max": price_max,
        "tpo_units": n_bins, "n_bins": n_bins,
    }


class TestBinAttributionMatchesTheCore:
    """行価格 → bin の帰属を MP core（参照実装）と一致させる。

    なぜ差分検定なのか: 2 つの式は代数的には同じでも、浮動小数では**境界ちょうど**で
    別の bin へ落ちる。`(price - price_min) / width` は幅を先に割るので丸めが 1 回増える。
    実測（price_min=38000 / price_max=42000 / n_bins=60）で bin 境界 61 点のうち 6 点が
    core と食い違った。表示は「隣の bin の濃さ」になるだけで**出力は正しく見える**ため、
    状態検証では原理的に落ちない種類のずれである。
    """

    def test_the_lattice_actually_separates_the_bins(self) -> None:
        """検定の検定: norm が重複していたら「同じ bin を引いた」が偽陽性になる。"""
        # Arrange / Act
        profile = lattice_profile(DIVERGENT_MIN, DIVERGENT_MAX, DIVERGENT_BINS)
        norms = [bin_["norm"] for bin_ in profile["bins"]]

        # Assert
        assert len(set(norms)) == DIVERGENT_BINS

    @pytest.mark.parametrize("n_bins", [DIVERGENT_BINS, 7])
    def test_every_bin_boundary_lands_in_the_same_bin_as_the_core(self, n_bins: int) -> None:
        """bin 境界ちょうどの価格で、gateway の引きが core と一致する。"""
        # Arrange
        bin_index = core_bin_index()
        profile = lattice_profile(DIVERGENT_MIN, DIVERGENT_MAX, n_bins)
        span = DIVERGENT_MAX - DIVERGENT_MIN
        width = span / n_bins
        # 境界ちょうど・境界の直前後（同値クラスの境界とその代表）。域内の絞り込みは
        #   素材の構築で行う——テスト本体に分岐を置かない（域外は None を返す面であり、
        #   帰属の話ではない）。
        prices = [
            price
            for step in range(n_bins + 1)
            for offset in (0.0, -1e-9, 1e-9)
            for price in (DIVERGENT_MIN + step * width + offset,)
            if DIVERGENT_MIN <= price <= DIVERGENT_MAX
        ]

        # Act / Assert
        for price in prices:
            expected = profile["bins"][bin_index(price, DIVERGENT_MIN, span, n_bins)]["norm"]
            assert _norm_at(profile, price) == expected, f"price={price!r} で core と食い違います"

    def test_a_dense_sweep_across_the_range_agrees_with_the_core(self) -> None:
        """境界以外も含めた掃引（帰属の一致は境界だけの話ではない）。"""
        # Arrange
        bin_index = core_bin_index()
        profile = lattice_profile(DIVERGENT_MIN, DIVERGENT_MAX, DIVERGENT_BINS)
        span = DIVERGENT_MAX - DIVERGENT_MIN
        steps = 5_000

        # Act
        mismatches = [
            price
            for step in range(steps + 1)
            for price in (DIVERGENT_MIN + span * step / steps,)
            if _norm_at(profile, price)
            != profile["bins"][bin_index(price, DIVERGENT_MIN, span, DIVERGENT_BINS)]["norm"]
        ]

        # Assert
        assert mismatches == []


class TestTheDefaultComputePath:
    """注入なし＝**実経路**（`_reference_compute` → api_loader → MP core の実計算）。

    他の検定はすべて `ComputeSpy` を注ぐので、既定の口が壊れても（探索パスの用意が抜けた・
    core 側で名前が変わった・引数の並びが変わった）どれも赤くならない。ここだけが実物を
    通す。素材ファイルは要らず、実測 0.3 秒で終わる（2026-09-06）。
    """

    def test_the_gateway_without_an_injected_compute_folds_a_real_profile(self) -> None:
        # Arrange: compute を渡さない（既定の参照実装が使われる）。
        port = MarketProfileGateway(
            bar_port=BarPortFake({MP_TIMEFRAME: _bars(3)}), store=MaterialStore(),
        )

        # Act: 窓（_bars(3) の末尾 1 本を落とした残り＝low 95..96 / high 105..106）の内と外。
        norms = port.norms_at(dataset_ref=REF, prices=(100.0, 1.0), now_unix=START)

        # Assert: 域内は 0..1 の密度・域外は None（発明しない）。
        assert norms[0] is not None and 0.0 <= norms[0] <= 1.0
        assert norms[1] is None

    def test_the_real_core_range_keeps_the_narrowest_windows_at_one_bin(self) -> None:
        """**実物の** core レンジを通した下限側の境界 2 点（面を差し替えない唯一の経路）。

        名前も中身も「実窓」「ゲートウェイ」ではない: ここは `_bins_for` を直に呼び、
        差し替えていない `market_profile_gateway._core_price_range`（＝実物の MP core）を
        通す。固定するのは、core が返すレンジでも最も狭い 2 つの窓が 1 ビンに収まること
        ——縮退窓（core が上端 = 下端 + 1 へ安全化）と、幅ちょうど 1 本ぶんの窓である。

        span の唯一源が core であることは、ここでは固定できない（手書きの
        max(high) - min(low) を注いでも同じ 1 ビンを返す・実測 2026-09-06）。それは
        `test_the_bin_count_follows_the_core_range_definition_not_the_raw_high_and_low`
        の担当である。
        """
        # Arrange
        degenerate = [{"time": 0, "open": 5.0, "high": 5.0, "low": 5.0, "close": 5.0}]
        # 幅ちょうど 1 本ぶんの広さ（境界の反対側・クランプもしない）。
        one_width = [
            {"time": 0, "open": 5.0, "high": 5.0 + MP_BIN_WIDTH_POINTS, "low": 5.0,
             "close": 5.0},
        ]

        # Act / Assert
        assert _bins_for(degenerate) == 1
        assert _bins_for(one_width) == 1

    def test_the_reference_compute_returns_the_shape_the_gateway_reads(self) -> None:
        """gateway が読む欄が実物に在ること（欄の名前は core の契約）。"""
        # Arrange: ビン数は幅から決まるので、ここでは任意の値を置いて**形**だけを見る。
        asked_bins = 24
        candles = [
            {"time": 0, "open": 1.0, "high": 3.0, "low": 0.5, "close": 2.0},
            {"time": 60, "open": 2.0, "high": 4.0, "low": 1.5, "close": 3.0},
        ]

        # Act
        profile = _reference_compute(candles, n_bins=asked_bins)

        # Assert: gateway の `_norm_at` が触る欄がすべて在る。
        for field in ("bins", "price_min", "price_max", "n_bins"):
            assert field in profile, f"core の応答に {field} がありません"
        # 実物が畳んだ証拠（Spy の作り物は n_bins=6 を返すので、ここは実経路でしか通らない）。
        assert profile["n_bins"] == asked_bins
        assert len(profile["bins"]) == asked_bins
        assert profile["price_min"] < profile["price_max"]
        # 正規化の定義（0..1）が core 側で崩れていないこと。
        assert all(0.0 <= float(bin_["norm"]) <= 1.0 for bin_ in profile["bins"])
        assert max(float(bin_["norm"]) for bin_ in profile["bins"]) == 1.0


class TestTheGatewayIsWiredToThePort:
    """具象が P-MP の面を満たすこと。

    usecase（`dashboard_ui/usecase/build_reach_sheet.py`）は `MarketProfilePort`
    越しにしか密度を知らない。面と
    具象がずれても、注入している側は Protocol を実行時に照合しないので**無言で通る**
    （合成の口が壊れていることは実 UI まで出て来ない）。ここで一度だけ突き合わせる。
    """

    def test_the_gateway_satisfies_the_market_profile_port(self) -> None:
        # Arrange
        gateway = MarketProfileGateway(
            bar_port=BarPortFake({MP_TIMEFRAME: _bars(2)}), store=MaterialStore(),
        )

        # Act / Assert
        assert isinstance(gateway, MarketProfilePort)


class TestSupplyFailuresAreNotSwallowed:
    """MP を作れない失敗は貫通させる（`sheet_ports.MarketProfilePort` の決定）。

    `None` は版面では「この価格に密度が無い」と読める値である。失敗を `None` に変換すると
    「計算できなかった」が「密度が無かった」として黙って表示され、読み手には区別が付かない
    ——しかも MP は instance を持たないので縮退告知の口も無い。握り潰しを禁ずる規律は
    docstring に書くだけでは守られないので、ここで機械的に固定する。
    """

    def test_a_broken_compute_surfaces_instead_of_becoming_empty_rows(self) -> None:
        # Arrange: core が配置されていない状況を模す（実経路の失敗の代表）。
        def broken_compute(candles, *, n_bins):
            raise ImportError("market_profile_api is not on the path")

        port = gateway_of(BarPortFake({MP_TIMEFRAME: _bars(10)}), broken_compute)

        # Act / Assert: 全行 None へ黙って落ちない。
        with pytest.raises(ImportError):
            port.norms_at(dataset_ref=REF, prices=(105.0, 145.0), now_unix=START)

    def test_a_core_contract_change_surfaces_too(self) -> None:
        """境界: 欄の名前・引数の並びが変わった場合も同じ扱い（黙って空にしない）。"""
        # Arrange
        def contract_changed(candles, *, n_bins):
            raise TypeError("compute_candle_profile() got an unexpected keyword")

        port = gateway_of(BarPortFake({MP_TIMEFRAME: _bars(10)}), contract_changed)

        # Act / Assert
        with pytest.raises(TypeError):
            port.norms_at(dataset_ref=REF, prices=(105.0,), now_unix=START)
