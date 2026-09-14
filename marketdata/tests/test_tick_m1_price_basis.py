"""``tick_m1`` の価格基準（price basis）拡張点の検定（ISSUE-447 / 依頼者裁定 2026-09-02）。

なぜ拡張点が要るのか:
    M1 の価格は長らく mid=(bid+ask)/2 の 1 通りしか無かった。Dukascopy 系（``jp225_tick``）は
    それでよい。しかし MT5 端末のチャートは **bid** を描いており（ISSUE.md 段階 0 実測 T5:
    中央値 ``duka(mid) - mt5(bid) = +6.97``・``chart_mode=0`` と整合）、同じティックから mid で
    M1 を作ると端末表示と系統的にずれる。基準を選べる**拡張点**を足し、既定（mid）は 1 バイトも
    変えない（OCP: 既存呼出は無改変で従来挙動）。

本ファイルが固定するもの:
    1. 既定は mid のまま（既存の全呼出が従来値を返す＝回帰の遮断）
    2. ``price_basis="bid"`` で価格系列が bid になる（OHLC 4 値と方向内訳 up/dn を含む）
    3. 未知の基準は :class:`ValueError`（fail-fast・黙って mid へ落ちない）
    4. **計算量**: 新設分岐が「行の複製・再計算」を生まない（列選択のみ）。bid 基準で ask 列を
       1 度も読まない＝作ってから捨てる計算が 0 であること、および列アクセス数が入力行数で
       増えないこと（2 点でのオーダー表明）。
"""

from __future__ import annotations

from typing import Any, List

import pandas as pd
import pytest

from marketdata import tick_m1


def _ticks(rows: "list[tuple[str, float, float]]") -> pd.DataFrame:
    """``(時刻, bid, ask)`` から生ティック frame を作る（既存検定と同じ形）。"""
    return pd.DataFrame(
        {
            "timestamp": pd.to_datetime([r[0] for r in rows]).tz_localize("UTC"),
            "bidPrice": [r[1] for r in rows],
            "askPrice": [r[2] for r in rows],
        }
    )


class _ColumnAccessSpy:
    """列アクセスを数える Test Spy（生ティック frame の代理）。

    測るのは時間ではなく**回数**である。bid 基準の出力は ask 列を使わないので、ask を 1 度でも
    読んでいれば「作ってから捨てる計算」が在る（出力が正しいままなので状態検証では落ちない）。
    """

    def __init__(self, frame: pd.DataFrame):
        self._frame = frame
        self.reads: "List[Any]" = []

    @property
    def columns(self):
        return self._frame.columns

    @property
    def empty(self) -> bool:
        return self._frame.empty

    def __getitem__(self, key):
        self.reads.append(key)
        return self._frame[key]

    def count_of(self, column: str) -> int:
        return sum(1 for key in self.reads if key == column)


def _minute_of_ticks(n: int) -> pd.DataFrame:
    """1 分に収まる ``n`` 本のティック（bid と ask が必ず異なる）。"""
    return _ticks(
        [
            (f"2026-09-01 00:00:{i % 60:02d}", 66000.0 + i * 0.1, 66010.0 + i * 0.1)
            for i in range(n)
        ]
    )


# =====================================================================
# 既定（mid）の不変
# =====================================================================

@pytest.mark.parametrize("explicit", [False, True], ids=["既定", "mid を明示"])
def test_the_mid_basis_yields_the_midpoint_of_bid_and_ask(explicit) -> None:
    """既定のままでも mid を明示しても、価格は mid=(bid+ask)/2 である（既存 ref の回帰遮断）。

    期待値は計算結果ではなく**書き下した数値**である（実装をもう一度呼んで比べると、
    両側が同時に間違ったときに気付けない）。
    """
    # Arrange: bid と ask が 10 離れたティック 3 本（mid は bid+5）。
    df = _ticks(
        [
            ("2026-09-01 00:00:05", 66000.0, 66010.0),  # mid 66005
            ("2026-09-01 00:00:30", 66020.0, 66030.0),  # mid 66025（high）
            ("2026-09-01 00:00:55", 65990.0, 66000.0),  # mid 65995（low・close）
        ]
    )

    # Act
    m1 = (
        tick_m1.ticks_to_m1(df, price_basis=tick_m1.PRICE_BASIS_MID) if explicit
        else tick_m1.ticks_to_m1(df)
    )

    # Assert
    row = m1.iloc[0]
    assert (row["open"], row["high"], row["low"], row["close"]) == (
        66005.0, 66025.0, 65995.0, 65995.0
    )


# =====================================================================
# bid 基準
# =====================================================================

def test_the_bid_basis_builds_the_ohlc_from_the_bid_series_not_the_mid() -> None:
    """``price_basis="bid"`` は OHLC 4 値すべてを bid から作る（mid ではない）。"""
    # Arrange: mid だと +5 ずれる（bid と ask が 10 離れている）。
    df = _ticks(
        [
            ("2026-09-01 00:00:05", 66000.0, 66010.0),
            ("2026-09-01 00:00:30", 66020.0, 66030.0),
            ("2026-09-01 00:00:55", 65990.0, 66000.0),
        ]
    )

    # Act
    m1 = tick_m1.ticks_to_m1(df, price_basis=tick_m1.PRICE_BASIS_BID)

    # Assert: bid そのもの（mid の 66005/66025/65995 ではない）。
    row = m1.iloc[0]
    assert (row["open"], row["high"], row["low"], row["close"]) == (
        66000.0, 66020.0, 65990.0, 65990.0
    )
    assert row["volume"] == 3.0  # volume 規則（ティック数）は基準に依らない。


