"""acquire_marketdata パイプラインのユニットテスト（自前ロジック中心・実取得は monkeypatch）。

検証対象（指示書「テスト」節）:
  1. 増分ティック開始日の算出（y/m/d tree 最新日→翌日・空 tree→エラー・--today 注入）。
  2. ingest の未処理日抽出と state による冪等性（既 ingest 日はスキップ）。
  3. 段階選択（--skip / --only）の分岐。
  4. 各ツール呼び出しは monkeypatch で差し替え（実ネットワーク/実取得を走らせない）。

実 Dukascopy・実データ依存は持ち込まない（すべて tmp_path 上の合成 tree / monkeypatch）。
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pytest

from tools import acquire_marketdata as am


# --------------------------------------------------------------------------- #
# 1. 増分ティック開始日の算出
# --------------------------------------------------------------------------- #
def _touch(root: Path, y: int, m: int, d: int, *, empty: bool = False) -> None:
    day_dir = root / f"{y:04d}" / f"{m:02d}" / f"{d:02d}"
    day_dir.mkdir(parents=True, exist_ok=True)
    name = "JP225_ticks.empty" if empty else "JP225_ticks.parquet"
    (day_dir / name).write_text("")


class TestNextTickStartDay:
    def test_returns_day_after_latest_parquet(self, tmp_path):
        # Arrange: 2025/01/03 が parquet を持つ最新日
        root = tmp_path / "ticks"
        _touch(root, 2025, 1, 1)
        _touch(root, 2025, 1, 3)
        # Act
        start = am.next_tick_start_day(root)
        # Assert: 翌日 2025-01-04（UTC・00:00）
        assert start == dt.datetime(2025, 1, 4, tzinfo=dt.timezone.utc)

    def test_empty_marker_counts_as_acquired(self, tmp_path):
        # Arrange: 最新日が .empty マーカーのみでも取得済みとして翌日を返す
        root = tmp_path / "ticks"
        _touch(root, 2025, 2, 10, empty=True)
        # Act
        start = am.next_tick_start_day(root)
        # Assert
        assert start == dt.datetime(2025, 2, 11, tzinfo=dt.timezone.utc)

    def test_empty_tree_raises(self, tmp_path):
        # Arrange: tree が存在するが日が 1 つもない
        root = tmp_path / "ticks"
        root.mkdir(parents=True)
        # Act / Assert
        with pytest.raises(am.PipelineError):
            am.next_tick_start_day(root)

    def test_missing_root_raises(self, tmp_path):
        # Arrange: root 自体が無い
        root = tmp_path / "nope"
        # Act / Assert
        with pytest.raises(am.PipelineError):
            am.next_tick_start_day(root)


# --------------------------------------------------------------------------- #
# 2. ingest 未処理日抽出 + state 冪等性
# --------------------------------------------------------------------------- #
class TestPendingIngestDays:
    def test_extracts_parquet_days_not_in_state(self, tmp_path):
        # Arrange: 3 日分 parquet、うち 1 日は state 済み
        root = tmp_path / "ticks"
        _touch(root, 2025, 1, 1)
        _touch(root, 2025, 1, 2)
        _touch(root, 2025, 1, 3)
        _touch(root, 2025, 1, 4, empty=True)  # .empty は ingest 対象外
        state = {"2025-01-02"}
        # Act
        pending = am.pending_ingest_days(root, state)
        # Assert: parquet 日のうち state 未登録の 01/01・01/03（.empty 01/04 は除外）
        got = {(p.day_str) for p in pending}
        assert got == {"2025-01-01", "2025-01-03"}

    def test_empty_state_yields_all_parquet_days(self, tmp_path):
        root = tmp_path / "ticks"
        _touch(root, 2025, 3, 5)
        pending = am.pending_ingest_days(root, set())
        assert {p.day_str for p in pending} == {"2025-03-05"}

    def test_all_ingested_yields_empty(self, tmp_path):
        root = tmp_path / "ticks"
        _touch(root, 2025, 3, 5)
        pending = am.pending_ingest_days(root, {"2025-03-05"})
        assert pending == []

    def test_missing_root_yields_empty(self, tmp_path):
        pending = am.pending_ingest_days(tmp_path / "nope", set())
        assert pending == []


class TestIngestState:
    def test_load_missing_returns_empty_set(self, tmp_path):
        assert am.load_ingest_state(tmp_path / "tickstore") == set()

    def test_save_then_load_roundtrip(self, tmp_path):
        root = tmp_path / "tickstore"
        am.save_ingest_state(root, {"2025-01-01", "2025-01-02"})
        assert am.load_ingest_state(root) == {"2025-01-01", "2025-01-02"}

    def test_save_writes_sorted_json_array(self, tmp_path):
        root = tmp_path / "tickstore"
        am.save_ingest_state(root, {"2025-01-03", "2025-01-01"})
        data = json.loads((root / "ingest_state.json").read_text())
        assert data == ["2025-01-01", "2025-01-03"]

    def test_load_corrupt_json_returns_empty_set(self, tmp_path):
        # 回帰: 破損 JSON で ingest 全停止せず空集合で続行（再 ingest は overwrite 冪等）。
        root = tmp_path / "tickstore"
        root.mkdir(parents=True)
        (root / "ingest_state.json").write_text('{"2025-01-01": tru')  # 途中切断
        assert am.load_ingest_state(root) == set()

    def test_load_non_array_json_returns_empty_set(self, tmp_path):
        # 回帰: 非配列（オブジェクト）を黙って誤解釈せず空集合に倒す。
        root = tmp_path / "tickstore"
        root.mkdir(parents=True)
        (root / "ingest_state.json").write_text('{"2025-01-01": true}')
        assert am.load_ingest_state(root) == set()


# --------------------------------------------------------------------------- #
# 3. 段階選択（--skip / --only）
# --------------------------------------------------------------------------- #
class TestStageSelection:
    def test_default_runs_all_four(self):
        sel = am.select_stages(skip=[], only=None)
        assert sel == ["bars", "daily", "ticks", "ingest"]

    def test_only_runs_single(self):
        assert am.select_stages(skip=[], only="ticks") == ["ticks"]

    def test_skip_removes_named(self):
        assert am.select_stages(skip=["daily", "ingest"], only=None) == ["bars", "ticks"]

    def test_only_overrides_skip(self):
        # only 指定時は skip を無視して単一段階のみ
        assert am.select_stages(skip=["bars"], only="bars") == ["bars"]


# --------------------------------------------------------------------------- #
# 4. ステージ実行のオーケストレーション（ツールは monkeypatch で差し替え）
# --------------------------------------------------------------------------- #
class TestRunBarsStage:
    def test_invokes_m1_main_with_empty_argv_in_incremental(self, monkeypatch):
        called = {}

        def fake_main(argv):
            called["argv"] = argv
            return 0

        monkeypatch.setattr(am, "_export_jp225_m1_main", fake_main)
        ctx = am.PipelineContext(full=False)
        rc = am.stage_bars(ctx)
        assert rc == 0
        assert called["argv"] == []

    def test_full_requires_start_and_end(self, monkeypatch):
        # 回帰: --full かつ start/end 欠落で bars が黙って増分実行してはならない
        # （ticks と契約一致＝PipelineError）。
        monkeypatch.setattr(am, "_export_jp225_m1_main", lambda argv: 0)
        with pytest.raises(am.PipelineError):
            am.stage_bars(am.PipelineContext(full=True))


class TestRunTicksStage:
    def test_incremental_computes_start_from_tree(self, tmp_path, monkeypatch):
        root = tmp_path / "ticks"
        _touch(root, 2025, 1, 3)
        captured = {}

        def fake_run(start, end, run_root):
            captured["start"] = start
            captured["end"] = end
            captured["root"] = run_root
            return 42

        monkeypatch.setattr(am, "_fetch_ticks_run", fake_run)
        ctx = am.PipelineContext(
            full=False,
            today=dt.date(2025, 1, 10),
            ticks_root=root,
        )
        rc = am.stage_ticks(ctx)
        assert rc == 0
        # 最新 01/03 → 翌日 01/04 start、end=today+1=01/11（[start,end)）
        assert captured["start"] == dt.datetime(2025, 1, 4, tzinfo=dt.timezone.utc)
        assert captured["end"] == dt.datetime(2025, 1, 11, tzinfo=dt.timezone.utc)
        assert captured["root"] == root

    def test_full_requires_start(self, tmp_path, monkeypatch):
        root = tmp_path / "ticks"
        monkeypatch.setattr(am, "_fetch_ticks_run", lambda *a, **k: 0)
        ctx = am.PipelineContext(full=True, today=dt.date(2025, 1, 10), ticks_root=root)
        with pytest.raises(am.PipelineError):
            am.stage_ticks(ctx)

    def test_empty_tree_without_start_raises(self, tmp_path, monkeypatch):
        root = tmp_path / "ticks"
        root.mkdir(parents=True)
        monkeypatch.setattr(am, "_fetch_ticks_run", lambda *a, **k: 0)
        ctx = am.PipelineContext(full=False, today=dt.date(2025, 1, 10), ticks_root=root)
        with pytest.raises(am.PipelineError):
            am.stage_ticks(ctx)

    def test_explicit_start_used(self, tmp_path, monkeypatch):
        root = tmp_path / "ticks"
        captured = {}
        monkeypatch.setattr(
            am, "_fetch_ticks_run",
            lambda start, end, run_root: captured.update(start=start, end=end) or 7,
        )
        ctx = am.PipelineContext(
            full=False,
            start=dt.date(2025, 1, 1),
            today=dt.date(2025, 1, 5),
            ticks_root=root,
        )
        rc = am.stage_ticks(ctx)
        assert rc == 0
        assert captured["start"] == dt.datetime(2025, 1, 1, tzinfo=dt.timezone.utc)
        assert captured["end"] == dt.datetime(2025, 1, 6, tzinfo=dt.timezone.utc)

    def test_explicit_end_is_respected_not_today(self, tmp_path, monkeypatch):
        # 回帰: --end 指定時は today+1 でなく end+1 を終端にする（過取得防止・bars と一致）。
        root = tmp_path / "ticks"
        captured = {}
        monkeypatch.setattr(
            am, "_fetch_ticks_run",
            lambda start, end, run_root: captured.update(start=start, end=end) or 3,
        )
        ctx = am.PipelineContext(
            full=False,
            start=dt.date(2025, 1, 1),
            end=dt.date(2025, 1, 2),
            today=dt.date(2025, 1, 31),  # today は無視され end が効く
            ticks_root=root,
        )
        assert am.stage_ticks(ctx) == 0
        assert captured["end"] == dt.datetime(2025, 1, 3, tzinfo=dt.timezone.utc)


class TestRunDailyStage:
    def test_incremental_passes_output_without_range(self, tmp_path, monkeypatch):
        captured = {}
        monkeypatch.setattr(am, "_export_jp225_csv_main",
                            lambda argv: captured.update(argv=argv) or 0)
        ctx = am.PipelineContext(full=False, daily_output=tmp_path / "d.csv")
        assert am.stage_daily(ctx) == 0
        assert "--start" not in captured["argv"]  # 増分=範囲なしで最新まで

    def test_full_requires_start_and_end(self, monkeypatch):
        # 回帰: --full かつ範囲欠落で daily が黙って全期間上書きせず PipelineError
        # （bars/ticks と契約一致・コードレビュー🔴-A）。
        monkeypatch.setattr(am, "_export_jp225_csv_main", lambda argv: 0)
        with pytest.raises(am.PipelineError):
            am.stage_daily(am.PipelineContext(full=True))


class TestRunIngestStage:
    def test_ingests_only_pending_and_updates_state(self, tmp_path, monkeypatch):
        ticks_root = tmp_path / "ticks"
        store_root = tmp_path / "tickstore"
        _touch(ticks_root, 2025, 1, 1)
        _touch(ticks_root, 2025, 1, 2)
        am.save_ingest_state(store_root, {"2025-01-01"})  # 01/01 は済み

        ingested = []

        def fake_ingest(raw_path, store, symbol, **kw):
            ingested.append((Path(raw_path), symbol))
            return object()

        monkeypatch.setattr(am, "_ingest_raw_parquet", fake_ingest)
        ctx = am.PipelineContext(ticks_root=ticks_root, tickstore_root=store_root)
        rc = am.stage_ingest(ctx)
        assert rc == 0
        # 01/02 のみ ingest される
        assert len(ingested) == 1
        assert ingested[0][1] == "JP225"
        assert "2025/01/02" in str(ingested[0][0]).replace("\\", "/")
        # state が更新され冪等（再実行で 0 件）
        assert am.load_ingest_state(store_root) == {"2025-01-01", "2025-01-02"}
        ingested.clear()
        rc2 = am.stage_ingest(ctx)
        assert rc2 == 0
        assert ingested == []


# --------------------------------------------------------------------------- #
# 5. パイプライン全体の段階選択分岐（main 経由・全ツール差し替え）
# --------------------------------------------------------------------------- #
class TestMainOrchestration:
    def test_only_bars_runs_just_bars(self, monkeypatch):
        order = []
        monkeypatch.setattr(am, "stage_bars", lambda ctx: order.append("bars") or 0)
        monkeypatch.setattr(am, "stage_daily", lambda ctx: order.append("daily") or 0)
        monkeypatch.setattr(am, "stage_ticks", lambda ctx: order.append("ticks") or 0)
        monkeypatch.setattr(am, "stage_ingest", lambda ctx: order.append("ingest") or 0)
        rc = am.main(["--only", "bars", "--today", "2025-01-10"])
        assert rc == 0
        assert order == ["bars"]

    def test_skip_excludes_stage(self, monkeypatch):
        order = []
        monkeypatch.setattr(am, "stage_bars", lambda ctx: order.append("bars") or 0)
        monkeypatch.setattr(am, "stage_daily", lambda ctx: order.append("daily") or 0)
        monkeypatch.setattr(am, "stage_ticks", lambda ctx: order.append("ticks") or 0)
        monkeypatch.setattr(am, "stage_ingest", lambda ctx: order.append("ingest") or 0)
        rc = am.main(["--skip", "daily", "--skip", "ticks", "--today", "2025-01-10"])
        assert rc == 0
        assert order == ["bars", "ingest"]

    def test_failure_aborts_subsequent_by_default(self, monkeypatch):
        order = []
        monkeypatch.setattr(am, "stage_bars", lambda ctx: order.append("bars") or 0)
        monkeypatch.setattr(am, "stage_daily", lambda ctx: order.append("daily") or 3)  # NG
        monkeypatch.setattr(am, "stage_ticks", lambda ctx: order.append("ticks") or 0)
        monkeypatch.setattr(am, "stage_ingest", lambda ctx: order.append("ingest") or 0)
        rc = am.main(["--today", "2025-01-10"])
        assert rc != 0
        # daily 失敗で ticks/ingest に進まない
        assert order == ["bars", "daily"]

    def test_continue_on_error_runs_all(self, monkeypatch):
        order = []
        monkeypatch.setattr(am, "stage_bars", lambda ctx: order.append("bars") or 0)
        monkeypatch.setattr(am, "stage_daily", lambda ctx: order.append("daily") or 3)
        monkeypatch.setattr(am, "stage_ticks", lambda ctx: order.append("ticks") or 0)
        monkeypatch.setattr(am, "stage_ingest", lambda ctx: order.append("ingest") or 0)
        rc = am.main(["--continue-on-error", "--today", "2025-01-10"])
        assert rc != 0  # 1 段でも NG なら非ゼロ
        assert order == ["bars", "daily", "ticks", "ingest"]

    def test_stage_exception_is_caught_as_failure(self, monkeypatch):
        def boom(ctx):
            raise am.PipelineError("boom")

        monkeypatch.setattr(am, "stage_bars", boom)
        rc = am.main(["--only", "bars", "--today", "2025-01-10"])
        assert rc != 0
