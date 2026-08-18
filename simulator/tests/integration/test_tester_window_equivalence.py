"""期間窓の写像と適用結果の事後検証（内部設計 §8.4 D-11・T-10 / T-11）。

本モジュールは**実装より先に書いたテスト**である（フェーズ 3 = Red で収集エラーから
開始した）。`simulator.main.tester_settings.window` は実装済みであり、現在は全件通過
する。

固定する仕様:
    1. §8.4.2: `CUSTOM` は `[from 00:00Z, to+1day 00:00Z)`（V-2「to_date 当日を含む」を
       半開へ写す）。`PRESET/ENTIRE_HISTORY` は窓なし（`None`）。
    2. 境界は **UTC aware datetime**。実測 W-3——窓境界は `datetime.timestamp()` で
       epoch 秒へ変換され、naive datetime はプロセスのローカル TZ で解釈される
       （`marketdata/csv_source.py`）——という**原因を除去**する。
       したがって `TZ=Asia/Tokyo` と `TZ=UTC` で採用バーが一致しなければならない。
    3. §8.4.4 N-16: `DatesPreset.LAST_YEAR` は起点定義が暫定（TBD-14）であり、
       バー最終時刻を知るにはデータを読む必要がある（K-14）ため実行を拒否する。
    4. §8.4.3 N-15: 要求した窓がエンジンに適用されなかった場合（`request.bars` が
       窓外を含む／空）を**結果で**検出する。機構（どの EA がどの Repository を使うか）
       を Settings 層に書き写して予測しない（実測 W-1）。

`ea_name` の受け渡しについて（未解決・実装フェーズへの申し送り）:
`RunBacktestRequest` は 6 フィールド（`config` / `bars` / `symbol_spec` /
`initial_deposit` / `stop_out_level` / `trading_start`）で ea_name を持たない（実測）。
一方 §8.4.4 は N-15 の `context` に `ea_name` を要求する。現行の
`verify_window_applied(request, window)` の 2 引数契約では ea_name を得る手段が無い。
本テストは呼出しを 2 引数のまま（契約どおり）にし、`ea_name` の欠落は
`test_rejection_context_carries_the_diagnostic_triple` の 1 件だけで露出させる。
"""
from __future__ import annotations

import os
import time
from contextlib import contextmanager
from datetime import date, datetime, timezone
from unittest import mock

import pytest

from simulator.domain.tester_settings_exceptions import UnsupportedSettingError
from simulator.main import build_interactor
from simulator.main.tester_settings.kwargs_mapper import to_interactor_kwargs
from simulator.main.tester_settings.window import resolve_data_window, verify_window_applied
from simulator.tests.tester_settings_engine_fixtures import (
    custom_range_settings,
    daily_epochs,
    engine_binding,
    runnable_settings,
    utc_midnight,
    write_comma_csv,
)
from simulator.usecase.run_backtest import RunBacktestRequest

#: 合成 comma CSV の 1 日 1 本・5 本（2024-01-01 〜 2024-01-05 の 00:00Z）。
FIRST_DAY = date(2024, 1, 1)
BAR_DAYS = 5

#: 要求する期間（`FromDate` / `ToDate`）。`ToDate` 当日を含む（V-2）。
FROM_DATE = date(2024, 1, 2)
TO_DATE = date(2024, 1, 3)

#: 上の要求に対する半開窓（§8.4.2）。
EXPECTED_WINDOW = (utc_midnight(FROM_DATE), utc_midnight(date(2024, 1, 4)))

#: 窓が効いたときに残るバーの epoch 秒（1/2 と 1/3 の 2 本）。
EXPECTED_BAR_EPOCHS = (
    int(utc_midnight(date(2024, 1, 2)).timestamp()),
    int(utc_midnight(date(2024, 1, 3)).timestamp()),
)

EA_NAME = "TC24051901"


def probe_timezone_other_than(tzname: "tuple[str, ...]") -> str:
    """``tzname`` と必ず異なるローカル TZ を返す（汚染に使う値の唯一の決め方）。

    周囲の TZ（`TZ=UTC` 実行でも `TZ=Asia/Tokyo` 実行でも）と同じ値を選ぶと、
    「復元されていない」状態が「復元済み」と区別できず assertion が弱体化する
    （漏洩を検出できないテストになる）。判定は呼び出し時点の実測値で行う。
    """
    return "Asia/Tokyo" if tzname[0] == "UTC" else "UTC"