def test_the_bid_basis_takes_the_direction_breakdown_from_the_bid_series() -> None:
    """方向内訳（up/dn）も bid の差分で数える（価格系列と方向が別基準にならない）。

    ask だけが動いた瞬間は bid 基準では**動いていない**。ここが mid のままだと、表示される足は
    bid なのに方向内訳だけが mid 由来という食い違いが残る。
    """
    # Arrange: bid は 3 本とも同値、ask だけが上下する。
    df = _ticks(
        [
            ("2026-09-01 00:00:05", 66000.0, 66010.0),
            ("2026-09-01 00:00:30", 66000.0, 66030.0),  # mid は上がるが bid は不動
            ("2026-09-01 00:00:55", 66000.0, 66004.0),  # mid は下がるが bid は不動
        ]
    )

    # Act
    m1 = tick_m1.ticks_to_m1(df, price_basis=tick_m1.PRICE_BASIS_BID)

    # Assert
    row = m1.iloc[0]
    assert (row["up"], row["dn"]) == (0.0, 0.0)


def test_an_unknown_basis_is_refused_instead_of_falling_back_to_mid() -> None:
    """未知の基準は :class:`ValueError`（黙って既定へ落ちない・fail-fast）。"""
    with pytest.raises(ValueError):
        tick_m1.ticks_to_m1(_minute_of_ticks(5), price_basis="last")


def test_an_empty_frame_yields_the_empty_ohlcv_shape_under_the_bid_basis() -> None:
    """境界: 空入力でも列と index 名は従来どおり（基準は空の形を変えない）。"""
    # Arrange
    empty = pd.DataFrame({c: [] for c in tick_m1._TICK_COLUMNS})

    # Act
    m1 = tick_m1.ticks_to_m1(empty, price_basis=tick_m1.PRICE_BASIS_BID)

    # Assert: 期待は書き下した形そのもの（実装をもう一度呼ばない）。
    assert len(m1) == 0
    assert list(m1.columns) == ["open", "high", "low", "close", "volume", "up", "dn"]
    assert m1.index.name == "date"


# =====================================================================
# 計算量（Test Spy・発行 − 使用 = 0）
# =====================================================================

def test_the_bid_basis_never_reads_the_ask_column() -> None:
    """CX: bid 基準で ask 列の読みが 0（作ってから捨てる計算が無い）。

    固定するのは回数ではなく**無駄の不在**である。bid 基準の出力は ask を 1 つも使わないため、
    ask を読んだ時点でそれは出力に使わない計算＝浪費である。
    """
    spy = _ColumnAccessSpy(_minute_of_ticks(120))

    tick_m1.ticks_to_m1(spy, price_basis=tick_m1.PRICE_BASIS_BID)

    assert spy.count_of("askPrice") == 0, (
        f"bid 基準なのに ask 列を {spy.count_of('askPrice')} 回読みました"
        "（出力に使わない計算＝浪費です）。"
    )
    assert spy.count_of("bidPrice") > 0  # 価格が bid から来ていることの裏取り。


def test_the_new_basis_branch_does_not_read_more_columns_than_the_default() -> None:
    """CX: 新設分岐が列読みを増やさない（列選択のみ・行の複製や再計算をしない）。"""
    mid_spy = _ColumnAccessSpy(_minute_of_ticks(120))
    bid_spy = _ColumnAccessSpy(_minute_of_ticks(120))

    tick_m1.ticks_to_m1(mid_spy)
    tick_m1.ticks_to_m1(bid_spy, price_basis=tick_m1.PRICE_BASIS_BID)

    assert len(bid_spy.reads) <= len(mid_spy.reads), (
        f"bid 基準の列読み {len(bid_spy.reads)} が既定の {len(mid_spy.reads)} を超えました。"
    )


@pytest.mark.parametrize("basis", ["mid", "bid"])
def test_column_reads_do_not_grow_with_the_number_of_ticks(basis) -> None:
    """CX（オーダーの表明）: 入力行数を 10 倍しても列読みは増えない（2 点で固定）。"""
    small = _ColumnAccessSpy(_minute_of_ticks(12))
    large = _ColumnAccessSpy(_minute_of_ticks(120))

    tick_m1.ticks_to_m1(small, price_basis=basis)
    tick_m1.ticks_to_m1(large, price_basis=basis)

    assert len(large.reads) == len(small.reads), (
        f"基準 {basis}: 行数 10 倍で列読みが {len(small.reads)} → {len(large.reads)} へ増えました"
        "（入力量に比例する計算が入り込んでいます）。"
    )


# =====================================================================
# 全量経路への伝播（権威経路も同じ基準で作れる）
# =====================================================================

def test_the_whole_day_builder_can_be_driven_on_the_bid_basis(tmp_path) -> None:
    """``build_m1_from_ticks`` に基準が伝わる（日次権威・再生成が bid で回せる）。"""
    # Arrange: 1 日分の parquet を tick 木へ置く。
    day = pd.Timestamp("2026-09-01")
    parquet = tick_m1.day_parquet_path(day, symbol="SPY225", data_dir=tmp_path)
    parquet.parent.mkdir(parents=True, exist_ok=True)
    _minute_of_ticks(30).to_parquet(parquet)

    # Act
    out = tick_m1.build_m1_from_ticks(
        day, day, symbol="SPY225", ref="basis_bid", data_dir=tmp_path,
        price_basis=tick_m1.PRICE_BASIS_BID,
    )

    # Assert: close は最終ティックの bid（mid ならこれより 5 大きい）。
    written = pd.read_csv(out)
    assert written["close"].iloc[-1] == 66000.0 + 29 * 0.1
