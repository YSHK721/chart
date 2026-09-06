"""MP 列の素材（依頼者承認 2026-09-06): 行の水準価格 → 直近 1D プロファイルの TPO 密度 norm。

固定する核心:
  - プロファイルは**シート共通の 1 本**（dataset_ref の 1D 確定足・直近 MP_WINDOW_BARS 本）。
  - 行価格 → bin は `price_min` と bin 幅の算術で引く（bins[].price は bin 中心・実測済み）。
  - 範囲外・素材なしは **None**（発明しない）。0.0 で埋めると「密度が最小」と読める。
  - 形成中の 1D 足は窓に入れない（epoch 安定・更新粒度はバー確定）。

計算量（絶対命令 §4.1）は `dashboard_ui/tests/complexity/test_market_profile_computed_once.py`
が別途固定する（本ファイルは状態検証＝出力の正しさだけを見る）。
"""
from __future__ import annotations

import pytest

from dashboard_ui.adapter.gateway.market_profile_gateway import (
    MP_BINS,
    MP_TIMEFRAME,
    MP_WINDOW_BARS,
    MarketProfileGateway,
    _norm_at,
    _reference_compute,
)
from dashboard_ui.adapter.gateway.material_store import MaterialStore
from dashboard_ui.domain.bar import Bar
from dashboard_ui.usecase.sheet_ports import MarketProfilePort

REF = "jp225_tick"
#: 2026-08-01 00:00:00 UTC（1D の境界に載る時刻）。
START = 1_785_542_400
DAY = 86_400


def _bars(count: int, *, base: float = 100.0) -> "tuple[Bar, ...]":
    return tuple(
        Bar(time=START + index * DAY, open=base + index, high=base + index + 5.0,
            low=base + index - 5.0, close=base + index)
        for index in range(count)
    )


class BarPortFake:
    """P-2 の代役。`forming` を立てると末尾の足を形成中として返す（bars の末尾と同一物）。"""

    def __init__(self, bars_by_timeframe, *, forming: bool = False) -> None:
        self._bars_by_timeframe = dict(bars_by_timeframe)
        self._forming = bool(forming)

    def bars(self, *, dataset_ref, timeframe):
        return self._bars_by_timeframe.get(timeframe, ())

    def forming_bar(self, *, dataset_ref, timeframe, now_unix):
        supplied = self._bars_by_timeframe.get(timeframe) or ()
        return supplied[-1] if (self._forming and supplied) else None


class ComputeSpy:
    """MP 計算面（参照実装 `compute_candle_profile`）の Test Spy。

    返す形は実測済みの応答そのもの（bins[].price は bin 中心・norm は 0..1）。
    価格域 [100, 160] を 6 bin（幅 10）に割り、bin ごとに違う norm を持たせる
    ＝「どの bin を引いたか」が出力から一意に読める素材にする。
    """

    def __init__(self) -> None:
        self.calls: "list[tuple[tuple, int]]" = []

    def __call__(self, candles, *, n_bins):
        self.calls.append((tuple(int(candle["time"]) for candle in candles), int(n_bins)))
        return profile()


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

    def test_a_dataset_without_daily_bars_has_no_norm_and_computes_nothing(self) -> None:
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