@contextmanager
def local_timezone_restored():
    """ブロック内の `TZ` 書き換えを巻き戻し、**復元の成立を自己検証する**。

    `os.environ["TZ"]` と `time.tzname` の両方を測る理由: `os.environ["TZ"]` を戻して
    も `time.tzset()` を呼ばなければ **C ランタイム側のローカル TZ が古いまま残る**。
    片方だけでは「戻したつもり」を検出できない。

    復元を `monkeypatch` に委ねられない理由（実測）: フィクスチャの後始末は LIFO で
    あり `restore_tz` の後始末は `monkeypatch` の undo より **先** に走る。よって
    `time.tzset()` を呼ぶ時点で `TZ` はまだ書き換え後の値であり、ローカル TZ は汚染値
    に**再設定される**（復元されない）。

    検証を `finally` の外に置く理由: ブロック内で例外が出た場合、この行は実行されない。
    復元自体は必ず試みたうえで、本来の失敗を復元検証で覆い隠さない。
    """
    saved_tz = os.environ.get("TZ")
    saved_tzname = time.tzname
    try:
        yield
    finally:
        if saved_tz is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = saved_tz
        time.tzset()
    if (os.environ.get("TZ"), time.tzname) != (saved_tz, saved_tzname):
        raise AssertionError(
            "プロセスのローカル TZ が復元されていない: "
            f"TZ={os.environ.get('TZ')!r}（期待 {saved_tz!r}）/ "
            f"time.tzname={time.tzname}（期待 {saved_tzname}）"
        )


@pytest.fixture()
def csv_path(tmp_path):
    return write_comma_csv(tmp_path / "jp225_daily.csv", daily_epochs(FIRST_DAY, BAR_DAYS))


@pytest.fixture()
def restore_tz():
    """`TZ` を書き換えるテストのためにプロセスのローカル TZ を復元する。

    復元と**その成立の検証**は `local_timezone_restored` が持つ。本フィクスチャを使う
    各テストが自分で復元を保証するため、検証は**テストの定義順に依存しない**（順序
    変更プラグインを入れても空振りしない）。
    """
    with local_timezone_restored():
        yield


def _request_for(settings, csv_path, **binding_overrides):
    kwargs = to_interactor_kwargs(
        settings, engine_binding(data_path=str(csv_path), **binding_overrides)
    )
    _, request = build_interactor(**kwargs)
    return request


class TestResolveDataWindow:
    """§8.4.2 の写像（純関数・データを読まない）。"""

    def test_custom_range_becomes_a_half_open_utc_window(self):
        window = resolve_data_window(custom_range_settings(FROM_DATE, TO_DATE).effective())
        assert window.marketdata_window == EXPECTED_WINDOW

    def test_window_boundaries_are_timezone_aware_utc(self):
        # W-3 の原因除去: naive datetime を渡さない
        start, end = resolve_data_window(
            custom_range_settings(FROM_DATE, TO_DATE).effective()
        ).marketdata_window
        assert start.tzinfo is not None and start.utcoffset() == timezone.utc.utcoffset(None)
        assert end.tzinfo is not None and end.utcoffset() == timezone.utc.utcoffset(None)

    def test_to_date_day_is_included_by_adding_one_day(self):
        # V-2: `ToDate` 当日を含む。半開の上端は翌日 00:00Z。
        _, end = resolve_data_window(
            custom_range_settings(FROM_DATE, TO_DATE).effective()
        ).marketdata_window
        assert end == datetime(2024, 1, 4, tzinfo=timezone.utc)

    def test_single_day_range_spans_exactly_one_day(self):
        window = resolve_data_window(custom_range_settings(FROM_DATE, FROM_DATE).effective())
        start, end = window.marketdata_window
        assert (end - start).days == 1

    def test_entire_history_preset_has_no_window(self):
        # `Dates=0`（F-7: entire history）。フィルタを掛けない。
        window = resolve_data_window(runnable_settings(Dates="0").effective())
        assert window.marketdata_window is None

    def test_last_year_preset_is_unsupported(self):
        # N-16: 起点定義が暫定（TBD-14）かつバー最終時刻はデータを読まないと分からない
        with pytest.raises(UnsupportedSettingError) as excinfo:
            resolve_data_window(runnable_settings(Dates="2").effective())
        context = excinfo.value.context
        assert context["unsupported_id"] == "N-16"
        assert context["tbd"] == "TBD-14"


