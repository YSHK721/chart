"""tf_period_profile_controller（GET /tf_period_profile の純ロジック）の検証。

handle_tf_period_profile(ref, timeframe, frm, to) -> (status, body)
  - 非 tick ref / 非対応 tf（1W/1M/未知）→ 400。
  - 不正窓（from>=to / 欠落）→ 400。
  - 正常: 窓 tick を最小単位でビニングした tf-period 列を返す（tick 読込は monkeypatch）。
"""
from __future__ import annotations

import numpy as np

from market_profile_api.controller import tf_period_profile_controller as ctl


def _fake_ticks(_symbol, start, end):
    # 2 周期分（1m）: period0[0..59] mids 10,10,11 / period60[60..119] mids 20,21。
    secs = np.array([0, 10, 20, 60, 70])
    mids = np.array([10.0, 10.0, 11.0, 20.0, 21.0])
    m = (secs >= start) & (secs < end)
    return secs[m], mids[m]


def test_non_tick_ref_400():
    st, body = ctl.handle_tf_period_profile("jp225_m1", "1m", 0, 120)
    assert st == 400 and body["ok"] is False


def test_unsupported_tf_400():
    # ISSUE-086: 1W/1M はバケット列として対応済み（旧: 400）。未知 tf のみ 400。
    st, body = ctl.handle_tf_period_profile("jp225_tick", "3h", 0, 120)
    assert st == 400


def test_bad_window_400():
    st, _ = ctl.handle_tf_period_profile("jp225_tick", "1m", 120, 120)  # from>=to
    assert st == 400
    st2, _ = ctl.handle_tf_period_profile("jp225_tick", "1m", None, 120)  # 欠落
    assert st2 == 400


def test_happy_path_returns_columns(monkeypatch):
    ctl._reset_tf_period_cache()
    monkeypatch.setattr(ctl, "_TFP_CACHE_ROOT", False)  # ディスク無効（テスト隔離）。
    monkeypatch.setattr(ctl._mpd, "_load_window_ticks", _fake_ticks)
    monkeypatch.setattr(ctl._mpd, "resolve_symbol", lambda ref: "JP225")
    st, body = ctl.handle_tf_period_profile("jp225_tick", "1m", 0, 120, now=1e12)
    assert st == 200 and body["ok"] is True
    assert body["tf"] == "1m" and body["from"] == 0 and body["to"] == 120
    # ISSUE-073: 1m は最小価格刻み 0.0255 でビニング（時間足別解像度・依頼者承認 2026-07-13）。
    assert body["unit"] == 0.0255
    times = [c["time"] for c in body["columns"]]
    assert times == [0, 60]
    # 0.0255 格子への量子化: mid 10→round(392.16)=392→9.996 / 11→431→10.9905 /
    # 20→784→19.992 / 21→824→21.012（price は 4 桁丸め）。
    assert body["columns"][0]["levels"] == [[9.996, 2], [10.9905, 1]]
    assert body["columns"][1]["levels"] == [[19.992, 1], [21.012, 1]]


def test_non_1m_tf_keeps_grid_w_unit(monkeypatch):
    """ISSUE-073: 1m 以外（例 15m）は ISSUE-068 の GRID_W(=10pt) を維持する。"""
    ctl._reset_tf_period_cache()
    monkeypatch.setattr(ctl, "_TFP_CACHE_ROOT", False)
    monkeypatch.setattr(ctl._mpd, "_load_window_ticks", _fake_ticks)
    monkeypatch.setattr(ctl._mpd, "resolve_symbol", lambda ref: "JP225")
    st, body = ctl.handle_tf_period_profile("jp225_tick", "15m", 0, 900, now=1e12)
    assert st == 200 and body["ok"] is True
    assert body["unit"] == 10.0
    # 全 mids 10,10,11,20,21 が単一 15m 周期に量子化される（round(/10): 1,1,1,2,2）。
    assert body["columns"][0]["levels"] == [[10.0, 3], [20.0, 2]]


