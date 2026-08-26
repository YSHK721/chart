"""A-3: 取得窓（`marketdata_window`）を MT5 タブ形式経路（`Mt5CsvOHLCRepository`）へ効かせる。

解消する欠陥（L-2・実測）: 実装前は `main/__init__.py:541` の
`isinstance(market_data, CsvOHLCRepository)` が真のときだけ委譲 repo へ差し替わり、
MT5 経路では窓が**無視**されていた。実測（`simulator/tests/fixtures/mt5/
ma_slope_jp225_202501/input/JP225_M1_202501.csv` / MA_Slope_EA）:

    窓なし          : 28097 本 / 2025-01-02T01:00:00 .. 2025-01-30T23:59:00
    窓 01-10..01-13 : 28097 本 / 2025-01-02T01:00:00 .. 2025-01-30T23:59:00（同一 sha256）

本モジュールは MT5 経路について**検定 0 件**だった 2 点を固定する:
  1. 窓を指定したとき採用 bars の先頭・末尾が窓 `[start, end)` の内側に入ること。
  2. その bars で **spread が保存される**こと（`MarketDataSourceRepository` は
     `marketdata_source.py:51` で `spread=0` 固定であり、そこへ寄せると spread 依存戦略
     （MA_Slope / MA_Slope_Pending / StopEntryProbe）の約定価格式が壊れる）。
  3. 窓なし（既定 `None`）は合成そのものが起きず、bars が実装前と byte 等価であること。
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path

import pytest

from simulator.adapter.repository.marketdata_source import MarketDataSourceRepository
from simulator.adapter.repository.ohlc_mt5_csv import Mt5CsvOHLCRepository
from simulator.adapter.repository.windowed_market_data import WindowedMarketDataRepository
from simulator.main import build_interactor

_FIXTURE_CSV = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "mt5"
    / "ma_slope_jp225_202501"
    / "input"
    / "JP225_M1_202501.csv"
)

#: 実装前に同一スクリプトで測った窓なし bars の指紋（A-3 完了条件 4 の基準値）。
#: 内容: sha256 over "time|open|high|low|close|volume|spread\n" per bar（時刻昇順）。
_NO_WINDOW_BARS = 28097
_NO_WINDOW_FIRST = "2025-01-02T01:00:00"
_NO_WINDOW_LAST = "2025-01-30T23:59:00"
_NO_WINDOW_SHA256 = "8b14f51fca8dace80068c0ca6c1b0268f1fb72a7895800262d78243f75dec6fb"


def _bars_digest(bars) -> str:
    h = hashlib.sha256()
    for b in bars:
        h.update(
            f"{b.time}|{b.open!r}|{b.high!r}|{b.low!r}|{b.close!r}|{b.volume!r}|{b.spread!r}\n".encode()
        )
    return h.hexdigest()


def _ma_slope_kwargs(csv_path: Path, **extra) -> dict:
    """⚠ ISSUE-445 段階 B: 本モジュールは銘柄仕様の**正しさを検証していない**。

    下の `contract_size=10.0` ほか 5 項目は供給元スナップショット
    （`marketdata/symbol_specs/OANDA-Japan-MT5-Live/JP225.json`）と食い違うが、
    本モジュールが見るのは repository の合成有無と **bars**（時刻・spread・件数・
    上の `_NO_WINDOW_SHA256`）だけであり、bars の読み込みに銘柄仕様は 1 つも入らない。
    実測（2026-08-26）: `contract_size` だけを真値 1.0 にしても、5 項目を対で真値へ
    寄せても、9 検定とも緑のまま通る（`_NO_WINDOW_SHA256` も不変）。

    したがって数値ピンを足す余地が無い。段階 C は本モジュールの緑を「銘柄仕様の是正が
    正しい」根拠にしてはならない。損益への波及は
    `simulator/tests/unit/test_is_oos_barmode_index.py` の不変ピンが見る。
    """
    base = dict(
        data_path=csv_path,
        symbol="JP225",
        period="M1",
        ea_name="MA_Slope_EA",
        initial_deposit=10_000.0,
        contract_size=10.0,
        volume_min=0.01,
        volume_max=100.0,
        volume_step=0.01,
        stops_level=0,
        digits=1,
        point_size=0.1,
        leverage=10.0,
        ma_period=20,
        ma_method="ema",
        lot_size=0.1,
        stop_loss_points=0,
        take_profit_points=0,
    )
    base.update(extra)
    return base


def _write_mt5_csv(path: Path) -> Path:
    """MT5 タブ形式・1 日 1 本 5 本（2024-01-01 〜 05）。spread は本ごとに異なる非 0 値。"""
    rows = ["<DATE>\t<TIME>\t<OPEN>\t<HIGH>\t<LOW>\t<CLOSE>\t<TICKVOL>\t<VOL>\t<SPREAD>"]
    for day in range(1, 6):
        rows.append(
            f"2024.01.0{day}\t00:00:00\t100.0\t100.5\t99.5\t100.2\t10\t0\t{10 * day}"
        )
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return path


_SYNTH_WINDOW = (
    datetime(2024, 1, 2, tzinfo=timezone.utc),
    datetime(2024, 1, 4, tzinfo=timezone.utc),
)
_REAL_WINDOW = (
    datetime(2025, 1, 10, tzinfo=timezone.utc),
    datetime(2025, 1, 13, tzinfo=timezone.utc),
)


@pytest.fixture()
def synth_csv(tmp_path):
    return _write_mt5_csv(tmp_path / "mt5_daily.csv")


class TestWindowIsAppliedToTheMt5Path:
    """L-2 の解消: MT5 経路でも窓が効く（合成デコレータ）。"""

    def test_market_data_is_wrapped_when_a_window_is_requested(self, synth_csv):
        controller, _ = build_interactor(
            **_ma_slope_kwargs(synth_csv, marketdata_window=_SYNTH_WINDOW)
        )
        assert isinstance(controller.market_data, WindowedMarketDataRepository)
        # 委譲経路（spread=0 固定）へ寄せてはならない（H-4）。
        assert isinstance(controller.market_data.inner, Mt5CsvOHLCRepository)
        assert not isinstance(controller.market_data.inner, MarketDataSourceRepository)

    def test_first_and_last_bars_are_inside_the_half_open_window(self, synth_csv):
        _, request = build_interactor(
            **_ma_slope_kwargs(synth_csv, marketdata_window=_SYNTH_WINDOW)
        )
        times = [str(b.time) for b in request.bars]
        assert times == ["2024-01-02T00:00:00", "2024-01-03T00:00:00"]

    def test_spread_is_preserved_on_the_windowed_mt5_path(self, synth_csv):
        _, request = build_interactor(
            **_ma_slope_kwargs(synth_csv, marketdata_window=_SYNTH_WINDOW)
        )
        # CSV の <SPREAD> は 10*day。窓 [01-02, 01-04) は 20 と 30 を残す。
        assert [b.spread for b in request.bars] == [20, 30]
        assert all(b.spread != 0 for b in request.bars)

    def test_window_outside_the_data_is_rejected_by_the_repository_or_empty(self, synth_csv):
        far = (
            datetime(2030, 1, 1, tzinfo=timezone.utc),
            datetime(2030, 1, 2, tzinfo=timezone.utc),
        )
        _, request = build_interactor(**_ma_slope_kwargs(synth_csv, marketdata_window=far))
        assert list(request.bars) == []


@pytest.mark.skipif(not _FIXTURE_CSV.exists(), reason="MT5 突合フィクスチャ CSV が無い")
class TestRealMt5FixtureUnderWindow:
    """実 MT5 データ（JP225 M1 2025-01・spread 50..480）での実測固定。"""

    def test_windowed_bars_are_inside_the_window_and_keep_spread(self):
        _, request = build_interactor(
            **_ma_slope_kwargs(_FIXTURE_CSV, marketdata_window=_REAL_WINDOW)
        )
        bars = list(request.bars)
        assert bars, "窓内にバーが 1 本も無い（窓の解釈が壊れている）"
        start, end = _REAL_WINDOW
        first, last = str(bars[0].time), str(bars[-1].time)
        assert first >= start.strftime("%Y-%m-%dT%H:%M:%S")
        assert last < end.strftime("%Y-%m-%dT%H:%M:%S")
        # spread が 0 へ潰れていない（委譲経路への誤結線の検出）。
        assert min(b.spread for b in bars) > 0

    def test_the_controller_reload_path_is_windowed_too(self):
        """端から端まで: `controller.run()` の再読込にも窓が効く。

        `build_interactor` が組んだ `request.bars` だけを測ると、`controller.run()` が
        `self._market_data.load(source_ref, ...)` で**もう一度読む**経路（`controller.py:63`）
        を素通ししてしまう。受け口だけ直して実走経路が窓なしのまま、という無言の死に方を
        塞ぐため、再読込の結果を直接測る。
        """
        controller, request = build_interactor(
            **_ma_slope_kwargs(_FIXTURE_CSV, marketdata_window=_REAL_WINDOW)
        )
        reloaded = controller.market_data.load(_FIXTURE_CSV, None, "M1")
        built = list(request.bars)
        assert len(reloaded) == len(built)
        assert str(reloaded[0].time) == str(built[0].time)
        assert str(reloaded[-1].time) == str(built[-1].time)
        assert min(b.spread for b in reloaded) > 0

    def test_windowed_bars_are_a_strict_subset_of_the_unwindowed_bars(self):
        _, full = build_interactor(**_ma_slope_kwargs(_FIXTURE_CSV))
        _, windowed = build_interactor(
            **_ma_slope_kwargs(_FIXTURE_CSV, marketdata_window=_REAL_WINDOW)
        )
        full_bars, win_bars = list(full.bars), list(windowed.bars)
        assert 0 < len(win_bars) < len(full_bars)
        # 絞るだけ＝残った Bar は元の Bar と完全一致（写像していない）。
        assert set(win_bars) <= set(full_bars)


@pytest.mark.skipif(not _FIXTURE_CSV.exists(), reason="MT5 突合フィクスチャ CSV が無い")
class TestNoWindowIsByteIdentical:
    """完了条件 4: 既定 `marketdata_window=None` は実装前と byte 等価。"""

    def test_repository_is_not_wrapped_without_a_window(self):
        controller, _ = build_interactor(**_ma_slope_kwargs(_FIXTURE_CSV))
        assert isinstance(controller.market_data, Mt5CsvOHLCRepository)
        assert not isinstance(controller.market_data, WindowedMarketDataRepository)

    def test_bars_match_the_pre_change_fingerprint(self):
        _, request = build_interactor(**_ma_slope_kwargs(_FIXTURE_CSV))
        bars = list(request.bars)
        assert len(bars) == _NO_WINDOW_BARS
        assert str(bars[0].time) == _NO_WINDOW_FIRST
        assert str(bars[-1].time) == _NO_WINDOW_LAST
        assert _bars_digest(bars) == _NO_WINDOW_SHA256
