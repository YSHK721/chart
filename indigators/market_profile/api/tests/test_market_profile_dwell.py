"""market_profile_dwell（adapter/compute/market_profile_dwell.py）と controller の src=dwell 検証。

対象:
  - 純ロジック（合成ティックで決定論）:
      _build_active_table / _session_dwell / compute_dwell_profile の
      dwell 集計・POC/VA・ビン整合・休場除外（薄い時間帯が 0 になる）。
  - controller: src='dwell'（既知 tick ref）で 200/profile 妥当、src='dwell'＆非 tick ref で 400、
      src 不正で 400、src 省略で candle 後方互換。
  - 統合（実データ・軽量）: jp225_tick の小窓で src=dwell が 200 かつ POC がレンジ内（全期間・ディスクキャッシュ）。

設計方針（AAA・handle_compute / candle テストの流儀に合わせる）:
  合成ティックは _load_window_ticks（単一注入点）を monkeypatch し、既知の (secs, mids) を注入する。
  日別ロールバック・active table・部分集計はすべて _load_window_ticks を経由するため、注入 1 点で決定論化する。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from market_profile_api.compute import market_profile_dwell as mpd
from market_profile_api.compute.market_profile import VA_PCT_DEFAULT
from market_profile_api.compute.rollup_dto import DayRollup
from market_profile_api.controller.market_profile_controller import handle_market_profile
# ISSUE-183 item5: 永続化設定（cache root / 形式版数）の単一情報源は gateway 側 cache_settings。
from market_profile_api.gateway import cache_settings as _mp_cache_settings

_DAY = 86400
# 2024-01-01 00:00 UTC（月曜・UTC 真夜中）。weekday = ((s//86400)+3)%7 = 0（月）。
_DAY0 = 1704067200
_HOT = 1000.0   # 活発時間帯に密集＝滞在が積み上がる価格。
_COLD = 1100.0  # 休場時間帯にのみ現れる価格＝滞在は除外され 0 になる。


# --------------------------------------------------------------------------- #
# テスト基盤: 合成ティック注入 / キャッシュ隔離
# --------------------------------------------------------------------------- #
@pytest.fixture(autouse=True)
def _isolate_caches(tmp_path, monkeypatch):
    """プロセス内キャッシュ（日別/部分/active table）とディスクキャッシュをテスト間で隔離する。

    ディスクキャッシュ基点を tmp へ差し替え、実データ DATA_DIR/cache への書込を完全に防ぐ
    （既存データ非破壊・cache ディレクトリのみ書込の制約を保証する）。
    """
    monkeypatch.setattr(_mp_cache_settings, "DWELL_CACHE_ROOT", tmp_path / "mp_dwell_cache")
    # 既存テストは署名(ソースティック署名)チェックを中和する（cur_sig="" ＝保存時の既定 "" と一致）。
    #   無効化(署名変化→再計算)は専用テスト TestCacheInvalidation で個別に検証する。
    monkeypatch.setattr(mpd, "_day_source_signature", lambda symbol, day_start: "")
    mpd._reset_caches()
    yield
    mpd._reset_caches()


def _pin_legacy_table(monkeypatch, secs=None):
    """ISSUE-089 後方互換ピン: 旧セマンティクス（窓ティックから構築した単一表）を固定する。

    _table_for_day（日アンカー表＝追加の _load_window_ticks を発行する）を差し替え、
    ①シナリオの表期待値を従来どおりに保つ ②loader スパイの呼数/最古日検証を汚さない。
    表そのものの検証は TestActiveTableWindowKeyedMemo が担う。
    """
    if secs is not None and len(secs):
        table = mpd._build_active_table(np.asarray(secs, dtype=np.int64))
    else:
        table = np.ones((7, 24), dtype=bool)
    monkeypatch.setattr(mpd, "_table_for_day", lambda _s, _d: table)


def _make_loader(master_secs, master_mids):
    """master 配列を [start, end) にフィルタして (secs, mids) を返す _load_window_ticks 代替。"""
    s = np.asarray(master_secs, dtype=np.int64)
    m = np.asarray(master_mids, dtype=np.float64)

    def _loader(symbol, start, end):
        win = (s >= int(start)) & (s < int(end))
        s2, m2 = s[win], m[win]
        order = np.argsort(s2, kind="stable")
        return s2[order], m2[order]

    return _loader


def _synthetic_master():
    """3 日ぶんの合成ティック。活発 hr2 に HOT 密集、休場 hr20 に COLD 疎ら。"""
    secs, mids = [], []
    for d in range(3):
        base = _DAY0 + d * _DAY
        # 活発時間帯（hr2=07200..10800s）: 30 ティックを 10 秒間隔で HOT に置く。
        for i in range(30):
            secs.append(base + 7200 + 10 * i)
            mids.append(_HOT)
        # 休場時間帯（hr20=72000..75600s）: 600 秒ギャップの 2 ティックを COLD に置く。
        secs.append(base + 72000)
        mids.append(_COLD)
        secs.append(base + 72600)
        mids.append(_COLD)
    return secs, mids


# --------------------------------------------------------------------------- #
# 純ロジック: _build_active_table
# --------------------------------------------------------------------------- #
class TestBuildActiveTable:
    def test_dense_bucket_active_sparse_bucket_inactive(self):
        # Arrange: (月, hr2) に 30 ティック、(月, hr20) に 2 ティック。
        secs, _ = _synthetic_master()
        s = np.asarray(secs, dtype=np.int64)

        # Act
        table = mpd._build_active_table(s)

        # Assert
        assert table.shape == (7, 24)
        assert table.dtype == np.bool_
        assert bool(table[0, 2]) is True     # 密集バケット＝活発。
        assert bool(table[0, 20]) is False   # 疎バケット（2 < max*0.10=3）＝休場。
        assert bool(table[0, 12]) is False   # ティック皆無＝休場。


# --------------------------------------------------------------------------- #
# 純ロジック: _session_dwell（活発秒の積分・休場 0・時間境界跨ぎ）
# --------------------------------------------------------------------------- #
class TestSessionDwell:
    def test_active_hour_gaps_are_counted(self):
        # Arrange: (月, hr2) のみ活発。同一活発時間内の 2 ギャップ。
        table = np.zeros((7, 24), dtype=bool)
        table[0, 2] = True
        base = _DAY0 + 7200
        secs = np.array([base, base + 30, base + 100], dtype=np.int64)

        # Act
        dwell = mpd._session_dwell(secs, table)

        # Assert: 活発時間内のギャップは満額計上（len=len(secs)-1）。
        assert list(np.round(dwell, 6)) == [30.0, 70.0]

    def test_closed_hour_gaps_are_zero(self):
        # Arrange: 全休場テーブル。
        table = np.zeros((7, 24), dtype=bool)
        base = _DAY0 + 7200
        secs = np.array([base, base + 30, base + 100], dtype=np.int64)

        # Act
        dwell = mpd._session_dwell(secs, table)

        # Assert: 休場帯の滞在は 0。
        assert list(dwell) == [0.0, 0.0]

    def test_hour_boundary_crossing_integrates_active_seconds(self):
        # Arrange: hr2 のみ活発。hr2→hr3（休場）を跨ぐギャップ。
        table = np.zeros((7, 24), dtype=bool)
        table[0, 2] = True
        secs = np.array([_DAY0 + 7200, _DAY0 + 10700, _DAY0 + 11000], dtype=np.int64)

        # Act
        dwell = mpd._session_dwell(secs, table)

        # Assert: 同一 hr2 内=3500、跨ぎは hr2 側 100 秒のみ活発、hr3 側は 0。
        assert list(np.round(dwell, 6)) == [3500.0, 100.0]


# --------------------------------------------------------------------------- #
# 純ロジック: compute_dwell_profile（窓合算→表示 bin・POC/VA・休場除外）
# --------------------------------------------------------------------------- #
class TestComputeDwellProfile:
    def _run(self, monkeypatch):
        secs, mids = _synthetic_master()
        monkeypatch.setattr(mpd, "_load_window_ticks", _make_loader(secs, mids))
        _pin_legacy_table(monkeypatch, secs)
        return mpd.compute_dwell_profile(
            "JP225", _DAY0, _DAY0 + 2 * _DAY, 990.0, 1110.0, 12, va_pct=0.70, bar_sec=_DAY
        )

    def test_schema_keys_match_candle(self, monkeypatch):
        profile = self._run(monkeypatch)
        assert set(profile) == {
            "bins", "poc", "va_low", "va_high",
            "price_min", "price_max", "tpo_units", "n_bins",
        }
        assert profile["n_bins"] == 12
        assert len(profile["bins"]) == 12
        assert set(profile["bins"][0]) == {"price", "tpo", "norm"}

    def test_poc_is_hot_price(self, monkeypatch):
        # HOT=1000 に滞在が集中 → POC は HOT のビン中心（1005）。
        profile = self._run(monkeypatch)
        assert profile["poc"] == 1005.0
        assert profile["price_min"] <= profile["poc"] <= profile["price_max"]
        assert profile["price_min"] <= profile["va_low"] <= profile["va_high"] <= profile["price_max"]

    def test_closed_session_price_excluded(self, monkeypatch):
        # COLD=1100 は休場帯にのみ出現 → その滞在は 0（除外）。
        profile = self._run(monkeypatch)
        cold_bin = next(b for b in profile["bins"] if b["price"] == 1105.0)
        assert cold_bin["tpo"] == 0

    def test_tpo_units_are_positive_dwell_seconds(self, monkeypatch):
        # tpo_units = 総 dwell 秒（int へ丸め）。HOT の活発滞在が積み上がる。
        profile = self._run(monkeypatch)
        assert profile["tpo_units"] > 0

    def test_full_period_scans_all_days_no_cap(self, monkeypatch):
        # 全期間化: 250日キャップを撤廃したので、t0 が大昔でも window を切り詰めず全日を走査する。
        secs, mids = _synthetic_master()
        calls = []
        base_loader = _make_loader(secs, mids)

        def _spy(symbol, start, end):
            calls.append((int(start), int(end)))
            return base_loader(symbol, start, end)

        monkeypatch.setattr(mpd, "_load_window_ticks", _spy)
        _pin_legacy_table(monkeypatch, secs)  # ISSUE-089: 表構築の追加 load をスパイから排除。
        # t0 を旧キャップ(250日)を超える過去に置く（300日窓）。
        t1 = _DAY0 + 2 * _DAY
        far_t0 = t1 - 300 * _DAY
        mpd.compute_dwell_profile(
            "JP225", far_t0, t1, 990.0, 1110.0, 12, va_pct=VA_PCT_DEFAULT, bar_sec=_DAY
        )
        # 走査開始は far_t0 の日境界まで遡る（旧 cap_from に丸められない）。
        old_cap_from = (t1 + _DAY) - mpd._MAX_DWELL_DAYS * _DAY
        day_calls = [s for s, _ in calls if s % _DAY == 0]  # 完全日ロールアップの呼び出し。
        earliest = min(day_calls)
        assert earliest == (far_t0 // _DAY) * _DAY   # 全期間の起点まで走査する。
        assert earliest < old_cap_from               # 旧キャップより過去まで確実に遡る。


# --------------------------------------------------------------------------- #
# Y2a: 当日（未確定 UTC 日）をキャッシュしない（完了日のみキャッシュ）
# --------------------------------------------------------------------------- #
class TestDayRollupCaching:
    def _spy_loader(self, monkeypatch):
        secs, mids = _synthetic_master()
        calls = []
        base = _make_loader(secs, mids)

        def _spy(symbol, start, end):
            calls.append((int(start), int(end)))
            return base(symbol, start, end)

        monkeypatch.setattr(mpd, "_load_window_ticks", _spy)
        _pin_legacy_table(monkeypatch)  # ISSUE-089: 表構築の追加 load を呼数検証から排除。
        return calls

    def test_completed_day_is_cached(self, monkeypatch):
        # Arrange: 完了日（day_start+86400 <= now）。
        calls = self._spy_loader(monkeypatch)
        table = np.ones((7, 24), dtype=bool)
        day = _DAY0
        now = day + _DAY + 1  # 日は完了済み。

        # Act: 同一日を 2 回呼ぶ。
        mpd._day_rollup("JP225", day, table, now)
        mpd._day_rollup("JP225", day, table, now)

        # Assert: 2 回目はキャッシュ命中＝loader は 1 回のみ。キャッシュに登録される。
        assert len(calls) == 1
        assert ("JP225", day) in mpd._DAY_CACHE

    def test_incomplete_day_is_not_cached(self, monkeypatch):
        # Arrange: 未完了日（day_start+86400 > now）＝現在進行中の当日。
        calls = self._spy_loader(monkeypatch)
        table = np.ones((7, 24), dtype=bool)
        day = _DAY0
        now = day + 100  # now < day+86400 → 日は未完了。

        # Act: 同一日を 2 回呼ぶ。
        mpd._day_rollup("JP225", day, table, now)
        mpd._day_rollup("JP225", day, table, now)

        # Assert: 未完了日は毎回再計算＝loader は 2 回。キャッシュに登録されない。
        assert len(calls) == 2
        assert ("JP225", day) not in mpd._DAY_CACHE

    def test_incomplete_partial_is_not_cached(self, monkeypatch):
        # Arrange: 当日の部分足（hi > now）。
        calls = self._spy_loader(monkeypatch)
        table = np.ones((7, 24), dtype=bool)
        lo = _DAY0
        hi = _DAY0 + 3600
        now = lo + 100  # now < hi → 部分窓は未完了。

        # Act
        mpd._partial_rollup("JP225", lo, hi, table, now)
        mpd._partial_rollup("JP225", lo, hi, table, now)

        # Assert: 未完了の部分窓は毎回再計算＝loader は 2 回。キャッシュに登録されない。
        assert len(calls) == 2
        assert ("JP225", lo, hi) not in mpd._PARTIAL_CACHE


# --------------------------------------------------------------------------- #
# ディスク永続キャッシュ: round-trip / 探索順 / 完了日永続化 / fail-safe
# --------------------------------------------------------------------------- #
class TestDiskCache:
    def test_roundtrip_preserves_kmin_and_variable_length_arrays(self):
        # Arrange: 可変長 dwell/cnt・kmin を持つロールアップ。
        roll = DayRollup(
            kmin=97,
            dwell=np.array([1.0, 0.0, 5.5, 2.25], dtype=float),
            cnt=np.array([3.0, 0.0, 7.0, 4.0], dtype=float),
        )
        path = mpd._cache_path("JP225", _DAY0)

        # Act: 保存 → 別プロセス相当（メモリ非依存）で読込。
        mpd._save_day_rollup(path, roll)
        loaded, _sig = mpd._load_day_rollup(path)

        # Assert: kmin・可変長配列が完全一致。
        assert loaded is not mpd.dwell_cache_miss() and loaded is not None
        assert loaded.kmin == 97
        assert np.array_equal(loaded.dwell, roll.dwell)
        assert np.array_equal(loaded.cnt, roll.cnt)
        # ISSUE-178: 層間 DTO は不変（読込側も write=False）。
        assert not loaded.dwell.flags.writeable and not loaded.cnt.flags.writeable

    def test_roundtrip_empty_day_is_none(self):
        # 実データ無しの完了日（None）は None として往復し、CACHE_MISS と区別される。
        path = mpd._cache_path("JP225", _DAY0)
        mpd._save_day_rollup(path, None)
        loaded, _sig = mpd._load_day_rollup(path)
        assert loaded is None  # 「実データ無しの完了日」＝再計算不要。

    def test_missing_file_returns_cache_miss(self):
        path = mpd._cache_path("JP225", _DAY0)
        assert mpd._load_day_rollup(path)[0] is mpd.dwell_cache_miss()

    def test_completed_day_is_persisted_to_disk(self, monkeypatch):
        # 完了日ロールアップはディスクにファイルが作られる。
        secs, mids = _synthetic_master()
        monkeypatch.setattr(mpd, "_load_window_ticks", _make_loader(secs, mids))
        table = np.ones((7, 24), dtype=bool)
        day = _DAY0
        now = day + _DAY + 1  # 完了日。

        mpd._day_rollup("JP225", day, table, now)

        assert mpd._cache_path("JP225", day).is_file()

    def test_incomplete_day_is_not_persisted_to_disk(self, monkeypatch):
        # 当日（未確定）はディスクに保存しない（都度計算・stale 化防止）。
        secs, mids = _synthetic_master()
        monkeypatch.setattr(mpd, "_load_window_ticks", _make_loader(secs, mids))
        table = np.ones((7, 24), dtype=bool)
        day = _DAY0
        now = day + 100  # 未完了日。

        mpd._day_rollup("JP225", day, table, now)

        assert not mpd._cache_path("JP225", day).is_file()

    def test_search_order_disk_hit_skips_compute(self, monkeypatch):
        # メモリ無 → ディスク有 でヒットし、loader（計算）は呼ばれない。
        table = np.ones((7, 24), dtype=bool)
        day = _DAY0
        now = day + _DAY + 1
        roll = DayRollup(kmin=100, dwell=np.array([9.0]), cnt=np.array([2.0]))
        mpd._save_day_rollup(mpd._cache_path("JP225", day), roll)  # 事前にディスクへ配置。

        calls = []
        monkeypatch.setattr(
            mpd, "_load_window_ticks",
            lambda s, a, b: calls.append((a, b)) or (mpd._EMPTY_SECS, mpd._EMPTY_MIDS),
        )
        got = mpd._day_rollup("JP225", day, table, now)

        assert calls == []                       # 計算は行われない（ディスクヒット）。
        assert got.kmin == 100
        assert np.array_equal(got.dwell, roll.dwell)
        assert ("JP225", day) in mpd._DAY_CACHE   # 以後はメモリからも返る。

    def test_search_order_both_empty_computes_and_saves(self, monkeypatch):
        # メモリ無・ディスク無 → 計算し、完了日ならディスクへ保存する。
        secs, mids = _synthetic_master()
        calls = []
        base = _make_loader(secs, mids)
        monkeypatch.setattr(
            mpd, "_load_window_ticks",
            lambda s, a, b: (calls.append((a, b)), base(s, a, b))[1],
        )
        table = np.ones((7, 24), dtype=bool)
        _pin_legacy_table(monkeypatch)
        day = _DAY0
        now = day + _DAY + 1

        mpd._day_rollup("JP225", day, table, now)

        assert len(calls) == 1                         # 計算された。
        assert mpd._cache_path("JP225", day).is_file()  # 保存された。

    def test_corrupt_file_is_ignored_and_recomputed(self, monkeypatch):
        # 破損ファイルは無視して再計算（fail-safe）。
        day = _DAY0
        path = mpd._cache_path("JP225", day)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"not-a-valid-npz")  # 破損データ。
        assert mpd._load_day_rollup(path)[0] is mpd.dwell_cache_miss()

        secs, mids = _synthetic_master()
        calls = []
        base = _make_loader(secs, mids)
        monkeypatch.setattr(
            mpd, "_load_window_ticks",
            lambda s, a, b: (calls.append((a, b)), base(s, a, b))[1],
        )
        table = np.ones((7, 24), dtype=bool)
        _pin_legacy_table(monkeypatch)
        got = mpd._day_rollup("JP225", day, table, day + _DAY + 1)

        assert len(calls) == 1        # 破損を無視して再計算。
        assert got is not None

    def test_version_mismatch_is_ignored(self):
        # バージョン不整合は無視して CACHE_MISS（再計算に委ねる）。
        day = _DAY0
        path = mpd._cache_path("JP225", day)
        roll = DayRollup(kmin=5, dwell=np.array([1.0]), cnt=np.array([1.0]))
        mpd._save_day_rollup(path, roll)
        monkeypatch_version = _mp_cache_settings.DWELL_CACHE_VERSION + 999
        # 保存済みファイルの version を実行時定数からずらして読む（不整合を模す）。
        import unittest.mock as _mock
        with _mock.patch.object(_mp_cache_settings, "DWELL_CACHE_VERSION", monkeypatch_version):
            assert mpd._load_day_rollup(path)[0] is mpd.dwell_cache_miss()


# --------------------------------------------------------------------------- #
# キャッシュ無効化: 完了日を空でキャッシュ後にティックが届く（署名変化）と再計算する
#   （実バグ修正: ティック到着前にウォームした日が空のまま配信され続けた stale-empty）
# --------------------------------------------------------------------------- #
class TestCacheInvalidation:
    def test_stale_empty_cache_recomputes_when_signature_changes(self, monkeypatch):
        # 1) ティック未着時に「空(None)」を署名 "" でキャッシュ（ウォーム済みだが空）。
        day = _DAY0
        now = day + _DAY + 1  # 完了日。
        path = mpd._cache_path("JP225", day)
        mpd._save_day_rollup(path, None, "")  # empty=True, sig=""
        mpd._reset_caches()                    # メモリを空にしディスク判定へ入らせる。

        # 2) その後ティックが届いた: 署名が変わり、_load_window_ticks が実データを返す。
        secs, mids = _synthetic_master()
        base = _make_loader(secs, mids)
        calls = []
        monkeypatch.setattr(
            mpd, "_load_window_ticks",
            lambda s, a, b: (calls.append((int(a), int(b))), base(s, a, b))[1],
        )
        monkeypatch.setattr(mpd, "_day_source_signature", lambda s, d: "ticks:123:456")
        _pin_legacy_table(monkeypatch)

        # 3) 署名変化 → disk の空を信用せず再計算する。
        got = mpd._day_rollup("JP225", day, np.ones((7, 24), dtype=bool), now)
        assert len(calls) == 1, "署名変化で再計算する（stale-empty を返さない）"
        assert got is not None, "実データがあるので None(空) を返さない"
        # 再計算後は新署名でディスクへ上書きされる。
        loaded, sig = mpd._load_day_rollup(path)
        assert loaded is not None and sig == "ticks:123:456"

    def test_matching_signature_serves_disk_without_recompute(self, monkeypatch):
        # 署名一致なら disk を信頼して再計算しない（過剰再計算を避ける）。
        day = _DAY0
        now = day + _DAY + 1
        path = mpd._cache_path("JP225", day)
        monkeypatch.setattr(mpd, "_day_source_signature", lambda s, d: "sameSig")
        roll = DayRollup(kmin=100, dwell=np.array([9.0]), cnt=np.array([2.0]))
        mpd._save_day_rollup(path, roll, "sameSig")
        mpd._reset_caches()
        calls = []
        monkeypatch.setattr(
            mpd, "_load_window_ticks",
            lambda s, a, b: calls.append((a, b)) or (mpd._EMPTY_SECS, mpd._EMPTY_MIDS),
        )
        got = mpd._day_rollup("JP225", day, np.ones((7, 24), dtype=bool), now)
        assert calls == [], "署名一致なら再計算しない"
        assert got.kmin == 100

    def test_empty_completed_day_is_not_memoized_and_recomputes_same_process(self, monkeypatch):
        # 🟡-1: 空(None)完了日はメモリにメモ化しない → 同一プロセス内でティック到着(署名変化)しても
        #   line 337 の早期 return で stale-empty を返さず、ディスク署名照合で再計算される。
        day = _DAY0
        now = day + _DAY + 1
        table = np.ones((7, 24), dtype=bool)
        # 1) ティック未着（空）＋署名 ""。
        monkeypatch.setattr(mpd, "_load_window_ticks", lambda s, a, b: (mpd._EMPTY_SECS, mpd._EMPTY_MIDS))
        monkeypatch.setattr(mpd, "_day_source_signature", lambda s, d: "")
        r1 = mpd._day_rollup("JP225", day, table, now)
        assert r1 is None                                   # 空。
        assert ("JP225", day) not in mpd._DAY_CACHE          # 空はメモリにメモ化しない（🟡-1）。
        # 2) ティック到着（署名変化＋実データ）: _reset_caches せずに（同一プロセス）再計算される。
        secs, mids = _synthetic_master()
        base = _make_loader(secs, mids)
        calls = []
        monkeypatch.setattr(
            mpd, "_load_window_ticks",
            lambda s, a, b: (calls.append((int(a), int(b))), base(s, a, b))[1],
        )
        monkeypatch.setattr(mpd, "_day_source_signature", lambda s, d: "ticks:1:2")
        r2 = mpd._day_rollup("JP225", day, table, now)
        assert len(calls) == 1, "空をメモリに残さず同一プロセスで再計算される"
        assert r2 is not None
        assert ("JP225", day) in mpd._DAY_CACHE               # 非空はメモ化する。


# --------------------------------------------------------------------------- #
# ウォーマー（事前ビルド）: 対象日のキャッシュ生成 / 冪等スキップ
# --------------------------------------------------------------------------- #
class TestWarmer:
    def _fake_files(self, tmp_path, days):
        """YYYY/MM/DD 構造の疑似 parquet パスを作る（day_parquet_files 差替用）。"""
        paths = []
        for day_start in days:
            ts = pd.Timestamp(day_start, unit="s")
            p = tmp_path / f"{ts.year:04d}" / f"{ts.month:02d}" / f"{ts.day:02d}" / "JP225_ticks.parquet"
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(b"x")  # 実在ファイル（中身は warmer が読まない・loader を注入する）。
            paths.append(p)
        return paths

    def test_warm_builds_completed_days_and_is_idempotent(self, tmp_path, monkeypatch):
        # Arrange: 3 完了日ぶんの疑似 parquet。loader は合成ティックを返す。
        days = [_DAY0, _DAY0 + _DAY, _DAY0 + 2 * _DAY]
        files = self._fake_files(tmp_path / "ticks", days)
        monkeypatch.setattr(mpd, "day_parquet_files", lambda lo, hi, symbol=None: files)
        secs, mids = _synthetic_master()
        monkeypatch.setattr(mpd, "_load_window_ticks", _make_loader(secs, mids))
        now = _DAY0 + 10 * _DAY  # 全日完了。

        # Act: 1 回目。
        r1 = mpd.warm_dwell_cache("JP225", now=now)

        # Assert: 対象 UTC 日を被覆する全セッション日のキャッシュが作られる（ISSUE-078: 3 UTC 日
        #   → 前後跨ぎで 4 セッション日。キーはセッション始端＝冬 22:00 UTC）。
        expected_sessions = sorted({mpd.session_day_start(d) for d in days}
                                   | {mpd.session_day_start(d + 86399) for d in days})
        assert r1["built"] == len(expected_sessions) and r1["skipped"] == 0
        for day in expected_sessions:
            assert mpd._cache_path("JP225", day).is_file()

        # Act: 2 回目（冪等）。
        r2 = mpd.warm_dwell_cache("JP225", now=now)

        # Assert: すべてスキップ（再構築しない）。
        assert r2["built"] == 0 and r2["skipped"] == len(expected_sessions)

    def test_warm_skips_incomplete_current_day(self, tmp_path, monkeypatch):
        # 当日（未確定）は永続化されない。
        days = [_DAY0, _DAY0 + _DAY]
        files = self._fake_files(tmp_path / "ticks", days)
        monkeypatch.setattr(mpd, "day_parquet_files", lambda lo, hi, symbol=None: files)
        secs, mids = _synthetic_master()
        monkeypatch.setattr(mpd, "_load_window_ticks", _make_loader(secs, mids))
        now = _DAY0 + _DAY + 100  # 2 日目は未完了。

        r = mpd.warm_dwell_cache("JP225", now=now)

        # ISSUE-078: セッション日キー。now 時点で完了しているのは初回セッション
        #   （2023-12-31 22:00 始まり・終端 2024-01-01 22:00 <= now）のみ。
        first_session = mpd.session_day_start(_DAY0)
        assert mpd._cache_path("JP225", first_session).is_file()  # 完了セッションは保存。
        assert not mpd._cache_path("JP225", _DAY0 + _DAY).is_file()  # 未確定当日は非保存。
        assert r["built"] == 1


# --------------------------------------------------------------------------- #
# controller: src 分岐
# --------------------------------------------------------------------------- #
def _patch_dwell_data(monkeypatch):
    """dwell 経路の load_candles（価格レンジ/時刻）と _load_window_ticks（滞在）を注入する。"""
    secs, mids = _synthetic_master()
    monkeypatch.setattr(mpd, "_load_window_ticks", _make_loader(secs, mids))
    candles = [
        {"time": _DAY0, "open": 1000, "high": 1110, "low": 990, "close": 1005},
        {"time": _DAY0 + _DAY, "open": 1005, "high": 1108, "low": 992, "close": 1002},
        {"time": _DAY0 + 2 * _DAY, "open": 1002, "high": 1106, "low": 991, "close": 1000},
    ]
    import market_profile_api.controller.market_profile_controller as ctrl
    monkeypatch.setattr(ctrl.dataset, "load_candles", lambda ref, tf, limit: candles)


class TestControllerDwell:
    def test_dwell_known_tick_ref_returns_200_valid(self, monkeypatch):
        # Arrange
        _patch_dwell_data(monkeypatch)

        # Act
        status, payload = handle_market_profile("jp225_tick", timeframe="1D", src="dwell")

        # Assert
        assert status == 200
        assert payload["ok"] is True
        assert payload["src"] == "dwell"
        assert "atom" in payload
        profile = payload["profile"]
        assert set(profile) >= {
            "bins", "poc", "va_low", "va_high",
            "price_min", "price_max", "tpo_units", "n_bins",
        }
        assert profile["price_min"] <= profile["poc"] <= profile["price_max"]

    def test_dwell_non_tick_ref_returns_400(self, monkeypatch):
        # 'jp225'（既知だが非 tick）で src=dwell → 400 validation。
        status, payload = handle_market_profile("jp225", src="dwell")
        assert status == 400
        assert payload["error"]["type"] == "validation"

    def test_invalid_src_returns_400(self):
        status, payload = handle_market_profile("sample", src="bogus")
        assert status == 400
        assert payload["error"]["type"] == "validation"

    def test_src_omitted_is_candle_backward_compatible(self):
        # src 省略 → 既存 candle 経路（後方互換）。
        status, payload = handle_market_profile("sample")
        assert status == 200
        assert payload["ok"] is True
        assert payload.get("src") == "candle"
        profile = payload["profile"]
        assert profile["n_bins"] == 60
        assert len(profile["bins"]) == 60


# --------------------------------------------------------------------------- #
# controller: リプレイ時間カーソル ``to`` — dwell 経路の as-seen-at-t（未来リーク無し）
# --------------------------------------------------------------------------- #
class TestControllerDwellReplayTo:
    """dwell 経路の ``to``（UNIX 秒）: T 以降の滞在が入らない（未来リーク無し）。

    3 日ぶんの合成ティック（各日 hr2 に HOT 密集）で、``to`` を 1 日目日中 T に置くと、
    集計窓 ``[t0, t1+bar_sec)`` の終端が T までの足に切り詰められ、2〜3 日目の滞在が入らない。
    ``to`` 省略時は全 3 日を集計する（後方互換）。
    """

    def _candles_3d(self):
        return [
            {"time": _DAY0, "open": 1000, "high": 1110, "low": 990, "close": 1005},
            {"time": _DAY0 + _DAY, "open": 1005, "high": 1108, "low": 992, "close": 1002},
            {"time": _DAY0 + 2 * _DAY, "open": 1002, "high": 1106, "low": 991, "close": 1000},
        ]

    def test_to_intraday_excludes_later_days_no_future_leak(self, monkeypatch):
        # Arrange: 全 3 日 candle を返すが、to を 1 日目の hr2（HOT 集中の直後）に置く。
        import market_profile_api.controller.market_profile_controller as ctrl
        candles = self._candles_3d()
        monkeypatch.setattr(ctrl.dataset, "load_candles", lambda ref, tf, limit: candles)
        # ティック窓の end（未来リーク検査点）を捕捉する spy loader。
        secs, mids = _synthetic_master()
        base = _make_loader(secs, mids)
        windows = []

        def _spy(symbol, start, end):
            windows.append((int(start), int(end)))
            return base(symbol, start, end)

        monkeypatch.setattr(mpd, "_load_window_ticks", _spy)
        # to = 1 日目日中（day0 の hr3 = HOT 集中 hr2 の後）。この足(idx0)までで打ち切る。
        to_t = _DAY0 + 7200 + 3600  # day0 hr3。idx0 の足 time(_DAY0) <= to < idx1 の足 time。

        # Act
        status, payload = handle_market_profile(
            "jp225_tick", timeframe="1D", src="dwell", to=str(to_t)
        )

        # Assert: 窓終端は day0 の足 + bar_sec までに収まり、2〜3 日目のティックを読まない。
        assert status == 200
        day_roll_ends = [e for s, e in windows if s % _DAY == 0]
        # dwell 集計に使う日別ロールアップの窓終端がすべて day0+bar_sec 以下（未来リーク無し）。
        assert max(day_roll_ends) <= _DAY0 + _DAY, day_roll_ends

    def test_to_omitted_scans_all_days_backward_compat(self, monkeypatch):
        # Arrange: to 省略 → 全 3 日を走査（従来）。
        import market_profile_api.controller.market_profile_controller as ctrl
        candles = self._candles_3d()
        monkeypatch.setattr(ctrl.dataset, "load_candles", lambda ref, tf, limit: candles)
        secs, mids = _synthetic_master()
        base = _make_loader(secs, mids)
        windows = []

        def _spy(symbol, start, end):
            windows.append((int(start), int(end)))
            return base(symbol, start, end)

        monkeypatch.setattr(mpd, "_load_window_ticks", _spy)

        # Act
        handle_market_profile("jp225_tick", timeframe="1D", src="dwell")

        # Assert: 窓終端は 3 日目の足 + bar_sec（全期間）まで及ぶ。
        assert max(e for _, e in windows) >= _DAY0 + 3 * _DAY


# --------------------------------------------------------------------------- #
# controller: ローリング窓 ``from`` — dwell 経路の過去リーク無し（増分2 A）
# --------------------------------------------------------------------------- #
class TestControllerDwellRollingFrom:
    """dwell 経路の ``from``（UNIX 秒）: from より前の滞在が入らない（過去リーク無し）。

    3 日ぶんの合成ティックで ``from`` を 2 日目に置くと、集計窓 ``[t0, t1+bar_sec)`` の起点が
    from 以降の足へ繰り上がり、1 日目の滞在が入らない。``from`` 省略時は全 3 日を集計する（後方互換）。
    """

    def _candles_3d(self):
        return [
            {"time": _DAY0, "open": 1000, "high": 1110, "low": 990, "close": 1005},
            {"time": _DAY0 + _DAY, "open": 1005, "high": 1108, "low": 992, "close": 1002},
            {"time": _DAY0 + 2 * _DAY, "open": 1002, "high": 1106, "low": 991, "close": 1000},
        ]

    def test_from_excludes_earlier_days_no_past_leak(self, monkeypatch):
        # Arrange: 全 3 日 candle を返すが、from を 2 日目に置く。1 日目のティック窓は走査されない。
        import market_profile_api.controller.market_profile_controller as ctrl
        candles = self._candles_3d()
        monkeypatch.setattr(ctrl.dataset, "load_candles", lambda ref, tf, limit: candles)
        secs, mids = _synthetic_master()
        base = _make_loader(secs, mids)
        windows = []

        def _spy(symbol, start, end):
            windows.append((int(start), int(end)))
            return base(symbol, start, end)

        monkeypatch.setattr(mpd, "_load_window_ticks", _spy)
        from_t = _DAY0 + _DAY  # 2 日目の足 time。idx1.. が残る。

        # Act
        status, payload = handle_market_profile(
            "jp225_tick", timeframe="1D", src="dwell", **{"from": str(from_t)}
        )

        # Assert: dwell 集計に使う日別ロールアップの窓起点がすべて day1 以上（過去リーク無し）。
        assert status == 200
        earliest = min(s for s, _ in windows if not (s == _DAY0 and False))
        # active table 構築窓（直近 _ACTIVE_TABLE_DAYS 日）は除外し、集計窓のみ検査する。
        agg_starts = [s for s, e in windows if e - s <= _DAY and s >= _DAY0]
        assert min(agg_starts) >= _DAY0 + _DAY, agg_starts

    def test_from_omitted_scans_all_days_backward_compat(self, monkeypatch):
        # Arrange: from 省略 → 全 3 日を走査（従来）。
        import market_profile_api.controller.market_profile_controller as ctrl
        candles = self._candles_3d()
        monkeypatch.setattr(ctrl.dataset, "load_candles", lambda ref, tf, limit: candles)
        secs, mids = _synthetic_master()
        base = _make_loader(secs, mids)
        windows = []

        def _spy(symbol, start, end):
            windows.append((int(start), int(end)))
            return base(symbol, start, end)

        monkeypatch.setattr(mpd, "_load_window_ticks", _spy)

        # Act
        handle_market_profile("jp225_tick", timeframe="1D", src="dwell")

        # Assert: 集計窓の起点は 1 日目まで及ぶ（全期間）。
        agg_starts = [s for s, e in windows if e - s <= _DAY]
        assert min(agg_starts) <= _DAY0, agg_starts


# --------------------------------------------------------------------------- #
# controller: スナップショット ``today=1`` — dwell 経路の最終日ぶん再ビン（増分2 C）
# --------------------------------------------------------------------------- #
class TestControllerDwellSnapshotToday:
    """dwell 経路の ``today=1``: 応答 profile に today[]/today_max（窓最終日ぶんの表示 bin 値）が付く。

    移植元 prototype_260630-01 want_today（dwell=最終日ロールアップの再ビン）。today 省略時は付かない。
    合成 3 日で最終日(day2)の滞在が today[] に乗り、累積 tpo とは別スケール（today_max）で返ることを検証。
    """

    def _candles_3d(self):
        return [
            {"time": _DAY0, "open": 1000, "high": 1110, "low": 990, "close": 1005},
            {"time": _DAY0 + _DAY, "open": 1005, "high": 1108, "low": 992, "close": 1002},
            {"time": _DAY0 + 2 * _DAY, "open": 1002, "high": 1106, "low": 991, "close": 1000},
        ]

    def test_today_omitted_has_no_today_keys(self, monkeypatch):
        import market_profile_api.controller.market_profile_controller as ctrl
        candles = self._candles_3d()
        monkeypatch.setattr(ctrl.dataset, "load_candles", lambda ref, tf, limit: candles)
        monkeypatch.setattr(mpd, "_load_window_ticks", _make_loader(*_synthetic_master()))

        _, payload = handle_market_profile("jp225_tick", timeframe="1D", src="dwell")
        assert "today" not in payload["profile"]
        assert "today_max" not in payload["profile"]

    def test_today_1_returns_last_day_only(self, monkeypatch):
        # Arrange: 全 3 日集計だが today[] は最終日(day2)ぶんのみ。HOT(1000)へ day2 の 30 ティック分が乗る。
        import market_profile_api.controller.market_profile_controller as ctrl
        candles = self._candles_3d()
        monkeypatch.setattr(ctrl.dataset, "load_candles", lambda ref, tf, limit: candles)
        monkeypatch.setattr(mpd, "_load_window_ticks", _make_loader(*_synthetic_master()))

        # Act
        _, payload = handle_market_profile(
            "jp225_tick", timeframe="1D", src="dwell", **{"today": "1"}
        )
        profile = payload["profile"]

        # Assert: today[] は n_bins 長・today_max>0・最終日の HOT 帯に非ゼロがある。
        assert "today" in profile
        assert len(profile["today"]) == profile["n_bins"]
        assert profile["today_max"] > 0
        assert sum(profile["today"]) > 0
        # today[] の総和（最終日ぶん）は 累積 tpo_units（全 3 日）より小さい（当日のみ）。
        assert sum(profile["today"]) < profile["tpo_units"]


# --------------------------------------------------------------------------- #
# controller: 全期間化 — dwell の価格レンジ/窓は全 candle 由来（250日キャップ撤廃）
# --------------------------------------------------------------------------- #
def _empty_loader(symbol, start, end):
    """dwell 集計を空にする _load_window_ticks 代替（レンジ/窓算出のみを検証するため）。"""
    return mpd._EMPTY_SECS, mpd._EMPTY_MIDS


class TestControllerDwellFullPeriod:
    def _candles_spanning_cap(self):
        """旧 cap（250日）を超える期間の candle 集合。

        旧 cap 外（290〜300 日前）に極端 low/high を置く。全期間化後はこれらもレンジに反映される。
        """
        t1 = _DAY0 + 300 * _DAY
        candles = [
            # 旧 cap 外（300/290 日前）: 極端な安値/高値。全期間化ではレンジに含まれる。
            {"time": t1 - 300 * _DAY, "open": 500, "high": 9999.0, "low": 1.0, "close": 500},
            {"time": t1 - 290 * _DAY, "open": 500, "high": 8000.0, "low": 5.0, "close": 500},
        ]
        # 直近側: 穏当なレンジ 990..1110。
        for k in range(20, -1, -1):
            candles.append(
                {"time": t1 - k * _DAY, "open": 1000, "high": 1110.0, "low": 990.0, "close": 1005}
            )
        return candles

    def test_dwell_price_range_uses_all_candles(self, monkeypatch):
        # 全期間化: レンジは全 candle の low/high 由来（旧 cap 外の極値 1/9999 も反映される）。
        import market_profile_api.controller.market_profile_controller as ctrl

        candles = self._candles_spanning_cap()
        monkeypatch.setattr(ctrl.dataset, "load_candles", lambda ref, tf, limit: candles)
        monkeypatch.setattr(mpd, "_load_window_ticks", _empty_loader)

        status, payload = handle_market_profile("jp225_tick", timeframe="1D", src="dwell")

        assert status == 200
        p = payload["profile"]
        assert p["price_min"] == 1.0      # 全 candle の最安（旧 cap で切られない）。
        assert p["price_max"] == 9999.0   # 全 candle の最高。

    def test_dwell_window_spans_full_period(self, monkeypatch):
        # 全期間化: 集計窓 [t0, t1+bar_sec) は全 candle の先頭〜末尾を覆う（旧 cap に丸められない）。
        import market_profile_api.controller.market_profile_controller as ctrl

        candles = self._candles_spanning_cap()
        monkeypatch.setattr(ctrl.dataset, "load_candles", lambda ref, tf, limit: candles)

        calls = []
        monkeypatch.setattr(
            mpd, "_load_window_ticks",
            lambda s, a, b: (calls.append((int(a), int(b))), (mpd._EMPTY_SECS, mpd._EMPTY_MIDS))[1],
        )
        _pin_legacy_table(monkeypatch)  # ISSUE-089: 表構築の追加 load を最古日検証から排除。
        handle_market_profile("jp225_tick", timeframe="1D", src="dwell")

        earliest = min(a for a, _ in calls)
        t0 = candles[0]["time"]
        assert earliest == (t0 // _DAY) * _DAY  # 走査は全期間の先頭 candle まで遡る。

    def test_candle_path_price_range_uses_all_candles(self, monkeypatch):
        # candle 経路（src 省略）は従来通り全 candle の low/high をそのまま使う（不変）。
        import market_profile_api.controller.market_profile_controller as ctrl

        candles = self._candles_spanning_cap()
        monkeypatch.setattr(ctrl.dataset, "load_candles", lambda ref, tf, limit: candles)

        status, payload = handle_market_profile("sample", timeframe="1D")

        assert status == 200
        assert payload["src"] == "candle"
        p = payload["profile"]
        assert p["price_min"] == 1.0
        assert p["price_max"] == 9999.0


# --------------------------------------------------------------------------- #
# 統合（実データ・軽量）: jp225_tick 小窓で src=dwell が 200 / POC レンジ内
# --------------------------------------------------------------------------- #
def _tick_data_available() -> bool:
    from adapter.compute import dataset
    try:
        return dataset.DATASET_WHITELIST["jp225_tick"].is_file()
    except Exception:
        return False


@pytest.mark.slow
@pytest.mark.skipif(not _tick_data_available(), reason="jp225_tick 実データが無い環境ではスキップ")
def test_integration_dwell_small_window_real_data():
    status, payload = handle_market_profile(
        "jp225_tick", timeframe="1D", limit="20", bins="30", src="dwell"
    )
    assert status == 200
    assert payload["ok"] is True
    assert payload["src"] == "dwell"
    p = payload["profile"]
    assert p["n_bins"] == 30
    assert p["price_min"] <= p["poc"] <= p["price_max"]


# --------------------------------------------------------------------------- #
# want_sessions（日別プロファイル分割・dwell 経路）: 移植元 prototype_260630-01 mp_core
# --------------------------------------------------------------------------- #
class TestComputeDwellProfileSessions:
    """compute_dwell_profile(want_sessions=True): 各カレンダー日の日別ロールアップを表示 bin へ再集計。

    合成 3 日（_synthetic_master）は各日 hr2 に HOT(1000) 密集・hr20 に COLD(1100) 疎ら。metric='count'
    （生ティック数）で決定論に検証する（dwell 秒はギャップ依存で日ごと同形だが count が最も明快）。
    省略時（want_sessions=False）は sessions キー無し（後方互換）。
    """

    def _profile(self, monkeypatch, **kw):
        secs, mids = _synthetic_master()
        monkeypatch.setattr(mpd, "_load_window_ticks", _make_loader(secs, mids))
        # 3 日を覆う窓（t0=day0 の足 time・t1=day2 の足 time・bar_sec=1D）。
        return mpd.compute_dwell_profile(
            "JP225", _DAY0, _DAY0 + 2 * _DAY, 900.0, 1200.0, 30,
            va_pct=0.70, bar_sec=_DAY, metric="count", now=_DAY0 + 10 * _DAY, **kw
        )

    def test_omitted_has_no_sessions_key(self, monkeypatch):
        out = self._profile(monkeypatch)
        assert "sessions" not in out

    def test_sessions_returns_one_entry_per_calendar_day(self, monkeypatch):
        out = self._profile(monkeypatch, want_sessions=True)
        sessions = out["sessions"]
        # 3 日ぶん・日付昇順・各 tpo 長 = n_bins。
        assert [s["date"] for s in sessions] == ["2024-01-01", "2024-01-02", "2024-01-03"]
        for s in sessions:
            assert len(s["tpo"]) == 30

    def test_sessions_include_per_day_poc_and_va(self, monkeypatch):
        # dwell 経路も各セッションへ POC/VA（_value_area 単一定義）を付与する（frontend 再実装を排除）。
        out = self._profile(monkeypatch, want_sessions=True)
        for s in out["sessions"]:
            assert {"poc", "va_low", "va_high"} <= set(s)
            assert s["va_low"] <= s["poc"] <= s["va_high"]

    def test_sessions_per_day_count_matches(self, monkeypatch):
        # 各日 count は 32 ティック（HOT 30 + COLD 2）。日別合計 = 32 が保存される。
        out = self._profile(monkeypatch, want_sessions=True)
        for s in out["sessions"]:
            assert round(sum(s["tpo"])) == 32, (s["date"], sum(s["tpo"]))

    def test_sessions_sum_matches_cumulative_tpo(self, monkeypatch):
        # 性質: 全日 sessions の bin 別合計 = 累積 tpo（分割の保存則・境界日合算含む）。
        out = self._profile(monkeypatch, want_sessions=True)
        acc = [0.0] * out["n_bins"]
        for s in out["sessions"]:
            for j, v in enumerate(s["tpo"]):
                acc[j] += v
        cum = [b["tpo"] for b in out["bins"]]
        # tpo は int 丸め済のため round で比較（再集計値と丸めの差は 1 未満）。
        for a, c in zip(acc, cum):
            assert abs(a - c) < 1.0

    def test_sessions_windowed_narrows_days(self, monkeypatch):
        # to/from 窓（t0/t1）で日が絞られる: day1 のみを覆う窓 → sessions は 1 日。
        secs, mids = _synthetic_master()
        monkeypatch.setattr(mpd, "_load_window_ticks", _make_loader(secs, mids))
        out = mpd.compute_dwell_profile(
            "JP225", _DAY0 + _DAY, _DAY0 + _DAY, 900.0, 1200.0, 30,
            va_pct=0.70, bar_sec=_DAY, metric="count", now=_DAY0 + 10 * _DAY,
            want_sessions=True,
        )
        assert [s["date"] for s in out["sessions"]] == ["2024-01-02"]

    # --- 修正2: 境界分割日の合算 / metric 尊重（hand-calc 回帰強化） ---------------- #
    #
    # 境界窓 [t0=day0+noon, t1=day0+noon]（bar_sec=1D）は win_to=day0+noon+1D となり、
    #   day0 の午後（partial）と day1 の午前（partial）へまたがる。各カレンダー日は sessions に
    #   **ちょうど 1 エントリ**として現れ（分割 2 エントリにならず同一 date へ合算される）、
    #   その値は当該部分窓の集計に一致する。合成ティックは各日 hr2(=午前)に HOT30・hr20(=午後)に
    #   COLD2 を置くため、day0(午後)=COLD2/HOT0、day1(午前)=HOT30/COLD0 に確定する。
    _BOUNDARY_T = _DAY0 + 43200  # day0 正午（部分窓の起点＝終点。win_to は +1D で day1 正午）。
    _BINW = (1200.0 - 900.0) / 30
    _HOT_BIN = int((1000.0 - 900.0) / _BINW)   # HOT=1000 の表示 bin index（=10）。
    _COLD_BIN = int((1100.0 - 900.0) / _BINW)  # COLD=1100 の表示 bin index（=20）。

    def _boundary_sessions(self, monkeypatch, metric):
        secs, mids = _synthetic_master()
        monkeypatch.setattr(mpd, "_load_window_ticks", _make_loader(secs, mids))
        _pin_legacy_table(monkeypatch, secs)  # ISSUE-089: 旧セマンティクスの表を固定。
        out = mpd.compute_dwell_profile(
            "JP225", self._BOUNDARY_T, self._BOUNDARY_T, 900.0, 1200.0, 30,
            va_pct=0.70, bar_sec=_DAY, metric=metric, now=_DAY0 + 10 * _DAY,
            want_sessions=True,
        )
        return out

    def test_boundary_split_day_merges_to_single_entry_per_date(self, monkeypatch):
        # 境界分割日の合算: 部分窓にまたがっても各 date はちょうど 1 エントリ（重複しない）。
        out = self._boundary_sessions(monkeypatch, "count")
        dates = [s["date"] for s in out["sessions"]]
        # day0 午後 + day1 午前 = 2 日ぶん・各 1 回だけ（partial+partial が別エントリに割れない）。
        assert dates == ["2024-01-01", "2024-01-02"]
        assert len(dates) == len(set(dates)), f"date が重複＝分割エントリ化: {dates}"
        # count 合算値のハンド計算: day0 午後=COLD2 のみ、day1 午前=HOT30 のみ。
        by_date = {s["date"]: s["tpo"] for s in out["sessions"]}
        assert round(sum(by_date["2024-01-01"])) == 2   # 午後の COLD 2 ティック。
        assert round(sum(by_date["2024-01-02"])) == 30  # 午前の HOT 30 ティック。
        # 保存則: sessions の bin 別合計 = 累積 tpo（合算経路が二重計上・欠落しない）。
        acc = [0.0] * out["n_bins"]
        for s in out["sessions"]:
            for j, v in enumerate(s["tpo"]):
                acc[j] += v
        for a, c in zip(acc, (b["tpo"] for b in out["bins"])):
            assert abs(a - c) < 1.0

    def test_metric_count_and_dwell_differ_at_closed_band(self, monkeypatch):
        # metric 尊重: 同一境界窓で count（生ティック）と dwell（滞在秒）の sessions 値が異なる。
        #   休場帯 bin（COLD=1100・day0 午後）は count>0（生ティックは数える）だが dwell==0
        #   （セッションマスクで休場帯の滞在は 0 に落とす）。
        out_count = self._boundary_sessions(monkeypatch, "count")
        mpd._reset_caches()  # metric 切替時のロールアップ再計算を保証（キャッシュは metric 非依存だが明示）。
        out_dwell = self._boundary_sessions(monkeypatch, "dwell")
        cnt_by_date = {s["date"]: s["tpo"] for s in out_count["sessions"]}
        dwl_by_date = {s["date"]: s["tpo"] for s in out_dwell["sessions"]}
        # 休場帯（day0 午後・COLD bin）: count は 2（生ティック）・dwell は 0（休場滞在は除外）。
        assert cnt_by_date["2024-01-01"][self._COLD_BIN] == 2.0
        assert dwl_by_date["2024-01-01"][self._COLD_BIN] == 0.0
        # 活発帯（day1 午前・HOT bin）: どちらも非ゼロだが値は異なる（count=30 生数・dwell=滞在秒）。
        assert cnt_by_date["2024-01-02"][self._HOT_BIN] == 30.0
        assert dwl_by_date["2024-01-02"][self._HOT_BIN] > 0.0
        assert dwl_by_date["2024-01-02"][self._HOT_BIN] != cnt_by_date["2024-01-02"][self._HOT_BIN]


class TestControllerDwellSessions:
    """controller: src=dwell & sessions=1 で応答トップレベルに sessions が付く（profile は不変）。"""

    def _candles_3d(self):
        return [
            {"time": _DAY0, "open": 1000, "high": 1110, "low": 990, "close": 1005},
            {"time": _DAY0 + _DAY, "open": 1005, "high": 1108, "low": 992, "close": 1002},
            {"time": _DAY0 + 2 * _DAY, "open": 1002, "high": 1106, "low": 991, "close": 1000},
        ]

    def test_sessions_1_adds_toplevel_sessions(self, monkeypatch):
        import market_profile_api.controller.market_profile_controller as ctrl
        candles = self._candles_3d()
        monkeypatch.setattr(ctrl.dataset, "load_candles", lambda ref, tf, limit: candles)
        monkeypatch.setattr(mpd, "_load_window_ticks", _make_loader(*_synthetic_master()))

        status, payload = handle_market_profile(
            "jp225_tick", timeframe="1D", src="dwell", **{"sessions": "1"}
        )
        assert status == 200
        assert "sessions" in payload
        assert len(payload["sessions"]) == 3
        # profile の 8 キーは不変（sessions は profile 内に入れない）。
        assert "sessions" not in payload["profile"]

    def test_sessions_omitted_no_toplevel_sessions(self, monkeypatch):
        import market_profile_api.controller.market_profile_controller as ctrl
        candles = self._candles_3d()
        monkeypatch.setattr(ctrl.dataset, "load_candles", lambda ref, tf, limit: candles)
        monkeypatch.setattr(mpd, "_load_window_ticks", _make_loader(*_synthetic_master()))

        status, payload = handle_market_profile("jp225_tick", timeframe="1D", src="dwell")
        assert status == 200
        assert "sessions" not in payload

    def test_sessions_total_equals_day_count_dwell_path(self, monkeypatch):
        # 注記の意味論整合（修正1・dwell 経路）: sessions_total はキャップ前の実日数（合成 3 日）。
        #   3 日 <= 60 なのでキャップは発火しないが、sessions_total は sessions と同数（3）を返す。
        import market_profile_api.controller.market_profile_controller as ctrl
        candles = self._candles_3d()
        monkeypatch.setattr(ctrl.dataset, "load_candles", lambda ref, tf, limit: candles)
        monkeypatch.setattr(mpd, "_load_window_ticks", _make_loader(*_synthetic_master()))

        status, payload = handle_market_profile(
            "jp225_tick", timeframe="1D", src="dwell", **{"sessions": "1"}
        )
        assert status == 200
        assert payload["sessions_total"] == 3
        assert payload["sessions_total"] == len(payload["sessions"])
        assert isinstance(payload["sessions_total"], int)

    def test_sessions_total_omitted_when_not_requested_dwell_path(self, monkeypatch):
        # 後方互換（dwell 経路）: sessions 非要求時は sessions_total を付けない。
        import market_profile_api.controller.market_profile_controller as ctrl
        candles = self._candles_3d()
        monkeypatch.setattr(ctrl.dataset, "load_candles", lambda ref, tf, limit: candles)
        monkeypatch.setattr(mpd, "_load_window_ticks", _make_loader(*_synthetic_master()))

        status, payload = handle_market_profile("jp225_tick", timeframe="1D", src="dwell")
        assert status == 200
        assert "sessions_total" not in payload



# --------------------------------------------------------------------------- #
# セッション日切り（ISSUE-078）: 日の走査・sessions ラベルが NY17:00 ET 基準のセッション日になる
# --------------------------------------------------------------------------- #
class TestSessionDaySplit:
    # 夏の月曜セッション始端（2026-07-12 21:00 UTC）と実測オープン（日曜 22:03 UTC）。
    MON_START = 1783890000
    SUN_OPEN = 1783893824  # 2026-07-12 22:03:44 UTC

    def test_sunday_evening_ticks_are_labeled_monday_session(self, monkeypatch):
        # 日曜夜（UTC）のティックが '2026-07-12' でなく月曜セッション '2026-07-13' に帰属する。
        secs = [self.SUN_OPEN, self.SUN_OPEN + 60, self.SUN_OPEN + 7200, self.SUN_OPEN + 7260]
        mids = [100.0, 100.0, 110.0, 110.0]
        monkeypatch.setattr(mpd, "_load_window_ticks", _make_loader(secs, mids))
        profile = mpd.compute_dwell_profile(
            "JP225", self.MON_START, self.MON_START, 95.0, 115.0, 4,
            va_pct=VA_PCT_DEFAULT, bar_sec=86400, now=self.MON_START + 3 * 86400,
            metric="count", want_sessions=True,
        )
        labels = [s["date"] for s in profile["sessions"]]
        assert labels == ["2026-07-13"], labels
        assert profile["tpo_units"] == 4  # 全ティックが単一セッションに計上される。

    def test_full_session_walker_requests_session_window(self, monkeypatch):
        # 完全日ウォークが [セッション始端, 翌セッション始端) の窓で読むこと（UTC 深夜切りでない）。
        windows = []

        def spy_loader(symbol, start, end):
            windows.append((int(start), int(end)))
            return _make_loader([self.SUN_OPEN], [100.0])(symbol, start, end)

        monkeypatch.setattr(mpd, "_load_window_ticks", spy_loader)
        mpd.compute_dwell_profile(
            "JP225", self.MON_START, self.MON_START, 95.0, 115.0, 4,
            va_pct=VA_PCT_DEFAULT, bar_sec=86400, now=self.MON_START + 3 * 86400,
            metric="count",
        )
        day_windows = [w for w in windows if w[0] == self.MON_START]
        assert day_windows and day_windows[0] == (self.MON_START, self.MON_START + 86400)

    def test_day_rollup_completion_uses_next_session_start(self, monkeypatch, tmp_path):
        # 完了判定がセッション終端（翌セッション始端）基準であること: now=終端-1 は未完了（非永続）。
        monkeypatch.setattr(mpd, "_load_window_ticks", _make_loader([self.SUN_OPEN], [100.0]))
        table = np.ones((7, 24), dtype=bool)
        path = mpd._cache_path("JP225", self.MON_START)
        mpd._day_rollup("JP225", self.MON_START, table, now=self.MON_START + 86400 - 1)
        assert not path.is_file(), "セッション未了は永続化しない"
        mpd._day_rollup("JP225", self.MON_START, table, now=self.MON_START + 86400)
        assert path.is_file(), "セッション完了で永続化する"


class TestActiveTableWindowKeyedMemo:
    """ISSUE-089: _active_table のメモは窓（at_from/win_to）をキーに含める。

    旧実装は symbol のみキー（先勝ち）で、プロセス内で最初に触った要求の窓のテーブルが
    以後の全要求へ流用され、境界日 partial・新規日 npz へ**プロセス履歴依存**の値が焼き込まれて
    いた（byte-parity golden が数時間で再赤化した真因）。窓が異なれば別テーブルを構築すること。
    """

    def test_different_windows_build_different_tables(self, monkeypatch):
        import numpy as np
        import market_profile_api.compute.market_profile_dwell as mpd

        mpd._reset_caches()

        def fake_ticks(symbol, start, end):
            # 窓 A（end<=1000）: 月曜 0 時台のみ活発 / 窓 B: 火曜 1 時台のみ活発、を模す
            #   1970-01-05 が月曜。月曜0時 = 4*86400 + 0h、火曜1時 = 5*86400 + 1h。
            if int(end) <= 1000 * 86400:
                base = 4 * 86400
            else:
                base = 5 * 86400 + 3600
            secs = np.arange(base, base + 3600, 10, dtype=np.int64)
            return secs, np.full(secs.shape, 100.0)

        monkeypatch.setattr(mpd, "_load_window_ticks", fake_ticks)
        t_a = mpd._active_table("JP225", 0, 999 * 86400)
        t_b = mpd._active_table("JP225", 0, 2000 * 86400)
        assert not np.array_equal(t_a, t_b), "窓が異なれば別テーブル（先勝ちメモの禁止）"
        # 同一窓は再構築しない（メモは窓キーで効く）。
        calls = []
        monkeypatch.setattr(mpd, "_load_window_ticks",
                            lambda *a: calls.append(1) or fake_ticks(*a))
        t_a2 = mpd._active_table("JP225", 0, 999 * 86400)
        assert np.array_equal(t_a, t_a2) and not calls, "同一窓はメモヒット"
        mpd._reset_caches()

    def test_day_rollup_table_is_anchored_to_days_month(self, monkeypatch):
        """ISSUE-089: 日次ロールアップの active table は「その日の属する月初」アンカー
        （[月初-120日, 月初)＝日の純関数・因果）で構築し、リクエスト窓 t1 に依存しない。"""
        import numpy as np
        import market_profile_api.compute.market_profile_dwell as mpd

        mpd._reset_caches()
        seen = []
        real = mpd._active_table

        def spy(symbol, at_from, win_to):
            seen.append((int(at_from), int(win_to)))
            return np.ones((7, 24), dtype=bool)

        monkeypatch.setattr(mpd, "_active_table", spy)
        monkeypatch.setattr(mpd, "_load_window_ticks",
                            lambda s, a, b: (np.array([], dtype=np.int64), np.array([])))
        # now=0（未完了扱い）でディスク経路を回避し、計算経路の表アンカーのみを観測する。
        from datetime import datetime, timezone
        from marketdata.session_day import session_day_start
        day = session_day_start(int(datetime(2026, 6, 5, 12, tzinfo=timezone.utc).timestamp()))
        month_start = int(datetime(2026, 6, 1, tzinfo=timezone.utc).timestamp())
        mpd._day_rollup("JP225", day, None, now=0)
        assert seen and seen[-1] == (month_start - 120 * 86400, month_start), \
            "日次表は月初アンカー（リクエスト非依存）"
        mpd._reset_caches()
        monkeypatch.setattr(mpd, "_active_table", real)