def test_completed_day_is_cached(monkeypatch):
    """完了日（day_start+86400<=now）は 2 回目以降 per-day キャッシュヒット＝tick 読込しない（ISSUE-055 B）。"""
    ctl._reset_tf_period_cache()
    monkeypatch.setattr(ctl, "_TFP_CACHE_ROOT", False)
    calls = {"n": 0}

    def counting_ticks(symbol, start, end):
        calls["n"] += 1
        return _fake_ticks(symbol, start, end)

    monkeypatch.setattr(ctl._mpd, "_load_window_ticks", counting_ticks)
    monkeypatch.setattr(ctl._mpd, "resolve_symbol", lambda ref: "JP225")
    st1, b1 = ctl.handle_tf_period_profile("jp225_tick", "1m", 0, 120, now=1e12)
    st2, b2 = ctl.handle_tf_period_profile("jp225_tick", "1m", 0, 120, now=1e12)
    assert st1 == 200 and st2 == 200
    assert calls["n"] == 1  # 2 回目はメモリキャッシュヒットで tick 読込を呼ばない（同一日）。
    assert b2["columns"] == b1["columns"]


def test_incomplete_day_not_cached(monkeypatch):
    """当日（day_start+86400>now）は成長しうるためキャッシュせず毎回再計算する（ISSUE-055 B）。"""
    ctl._reset_tf_period_cache()
    monkeypatch.setattr(ctl, "_TFP_CACHE_ROOT", False)
    calls = {"n": 0}

    def counting_ticks(symbol, start, end):
        calls["n"] += 1
        return _fake_ticks(symbol, start, end)

    monkeypatch.setattr(ctl._mpd, "_load_window_ticks", counting_ticks)
    monkeypatch.setattr(ctl._mpd, "resolve_symbol", lambda ref: "JP225")
    # now=100 → 日 [0,86400) は未完了（86400>100）。
    ctl.handle_tf_period_profile("jp225_tick", "1m", 0, 120, now=100)
    ctl.handle_tf_period_profile("jp225_tick", "1m", 0, 120, now=100)
    assert calls["n"] == 2  # 当日はキャッシュされず 2 回とも再計算。


def test_completed_day_persists_to_disk(monkeypatch, tmp_path):
    """完了日はディスク JSON へ永続し、メモリ消去後（＝再起動相当）でも tick 再読込なしで復元する（ISSUE-055 B per-day）。"""
    ctl._reset_tf_period_cache()
    monkeypatch.setattr(ctl, "_TFP_CACHE_ROOT", str(tmp_path))  # ディスクを tmp に隔離。
    monkeypatch.setattr(ctl._mpd, "resolve_symbol", lambda ref: "JP225")
    monkeypatch.setattr(ctl._mpd, "_load_window_ticks", _fake_ticks)
    st1, b1 = ctl.handle_tf_period_profile("jp225_tick", "1m", 0, 120, now=1e12)  # 計算＋ディスク保存。
    assert st1 == 200
    # 「再起動相当」: メモリを消し、tick 読込を呼べば失敗するようにする（呼ばれない＝ディスク復元の証明）。
    ctl._reset_tf_period_cache()

    def boom(*a, **k):
        raise AssertionError("tick 再読込が呼ばれた（ディスク復元されていない）")

    monkeypatch.setattr(ctl._mpd, "_load_window_ticks", boom)
    st2, b2 = ctl.handle_tf_period_profile("jp225_tick", "1m", 0, 120, now=1e12)
    assert st2 == 200
    assert b2["columns"] == b1["columns"]  # ディスクから同一結果を復元。


