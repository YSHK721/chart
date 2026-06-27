"""tools.build_tick_rollup のテスト — 取得範囲算出・段階合成・M1→rollup の実配線。

外部/重い呼び出し（Dukascopy 取得）はモジュール関数（_fetch_ticks_run / _next_tick_start_day）を
monkeypatch して遮断する。M1→rollup 段のみ実 marketdata.tick_m1 + rollup を小さな合成 parquet で
end-to-end に通し、tick 由来 ref（jp225_tick）の派生物が生成され既存データに触れないことを検証する。
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pandas as pd
import pytest

from tools import build_tick_rollup as btr


# --------------------------------------------------------------------------- #
# 取得範囲算出（純粋ロジック）
# --------------------------------------------------------------------------- #
def test_acquire_range_empty_tree_uses_full_start(monkeypatch) -> None:
    # 空 tree（_next_tick_start_day が PipelineError）→ full_start から全期間取得。
    def _raise(_root):
        raise btr.PipelineError("空 tree")

    monkeypatch.setattr(btr, "_next_tick_start_day", _raise)
    rng = btr.compute_acquire_range(
        Path("/x"), dt.date(2026, 6, 27), full_start=dt.date(2012, 6, 14)
    )
    assert rng == (
        dt.datetime(2012, 6, 14, tzinfo=dt.timezone.utc),
        dt.datetime(2026, 6, 28, tzinfo=dt.timezone.utc),  # today+1（半開）
    )


def test_acquire_range_existing_tree_is_incremental(monkeypatch) -> None:
    # 既存 tree → 最新取得日の翌日から（増分追記・既存日は委譲先 fetch が skip）。
    monkeypatch.setattr(
        btr, "_next_tick_start_day",
        lambda _root: dt.datetime(2026, 6, 26, tzinfo=dt.timezone.utc),
    )
    rng = btr.compute_acquire_range(
        Path("/x"), dt.date(2026, 6, 27), full_start=dt.date(2012, 6, 14)
    )
    assert rng[0] == dt.datetime(2026, 6, 26, tzinfo=dt.timezone.utc)
    assert rng[1] == dt.datetime(2026, 6, 28, tzinfo=dt.timezone.utc)


def test_acquire_range_explicit_start_overrides(monkeypatch) -> None:
    # --start 明示時は tree を見ずその日から。
    monkeypatch.setattr(btr, "_next_tick_start_day", lambda _root: pytest.fail("呼ばれてはいけない"))
    rng = btr.compute_acquire_range(
        Path("/x"), dt.date(2026, 6, 27), full_start=dt.date(2012, 6, 14),
        start=dt.date(2025, 1, 1), end=dt.date(2025, 1, 31),
    )
    assert rng == (
        dt.datetime(2025, 1, 1, tzinfo=dt.timezone.utc),
        dt.datetime(2025, 2, 1, tzinfo=dt.timezone.utc),
    )


def test_acquire_range_none_when_up_to_date(monkeypatch) -> None:
    # 最新まで取得済み（翌日が today+1 以降）→ 取得対象なし（None）。
    monkeypatch.setattr(
        btr, "_next_tick_start_day",
        lambda _root: dt.datetime(2026, 6, 28, tzinfo=dt.timezone.utc),
    )
    assert btr.compute_acquire_range(
        Path("/x"), dt.date(2026, 6, 27), full_start=dt.date(2012, 6, 14)
    ) is None


# --------------------------------------------------------------------------- #
# 段階選択・合成
# --------------------------------------------------------------------------- #
def test_select_stages_default_and_only_and_skip() -> None:
    assert btr.select_stages([], None) == ["acquire", "m1", "rollup"]
    assert btr.select_stages(["acquire"], None) == ["m1", "rollup"]
    assert btr.select_stages(["acquire"], "rollup") == ["rollup"]  # only は skip を無視。


def test_run_pipeline_runs_stages_in_order(monkeypatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(btr, "stage_acquire", lambda ctx: calls.append("acquire") or 0)
    monkeypatch.setattr(btr, "stage_m1", lambda ctx: calls.append("m1") or 0)
    monkeypatch.setattr(btr, "stage_rollup", lambda ctx: calls.append("rollup") or 0)
    rc = btr.run_pipeline(btr.PipelineContext(today=dt.date(2026, 6, 27)), ["acquire", "m1", "rollup"])
    assert rc == 0
    assert calls == ["acquire", "m1", "rollup"]


def test_run_pipeline_stops_on_failure(monkeypatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(btr, "stage_acquire", lambda ctx: calls.append("acquire") or 0)
    monkeypatch.setattr(btr, "stage_m1", lambda ctx: (_ for _ in ()).throw(btr.PipelineError("boom")))
    monkeypatch.setattr(btr, "stage_rollup", lambda ctx: calls.append("rollup") or 0)
    rc = btr.run_pipeline(btr.PipelineContext(today=dt.date(2026, 6, 27)), ["acquire", "m1", "rollup"])
    assert rc == 1
    assert calls == ["acquire"]  # m1 失敗で rollup は実行されない。


def test_stage_acquire_delegates_with_computed_range(monkeypatch, tmp_path) -> None:
    recorded: dict = {}
    monkeypatch.setattr(btr, "_next_tick_start_day", lambda _r: (_ for _ in ()).throw(btr.PipelineError("空")))

    def _fake_fetch(start, end, root):
        recorded.update(start=start, end=end, root=root)
        return 123

    monkeypatch.setattr(btr, "_fetch_ticks_run", _fake_fetch)
    ctx = btr.PipelineContext(today=dt.date(2026, 6, 27), full_start=dt.date(2012, 6, 14), data_dir=tmp_path)
    assert btr.stage_acquire(ctx) == 0
    assert recorded["start"] == dt.datetime(2012, 6, 14, tzinfo=dt.timezone.utc)
    assert recorded["end"] == dt.datetime(2026, 6, 28, tzinfo=dt.timezone.utc)
    assert recorded["root"] == tmp_path / "ticks"  # data_dir 由来の単一真実源。


# --------------------------------------------------------------------------- #
# M1 → rollup の実配線（acquire はスキップ・小合成 parquet で end-to-end）
# --------------------------------------------------------------------------- #
def _write_tick_parquet(root: Path, ymd: tuple[int, int, int], rows: list[tuple[str, float, float]]) -> None:
    d = root / "ticks" / f"{ymd[0]:04d}" / f"{ymd[1]:02d}" / f"{ymd[2]:02d}"
    d.mkdir(parents=True)
    ts = pd.to_datetime([r[0] for r in rows]).tz_localize("UTC")
    pd.DataFrame(
        {"timestamp": ts, "bidPrice": [r[1] for r in rows], "askPrice": [r[2] for r in rows]}
    ).to_parquet(d / "JP225_ticks.parquet")


def test_m1_and_rollup_stages_generate_tick_ref_outputs(tmp_path) -> None:
    # 2 日分の合成ティックを置き、m1→rollup を実 marketdata で通す。
    _write_tick_parquet(tmp_path, (2025, 1, 2), [
        ("2025-01-02 09:00:10", 100.0, 102.0),
        ("2025-01-02 09:00:50", 104.0, 106.0),
        ("2025-01-02 09:06:10", 108.0, 110.0),
    ])
    _write_tick_parquet(tmp_path, (2025, 1, 3), [("2025-01-03 09:00:10", 200.0, 200.0)])

    ctx = btr.PipelineContext(
        today=dt.date(2025, 1, 3),
        full_start=dt.date(2025, 1, 1),
        data_dir=tmp_path,
    )

    assert btr.stage_m1(ctx) == 0
    m1 = tmp_path / "jp225_tick_m1.csv"
    assert m1.is_file()
    assert m1.read_text(encoding="utf-8").splitlines()[0] == "date,open,high,low,close,volume"

    assert btr.stage_rollup(ctx) == 0
    rollup_5m = tmp_path / "rollups" / "jp225_tick" / "jp225_tick_5m.csv"  # ref 専用サブ dir。
    assert rollup_5m.is_file()
    up = pd.read_csv(rollup_5m, parse_dates=["date"]).set_index("date")
    # 09:00 台 5m バー: open=最初 tick mid(101)・volume=その窓のティック数(2)。
    bar0 = up.loc[pd.Timestamp("2025-01-02 09:00:00")]
    assert bar0["open"] == 101.0
    assert bar0["volume"] == 2.0


def test_stage_rollup_requires_m1_first(tmp_path) -> None:
    ctx = btr.PipelineContext(today=dt.date(2025, 1, 3), data_dir=tmp_path)
    with pytest.raises(btr.PipelineError, match="M1 が存在しません"):
        btr.stage_rollup(ctx)


def test_stage_rollup_does_not_touch_existing_jp225m1_rollups(tmp_path) -> None:
    # データ保全の回帰: 既存 jp225_m1 ロールアップ + 共有 rollup_state.json を直下に置いた状態で
    # tick rollup を実行しても、既存 state/CSV は不変（tick 由来は ref 専用サブ dir へ隔離）。
    shared = tmp_path / "rollups"
    shared.mkdir(parents=True)
    (shared / "rollup_state.json").write_text('{"owner":"jp225_m1"}', encoding="utf-8")
    (shared / "jp225_m1_5m.csv").write_text("date,open,high,low,close,volume\n", encoding="utf-8")
    state_before = (shared / "rollup_state.json").read_text(encoding="utf-8")
    csv_before = (shared / "jp225_m1_5m.csv").read_text(encoding="utf-8")

    _write_tick_parquet(tmp_path, (2025, 1, 2), [("2025-01-02 09:00:10", 100.0, 100.0)])
    ctx = btr.PipelineContext(today=dt.date(2025, 1, 2), full_start=dt.date(2025, 1, 1), data_dir=tmp_path)
    assert btr.stage_m1(ctx) == 0
    assert btr.stage_rollup(ctx) == 0

    # 既存共有 dir 直下は一切変化しない。
    assert (shared / "rollup_state.json").read_text(encoding="utf-8") == state_before
    assert (shared / "jp225_m1_5m.csv").read_text(encoding="utf-8") == csv_before
    # tick 由来の CSV/state は専用サブ dir 内に隔離される。
    assert (shared / "jp225_tick" / "jp225_tick_5m.csv").is_file()
    assert (shared / "jp225_tick" / "rollup_state.json").is_file()


def test_run_pipeline_continue_on_error_runs_rest_and_returns_1(monkeypatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(btr, "stage_acquire", lambda ctx: (_ for _ in ()).throw(btr.PipelineError("x")))
    monkeypatch.setattr(btr, "stage_m1", lambda ctx: calls.append("m1") or 0)
    monkeypatch.setattr(btr, "stage_rollup", lambda ctx: calls.append("rollup") or 0)
    ctx = btr.PipelineContext(today=dt.date(2026, 6, 27), continue_on_error=True)
    rc = btr.run_pipeline(ctx, ["acquire", "m1", "rollup"])
    assert rc == 1  # 失敗段ありで overall NG。
    assert calls == ["m1", "rollup"]  # だが後続は継続実行される。