class TestWindowAppliedToBars:
    """T-10: 要求窓が実際の採用バーに効くこと（結果で測る）。"""

    def test_only_bars_inside_the_window_are_loaded(self, csv_path):
        request = _request_for(custom_range_settings(FROM_DATE, TO_DATE), csv_path)
        assert tuple(bar.time for bar in request.bars) == EXPECTED_BAR_EPOCHS

    def test_first_and_last_bars_match_the_half_open_boundaries(self, csv_path):
        request = _request_for(custom_range_settings(FROM_DATE, TO_DATE), csv_path)
        start, end = EXPECTED_WINDOW
        assert request.bars[0].time == int(start.timestamp())
        assert request.bars[-1].time < int(end.timestamp())

    def test_without_a_window_every_bar_is_loaded(self, csv_path):
        # 窓なし（`Dates=0`）の対照。窓の効果が「たまたま全件」でないことを固定する。
        request = _request_for(runnable_settings(Dates="0"), csv_path)
        assert len(request.bars) == BAR_DAYS


class TestTimezoneIndependence:
    """T-10: `TZ=Asia/Tokyo` と `TZ=UTC` で同一結果（W-3 の原因除去の検証）。"""

    @pytest.mark.parametrize("tz_name", ["UTC", "Asia/Tokyo"])
    def test_loaded_bars_are_identical_under_any_local_timezone(
        self, csv_path, monkeypatch, restore_tz, tz_name
    ):
        # Arrange: プロセスのローカル TZ を差し替える
        monkeypatch.setenv("TZ", tz_name)
        time.tzset()
        # Act
        request = _request_for(custom_range_settings(FROM_DATE, TO_DATE), csv_path)
        # Assert: JST は UTC+9。naive datetime を渡していれば 9 時間ずれて件数が変わる。
        assert tuple(bar.time for bar in request.bars) == EXPECTED_BAR_EPOCHS

    @pytest.mark.parametrize("tz_name", ["UTC", "Asia/Tokyo"])
    def test_resolved_window_is_identical_under_any_local_timezone(
        self, monkeypatch, restore_tz, tz_name
    ):
        monkeypatch.setenv("TZ", tz_name)
        time.tzset()
        window = resolve_data_window(custom_range_settings(FROM_DATE, TO_DATE).effective())
        assert window.marketdata_window == EXPECTED_WINDOW