def test_disk_generation_follows_tfp_cache_version(monkeypatch, tmp_path):
    """非 zp のディスク世代 subdir は _TFP_CACHE_VERSION に連動する（ISSUE-091 A3・手書き 's1' 排除）。

    version bump で保存先ディレクトリが変わる（＝新旧コード併走でも同一ファイルを奪い合わない）
    ことを、実ディレクトリ名で確認する。
    """
    ctl._reset_tf_period_cache()
    monkeypatch.setattr(ctl, "_TFP_CACHE_ROOT", str(tmp_path))
    monkeypatch.setattr(ctl._mpd, "resolve_symbol", lambda ref: "JP225")
    monkeypatch.setattr(ctl._mpd, "_load_window_ticks", _fake_ticks)
    st, _ = ctl.handle_tf_period_profile("jp225_tick", "1m", 0, 120, now=1e12)
    assert st == 200
    assert list((tmp_path / "JP225").glob("1m/s1/*")), "v1 は従来 's1' と同一パスへ保存する"

    ctl._reset_tf_period_cache()
    monkeypatch.setattr(ctl, "_TFP_CACHE_VERSION", 2)  # bump をシミュレート。
    st2, _ = ctl.handle_tf_period_profile("jp225_tick", "1m", 0, 120, now=1e12)
    assert st2 == 200
    assert list((tmp_path / "JP225").glob("1m/s2/*")), "bump 後は s2 subdir へ書く（旧世代と不混在）"


def test_default_disk_root_resolves_via_tick_store_port(monkeypatch, tmp_path):
    """既定 root（_TFP_CACHE_ROOT=None）が TickStorePort 経由で解決される（ISSUE-092 回帰）。

    既存テストは全て _TFP_CACHE_ROOT を注入して既定経路を通らないため、撤去済み属性への
    参照（旧 _mpd._paths）が実行時 AttributeError として本番経路だけで発火していた。
    既定経路そのものを実行して固定する。
    """
    from market_profile_api.compute import tick_store_port as tsp

    class _FakeStore:
        def data_dir(self):
            return tmp_path

    monkeypatch.setattr(tsp, "_STORE", _FakeStore())
    monkeypatch.setattr(ctl, "_TFP_CACHE_ROOT", None)
    assert ctl._tfp_disk_root() == tmp_path / "cache" / "tf_period"


# --------------------------------------------------------------------------- #
# セッション日切り（ISSUE-078）
# --------------------------------------------------------------------------- #
_MON_START = 1783890000  # 2026-07-12 21:00 UTC（夏・月曜セッション始端）。


def _sunday_ticks(_symbol, start, end):
    # 週明けオープン近傍（日曜 22:03 UTC〜）のティック（mid 100/101）。
    secs = np.array([_MON_START + 3800, _MON_START + 3860, _MON_START + 80000], dtype=np.int64)
    mids = np.array([100.0, 101.0, 200.0])
    m = (secs >= int(start)) & (secs < int(end))
    return secs[m], mids[m]


def test_session_walker_includes_sunday_evening_in_monday_session(monkeypatch):
    """日曜夜 UTC のティックが月曜セッションの周期列として返る（薄い日曜原子の消滅）。"""
    ctl._reset_tf_period_cache()
    monkeypatch.setattr(ctl, "_TFP_CACHE_ROOT", False)
    monkeypatch.setattr(ctl._mpd, "_load_window_ticks", _sunday_ticks)
    monkeypatch.setattr(ctl._mpd, "resolve_symbol", lambda ref: "JP225")
    st, body = ctl.handle_tf_period_profile(
        "jp225_tick", "1m", _MON_START, _MON_START + 7200, now=1e12)
    assert st == 200
    times = [c["time"] for c in body["columns"]]
    assert times == [_MON_START + 3780, _MON_START + 3840]  # 22:03/22:04 UTC の 1m 周期。


def test_4h_straddling_period_assigned_once(monkeypatch):
    """4h 周期グリッドは UTC floor のまま・セッション跨ぎ周期は始端所属で一意（重複列なし）。"""
    ctl._reset_tf_period_cache()
    monkeypatch.setattr(ctl, "_TFP_CACHE_ROOT", False)
    monkeypatch.setattr(ctl._mpd, "_load_window_ticks", _sunday_ticks)
    monkeypatch.setattr(ctl._mpd, "resolve_symbol", lambda ref: "JP225")
    st, body = ctl.handle_tf_period_profile(
        "jp225_tick", "4h", _MON_START - 86400, _MON_START + 2 * 86400, now=1e12)
    assert st == 200
    times = [c["time"] for c in body["columns"]]
    assert len(times) == len(set(times)), f"重複列: {times}"
    for t in times:
        assert t % (4 * 3600) == 0, "4h 周期は UTC floor グリッド（バー時刻整合）"


