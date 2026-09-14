"""rollup_builder の検証（TDD: Red→Green）— 上位足の増分ロールアップ（メモリ有界化）。

検証対象（合成1分足のみ・実 284MB jp225_m1.csv は読まない・決定論・メモリ小）:
  - merge_same_period: 同一 period の OHLCV 結合（open=first/high=max/low=min/close=last/volume=sum・結合的）。
  - stream_build: チャンク跨ぎ carry-over を含む数値一致（== resample_ohlc_tf(全件, tf)・最重要 D-1）。
  - incremental_update: 追記 tail のみ読み各 TF をマージ（形成中バー上書き＋確定 append）== resample_ohlc_tf(全件)。
  - RollupState: json load/save（last_processed_ts）。
  - メモリ有界: stream_build が chunk 単位処理（全件を同時に DataFrame 化しない）。

数値一致の根拠（厳守）: resample の規則（W-FRI/ME/5min/セッション日切り）を再実装せず、
marketdata.resample.resample_ohlc_tf(全件) を真値（oracle）として stream_build / incremental_update を照合する。
"""

from __future__ import annotations

import csv as _csv
import tracemalloc
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import pytest

import rollup_builder as rb

# 規則源 resample_ohlc_tf（ISSUE-078: 1D/1W/1M はセッション日集計）を oracle として直接利用する。
# ISSUE-093: 旧 oracle（plain resample_ohlc + TIMEFRAME_RULES）は f0584f1 のセッション日移行に
#   未追随で 1D/1W/1M が恒常失敗していた（実装は正・テスト陳腐化）。
from marketdata import resample as md_resample
from adapter.compute import dataset  # noqa: F401  (api 経路の配線・後方互換)


# --------------------------------------------------------------------------- #
# 合成1分足ジェネレータ（決定論・週/月境界を跨ぐ十分な期間・メモリ小）
# --------------------------------------------------------------------------- #
def _synthetic_m1(start: str, minutes: int):
    """start から minutes 本の合成1分足 DataFrame（date index・OHLCV）を返す（決定論）。"""
    idx = pd.date_range(start, periods=minutes, freq="1min")
    base = list(range(minutes))
    return pd.DataFrame(
        {
            "open": [100.0 + b for b in base],
            "high": [100.0 + b + 0.5 for b in base],
            "low": [100.0 + b - 0.5 for b in base],
            "close": [100.0 + b + 0.2 for b in base],
            "volume": [1.0 + (b % 7) for b in base],
        },
        index=idx,
    )


def _write_m1_csv(path: Path, df: pd.DataFrame) -> None:
    """合成1分足を date,open,high,low,close,volume 形式（loader 互換）で書き出す。"""
    with open(path, "w", newline="") as fh:
        w = _csv.writer(fh)
        w.writerow(["date", "open", "high", "low", "close", "volume"])
        for ts, row in df.iterrows():
            w.writerow([
                ts.strftime("%Y-%m-%d %H:%M:%S"),
                row["open"], row["high"], row["low"], row["close"], row["volume"],
            ])


def _read_rollup_csv(path: Path) -> pd.DataFrame:
    """ロールアップ CSV を date index で読む（loader 互換形式の検証用・全読み）。"""
    df = pd.read_csv(path)
    df["date"] = pd.to_datetime(df["date"])
    return df.set_index("date")


# --------------------------------------------------------------------------- #
# merge_same_period（純粋・結合的）
# --------------------------------------------------------------------------- #
def test_merge_same_period_combines_ohlcv_open_first_high_max_low_min_close_last_volume_sum():
    # Arrange: 同一 period に属する 2 つの partial bar（前半・後半）。
    prev_bar = {"open": 10.0, "high": 12.0, "low": 9.0, "close": 11.0, "volume": 100.0}
    new_bar = {"open": 11.0, "high": 15.0, "low": 8.0, "close": 14.0, "volume": 50.0}
    # Act
    merged = rb.merge_same_period(prev_bar, new_bar)
    # Assert: open=最初の bar の open / high=max / low=min / close=後の bar の close / volume=合算。
    assert merged["open"] == 10.0
    assert merged["high"] == 15.0
    assert merged["low"] == 8.0
    assert merged["close"] == 14.0
    assert merged["volume"] == 150.0


