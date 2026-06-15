"""export_jp225_m1 の自動増分更新＋継続ポーリング機能の検証（TDD: Red→Green）。

検証対象（1 分足原子 CSV ``jp225_m1.csv`` の自動更新のみ。上位足 resample は対象外）:
  - read_last_timestamp: 既存 CSV 末尾 ``date`` の UTC ナイーブ datetime / 不在・空・ヘッダーのみ → None。
  - compute_fetch_window: now 注入の純粋関数。lag による未確定足除外境界・start>=end→None。
  - append_incremental: 追記モード "a"・ヘッダー二重書き禁止・last_ts 以前の重複除去。
  - run_watch: sleep_fn 注入＋stop_after で有限終了 / KeyboardInterrupt 正常停止。
  - main: --interval < 60 の argparse 拒否（下限フロア）。

決定論性（F.I.R.S.T）: Dukascopy への実ネットワークアクセスは行わない。fetch 部はモックで差し替える。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import pytest

import export_jp225_m1 as mod


# --------------------------------------------------------------------------- #
# compute_fetch_window（純粋・now 注入）
# --------------------------------------------------------------------------- #
def test_compute_fetch_window_uses_default_start_when_last_ts_is_none():
    # Arrange
    now = datetime(2026, 6, 15, 12, 0, 0)
    default_start = datetime(2011, 6, 1, 0, 0, 0)
    # Act
    window = mod.compute_fetch_window(
        None, now, lag_minutes=3, default_start=default_start
    )
    # Assert: last_ts=None なら default_start から、end は now-lag。
    assert window == (default_start, datetime(2026, 6, 15, 11, 57, 0))


def test_compute_fetch_window_starts_from_last_ts_when_present():
    # Arrange
    now = datetime(2026, 6, 15, 12, 0, 0)
    last_ts = datetime(2026, 6, 15, 9, 30, 0)
    # Act
    window = mod.compute_fetch_window(
        last_ts, now, lag_minutes=3, default_start=datetime(2011, 6, 1)
    )
    # Assert: last_ts ありはその後から、end=now-lag。
    assert window == (last_ts, datetime(2026, 6, 15, 11, 57, 0))


def test_compute_fetch_window_end_excludes_lag_minutes():
    # Arrange: 「数分前まで」境界＝未確定足を除外（end = now - lag）。
    now = datetime(2026, 6, 15, 12, 0, 0)
    # Act
    window = mod.compute_fetch_window(
        datetime(2026, 6, 1), now, lag_minutes=5, default_start=datetime(2011, 6, 1)
    )
    # Assert
    assert window[1] == datetime(2026, 6, 15, 11, 55, 0)


def test_compute_fetch_window_returns_none_when_start_ge_end():
    # Arrange: now-lag <= last_ts のとき、取りに行くべき新規が無く未確定足を取らない。
    now = datetime(2026, 6, 15, 12, 0, 0)
    last_ts = datetime(2026, 6, 15, 11, 58, 0)  # now-3min = 11:57 より後
    # Act
    window = mod.compute_fetch_window(
        last_ts, now, lag_minutes=3, default_start=datetime(2011, 6, 1)
    )
    # Assert: start >= end なので None。
    assert window is None


def test_compute_fetch_window_returns_none_when_start_equals_end():
    # Arrange: 境界値（start == end）も「新規なし」として None。
    now = datetime(2026, 6, 15, 12, 0, 0)
    last_ts = datetime(2026, 6, 15, 11, 57, 0)  # ちょうど now-3min
    # Act
    window = mod.compute_fetch_window(
        last_ts, now, lag_minutes=3, default_start=datetime(2011, 6, 1)
    )
    # Assert
    assert window is None


# --------------------------------------------------------------------------- #
# read_last_timestamp（I/O 小）
# --------------------------------------------------------------------------- #
def test_read_last_timestamp_returns_last_data_row_as_utc_naive(tmp_path: Path):
    # Arrange
    csv_path = tmp_path / "jp225_m1.csv"
    csv_path.write_text(
        "date,open,high,low,close,volume\n"
        "2026-06-15 09:00:00,100.0,101.0,99.0,100.5,10.0\n"
        "2026-06-15 09:01:00,100.5,102.0,100.0,101.5,12.0\n",
        encoding="utf-8",
    )
    # Act
    last = mod.read_last_timestamp(csv_path)
    # Assert: 末尾データ行の date を UTC ナイーブ datetime で返す。
    assert last == datetime(2026, 6, 15, 9, 1, 0)
    assert last.tzinfo is None


def test_read_last_timestamp_robust_to_trailing_newline(tmp_path: Path):
    # Arrange: 末尾改行に頑健であること。
    csv_path = tmp_path / "jp225_m1.csv"
    csv_path.write_text(
        "date,open,high,low,close,volume\n"
        "2026-06-15 09:00:00,100.0,101.0,99.0,100.5,10.0\n"
        "2026-06-15 09:01:00,100.5,102.0,100.0,101.5,12.0\n\n",
        encoding="utf-8",
    )
    # Act
    last = mod.read_last_timestamp(csv_path)
    # Assert
    assert last == datetime(2026, 6, 15, 9, 1, 0)


def test_read_last_timestamp_returns_none_when_file_missing(tmp_path: Path):
    # Arrange: 不在。
    csv_path = tmp_path / "does_not_exist.csv"
    # Act / Assert
    assert mod.read_last_timestamp(csv_path) is None


def test_read_last_timestamp_returns_none_when_file_empty(tmp_path: Path):
    # Arrange: 空ファイル。
    csv_path = tmp_path / "empty.csv"
    csv_path.write_text("", encoding="utf-8")
    # Act / Assert
    assert mod.read_last_timestamp(csv_path) is None


def test_read_last_timestamp_returns_none_when_header_only(tmp_path: Path):
    # Arrange: ヘッダーのみ（データ行なし）。
    csv_path = tmp_path / "header_only.csv"
    csv_path.write_text("date,open,high,low,close,volume\n", encoding="utf-8")
    # Act / Assert
    assert mod.read_last_timestamp(csv_path) is None


# --------------------------------------------------------------------------- #
# append_incremental（副作用・追記）
# --------------------------------------------------------------------------- #
def _make_df(timestamps, base=100.0):
    """UTC index・open/high/low/close/volume 列の DataFrame を作る（fetch_chunk 戻り値模倣）。

    実 ``dukascopy_python.fetch`` は ``pd.to_datetime(..., utc=True)`` で **tz-aware UTC**
    index を返す。回帰を非空虚化するため、その契約を ``utc=True`` で忠実に再現する
    （naive な ``last_ts`` との dedup 比較が tz 不整合で壊れないことを固定）。
    """
    idx = pd.to_datetime(list(timestamps), utc=True)
    n = len(timestamps)
    return pd.DataFrame(
        {
            "open": [base] * n,
            "high": [base + 1] * n,
            "low": [base - 1] * n,
            "close": [base + 0.5] * n,
            "volume": [10.0] * n,
        },
        index=idx,
    )


def test_append_incremental_writes_header_once_for_new_file(
    tmp_path: Path, monkeypatch
):
    # Arrange: 新規ファイル。fetch をスタブ（実ネット禁止）。
    csv_path = tmp_path / "jp225_m1.csv"
    df = _make_df(["2026-06-15 09:00:00", "2026-06-15 09:01:00"])
    monkeypatch.setattr(mod, "fetch_chunk", lambda *a, **k: df)
    now = datetime(2026, 6, 15, 12, 0, 0)
    # Act
    written = mod.append_incremental(
        csv_path, now=now, lag_minutes=3, default_start=datetime(2026, 6, 15)
    )
    # Assert: ヘッダー 1 行＋データ 2 行。
    lines = csv_path.read_text(encoding="utf-8").strip().splitlines()
    assert lines[0] == "date,open,high,low,close,volume"
    assert lines.count("date,open,high,low,close,volume") == 1
    assert written == 2


def test_append_incremental_does_not_duplicate_header_on_existing_file(
    tmp_path: Path, monkeypatch
):
    # Arrange: 既存ファイル（last_ts=09:01）。新規 fetch は 09:02 のみ採用されるべき。
    csv_path = tmp_path / "jp225_m1.csv"
    csv_path.write_text(
        "date,open,high,low,close,volume\n"
        "2026-06-15 09:00:00,100.0,101.0,99.0,100.5,10.0\n"
        "2026-06-15 09:01:00,100.5,102.0,100.0,101.5,12.0\n",
        encoding="utf-8",
    )
    df = _make_df(["2026-06-15 09:01:00", "2026-06-15 09:02:00"])  # 09:01 は重複
    monkeypatch.setattr(mod, "fetch_chunk", lambda *a, **k: df)
    now = datetime(2026, 6, 15, 12, 0, 0)
    # Act
    written = mod.append_incremental(
        csv_path, now=now, lag_minutes=3, default_start=datetime(2026, 6, 15)
    )
    # Assert: ヘッダーは 1 つだけ、重複 09:01 は書かれず 09:02 のみ追記。
    lines = csv_path.read_text(encoding="utf-8").strip().splitlines()
    assert lines.count("date,open,high,low,close,volume") == 1
    assert written == 1
    assert lines[-1].startswith("2026-06-15 09:02:00")
    # 09:01 が二重に存在しないこと（重複除去）。
    assert sum(1 for ln in lines if ln.startswith("2026-06-15 09:01:00")) == 1


def test_append_incremental_returns_zero_when_no_new_window(
    tmp_path: Path, monkeypatch
):
    # Arrange: last_ts が now-lag より後 → compute_fetch_window=None → 何もしない。
    csv_path = tmp_path / "jp225_m1.csv"
    csv_path.write_text(
        "date,open,high,low,close,volume\n"
        "2026-06-15 11:59:00,100.0,101.0,99.0,100.5,10.0\n",
        encoding="utf-8",
    )

    def _fail_fetch(*a, **k):
        raise AssertionError("fetch_chunk must not be called when window is None")

    monkeypatch.setattr(mod, "fetch_chunk", _fail_fetch)
    now = datetime(2026, 6, 15, 12, 0, 0)  # now-3min = 11:57 < 11:59
    # Act
    written = mod.append_incremental(
        csv_path, now=now, lag_minutes=3, default_start=datetime(2026, 6, 15)
    )
    # Assert
    assert written == 0


# --------------------------------------------------------------------------- #
# run_watch（副作用・継続ポーリング）
# --------------------------------------------------------------------------- #
def test_run_watch_calls_update_fn_stop_after_times():
    # Arrange: sleep_fn 注入・stop_after で有限終了。
    calls = {"update": 0, "sleep": []}

    def update_fn():
        calls["update"] += 1

    def sleep_fn(sec):
        calls["sleep"].append(sec)

    # Act
    rc = mod.run_watch(update_fn, interval=60, sleep_fn=sleep_fn, stop_after=3)
    # Assert: update_fn を所定回数（3 回）呼ぶ。
    assert calls["update"] == 3
    assert rc == 0


def test_run_watch_continues_after_transient_update_error():
    # Arrange: 1 回目で一過性例外（fetch 失敗等）。捕捉して次サイクルへ継続することを固定。
    state = {"n": 0}

    def update_fn():
        state["n"] += 1
        if state["n"] == 1:
            raise RuntimeError("transient network failure")

    # Act: 例外を伝播させずポーリングを継続し、stop_after で有限終了する。
    rc = mod.run_watch(update_fn, interval=60, sleep_fn=lambda s: None, stop_after=3)
    # Assert: 例外で死なず所定回数（3 回）試行し正常終了（0）。
    assert state["n"] == 3
    assert rc == 0


def test_run_watch_stops_on_keyboard_interrupt():
    # Arrange: 2 回目で KeyboardInterrupt を投げて正常停止を確認。
    state = {"n": 0}

    def update_fn():
        state["n"] += 1
        if state["n"] == 2:
            raise KeyboardInterrupt

    # Act
    rc = mod.run_watch(update_fn, interval=60, sleep_fn=lambda s: None, stop_after=None)
    # Assert: KeyboardInterrupt を捕捉して正常終了（0）。
    assert rc == 0
    assert state["n"] == 2


# --------------------------------------------------------------------------- #
# CLI: --interval 下限フロア
# --------------------------------------------------------------------------- #
def _parse_cli(argv):
    """main の argparse 部のみを駆動するヘルパ（実行副作用なしで引数検証を確認）。

    --interval の下限フロアを「弱い assertion（未知引数による SystemExit）」と
    取り違えないため、有効値 60 は parse 成功する／59 は失敗するの両方を固定する。
    """
    return mod.build_arg_parser().parse_args(argv)


def test_main_rejects_interval_below_60():
    # Arrange / Act / Assert: --interval < 60 は argparse エラー（SystemExit）。
    #   下限フロア違反を「未知引数 SystemExit」で偽陽性 pass しないよう、
    #   build_arg_parser 経由で必須引数を満たした状態の純粋な argparse 検証にする。
    with pytest.raises(SystemExit):
        _parse_cli(
            ["--start", "2026-06-01", "--end", "2026-06-02", "--watch", "--interval", "59"]
        )


def test_main_accepts_interval_floor_60():
    # Arrange / Act: 境界値 60（下限ちょうど）は受理される（フロア＝60 を固定）。
    args = _parse_cli(
        ["--start", "2026-06-01", "--end", "2026-06-02", "--watch", "--interval", "60"]
    )
    # Assert
    assert args.interval == 60
    assert args.watch is True


# --------------------------------------------------------------------------- #
# main: 起動モード推論（合意仕様#2＝起動時ワンショット増分／後方互換維持）
# --------------------------------------------------------------------------- #
def _spy_dispatch(monkeypatch):
    """main のモード分岐先 3 関数をスパイ化して呼び出し回数を記録する（実ネット・実I/O禁止）。

    どの分岐（stream_to_csv "w" / append_incremental 1 回 / run_watch）に入ったかを
    呼び出し回数で固定する。ワンショット増分が「append_incremental だけ 1 回」かを、
    他 2 経路が 0 回であることと併せて検証することで、弱い assertion（分岐先取り違え）を防ぐ。
    """
    calls = {"stream_to_csv": 0, "append_incremental": 0, "run_watch": 0}

    def _stream(*a, **k):
        calls["stream_to_csv"] += 1
        return 1  # 非 0 を返し main の「空なら 1 を返す」分岐に巻き込まれないようにする

    def _append(*a, **k):
        calls["append_incremental"] += 1
        return 0

    def _watch(*a, **k):
        calls["run_watch"] += 1
        return 0

    monkeypatch.setattr(mod, "stream_to_csv", _stream)
    monkeypatch.setattr(mod, "append_incremental", _append)
    monkeypatch.setattr(mod, "run_watch", _watch)
    return calls


def test_main_one_shot_calls_append_incremental_once_when_no_range_no_watch(
    tmp_path: Path, monkeypatch
):
    # Arrange: --start/--end 省略・--watch なし＝起動時ワンショット増分。
    calls = _spy_dispatch(monkeypatch)
    out = tmp_path / "jp225_m1.csv"
    # Act
    rc = mod.main(["--output", str(out)])
    # Assert: append_incremental だけ 1 回。stream_to_csv / run_watch は呼ばれない。
    assert calls["append_incremental"] == 1
    assert calls["stream_to_csv"] == 0
    assert calls["run_watch"] == 0
    assert rc == 0


def test_main_one_shot_returns_zero_when_no_new_window(tmp_path: Path, monkeypatch):
    # Arrange: 取得すべき新規がない（append_incremental が 0 を返す）場合でも正常終了。
    calls = _spy_dispatch(monkeypatch)
    out = tmp_path / "jp225_m1.csv"
    # Act
    rc = mod.main(["--output", str(out)])
    # Assert: 追記 0 でも rc=0（空＝異常ではない／--start/--end 経路の rc=1 とは分ける）。
    assert calls["append_incremental"] == 1
    assert rc == 0


def test_main_full_overwrite_calls_stream_to_csv_when_range_given(
    tmp_path: Path, monkeypatch
):
    # Arrange: --start/--end 両指定＝従来の全期間上書き（stream_to_csv "w"）経路（後方互換）。
    calls = _spy_dispatch(monkeypatch)
    out = tmp_path / "jp225_m1.csv"
    # Act
    rc = mod.main(["--start", "2026-06-01", "--end", "2026-06-02", "--output", str(out)])
    # Assert: stream_to_csv だけ呼ばれる。増分・ポーリングには入らない。
    assert calls["stream_to_csv"] == 1
    assert calls["append_incremental"] == 0
    assert calls["run_watch"] == 0
    assert rc == 0


def test_main_watch_calls_run_watch(tmp_path: Path, monkeypatch):
    # Arrange: --watch 指定＝継続ポーリング（run_watch）経路。
    calls = _spy_dispatch(monkeypatch)
    out = tmp_path / "jp225_m1.csv"
    # Act
    rc = mod.main(["--watch", "--output", str(out)])
    # Assert: run_watch だけ呼ばれる。
    assert calls["run_watch"] == 1
    assert calls["stream_to_csv"] == 0
    assert calls["append_incremental"] == 0
    assert rc == 0


def test_main_rejects_start_only(tmp_path: Path, monkeypatch):
    # Arrange: 片側だけ指定（--start のみ）は曖昧モード禁止＝エラー。
    _spy_dispatch(monkeypatch)
    out = tmp_path / "jp225_m1.csv"
    # Act / Assert: --end 欠落で SystemExit。
    with pytest.raises(SystemExit):
        mod.main(["--start", "2026-06-01", "--output", str(out)])


def test_main_rejects_end_only(tmp_path: Path, monkeypatch):
    # Arrange: 片側だけ指定（--end のみ）も曖昧モード禁止＝エラー。
    _spy_dispatch(monkeypatch)
    out = tmp_path / "jp225_m1.csv"
    # Act / Assert: --start 欠落で SystemExit。
    with pytest.raises(SystemExit):
        mod.main(["--end", "2026-06-02", "--output", str(out)])
