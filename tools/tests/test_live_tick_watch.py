"""tools.live_tick_watch のテスト — jp225_tick 系ライブ watch の純粋ロジックと段階合成。

外部/重い呼び出し（Dukascopy 取得）はモジュール関数（_fetch_day / _fetch_ticks_run /
_compute_acquire_range）または seam（_append_m1 / _rollup_update / refresh_day_parquet）を
monkeypatch して遮断する。ネットワークは一切叩かない（全フェイク）。update_once の end-to-end
のみ実 marketdata.tick_m1 + rollup を小合成ティックで通し、until による形成中バー除外と
tick 由来 ref（jp225_tick）派生物の生成を検証する。tools.tests.test_build_tick_rollup の様式を踏襲。
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pandas as pd
import pytest

from tools import live_tick_watch as ltw


def _tick_df(rows: list[tuple[str, float, float]]) -> pd.DataFrame:
    ts = pd.to_datetime([r[0] for r in rows]).tz_localize("UTC")
    return pd.DataFrame(
        {"timestamp": ts, "bidPrice": [r[1] for r in rows], "askPrice": [r[2] for r in rows]}
    )


# --------------------------------------------------------------------------- #
# refresh_days — 再取得対象日（純粋ロジック）
# --------------------------------------------------------------------------- #
def test_refresh_days_normal_returns_today_only() -> None:
    now = dt.datetime(2026, 7, 6, 12, 0, 0)
    assert ltw.refresh_days(now, 60) == [dt.date(2026, 7, 6)]


def test_refresh_days_after_midnight_includes_prev_day() -> None:
    # 00:01, interval=60 → now-120s = 2026-07-05 23:59 → 前日+当日（日末尾取りこぼし防止）。
    now = dt.datetime(2026, 7, 6, 0, 1, 0)
    assert ltw.refresh_days(now, 60) == [dt.date(2026, 7, 5), dt.date(2026, 7, 6)]


# --------------------------------------------------------------------------- #
# refresh_day_parquet — 当日 tick 全量再取得の原子スワップ／空温存
# --------------------------------------------------------------------------- #
def test_refresh_day_parquet_overwrites_atomically(tmp_path: Path) -> None:
    day = dt.date(2026, 7, 6)
    d = tmp_path / "ticks" / "2026" / "07" / "06"
    d.mkdir(parents=True)
    _tick_df([("2026-07-06 00:00:00", 1.0, 1.0)]).to_parquet(d / "JP225_ticks.parquet", index=False)

    new = _tick_df([("2026-07-06 09:00:00", 100.0, 100.0), ("2026-07-06 09:01:00", 101.0, 101.0)])
    n = ltw.refresh_day_parquet(day, tmp_path, fetch_fn=lambda a, b: new)

    assert n == 2
    got = pd.read_parquet(d / "JP225_ticks.parquet")
    assert len(got) == 2  # 旧 1 行が新 2 行へ置換された（原子上書き）。


def test_refresh_day_parquet_empty_preserves_existing(tmp_path: Path) -> None:
    day = dt.date(2026, 7, 6)
    d = tmp_path / "ticks" / "2026" / "07" / "06"
    d.mkdir(parents=True)
    pq = d / "JP225_ticks.parquet"
    _tick_df([("2026-07-06 00:00:00", 1.0, 1.0)]).to_parquet(pq, index=False)
    before = pq.read_bytes()

    n = ltw.refresh_day_parquet(day, tmp_path, fetch_fn=lambda a, b: pd.DataFrame())

    assert n == 0  # 空取得（休場/一過性障害）。
    assert pq.read_bytes() == before  # 既存 parquet を温存（上書きしない）。


def test_refresh_day_parquet_removes_stale_empty_marker(tmp_path: Path) -> None:
    # 休場マーカー（fetch_ticks_ymd の .empty）が残る日に tick を書き込んだら、マーカーを除去して
    # parquet と .empty の同居（状態不整合）を残さない。
    day = dt.date(2026, 7, 6)
    d = tmp_path / "ticks" / "2026" / "07" / "06"
    d.mkdir(parents=True)
    marker = d / "JP225_ticks.empty"
    marker.write_text("")

    new = _tick_df([("2026-07-06 09:00:00", 100.0, 100.0)])
    n = ltw.refresh_day_parquet(day, tmp_path, fetch_fn=lambda a, b: new)

    assert n == 1
    assert (d / "JP225_ticks.parquet").is_file()
    assert not marker.exists()  # マーカー除去（parquet が単一の真実）。


# --------------------------------------------------------------------------- #
# update_once — fetch → m1 → rollup の配線（seam を monkeypatch）
# --------------------------------------------------------------------------- #
def test_update_once_wires_fetch_m1_rollup_with_until(monkeypatch, tmp_path) -> None:
    calls: list = []
    rec: dict = {}
    monkeypatch.setattr(ltw, "refresh_day_parquet", lambda day, root, **k: calls.append(("refresh", day)) or 1)

    def _append(start, end, until, *, data_dir):
        calls.append(("m1", start, end))
        rec.update(start=start, end=end, until=until, data_dir=data_dir)

    monkeypatch.setattr(ltw, "_append_m1", _append)
    monkeypatch.setattr(ltw, "_rollup_update", lambda data_dir: calls.append(("rollup", data_dir)))

    now = dt.datetime(2026, 7, 6, 12, 0, 30)
    ltw.update_once(now, tmp_path, interval=60)

    # 順序: fetch(refresh) → m1 → rollup。
    assert [c[0] for c in calls] == ["refresh", "m1", "rollup"]
    assert rec["until"] == pd.Timestamp("2026-07-06 12:00:00")  # floor(now, "min")。
    # start は full_start（追記窓は append_m1_from_ticks の resume 規則へ委譲）。当日を渡すと
    # 既存 M1 が数日前で停止している初回起動でその間の日が永久欠落する（回帰禁止）。
    assert rec["start"] == "2012-06-14"
    assert rec["end"] == "2026-07-07"  # today+1（半開の m1 集計終端）。


def test_update_once_end_to_end_excludes_forming_and_writes_tick_ref(monkeypatch, tmp_path) -> None:
    # 実 marketdata.tick_m1 + rollup を小合成ティックで通す。until=12:00 で 12:00 バーを除外し、
    # 派生物は ref_prefix=jp225_tick（専用サブ dir）に生成される。
    def _fake_fetch_day(day, nxt):
        if day != dt.date(2026, 7, 6):
            return pd.DataFrame()
        return _tick_df([
            ("2026-07-06 09:00:10", 100.0, 102.0),
            ("2026-07-06 09:00:50", 104.0, 106.0),
            ("2026-07-06 09:01:10", 108.0, 110.0),
            ("2026-07-06 12:00:05", 999.0, 999.0),  # 形成中分バー（>= until）→ 除外。
        ])

    monkeypatch.setattr(ltw, "_fetch_day", _fake_fetch_day)
    now = dt.datetime(2026, 7, 6, 12, 0, 30)
    ltw.update_once(now, tmp_path, interval=60)

    m1 = pd.read_csv(tmp_path / "jp225_tick_m1.csv")
    dates = set(m1["date"])
    assert "2026-07-06 09:00:00" in dates
    assert "2026-07-06 12:00:00" not in dates  # until=12:00 で形成中を除外。
    # rollup は ref_prefix=jp225_tick の専用サブ dir へ。
    assert (tmp_path / "rollups" / "jp225_tick" / "jp225_tick_5m.csv").is_file()


def test_update_once_backfills_gap_days_from_caught_up_parquet(monkeypatch, tmp_path) -> None:
    # 回帰禁止: 既存 M1 が数日前（7/2）で停止していても、catch_up 済みの丸日 parquet（7/3）から
    # 欠損日が M1 へ自己修復される（m1 start に当日を渡すと 7/3 が永久欠落する誤りの固定）。
    from marketdata.tick_m1 import build_m1_from_ticks

    root = tmp_path / "ticks"

    def _put_day(day: dt.date, rows) -> None:
        d = root / f"{day:%Y}" / f"{day:%m}" / f"{day:%d}"
        d.mkdir(parents=True)
        _tick_df(rows).to_parquet(d / "JP225_ticks.parquet", index=False)

    # 既存 M1 は 7/2 まで（数日前で停止＝ライブ供給が止まっていた状態を再現）。7/3 parquet は
    # M1 構築の**後**に置く（catch_up の追い付き取得を模擬）——build を両日で走らせると既存 M1 が
    # 7/3 を含んでしまい、バグ版（m1 start=当日）でもテストが通過する見せかけ緑になる（review🟡）。
    _put_day(dt.date(2026, 7, 2), [("2026-07-02 09:00:10", 100.0, 102.0)])
    build_m1_from_ticks("2026-07-02", "2026-07-02", ref="jp225_tick", data_dir=tmp_path)
    _put_day(dt.date(2026, 7, 3), [("2026-07-03 09:00:10", 200.0, 202.0)])
    assert "2026-07-03 09:00:00" not in set(
        pd.read_csv(tmp_path / "jp225_tick_m1.csv")["date"]
    )  # 前提の実証: この時点で 7/3 は M1 未収載（テスト自体の識別力を固定）。

    def _fake_fetch_day(day, nxt):
        if day != dt.date(2026, 7, 6):
            return pd.DataFrame()
        return _tick_df([("2026-07-06 09:00:10", 300.0, 302.0)])

    monkeypatch.setattr(ltw, "_fetch_day", _fake_fetch_day)
    ltw.update_once(dt.datetime(2026, 7, 6, 12, 0, 30), tmp_path, interval=60)

    dates = set(pd.read_csv(tmp_path / "jp225_tick_m1.csv")["date"])
    assert "2026-07-03 09:00:00" in dates  # 欠損日（7/3）が自己修復される。
    assert "2026-07-06 09:00:00" in dates  # 当日も追記される。


# --------------------------------------------------------------------------- #
# catch_up — 起動時 1 回の丸日追い付き（end=昨日・取得対象なしは no-op）
# --------------------------------------------------------------------------- #
def test_catch_up_noop_when_no_range(monkeypatch, tmp_path) -> None:
    rec: dict = {}

    def _range(ticks_root, today, *, full_start, end):
        rec.update(today=today, full_start=full_start, end=end)
        return None

    monkeypatch.setattr(ltw, "_compute_acquire_range", _range)
    monkeypatch.setattr(ltw, "_fetch_ticks_run", lambda *a: pytest.fail("取得対象なしで呼ばれてはいけない"))

    n = ltw.catch_up(tmp_path, dt.date(2026, 7, 6), full_start=dt.date(2012, 6, 14))

    assert n == 0
    assert rec["end"] == dt.date(2026, 7, 5)  # end=昨日（当日は毎分 full-refresh が担当）。


def test_catch_up_delegates_when_range(monkeypatch, tmp_path) -> None:
    rng = (
        dt.datetime(2026, 7, 1, tzinfo=dt.timezone.utc),
        dt.datetime(2026, 7, 6, tzinfo=dt.timezone.utc),
    )
    monkeypatch.setattr(ltw, "_compute_acquire_range", lambda *a, **k: rng)
    got: dict = {}
    monkeypatch.setattr(ltw, "_fetch_ticks_run", lambda s, e, r: got.update(s=s, e=e, r=r) or 42)

    n = ltw.catch_up(tmp_path, dt.date(2026, 7, 6), full_start=dt.date(2012, 6, 14))

    assert n == 42
    assert got["s"] == rng[0] and got["e"] == rng[1]
    assert got["r"] == tmp_path / "ticks"


def test_catch_up_refreshes_latest_partial_day(monkeypatch, tmp_path) -> None:
    # 既存最新日（過去日）は日中取得の部分日でありうる。fetch_ticks_ymd は既存日を skip するため、
    # catch_up がその日を上書き再取得して自己修復する（7/2 午後の欠落バグの回帰禁止）。
    refreshed: list = []
    monkeypatch.setattr(ltw, "_latest_tick_day", lambda root: dt.date(2026, 7, 2))
    monkeypatch.setattr(
        ltw, "refresh_day_parquet", lambda day, root, **k: refreshed.append(day) or 7
    )
    monkeypatch.setattr(ltw, "_compute_acquire_range", lambda *a, **k: None)

    ltw.catch_up(tmp_path, dt.date(2026, 7, 6), full_start=dt.date(2012, 6, 14))

    assert refreshed == [dt.date(2026, 7, 2)]


def test_catch_up_skips_latest_refresh_when_today_or_empty(monkeypatch, tmp_path) -> None:
    # 最新日＝当日（毎分 full-refresh が担当）と空 tree（None）は上書き再取得しない。
    monkeypatch.setattr(
        ltw, "refresh_day_parquet",
        lambda *a, **k: pytest.fail("当日/空 tree で refresh してはいけない"),
    )
    monkeypatch.setattr(ltw, "_compute_acquire_range", lambda *a, **k: None)

    monkeypatch.setattr(ltw, "_latest_tick_day", lambda root: dt.date(2026, 7, 6))
    ltw.catch_up(tmp_path, dt.date(2026, 7, 6), full_start=dt.date(2012, 6, 14))

    monkeypatch.setattr(ltw, "_latest_tick_day", lambda root: None)
    ltw.catch_up(tmp_path, dt.date(2026, 7, 6), full_start=dt.date(2012, 6, 14))


# --------------------------------------------------------------------------- #
# CLI 引数検証（--interval 下限）
# --------------------------------------------------------------------------- #
def test_cli_rejects_interval_below_floor() -> None:
    parser = ltw.build_arg_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["--interval", "10"])  # 30 未満は argparse エラー。


def test_cli_once_flag_and_defaults() -> None:
    args = ltw.build_arg_parser().parse_args(["--once"])
    assert args.once is True
    assert args.interval == 60
    assert args.full_start == dt.date(2012, 6, 14)


# --------------------------------------------------------------------------- #
# ISSUE-161 根治: ストリーミング（増分カーソル）の純関数
# --------------------------------------------------------------------------- #
import datetime as _dt

import pandas as _pd

from tools.live_tick_watch import _TICK_COLUMNS, _cursor_ms_of, _rows_to_frame


def test_rows_to_frame_matches_day_parquet_schema():
    rows = [(1784807642333, 66132.999, 66137.532, 12000.0, 12000.0)]
    df = _rows_to_frame(rows)
    assert list(df.columns) == _TICK_COLUMNS
    assert str(df["timestamp"].dtype) == "datetime64[ms, UTC]"
    assert all(str(df[c].dtype) == "float64" for c in _TICK_COLUMNS[1:])


def test_rows_to_frame_normalizes_volume_units():
    # 増分 API の出来高は日次取得の 1e6 倍（実測）→ 日別 parquet の単位へ正規化する。
    df = _rows_to_frame([(1784807642333, 1.0, 2.0, 12000.0, 34000.0)])
    assert df["bidVolume"].iloc[0] == 0.012
    assert df["askVolume"].iloc[0] == 0.034


def test_cursor_ms_uses_last_tick_or_30min_window():
    df = _rows_to_frame([(1784807642333, 1.0, 2.0, 0.0, 0.0), (1784807650000, 1.0, 2.0, 0.0, 0.0)])
    now = _dt.datetime(2026, 7, 23, 12, 0)
    assert _cursor_ms_of(df, now) == 1784807650000
    # 空バッファ: now-30 分（参照実装 prototype_260707-01 と同じ catch-up 窓）
    expected = int((_pd.Timestamp(now, tz="UTC").timestamp() - 1800) * 1000)
    assert _cursor_ms_of(None, now) == expected
    assert _cursor_ms_of(df.iloc[0:0], now) == expected