def test_merge_same_period_is_associative_for_three_partials():
    # 結合性: merge(merge(a,b),c) == merge(a, merge(b,c))（carry-over の正しさの根拠）。
    a = {"open": 1.0, "high": 3.0, "low": 1.0, "close": 2.0, "volume": 10.0}
    b = {"open": 2.0, "high": 5.0, "low": 0.5, "close": 4.0, "volume": 20.0}
    c = {"open": 4.0, "high": 6.0, "low": 2.0, "close": 5.0, "volume": 30.0}
    left = rb.merge_same_period(rb.merge_same_period(a, b), c)
    right = rb.merge_same_period(a, rb.merge_same_period(b, c))
    assert left == right
    # かつ全体集約（open=最初/high=全体max/low=全体min/close=最終/volume=総和）。
    assert left == {"open": 1.0, "high": 6.0, "low": 0.5, "close": 5.0, "volume": 60.0}


# --------------------------------------------------------------------------- #
# stream_build（数値一致・最重要・チャンク跨ぎ carry-over = D-1）
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("tf", ["5m", "1h", "1D", "1W", "1M"])
def test_stream_build_matches_resample_ohlc_on_full_data_across_chunks(tmp_path, tf):
    # Arrange: 週/月境界を跨ぐ十分な期間（約 40 日 = 57600 分）を小 chunk で複数分割。
    df = _synthetic_m1("2020-01-01 00:00:00", 60 * 24 * 40)
    m1_csv = tmp_path / "m1.csv"
    _write_m1_csv(m1_csv, df)
    out_dir = tmp_path / "rollups"
    # Act: chunk_rows を小さくして複数チャンクに割る（チャンク跨ぎ carry-over を強制）。
    rb.stream_build(m1_csv, [tf], out_dir, chunk_rows=7000)
    # Assert: stream_build 結果 == resample_ohlc(全件, rule)（各 TF 完全一致）。
    expected = md_resample.resample_ohlc_tf(df, tf)
    actual = _read_rollup_csv(out_dir / f"jp225_m1_{tf}.csv")
    assert len(actual) == len(expected)
    assert list(actual.index) == list(expected.index)
    for col in ("open", "high", "low", "close", "volume"):
        assert actual[col].to_numpy() == pytest.approx(expected[col].to_numpy())


def test_stream_build_streaming_write_is_byte_identical_to_full_sorted_write(tmp_path):
    # Refactor 回帰: stream_build の streaming-write（確定バー逐次 flush）出力が、全件を
    #   _write_rollup（period 昇順一括書き）した結果と「バイト一致」する。streaming の確定順崩れ・
    #   行整形の乖離（_bar_to_csv_row 共用の破れ）を検出する回帰テスト。
    # Arrange: 週/月境界を跨ぐ期間を複数チャンクに割る（carry-over を強制）。
    tf = "1h"
    df = _synthetic_m1("2020-01-01 00:00:00", 60 * 24 * 12)
    m1_csv = tmp_path / "m1.csv"
    _write_m1_csv(m1_csv, df)
    out_dir = tmp_path / "rollups"
    # Act: streaming-write 経路（小 chunk で複数分割）。
    rb.stream_build(m1_csv, [tf], out_dir, chunk_rows=2500)
    streamed_bytes = (out_dir / f"jp225_m1_{tf}.csv").read_bytes()
    # Oracle: 全件 resample を _write_rollup で period 昇順一括書きした結果。
    expected_bars = rb._resample_chunk(df, tf)
    oracle_dir = tmp_path / "oracle"
    rb._write_rollup(oracle_dir, tf, expected_bars)
    oracle_bytes = (oracle_dir / f"jp225_m1_{tf}.csv").read_bytes()
    # Assert: バイト一致（外部挙動＝出力 CSV の内容が streaming-write でも不変）。
    assert streamed_bytes == oracle_bytes


