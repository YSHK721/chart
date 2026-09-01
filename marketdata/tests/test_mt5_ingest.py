"""検証・UTC 変換・日分割・列整形の検定（ISSUE-447 段階 1 / 検定 E-1〜E-3・B-2）。

ここが「MT5 の生の並び」と「marketdata の台帳」の境目である。台帳側の規約（列・dtype・
UTC 日 partition）は :mod:`marketdata.tick_m1` が権威であり、本層はそこへ**委譲**する。
異常はすべて Fail-Stop で、部分的に書かれた台帳を残さない。
"""
from __future__ import annotations

import datetime as dt

import pytest

from marketdata import tick_m1
from marketdata.mt5_ticks import ingest
from marketdata.mt5_ticks.port import Mt5SupplyError


def _ms(y, mo, d, h, mi=0, s=0) -> int:
    return int(dt.datetime(y, mo, d, h, mi, s, tzinfo=dt.timezone.utc).timestamp() * 1000)


def _rows(*specs):
    return [(int(ms), float(bid), float(ask)) for ms, bid, ask in specs]


# =====================================================================
# 銘柄トークン（M-1: sanitize は既存権威を import する）
# =====================================================================

def test_token_joins_the_sanitized_symbol_and_server():
    """例 ``JP225@OANDA-Japan-MT5-Live``（設計 §4）。"""
    assert ingest.token_for("JP225", "OANDA-Japan MT5 Live") == "JP225@OANDA-Japan-MT5-Live"


def test_token_sanitizes_path_hostile_components():
    """パス区切り・``..`` を含む供給元名でも tick 木の外へ出ない。"""
    assert "/" not in ingest.token_for("JP225", "a/b")
    with pytest.raises(Exception):
        ingest.token_for("JP225", "..")


def test_token_uses_the_existing_sanitizer_rather_than_a_private_copy():
    """M-1: 変換規則の第 2 実装を持たない（``_SAFE_CHARS`` 相当を自前で持たない）。"""
    from tools.capture_mt5_symbol_spec import sanitize_path_component

    assert ingest.sanitize_path_component is sanitize_path_component


# =====================================================================
# E-1〜E-3 異常系（すべて Fail-Stop）
# =====================================================================

def test_ascending_rows_inside_the_window_pass_validation():
    ingest.validate_rows(_rows((1000, 1.0, 2.0), (1001, 1.0, 2.0)), from_msc=1000)


def test_non_monotonic_time_msc_is_fail_stop():
    """E-1: 非単調 → ``Mt5SupplyError``。"""
    with pytest.raises(Mt5SupplyError):
        ingest.validate_rows(_rows((1001, 1.0, 2.0), (1000, 1.0, 2.0)), from_msc=1000)


def test_equal_time_msc_is_allowed_because_ticks_share_milliseconds():
    """同一 ms は正常（非単調ではない）。ここを弾くと実データが通らない。"""
    ingest.validate_rows(_rows((1000, 1.0, 2.0), (1000, 1.1, 2.1)), from_msc=1000)


def test_rows_before_the_requested_window_are_fail_stop():
    """E-2: ``ms < from_msc`` → ``Mt5SupplyError``。"""
    with pytest.raises(Mt5SupplyError):
        ingest.validate_rows(_rows((999, 1.0, 2.0)), from_msc=1000)


def test_rows_after_the_requested_window_are_fail_stop():
    """上端を指定した場合はその外も拒む。"""
    with pytest.raises(Mt5SupplyError):
        ingest.validate_rows(_rows((2001, 1.0, 2.0)), from_msc=1000, to_msc=2000)


@pytest.mark.parametrize(
    "bad,why",
    [
        ((1000, 2.0, 1.0), "ask < bid"),
        ((1000, 0.0, 2.0), "bid <= 0"),
        ((1000, -1.0, 2.0), "bid < 0"),
        ((1000, float("nan"), 2.0), "bid NaN"),
        ((1000, 1.0, float("nan")), "ask NaN"),
        ((1000, 1.0, float("inf")), "ask inf"),
    ],
)
def test_impossible_quotes_are_fail_stop(bad, why):
    """E-3: ``ask < bid`` / ``bid <= 0`` / NaN → ``Mt5SupplyError``（黙って通さない）。"""
    with pytest.raises(Mt5SupplyError):
        ingest.validate_rows([bad], from_msc=1000)


def test_ask_equal_to_bid_is_allowed():
    """スプレッド 0 は異常ではない（実在しうる）。過剰に弾かない。"""
    ingest.validate_rows(_rows((1000, 1.0, 1.0)), from_msc=1000)