class TestWindow:
    def test_the_forming_daily_bar_is_left_out_of_the_window(self) -> None:
        """形成中の 1D 足を入れると epoch が毎ティック動く（更新粒度はバー確定・§7）。"""
        # Arrange
        supplied = _bars(10)
        compute = ComputeSpy()
        port = gateway_of(BarPortFake({MP_TIMEFRAME: supplied}, forming=True), compute)

        # Act
        port.norms_at(dataset_ref=REF, prices=(105.0,), now_unix=START)

        # Assert: 末尾（形成中）の 1 本だけが窓から外れる。
        times, _n_bins = compute.calls[0]
        assert times == tuple(int(bar.time) for bar in supplied[:-1])

    def test_a_confirmed_last_bar_stays_in_the_window(self) -> None:
        """形成中足が無い（周期が閉じている）なら末尾も確定足＝窓に入る。"""
        # Arrange
        supplied = _bars(10)
        compute = ComputeSpy()
        port = gateway_of(BarPortFake({MP_TIMEFRAME: supplied}), compute)

        # Act
        port.norms_at(dataset_ref=REF, prices=(105.0,), now_unix=START)

        # Assert
        times, _n_bins = compute.calls[0]
        assert times == tuple(int(bar.time) for bar in supplied)

    def test_the_window_keeps_only_the_declared_number_of_the_latest_bars(self) -> None:
        """窓は末尾 MP_WINDOW_BARS 本（依頼者承認 2026-09-06: 1D×60 本）。"""
        # Arrange
        supplied = _bars(MP_WINDOW_BARS + 7)
        compute = ComputeSpy()
        port = gateway_of(BarPortFake({MP_TIMEFRAME: supplied}), compute)

        # Act
        port.norms_at(dataset_ref=REF, prices=(105.0,), now_unix=START)

        # Assert
        times, n_bins = compute.calls[0]
        assert times == tuple(int(bar.time) for bar in supplied[-MP_WINDOW_BARS:])
        assert n_bins == MP_BINS

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
        assert times == tuple(int(bar.time) for bar in supplied)


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
        profile = lattice_profile(DIVERGENT_MIN, DIVERGENT_MAX, MP_BINS)
        norms = [bin_["norm"] for bin_ in profile["bins"]]

        # Assert
        assert len(set(norms)) == MP_BINS

    @pytest.mark.parametrize("n_bins", [MP_BINS, 7])
    def test_every_bin_boundary_lands_in_the_same_bin_as_the_core(self, n_bins: int) -> None:
        """bin 境界ちょうどの価格で、gateway の引きが core と一致する。"""
        # Arrange
        bin_index = core_bin_index()
        profile = lattice_profile(DIVERGENT_MIN, DIVERGENT_MAX, n_bins)
        span = DIVERGENT_MAX - DIVERGENT_MIN
        width = span / n_bins
        # 境界ちょうど・境界の直前後（同値クラスの境界とその代表）。
        prices = [
            DIVERGENT_MIN + step * width + offset
            for step in range(n_bins + 1)
            for offset in (0.0, -1e-9, 1e-9)
        ]

        # Act / Assert
        for price in prices:
            if not (DIVERGENT_MIN <= price <= DIVERGENT_MAX):
                continue  # 域外は None を返す面（帰属の話ではない）。
            expected = profile["bins"][bin_index(price, DIVERGENT_MIN, span, n_bins)]["norm"]
            assert _norm_at(profile, price) == expected, f"price={price!r} で core と食い違います"

    def test_a_dense_sweep_across_the_range_agrees_with_the_core(self) -> None:
        """境界以外も含めた掃引（帰属の一致は境界だけの話ではない）。"""
        # Arrange
        bin_index = core_bin_index()
        profile = lattice_profile(DIVERGENT_MIN, DIVERGENT_MAX, MP_BINS)
        span = DIVERGENT_MAX - DIVERGENT_MIN
        steps = 5_000

        # Act
        mismatches = [
            price
            for step in range(steps + 1)
            for price in (DIVERGENT_MIN + span * step / steps,)
            if _norm_at(profile, price)
            != profile["bins"][bin_index(price, DIVERGENT_MIN, span, MP_BINS)]["norm"]
        ]

        # Assert
        assert mismatches == []


class TestTheDefaultComputePath:
    """注入なし＝**実経路**（`_reference_compute` → api_loader → `compute_candle_profile`）。

    他の検定はすべて `ComputeSpy` を注ぐので、既定の口が壊れても（探索パスの用意が抜けた・
    core 側で名前が変わった・引数の並びが変わった）どれも赤くならない。ここだけが実物を
    通す。素材ファイルは要らず、実測 0.3 秒で終わる（2026-09-06）。
    """

    def test_the_gateway_without_an_injected_compute_folds_a_real_profile(self) -> None:
        # Arrange: compute を渡さない（既定の参照実装が使われる）。
        port = MarketProfileGateway(
            bar_port=BarPortFake({MP_TIMEFRAME: _bars(2)}), store=MaterialStore(),
        )

        # Act: 素材の値域（_bars(2) は low=95..96 / high=105..106）の内と外。
        norms = port.norms_at(dataset_ref=REF, prices=(100.0, 1.0), now_unix=START)

        # Assert: 域内は 0..1 の密度・域外は None（発明しない）。
        assert norms[0] is not None and 0.0 <= norms[0] <= 1.0
        assert norms[1] is None

    def test_the_reference_compute_returns_the_shape_the_gateway_reads(self) -> None:
        """gateway が読む欄が実物に在ること（欄の名前は core の契約）。"""
        # Arrange
        candles = [
            {"time": 0, "open": 1.0, "high": 3.0, "low": 0.5, "close": 2.0},
            {"time": 86_400, "open": 2.0, "high": 4.0, "low": 1.5, "close": 3.0},
        ]

        # Act
        profile = _reference_compute(candles, n_bins=MP_BINS)

        # Assert: gateway の `_norm_at` が触る欄がすべて在る。
        for field in ("bins", "price_min", "price_max", "n_bins"):
            assert field in profile, f"core の応答に {field} がありません"
        # 実物が畳んだ証拠（Spy の作り物は n_bins=6 を返すので、ここは実経路でしか通らない）。
        assert profile["n_bins"] == MP_BINS
        assert len(profile["bins"]) == MP_BINS
        assert profile["price_min"] < profile["price_max"]
        # 正規化の定義（0..1）が core 側で崩れていないこと。
        assert all(0.0 <= float(bin_["norm"]) <= 1.0 for bin_ in profile["bins"])
        assert max(float(bin_["norm"]) for bin_ in profile["bins"]) == 1.0


class TestTheGatewayIsWiredToThePort:
    """具象が P-MP の面を満たすこと。

    usecase（`build_reach_sheet`）は `MarketProfilePort` 越しにしか密度を知らない。面と
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