# --------------------------------------------------------------------------- #
# incremental_update（増分一致・形成中バー上書き＋確定 append）
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("tf", ["5m", "1h", "1D"])
def test_incremental_update_matches_full_resample_after_append(tmp_path, tf):
    # Arrange: 初回 build（最初の期間）→ state を得る。
    df_initial = _synthetic_m1("2020-01-01 00:00:00", 60 * 24 * 3)  # 3 日
    m1_csv = tmp_path / "m1.csv"
    _write_m1_csv(m1_csv, df_initial)
    out_dir = tmp_path / "rollups"
    rb.stream_build(m1_csv, [tf], out_dir, chunk_rows=2000)
    state = rb.RollupState.load(out_dir)

    # Act: 1分足を数本追記（形成中 period の継続＋次 period のクローズを跨ぐ）→ incremental_update。
    df_more = _synthetic_m1("2020-01-04 00:00:00", 130)  # 形成中バー上書き＋確定 append を誘発
    df_full = pd.concat([df_initial, df_more])
    _write_m1_csv(m1_csv, df_full)
    new_state = rb.incremental_update(m1_csv, state, [tf], out_dir)

    # Assert: 各 TF ロールアップ == resample_ohlc(全件)（形成中バー上書き＋確定 append の正しさ）。
    expected = md_resample.resample_ohlc_tf(df_full, tf)
    actual = _read_rollup_csv(out_dir / f"jp225_m1_{tf}.csv")
    assert list(actual.index) == list(expected.index)
    for col in ("open", "high", "low", "close", "volume"):
        assert actual[col].to_numpy() == pytest.approx(expected[col].to_numpy())
    # 新 state は最終処理タイムスタンプを更新している。
    assert new_state.last_processed_ts == df_full.index.max().to_pydatetime()


def test_incremental_update_uses_tail_probe_not_full_scan(tmp_path, monkeypatch):
    # 🟡-3 回帰: probe（逆シーク末尾読み）が state 以降を内包する通常追記では、
    # incremental_update は全件 pd.read_csv(chunksize=...) を一切呼ばない（--watch 毎分の
    # 全件スキャン＝RSS 肥大の再発を禁ずる）。probe が tail を拾えば正しく増分し続ける。
    df_initial = _synthetic_m1("2020-01-01 00:00:00", 60 * 24 * 3)  # 3 日
    m1_csv = tmp_path / "m1.csv"
    _write_m1_csv(m1_csv, df_initial)
    out_dir = tmp_path / "rollups"
    rb.stream_build(m1_csv, ["1h"], out_dir, chunk_rows=2000)
    state = rb.RollupState.load(out_dir)

    # 数本だけ追記（probe ≈14 日分が確実に内包する通常 --watch 追記を模す）。
    df_more = _synthetic_m1("2020-01-04 00:00:00", 130)
    df_full = pd.concat([df_initial, df_more])
    _write_m1_csv(m1_csv, df_full)

    real_read_csv = pd.read_csv
    calls = {"with_chunksize": 0}

    def _spy_read_csv(*args, **kwargs):
        if kwargs.get("chunksize"):
            calls["with_chunksize"] += 1
        return real_read_csv(*args, **kwargs)

    monkeypatch.setattr(rb.pd, "read_csv", _spy_read_csv)
    new_state = rb.incremental_update(m1_csv, state, ["1h"], out_dir)

    # 全件チャンクスキャンを 1 度も呼ばない（probe で完結）。
    assert calls["with_chunksize"] == 0
    # それでも結果は resample_ohlc(全件) と一致し、state も進む。
    expected = md_resample.resample_ohlc_tf(df_full, "1h")
    actual = _read_rollup_csv(out_dir / "jp225_m1_1h.csv")
    assert list(actual.index) == list(expected.index)
    for col in ("open", "high", "low", "close", "volume"):
        assert actual[col].to_numpy() == pytest.approx(expected[col].to_numpy())
    assert new_state.last_processed_ts == df_full.index.max().to_pydatetime()