@pytest.mark.parametrize("bad", [("1000", 1.0, 2.0), (1000, "1.0", 2.0), (1000, 1.0, None)])
def test_wrong_types_are_fail_stop(bad):
    """dtype 不一致 → ``Mt5SupplyError``（文字列を数へ暗黙変換しない）。"""
    with pytest.raises(Mt5SupplyError):
        ingest.validate_rows([bad], from_msc=1000)


# =====================================================================
# B-2 境界: UTC 日跨ぎ
# =====================================================================

def test_a_single_response_that_crosses_a_utc_day_is_split_in_ascending_order():
    """B-2: 1 応答が 2 つの UTC 日へ昇順で分かれる。"""
    # Arrange: 夏（ラベル = UTC+3）。ラベル 02:59:59 は前日 UTC、03:00:00 は当日 UTC。
    rows = _rows(
        (_ms(2026, 8, 1, 2, 59, 58), 1.0, 2.0),
        (_ms(2026, 8, 1, 2, 59, 59), 1.0, 2.0),
        (_ms(2026, 8, 1, 3, 0, 0), 1.0, 2.0),
        (_ms(2026, 8, 1, 3, 0, 1), 1.0, 2.0),
    )
    # Act
    got = ingest.split_by_utc_day(rows)
    # Assert
    assert [day for day, _ in got] == [dt.date(2026, 7, 31), dt.date(2026, 8, 1)]
    assert [len(chunk) for _, chunk in got] == [2, 2]


def test_a_response_inside_one_day_is_not_split():
    rows = _rows((_ms(2026, 8, 25, 12), 1.0, 2.0), (_ms(2026, 8, 25, 13), 1.0, 2.0))
    got = ingest.split_by_utc_day(rows)
    assert len(got) == 1 and got[0][0] == dt.date(2026, 8, 25)


def test_splitting_preserves_every_row_exactly_once():
    """分割で 1 行も落ちず・増えない。"""
    rows = _rows(*[(_ms(2026, 8, 1, 2, 59, 50) + i * 1000, 1.0, 2.0) for i in range(30)])
    got = ingest.split_by_utc_day(rows)
    flat = [r for _, chunk in got for r in chunk]
    assert flat == rows


def test_splitting_an_empty_response_yields_nothing():
    assert ingest.split_by_utc_day([]) == []


# =====================================================================
# 列整形（列・dtype は tick_m1 が権威）
# =====================================================================

def test_rows_to_frame_matches_the_day_parquet_schema():
    """N-3 の骨: 列は ``tick_m1._TICK_COLUMNS``・dtype は既存 tick 木と同一主張。

    dtype の期待値は ``tools/tests/test_live_tick_watch.py`` の
    ``test_rows_to_frame_matches_day_parquet_schema`` と同じである（同一の台帳へ
    書く以上、主張も同じでなければならない）。
    """
    df = ingest.rows_to_frame(_rows((_ms(2026, 8, 25, 12), 66020.1, 66035.1)))
    assert list(df.columns) == tick_m1._TICK_COLUMNS
    assert str(df["timestamp"].dtype) == "datetime64[ms, UTC]"
    assert all(str(df[c].dtype) == "float64" for c in tick_m1._TICK_COLUMNS[1:])


def test_rows_to_frame_converts_the_server_label_to_utc():
    """ラベルをそのまま timestamp にしない（夏は 3 時間ずれる）。"""
    import pandas as pd

    label = _ms(2026, 8, 25, 12)
    df = ingest.rows_to_frame(_rows((label, 1.0, 2.0)))
    assert df["timestamp"].iloc[0] == pd.Timestamp("2026-08-25 09:00:00", tz="UTC")


def test_rows_to_frame_on_no_rows_keeps_the_schema():
    """0 行でも列・dtype が保たれる（空だけ別形にしない）。"""
    df = ingest.rows_to_frame([])
    assert list(df.columns) == tick_m1._TICK_COLUMNS
    assert str(df["timestamp"].dtype) == "datetime64[ms, UTC]"
    assert len(df) == 0


def test_the_frame_does_not_invent_volume_columns():
    """MT5 に対応物のない ``bidVolume`` / ``askVolume`` を 0 で埋めない（ISSUE-447 方針 3）。"""
    df = ingest.rows_to_frame(_rows((_ms(2026, 8, 25, 12), 1.0, 2.0)))
    assert "bidVolume" not in df.columns and "askVolume" not in df.columns