class TestLocalTimezoneIsRestored:
    """復元機構そのものを**順序に依存せず**測る。

    以前は「前のテストが汚し、次のテストが残留を測る」定義順依存の 2 件だった。単独
    実行・順序変更（順序変更プラグインの導入）では汚染源が走らず、残留を測る側は
    **失敗ではなく空振り**になり検出力を失う（実測: 復元を壊した状態で本クラスを除外
    して実行すると 18 件が全通過した）。よって復元の検証は
    `local_timezone_restored` の後始末に置き、各テストが自分で保証する。
    """

    def test_the_context_manager_restores_the_process_timezone(self):
        # Arrange
        before_tz, before_tzname = os.environ.get("TZ"), time.tzname
        # Act
        with local_timezone_restored():
            os.environ["TZ"] = probe_timezone_other_than(before_tzname)
            time.tzset()
            # 検出力の前提: 汚染が実際に効いていなければ復元も測れない
            assert time.tzname != before_tzname
        # Assert: 環境変数と C ランタイム側の**両方**が戻っている
        assert os.environ.get("TZ") == before_tz
        assert time.tzname == before_tzname

    def test_a_defeated_restore_is_reported_instead_of_passing_silently(self):
        """復元が成立しない状態を後始末が**落とす**こと（空振りしないことの実証）。

        `monkeypatch` へ復元を委ねた実装（`os.environ` は戻すが `time.tzset()` が
        効かない）を `time.tzset` の無力化で再現する。この変異が検出されなければ、
        後始末は「復元したつもり」を通すだけの飾りである。
        """
        # Arrange
        before_tz, before_tzname = os.environ.get("TZ"), time.tzname
        patcher = mock.patch.object(time, "tzset", lambda: None)
        started = False
        try:
            # Act / Assert: 後始末が AssertionError を上げる
            with pytest.raises(AssertionError, match="復元されていない"):
                with local_timezone_restored():
                    os.environ["TZ"] = probe_timezone_other_than(before_tzname)
                    time.tzset()
                    assert time.tzname != before_tzname
                    patcher.start()  # 以降 `time.tzset()` は何もしない
                    started = True
        finally:
            # 本テストは意図的に復元を壊すため、後片付けを自分で行う。
            # `started` を見るのは、汚染前に失敗したとき `stop()` が
            # `RuntimeError（未開始）` を上げて本来の失敗を覆い隠すのを防ぐため。
            if started:
                patcher.stop()
            if before_tz is None:
                os.environ.pop("TZ", None)
            else:
                os.environ["TZ"] = before_tz
            time.tzset()
        assert time.tzname == before_tzname


class TestVerifyWindowApplied:
    """T-11 / N-15: 窓が適用されなかった結果を検出する（機構を予測しない）。"""

    def test_bars_outside_the_requested_window_are_rejected(self, csv_path):
        # Arrange: 窓を渡さずに実行し「窓が効かなかった経路」を再現する（§9.2 T-11）
        request = _request_for(runnable_settings(Dates="0"), csv_path)
        window = resolve_data_window(custom_range_settings(FROM_DATE, TO_DATE).effective())
        # Act / Assert
        with pytest.raises(UnsupportedSettingError) as excinfo:
            verify_window_applied(request, window)
        assert excinfo.value.context["unsupported_id"] == "N-15"

    def test_rejection_context_carries_the_diagnostic_triple(self, csv_path):
        request = _request_for(runnable_settings(Dates="0"), csv_path)
        window = resolve_data_window(custom_range_settings(FROM_DATE, TO_DATE).effective())
        with pytest.raises(UnsupportedSettingError) as excinfo:
            verify_window_applied(request, window)
        context = excinfo.value.context
        # §8.4.4: 要求窓・実バー範囲・EA 名。どれが欠けても「どの EA でどの窓が効かな
        # かったか」を特定できず、W-1（Repository 差）の切り分けができない。
        # `ea_name` は現行の 2 引数契約では供給源が無い（本モジュール冒頭の申し送り）。
        assert {"requested_window", "actual_range", "ea_name"} <= set(context)

    def test_empty_bars_are_rejected(self):
        # §8.4.3: bars が空なら Repository が既に DataError を出しているはず（K-14）。
        # ここへ到達した空列は「窓が効いて 0 件になった」として N-15 で止める。
        empty = RunBacktestRequest(config=None, bars=[], symbol_spec=None, initial_deposit=0.0)
        window = resolve_data_window(custom_range_settings(FROM_DATE, TO_DATE).effective())
        with pytest.raises(UnsupportedSettingError) as excinfo:
            verify_window_applied(empty, window)
        assert excinfo.value.context["unsupported_id"] == "N-15"

    def test_bars_inside_the_window_pass(self, csv_path):
        request = _request_for(custom_range_settings(FROM_DATE, TO_DATE), csv_path)
        window = resolve_data_window(custom_range_settings(FROM_DATE, TO_DATE).effective())
        # 例外を出さないこと（合格側を固定しないと「常に落ちる実装」が通ってしまう）
        assert verify_window_applied(request, window) is None

    def test_no_window_requested_is_always_accepted(self, csv_path):
        # `marketdata_window is None`（`ENTIRE_HISTORY`）は窓の主張が無い＝検証対象外
        request = _request_for(runnable_settings(Dates="0"), csv_path)
        window = resolve_data_window(runnable_settings(Dates="0").effective())
        assert verify_window_applied(request, window) is None