def test_incremental_update_falls_back_to_full_scan_when_probe_misses_tail(tmp_path, monkeypatch):
    # probe が last_ts を内包できない長期 catch-up は全件スキャンへフォールバックして正しく増分する。
    df_initial = _synthetic_m1("2020-01-01 00:00:00", 60 * 24 * 3)
    m1_csv = tmp_path / "m1.csv"
    _write_m1_csv(m1_csv, df_initial)
    out_dir = tmp_path / "rollups"
    rb.stream_build(m1_csv, ["1h"], out_dir, chunk_rows=2000)
    state = rb.RollupState.load(out_dir)

    df_more = _synthetic_m1("2020-01-04 00:00:00", 200)
    df_full = pd.concat([df_initial, df_more])
    _write_m1_csv(m1_csv, df_full)

    # probe を極小化（last_ts より後ろしか拾えない）→ probe.index.min() > last_ts でフォールバック誘発。
    monkeypatch.setattr(rb, "_INCREMENTAL_TAIL_PROBE_ROWS", 5)
    real_read_csv = pd.read_csv
    calls = {"with_chunksize": 0}

    def _spy_read_csv(*args, **kwargs):
        if kwargs.get("chunksize"):
            calls["with_chunksize"] += 1
        return real_read_csv(*args, **kwargs)

    monkeypatch.setattr(rb.pd, "read_csv", _spy_read_csv)
    new_state = rb.incremental_update(m1_csv, state, ["1h"], out_dir)

    # probe では last_ts を内包できず、全件チャンクスキャンへフォールバックする。
    assert calls["with_chunksize"] >= 1
    expected = md_resample.resample_ohlc_tf(df_full, "1h")
    actual = _read_rollup_csv(out_dir / "jp225_m1_1h.csv")
    assert list(actual.index) == list(expected.index)
    assert new_state.last_processed_ts == df_full.index.max().to_pydatetime()


def test_incremental_update_is_vectorized_no_iterrows_over_rollup(tmp_path, monkeypatch):
    # ISSUE-012 回帰: incremental_update は「ロールアップ全体規模」を iterrows しない。
    #   90 万行規模を iterrows→dict-of-dict すると RSS が 618MB へ急騰し OOM を再発させる。
    #   O(新規) 経路は末尾 suffix（高々数本）のみ iterrows するため、iterrows 対象フレームの
    #   行数は常に小さい（ロールアップ本数に比例しない）ことを不変条件とする。
    df_initial = _synthetic_m1("2020-01-01 00:00:00", 60 * 24 * 5)  # 5 日（5m=1440 本規模）
    m1_csv = tmp_path / "m1.csv"
    _write_m1_csv(m1_csv, df_initial)
    out_dir = tmp_path / "rollups"
    rb.stream_build(m1_csv, ["5m", "1h", "1D"], out_dir, chunk_rows=2000)
    state = rb.RollupState.load(out_dir)

    df_more = _synthetic_m1("2020-01-06 00:00:00", 130)
    df_full = pd.concat([df_initial, df_more])
    _write_m1_csv(m1_csv, df_full)

    real_iterrows = pd.DataFrame.iterrows
    sizes = {"max": 0}

    def _spy_iterrows(self, *a, **k):
        sizes["max"] = max(sizes["max"], len(self))
        return real_iterrows(self, *a, **k)

    monkeypatch.setattr(pd.DataFrame, "iterrows", _spy_iterrows)
    new_state = rb.incremental_update(m1_csv, state, ["5m", "1h", "1D"], out_dir)

    # iterrows 対象は末尾 suffix（数本）のみ。ロールアップ規模（5m=1440 本）を iterrows しない。
    assert sizes["max"] < 50, f"iterrows 対象が大きすぎる（{sizes['max']} 行）＝ロールアップ全体走査の疑い"
    # それでも各 TF は resample_ohlc(全件) と一致する。
    for tf in ("5m", "1h", "1D"):
        expected = md_resample.resample_ohlc_tf(df_full, tf)
        actual = _read_rollup_csv(out_dir / f"jp225_m1_{tf}.csv")
        assert list(actual.index) == list(expected.index)
        for col in ("open", "high", "low", "close", "volume"):
            assert actual[col].to_numpy() == pytest.approx(expected[col].to_numpy())
    assert new_state.last_processed_ts == df_full.index.max().to_pydatetime()