def test_1d_column_is_one_per_session_keyed_by_session_start(monkeypatch):
    """1D はセッション日＝1 周期（time=1D バー規約＝セッション日ラベルの UTC 深夜）。"""
    ctl._reset_tf_period_cache()
    monkeypatch.setattr(ctl, "_TFP_CACHE_ROOT", False)
    monkeypatch.setattr(ctl._mpd, "_load_window_ticks", _sunday_ticks)
    monkeypatch.setattr(ctl._mpd, "resolve_symbol", lambda ref: "JP225")
    st, body = ctl.handle_tf_period_profile(
        "jp225_tick", "1D", _MON_START, _MON_START + 86400, now=1e12)
    assert st == 200
    assert len(body["columns"]) == 1
    col = body["columns"][0]
    assert col["time"] == 1783900800  # 2026-07-13 00:00 UTC（ラベル深夜＝1D バー時刻）。
    assert col["tpo_units"] == 3  # 日曜夜 2 + 月曜昼 1 の全ティックが単一セッション列に入る。


def test_live_ticks_augment_incomplete_day(monkeypatch):
    """ISSUE-083 追補: 当日（未完了セッション）は live_ticks（served の in-memory buffer 末尾）を
    parquet 優先 dedup＋中央値±30% 外れ値除去で合成し、最新ティックが列へ即時反映される。"""
    ctl._reset_tf_period_cache()
    monkeypatch.setattr(ctl, "_TFP_CACHE_ROOT", False)
    monkeypatch.setattr(ctl._mpd, "_load_window_ticks", _fake_ticks)
    monkeypatch.setattr(ctl._mpd, "resolve_symbol", lambda ref: "JP225")
    live = [
        (70_000, 11.0),    # parquet 末尾（70s）と同秒 → dedup（追加しない・parquet 優先）
        (80_000, 12.0),    # 末尾より後 → 追加
        (95_000, 12.0),    # 追加
        (96_000, 1000.0),  # 合成中央値±30% の外れ値 → 除外（_load_window_ticks と同規約）
    ]
    st, body = ctl.handle_tf_period_profile(
        "jp225_tick", "1m", 0, 120, now=100.0, live_ticks=live)
    assert st == 200 and body["ok"] is True
    by_time = {c["time"]: c for c in body["columns"]}
    # period0 は不変（parquet のみ）。
    assert by_time[0]["levels"] == [[9.996, 2], [10.9905, 1]]
    # period60 に live 末尾 12.0×2 が加算される（12→round(/0.0255)=471→12.0105）。
    # 同秒 dedup（11.0@70s）と外れ値（1000.0）は入らない。
    assert by_time[60]["levels"] == [[12.0105, 2], [19.992, 1], [21.012, 1]]


def test_live_ticks_ignored_for_completed_day(monkeypatch):
    """ISSUE-083 追補: 完了日は live_ticks を無視する（不変列のキャッシュを汚さない）。"""
    ctl._reset_tf_period_cache()
    monkeypatch.setattr(ctl, "_TFP_CACHE_ROOT", False)
    monkeypatch.setattr(ctl._mpd, "_load_window_ticks", _fake_ticks)
    monkeypatch.setattr(ctl._mpd, "resolve_symbol", lambda ref: "JP225")
    st, body = ctl.handle_tf_period_profile(
        "jp225_tick", "1m", 0, 120, now=1e12, live_ticks=[(80_000, 12.0)])
    assert st == 200
    by_time = {c["time"]: c for c in body["columns"]}
    assert by_time[60]["levels"] == [[19.992, 1], [21.012, 1]], "完了日は合成しない（byte 不変）"


