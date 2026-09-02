"""tick_m1 の ISSUE-455 再発防止（TDD Red→Green）。

ISSUE-455 の連鎖:
  1. 追記経路で up/dn がヘッダ未更新のまま本体だけ 8 列化 → tail の列数がヘッダを超える。
  2. read_tail が余剰フィールドを index へ回して列がずれ date 列へ価格（high）が入る。
  3. pd.to_datetime(価格) が数値をナノ秒と解釈して 1970-01-01 を返す。
  4. resume ガード（index > last_date=1970）が全履歴を選択し毎分再追記 → 8 重連結。

本テスト群は以下を固定する:
  A2/A3: _is_healthy_m1_row が「エポック近傍 / データ開始前」の実装不能日を不健全と判定する
         （read_tail の構造検査を潜り抜けた意味的破損に対する多重防御）。
  A4:    ヘッダ超過の壊れ CSV を持つ状態で append_m1_from_ticks が **クラッシュせず**原子的
         全構築へフォールバックし、全履歴の 8 重再追記を起こさない（根本症状の除去）。
  A4-CX: フォールバック後の出力行数 = 一意 M1 バー数（作った数 − 使った数 = 0・浪費の不在）。
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from marketdata import tick_m1


def _tail(date_str: str) -> pd.DataFrame:
    """read_tail(p, 1) 相当の末尾 1 行 DataFrame（date index・OHLCV 列）を合成する。"""
    idx = pd.DatetimeIndex([pd.Timestamp(date_str)], name="date")
    return pd.DataFrame(
        [{"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.5, "volume": 3.0}],
        index=idx,
    )


def test_is_healthy_rejects_epoch_near_date() -> None:
    # 1970-01-01（価格をナノ秒解釈した誤変換の帰結）は不健全。
    assert tick_m1._is_healthy_m1_row(_tail("1970-01-01 00:00:00")) is False


def test_is_healthy_rejects_date_before_data_start() -> None:
    # データ開始（実測 2012-06-14）より前の実装不能日は不健全（境界の下側）。
    assert tick_m1._is_healthy_m1_row(_tail("1999-12-31 23:59:00")) is False


def test_is_healthy_accepts_plausible_recent_date() -> None:
    # 通常の近時日は健全（回帰防止・上側）。
    assert tick_m1._is_healthy_m1_row(_tail("2025-01-02 09:00:00")) is True


def _ticks(rows: list[tuple[str, float, float]]) -> pd.DataFrame:
    ts = pd.to_datetime([r[0] for r in rows]).tz_localize("UTC")
    return pd.DataFrame(
        {"timestamp": ts, "bidPrice": [r[1] for r in rows], "askPrice": [r[2] for r in rows]}
    )


def _put_day(data_dir: Path, ymd: tuple[int, int, int],
             rows: list[tuple[str, float, float]]) -> None:
    d = data_dir / "ticks" / f"{ymd[0]:04d}" / f"{ymd[1]:02d}" / f"{ymd[2]:02d}"
    d.mkdir(parents=True)
    _ticks(rows).to_parquet(d / "JP225_ticks.parquet")


_ALL_ROWS = [
    ("2025-01-02 09:00:10", 100.0, 100.0),
    ("2025-01-02 09:01:10", 102.0, 102.0),
    ("2025-01-03 09:00:10", 200.0, 200.0),
]


def test_append_falls_back_to_build_when_tail_is_column_overrun(tmp_path: Path) -> None:
    # 真値ソース: parquet 2 日分（3 分バー）。
    _put_day(tmp_path, (2025, 1, 2), _ALL_ROWS[:2])
    _put_day(tmp_path, (2025, 1, 3), _ALL_ROWS[2:])

    # 既存 CSV を ISSUE-455 の急性状態へ: ヘッダ 6 列・本体 8 フィールド（up/dn がヘッダ未反映で
    # 本体だけ 8 列化＝列数超過）。この tail を read_tail に通すと 1970 誤読が起きていた。
    out = tick_m1.m1_csv_path(data_dir=tmp_path)
    out.write_text(
        "date,open,high,low,close,volume\n"
        "2025-01-02 09:00:00,100.0,100.0,100.0,100.0,1.0,1,0\n"
        "2025-01-02 09:01:00,102.0,102.0,102.0,102.0,1.0,1,0\n",
        encoding="utf-8",
    )

    # append: クラッシュ（ValueError 伝播）せず、原子的全構築へフォールバックして自己修復する。
    res = tick_m1.append_m1_from_ticks("2025-01-01", "2025-01-03", data_dir=tmp_path)
    got = pd.read_csv(res, parse_dates=["date"]).set_index("date")

    # 状態検証: parquet 由来の一意 M1 バーへ復元（全履歴の 8 重再追記ではない）。
    expected = tick_m1.ticks_to_m1(_ticks(_ALL_ROWS))
    pd.testing.assert_frame_equal(got, expected, check_names=True)


def test_append_fallback_output_rows_equal_unique_bar_count(tmp_path: Path) -> None:
    # 計算量検定（浪費の不在）: フォールバック後の出力行数 = 一意 M1 バー数。
    # 「作った数 − 使った数 = 0」— 8 重連結（作って捨てる重複）が 1 行も残らないことを固定する。
    # 回数そのものを焼き込まず、出力量が入力の一意バー数だけで決まることを表明する。
    _put_day(tmp_path, (2025, 1, 2), _ALL_ROWS[:2])
    _put_day(tmp_path, (2025, 1, 3), _ALL_ROWS[2:])
    out = tick_m1.m1_csv_path(data_dir=tmp_path)
    out.write_text(
        "date,open,high,low,close,volume\n"
        "2025-01-02 09:00:00,1.0,1.0,1.0,1.0,1.0,1,0\n",  # 列数超過の壊れ tail
        encoding="utf-8",
    )
    res = tick_m1.append_m1_from_ticks("2025-01-01", "2025-01-03", data_dir=tmp_path)
    df = pd.read_csv(res)
    unique_bars = tick_m1.ticks_to_m1(_ticks(_ALL_ROWS)).shape[0]
    assert len(df) == unique_bars           # 出力=一意バー数（余剰 0）。
    assert df["date"].is_unique             # 重複 date が 1 件も無い。


def _m1_with_updown(date_str: str) -> pd.DataFrame:
    """up/dn を持つ（8 列相当の）新規 M1 バー 1 行。"""
    idx = pd.DatetimeIndex([pd.Timestamp(date_str)], name="date")
    return pd.DataFrame(
        [{"open": 1.0, "high": 2.0, "low": 0.0, "close": 1.0, "volume": 3.0, "up": 2.0, "dn": 1.0}],
        index=idx,
    )


def test_append_m1_csv_rejects_header_column_mismatch(tmp_path: Path) -> None:
    # 既存 CSV は 6 列（up/dn 無し）。追記行は up/dn を持つ 8 列 → 列がヘッダと食い違う。
    # 黙って 8 フィールド行を 6 列ヘッダ下へ書く（＝ラガーの発生源）ことを拒否し ValueError。
    p = tmp_path / "jp225_tick_m1.csv"
    p.write_text(
        "date,open,high,low,close,volume\n2025-01-02 09:00:00,1.0,2.0,0.0,1.0,3.0\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="ヘッダ"):
        tick_m1._append_m1_csv(_m1_with_updown("2025-01-02 09:01:00"), p)


def test_append_m1_rows_rejects_header_column_mismatch(tmp_path: Path) -> None:
    # 公開入口も同契約: 既存 6 列ファイルへ 8 列行を追記しようとすると ValueError で拒否。
    p = tmp_path / "jp225_tick_m1.csv"
    p.write_text(
        "date,open,high,low,close,volume\n2025-01-02 09:00:00,1.0,2.0,0.0,1.0,3.0\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="ヘッダ"):
        tick_m1.append_m1_rows(_m1_with_updown("2025-01-02 09:01:00"), p)


def test_append_from_ticks_rebuilds_when_existing_header_lags_updown(tmp_path: Path) -> None:
    # ラガーの端から端まで: 既存 6 列 CSV（健全 tail）＋ tick 由来 m1_new は up/dn 付き 8 列。
    # 追記時のヘッダ不一致を検出し、原子的全構築で 8 列ヘッダへ書き直す（黙って 8 列化しない）。
    _put_day(tmp_path, (2025, 1, 2), _ALL_ROWS[:2])
    _put_day(tmp_path, (2025, 1, 3), _ALL_ROWS[2:])
    out = tick_m1.m1_csv_path(data_dir=tmp_path)
    # day2 の健全な 6 列既存（read_tail OK・_is_healthy True）。
    out.write_text(
        "date,open,high,low,close,volume\n"
        "2025-01-02 09:00:00,100.0,100.0,100.0,100.0,1.0\n"
        "2025-01-02 09:01:00,102.0,102.0,102.0,102.0,1.0\n",
        encoding="utf-8",
    )
    res = tick_m1.append_m1_from_ticks("2025-01-01", "2025-01-03", data_dir=tmp_path)
    header = res.read_text(encoding="utf-8").splitlines()[0]
    assert header == "date,open,high,low,close,volume,up,dn"   # ヘッダが 8 列へ是正。
    got = pd.read_csv(res, parse_dates=["date"]).set_index("date")
    expected = tick_m1.ticks_to_m1(_ticks(_ALL_ROWS))
    pd.testing.assert_frame_equal(got, expected, check_names=True)