def test_incremental_update_peak_memory_is_bounded(tmp_path):
    # 回帰（数値版）: 大ロールアップへの 1 tick 増分の tracemalloc peak が上限未満。
    #   O(新規) 経路は末尾だけ truncate+append し全体を read/write しないため peak は極小
    #   （400k 行で実測 ~0.2MB）。全件 read/write 退行（旧 DataFrame 全件 119MB／dict 230MB）へ
    #   戻ると 40MB 超で FAIL する。閾値は O(新規)0.2MB と全件退行 119MB+ の広い間隙（非 flaky）。
    n = 400_000
    out_dir = tmp_path / "rollups"
    out_dir.mkdir(parents=True)
    # 既存 5m ロールアップを直接書く（stream_build の 1m 全件生成は遅いため合成 CSV を用意）。
    idx = pd.date_range("2018-01-01", periods=n, freq="5min")
    pd.DataFrame(
        {"date": idx, "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.5, "volume": 10.0}
    ).to_csv(out_dir / "jp225_m1_5m.csv", index=False)
    last_ts = idx[-1]
    rb.RollupState(last_processed_ts=last_ts.to_pydatetime()).save(out_dir)
    # m1: probe が last_ts を内包するよう <=last_ts も数本含めつつ、新規追記を数本与える。
    m1_idx = pd.date_range(last_ts - timedelta(minutes=3), periods=8, freq="1min")
    m1_csv = tmp_path / "m1.csv"
    _write_m1_csv(
        m1_csv,
        pd.DataFrame(
            {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.5, "volume": 10.0},
            index=m1_idx,
        ),
    )

    state = rb.RollupState.load(out_dir)
    tracemalloc.start()
    try:
        rb.incremental_update(m1_csv, state, ["5m"], out_dir)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    peak_mb = peak / 1e6
    assert peak_mb < 40, (
        f"incremental_update peak {peak_mb:.0f}MB が上限 40MB を超過。"
        f"末尾 truncate+append（O新規）でなく全体 read/write へ退行した疑い。"
    )


def test_incremental_update_appends_tail_without_rewriting_history(tmp_path):
    # O(新規) 回帰: 過去確定足（最終データ行より前の prefix バイト列）は incremental tick 後も
    #   1 バイトも変わらない＝末尾だけ truncate+append し、履歴を read/write しない。
    df_initial = _synthetic_m1("2020-01-01 00:00:00", 60 * 24 * 3)  # 3 日
    m1_csv = tmp_path / "m1.csv"
    _write_m1_csv(m1_csv, df_initial)
    out_dir = tmp_path / "rollups"
    rb.stream_build(m1_csv, ["5m"], out_dir, chunk_rows=2000)
    state = rb.RollupState.load(out_dir)
    path = out_dir / "jp225_m1_5m.csv"
    offset = rb._last_data_line_offset(path)  # 最終データ行（形成中バー）の先頭。
    prefix_before = path.read_bytes()[:offset]

    df_more = _synthetic_m1("2020-01-04 00:00:00", 130)  # 形成中更新＋新 period append を跨ぐ。
    df_full = pd.concat([df_initial, df_more])
    _write_m1_csv(m1_csv, df_full)
    rb.incremental_update(m1_csv, state, ["5m"], out_dir)

    # prefix（過去確定足）はバイト一致（履歴を書き直していない）。
    assert path.read_bytes()[:offset] == prefix_before
    # かつ結果は resample_ohlc(全件) と一致（末尾追記が正しい）。
    expected = md_resample.resample_ohlc_tf(df_full, "5m")
    actual = _read_rollup_csv(path)
    assert list(actual.index) == list(expected.index)
    for col in ("open", "high", "low", "close", "volume"):
        assert actual[col].to_numpy() == pytest.approx(expected[col].to_numpy())


def test_incremental_update_is_idempotent_on_reprocess(tmp_path):
    # O(新規) 安全性: 形成中バーは probe から再計算（上書き）するため、同じ古い state で 2 回
    #   処理しても volume を二重計上しない（書込中 crash 後の再処理が安全＝冪等）。
    df_initial = _synthetic_m1("2020-01-01 00:00:00", 60 * 24 * 3)
    m1_csv = tmp_path / "m1.csv"
    _write_m1_csv(m1_csv, df_initial)
    out_dir = tmp_path / "rollups"
    rb.stream_build(m1_csv, ["5m"], out_dir, chunk_rows=2000)
    state = rb.RollupState.load(out_dir)

    df_more = _synthetic_m1("2020-01-04 00:00:00", 130)
    df_full = pd.concat([df_initial, df_more])
    _write_m1_csv(m1_csv, df_full)
    rb.incremental_update(m1_csv, state, ["5m"], out_dir)  # 1 回目
    rb.incremental_update(m1_csv, state, ["5m"], out_dir)  # 2 回目（同じ古い state ＝ 再処理）

    # 2 回処理しても resample(全件) と一致（merge だと volume 二重計上で不一致になる）。
    expected = md_resample.resample_ohlc_tf(df_full, "5m")
    actual = _read_rollup_csv(out_dir / "jp225_m1_5m.csv")
    assert list(actual.index) == list(expected.index)
    for col in ("open", "high", "low", "close", "volume"):
        assert actual[col].to_numpy() == pytest.approx(expected[col].to_numpy())


def test_incremental_update_falls_back_to_stream_build_when_state_absent(tmp_path):
    # state 不在（初回・RollupState なし）は stream_build へフォールバックする。
    df = _synthetic_m1("2020-01-01 00:00:00", 60 * 24 * 2)
    m1_csv = tmp_path / "m1.csv"
    _write_m1_csv(m1_csv, df)
    out_dir = tmp_path / "rollups"
    # Act: state=None（不在）で呼ぶ。
    new_state = rb.incremental_update(m1_csv, None, ["1h"], out_dir)
    # Assert: フォールアウト build が走り、resample(全件) と一致し、state が返る。
    expected = md_resample.resample_ohlc_tf(df, "1h")
    actual = _read_rollup_csv(out_dir / "jp225_m1_1h.csv")
    assert list(actual.index) == list(expected.index)
    assert new_state.last_processed_ts == df.index.max().to_pydatetime()


# --------------------------------------------------------------------------- #
# RollupState（json load/save・last_processed_ts）
# --------------------------------------------------------------------------- #
def test_rollup_state_save_then_load_roundtrips_last_processed_ts(tmp_path):
    # Arrange
    ts = datetime(2020, 1, 5, 12, 34, 0)
    state = rb.RollupState(last_processed_ts=ts)
    # Act
    state.save(tmp_path)
    loaded = rb.RollupState.load(tmp_path)
    # Assert: json 経由で last_processed_ts が往復する。
    assert loaded.last_processed_ts == ts


def test_rollup_state_load_returns_none_when_absent(tmp_path):
    # state ファイル不在は None（incremental_update のフォールバック判定の真実源）。
    assert rb.RollupState.load(tmp_path) is None


# --------------------------------------------------------------------------- #
# メモリ有界（stream_build が chunk 単位処理 = 全件を同時に DataFrame 化しない）
# --------------------------------------------------------------------------- #
def test_stream_build_reads_in_chunks_not_whole_file(tmp_path, monkeypatch):
    # Arrange: 合成1分足（複数チャンクに割れる行数）。
    df = _synthetic_m1("2020-01-01 00:00:00", 60 * 24 * 10)  # 14400 分
    m1_csv = tmp_path / "m1.csv"
    _write_m1_csv(m1_csv, df)
    out_dir = tmp_path / "rollups"

    # pd.read_csv が chunksize 指定（イテレータ）で呼ばれることを検証する（全件 read_csv を禁ずる）。
    real_read_csv = pd.read_csv
    calls = {"with_chunksize": 0, "without_chunksize": 0}

    def _spy_read_csv(*args, **kwargs):
        if kwargs.get("chunksize"):
            calls["with_chunksize"] += 1
        else:
            calls["without_chunksize"] += 1
        return real_read_csv(*args, **kwargs)

    monkeypatch.setattr(rb.pd, "read_csv", _spy_read_csv)
    # Act
    rb.stream_build(m1_csv, ["1h"], out_dir, chunk_rows=3000)
    # Assert: chunksize 付き read_csv で読む（全件一括 read_csv は呼ばない＝メモリ有界）。
    assert calls["with_chunksize"] >= 1
    assert calls["without_chunksize"] == 0


# --------------------------------------------------------------------------- #
# 原子性（🔴 回帰）: 書込中 crash で確定パスを汚さない（tmp→os.replace）。
#   --watch は毎分 incremental_update→_write_rollup で各 TF を全書き直しするため、
#   書込中の OOM-kill/crash で確定パスに部分 CSV が残ると cold-start の server が
#   不完全データを配信する。確定パスは「完全な新 CSV」か「旧 CSV」のいずれかに限定する。
# --------------------------------------------------------------------------- #
def test_write_rollup_is_atomic_on_midwrite_failure(tmp_path: Path) -> None:
    out_dir = tmp_path
    out_dir.mkdir(parents=True, exist_ok=True)
    final = rb._rollup_path(out_dir, "1D")
    final.write_text("date,open,high,low,close,volume\nOLD\n", encoding="utf-8")
    # 2 本目で KeyError（'open' 欠落）＝書込中 crash を模す。
    bars = {
        pd.Timestamp("2020-01-01"): {"open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5, "volume": 10.0},
        pd.Timestamp("2020-01-02"): {"high": 2.0, "low": 0.5, "close": 1.5, "volume": 10.0},
    }
    with pytest.raises(KeyError):
        rb._write_rollup(out_dir, "1D", bars)
    # 確定パスは OLD のまま（部分 CSV で上書きしない）・tmp 残骸なし。
    assert final.read_text(encoding="utf-8") == "date,open,high,low,close,volume\nOLD\n"
    assert list(out_dir.glob("*.tmp")) == []


def test_rollup_writer_discards_tmp_when_not_committed(tmp_path: Path) -> None:
    out_dir = tmp_path
    out_dir.mkdir(parents=True, exist_ok=True)
    w = rb._RollupWriter(out_dir, "1D")
    w.write(pd.Timestamp("2020-01-01"), {"open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5, "volume": 10.0})
    # commit せず close（＝書込中 crash 相当）→ 確定パスは作られず tmp も残らない。
    w.close()
    assert not rb._rollup_path(out_dir, "1D").exists()
    assert list(out_dir.glob("*.tmp")) == []