def test_week_bucket_merges_session_days(monkeypatch):
    """ISSUE-086: tf=1W はセッション日次（1D 列＝既存キャッシュ経路）を W-FRI バケットへ畳み込み、
    1 バケット=1 列（time=週ラベル金曜の UTC 深夜）で返す。levels はセル価格ごとの加算。"""
    import numpy as np
    from marketdata.session_day import session_label_to_start

    ctl._reset_tf_period_cache()
    monkeypatch.setattr(ctl, "_TFP_CACHE_ROOT", False)
    monkeypatch.setattr(ctl._mpd, "resolve_symbol", lambda ref: "JP225")
    d_mon = session_label_to_start("2026-07-13")  # 月曜セッション
    d_tue = session_label_to_start("2026-07-14")  # 火曜セッション

    def fake_ticks(_symbol, start, end):
        # 月曜: 100 を 2 tick / 火曜: 100 を 1 tick + 110 を 1 tick（GRID_W=10 格子で 100/110 セル）。
        secs = np.array([d_mon + 3600, d_mon + 3700, d_tue + 3600, d_tue + 3800])
        mids = np.array([100.0, 100.0, 100.0, 110.0])
        m = (secs >= start) & (secs < end)
        return secs[m], mids[m]

    monkeypatch.setattr(ctl._mpd, "_load_window_ticks", fake_ticks)
    now = session_label_to_start("2026-07-15") + 3600  # 週の途中（未完了バケット）。
    label_mid = 1784246400  # 2026-07-17 00:00 UTC（週ラベル金曜）。
    st, body = ctl.handle_tf_period_profile(
        "jp225_tick", "1W", label_mid - 86400, label_mid + 1, now=now)
    assert st == 200 and body["ok"] is True
    assert len(body["columns"]) == 1, "1 バケット = 1 列"
    col = body["columns"][0]
    assert col["time"] == label_mid, "列 time は週ラベル（W-FRI）の UTC 深夜"
    assert col["levels"] == [[100.0, 3], [110.0, 1]], "セッション日次の加算"
    assert col["tpo_units"] == 4
    assert col["poc"] == 100.0
    assert col["va_low"] <= 100.0 <= col["va_high"]


def test_month_bucket_and_validation(monkeypatch):
    """ISSUE-086: tf=1M も受理され（旧: 400）、月バケット（ME ラベル）で列を返す。"""
    import numpy as np
    from marketdata.session_day import session_label_to_start

    ctl._reset_tf_period_cache()
    monkeypatch.setattr(ctl, "_TFP_CACHE_ROOT", False)
    monkeypatch.setattr(ctl._mpd, "resolve_symbol", lambda ref: "JP225")
    d1 = session_label_to_start("2026-07-13")

    def fake_ticks(_symbol, start, end):
        secs = np.array([d1 + 3600, d1 + 3700])
        mids = np.array([100.0, 100.0])
        m = (secs >= start) & (secs < end)
        return secs[m], mids[m]

    monkeypatch.setattr(ctl._mpd, "_load_window_ticks", fake_ticks)
    now = session_label_to_start("2026-07-15") + 3600
    label_mid = 1785456000  # 2026-07-31 00:00 UTC（ME ラベル）。
    st, body = ctl.handle_tf_period_profile(
        "jp225_tick", "1M", label_mid - 86400 * 30, label_mid + 1, now=now)
    assert st == 200 and body["ok"] is True
    assert [c["time"] for c in body["columns"]] == [label_mid]
    assert body["columns"][0]["levels"] == [[100.0, 2]]


def test_bucket_completed_boundary():
    """ISSUE-088 🔵-5: バケット完了判定＝次バケット先頭セッションの始端が now 以前。"""
    from marketdata.session_day import session_label_to_start

    # 週 2026-07-17（金）: 次バケット先頭は土曜ラベル 2026-07-18 のセッション始端。
    nxt_first = session_label_to_start("2026-07-18")
    assert ctl._bucket_completed("1W", "2026-07-17", nxt_first) is True
    assert ctl._bucket_completed("1W", "2026-07-17", nxt_first - 1) is False
    # 月 2026-02-28: 次バケット先頭は 2026-03-01 のセッション始端。
    nxt_m = session_label_to_start("2026-03-01")
    assert ctl._bucket_completed("1M", "2026-02-28", nxt_m) is True
    assert ctl._bucket_completed("1M", "2026-02-28", nxt_m - 1) is False
