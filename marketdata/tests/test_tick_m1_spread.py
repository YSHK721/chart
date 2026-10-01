"""``tick_m1`` の spread 列（opt-in の ``point`` 注入）の検定（ISSUE-511 段階 2）。

規則: spread = 分内 min((ask − bid) / point) を最近接整数（.5 は偶数）へ丸めた int points。
規則そのものの検定は ``test_quote_spread.py``。本ファイルは M1 組み立て・CSV 書き出しへの
結線と、既存データを書き換えない不変条件（``point=None`` で従来と byte 一致）を固定する。

point はテストが注入する値である（銘柄仕様 ``marketdata.symbol_spec_snapshot.load_spec_fields`` は呼ばない）。

計算量（絶対命令 2026-08-28）: ``quote_spread.minute_spread_points`` を Test Spy で包み、
**発行した気配幅の数 − 出力に使った数 = 0** を表明する。回数そのものは期待値に焼き込まない。
"""
from __future__ import annotations

import importlib
import math

import pandas as pd
import pytest

from marketdata import tick_m1

_POINT = 0.1  # テストが注入する point（銘柄仕様は読まない）
_SYMBOL = "SPY225"
_OHLCV_UPDOWN = ["open", "high", "low", "close", "volume", "up", "dn"]


def _ticks(rows) -> pd.DataFrame:
    """``(時刻, bid, ask)`` から生ティック frame を作る（既存検定と同じ形）。"""
    return pd.DataFrame(
        {
            "timestamp": pd.to_datetime([r[0] for r in rows]).tz_localize("UTC"),
            "bidPrice": [r[1] for r in rows],
            "askPrice": [r[2] for r in rows],
        }
    )


def _two_minutes() -> pd.DataFrame:
    """2 分・分ごとの最小幅が 7.12（→ 71）と 7.18（→ 72）になるティック。"""
    return _ticks(
        [
            ("2026-09-01 00:00:05", 63057.6, 63067.6),   # 幅 10.0
            ("2026-09-01 00:00:30", 63057.6, 63064.72),  # 幅 7.12（分 0 の最小）
            ("2026-09-01 00:00:55", 63058.0, 63066.0),   # 幅 8.0
            ("2026-09-01 00:01:10", 63059.0, 63066.18),  # 幅 7.18（分 1 の最小）
            ("2026-09-01 00:01:40", 63059.5, 63069.5),   # 幅 10.0
        ]
    )


def _put_day(data_dir, day, frame: pd.DataFrame) -> None:
    p = tick_m1.day_parquet_path(pd.Timestamp(day), symbol=_SYMBOL, data_dir=data_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(p)


# golden 用の 2 日分のティック（mid 基準で整数に近い値になるよう bid/ask を選んだ）。
_GOLDEN_DAYS = {
    "2026-09-01": [
        ("2026-09-01 00:00:05", 66000.0, 66010.0),
        ("2026-09-01 00:00:30", 66020.0, 66030.0),
        ("2026-09-01 00:00:55", 65990.0, 66000.0),
        ("2026-09-01 00:01:10", 66000.0, 66008.0),
    ],
    "2026-09-02": [
        ("2026-09-02 00:00:05", 66100.0, 66106.0),
    ],
}

# point 無し（既定 mid）の全文。変更前のコードで生成して手計算と一致を確認した値を書き下す
# （mid 66005/66025/65995/65995・volume 3・up 1 dn 1 …）。1 バイトでも変われば既存データの書換。
_GOLDEN_MID_CSV = (
    b"date,open,high,low,close,volume,up,dn\n"
    b"2026-09-01 00:00:00,66005.0,66025.0,65995.0,65995.0,3.0,1.0,1.0\n"
    b"2026-09-01 00:01:00,66004.0,66004.0,66004.0,66004.0,1.0,0.0,0.0\n"
    b"2026-09-02 00:00:00,66103.0,66103.0,66103.0,66103.0,1.0,0.0,0.0\n"
)

# point=0.1 の全文（分内最小幅 10.0 → 100・8.0 → 80・6.0 → 60）。
_GOLDEN_MID_CSV_WITH_SPREAD = (
    b"date,open,high,low,close,volume,up,dn,spread\n"
    b"2026-09-01 00:00:00,66005.0,66025.0,65995.0,65995.0,3.0,1.0,1.0,100\n"
    b"2026-09-01 00:01:00,66004.0,66004.0,66004.0,66004.0,1.0,0.0,0.0,80\n"
    b"2026-09-02 00:00:00,66103.0,66103.0,66103.0,66103.0,1.0,0.0,0.0,60\n"
)


def _put_golden_day(data_dir, day: str) -> None:
    _put_day(data_dir, day, _ticks(_GOLDEN_DAYS[day]))


# =====================================================================
# point 無し＝従来どおり（既存データを書き換えない）
# =====================================================================

@pytest.mark.parametrize("basis", ["mid", "bid"])
def test_without_a_point_there_is_no_spread_column(basis) -> None:
    """``point=None`` なら spread 列は作らない（列は従来の 7 列のまま）。"""
    # Act
    m1 = tick_m1.ticks_to_m1(_two_minutes(), price_basis=basis, point=None)

    # Assert
    assert list(m1.columns) == _OHLCV_UPDOWN


def test_without_a_point_the_built_csv_is_byte_identical_to_the_golden(tmp_path) -> None:
    """``point=None`` の全構築 CSV は従来の全文と 1 バイトも違わない。"""
    # Arrange
    for day in _GOLDEN_DAYS:
        _put_golden_day(tmp_path, day)

    # Act
    out = tick_m1.build_m1_from_ticks(
        pd.Timestamp("2026-09-01"), pd.Timestamp("2026-09-02"),
        symbol=_SYMBOL, ref="golden_none", data_dir=tmp_path, point=None,
    )

    # Assert
    assert out.read_bytes() == _GOLDEN_MID_CSV


# =====================================================================
# point 有り＝分内最小の気配幅（整数 points）
# =====================================================================

def test_with_a_point_each_minute_carries_its_smallest_width_in_points() -> None:
    """分ごとの spread は分内最小幅 / point の最近接整数（書き下し 71・72）。"""
    # Act
    m1 = tick_m1.ticks_to_m1(_two_minutes(), point=_POINT)

    # Assert
    assert m1["spread"].tolist() == [71, 72]


def test_the_spread_does_not_depend_on_the_price_basis() -> None:
    """spread は気配幅なので、価格基準（mid/bid）に依らず同じ。"""
    # Act
    mid = tick_m1.ticks_to_m1(_two_minutes(), price_basis=tick_m1.PRICE_BASIS_MID, point=_POINT)
    bid = tick_m1.ticks_to_m1(_two_minutes(), price_basis=tick_m1.PRICE_BASIS_BID, point=_POINT)

    # Assert
    assert mid["spread"].tolist() == [71, 72]
    assert bid["spread"].tolist() == [71, 72]


def test_an_empty_frame_with_a_point_yields_eight_columns_with_integer_spread() -> None:
    """境界: 空入力 + point は 8 列（末尾 spread・int64）の空フレーム。"""
    # Arrange
    empty = pd.DataFrame({c: [] for c in tick_m1.TICK_COLUMNS})

    # Act
    m1 = tick_m1.ticks_to_m1(empty, point=_POINT)

    # Assert
    assert len(m1) == 0
    assert list(m1.columns) == [*_OHLCV_UPDOWN, "spread"]
    assert m1["spread"].dtype == "int64"
    assert m1.index.name == "date"


@pytest.mark.parametrize("point", [0, -0.1, math.nan], ids=["zero", "negative", "nan"])
def test_an_invalid_point_is_refused_even_for_an_empty_frame(point) -> None:
    """異常系: 空入力でも不正な point は :class:`ValueError`（空だから通す、をしない）。"""
    empty = pd.DataFrame({c: [] for c in tick_m1.TICK_COLUMNS})
    with pytest.raises(ValueError):
        tick_m1.ticks_to_m1(empty, point=point)


def test_an_invalid_point_is_refused_at_the_entrance_of_build_and_append(tmp_path) -> None:
    """異常系: build / append の入口で不正 point を拒否する（集計に届かない経路でも黙って通さない）。

    build はティック 0 本（FileNotFoundError より先）、append は追記すべき日が無い
    （集計を呼ばずに返る）経路で確かめる。
    """
    # build: parquet が 1 つも無い。
    with pytest.raises(ValueError):
        tick_m1.build_m1_from_ticks(
            pd.Timestamp("2026-09-01"), pd.Timestamp("2026-09-01"),
            symbol=_SYMBOL, ref="bad_point", data_dir=tmp_path, point=0,
        )

    # append: 既存 CSV の最終日より前の期間＝追記すべき日が無い。
    _put_golden_day(tmp_path, "2026-09-01")
    out = tick_m1.build_m1_from_ticks(
        pd.Timestamp("2026-09-01"), pd.Timestamp("2026-09-01"),
        symbol=_SYMBOL, ref="bad_point", data_dir=tmp_path, point=_POINT,
    )
    before = out.read_bytes()
    with pytest.raises(ValueError):
        tick_m1.append_m1_from_ticks(
            pd.Timestamp("2026-08-01"), pd.Timestamp("2026-08-31"),
            symbol=_SYMBOL, ref="bad_point", data_dir=tmp_path, point=0,
        )
    assert out.read_bytes() == before


# =====================================================================
# CSV（全構築・追記）
# =====================================================================

def test_a_built_csv_with_a_point_ends_its_header_with_spread_and_writes_integers(tmp_path) -> None:
    """build(point=P) の CSV はヘッダ末尾が ``,spread``・行末は整数表記（書き下しの全文）。"""
    # Arrange
    for day in _GOLDEN_DAYS:
        _put_golden_day(tmp_path, day)

    # Act
    out = tick_m1.build_m1_from_ticks(
        pd.Timestamp("2026-09-01"), pd.Timestamp("2026-09-02"),
        symbol=_SYMBOL, ref="golden_spread", data_dir=tmp_path, point=_POINT,
    )

    # Assert
    assert out.read_bytes() == _GOLDEN_MID_CSV_WITH_SPREAD


def test_appending_with_a_point_keeps_the_existing_bytes_and_adds_spread_to_new_rows(tmp_path) -> None:
    """point 付き build → 翌日追加 → append(point=P): 既存 bytes は接頭辞のまま・新行に spread。"""
    # Arrange: 1 日目だけで build。
    _put_golden_day(tmp_path, "2026-09-01")
    out = tick_m1.build_m1_from_ticks(
        pd.Timestamp("2026-09-01"), pd.Timestamp("2026-09-01"),
        symbol=_SYMBOL, ref="append_spread", data_dir=tmp_path, point=_POINT,
    )
    before = out.read_bytes()
    _put_golden_day(tmp_path, "2026-09-02")

    # Act
    tick_m1.append_m1_from_ticks(
        pd.Timestamp("2026-09-01"), pd.Timestamp("2026-09-02"),
        symbol=_SYMBOL, ref="append_spread", data_dir=tmp_path, point=_POINT,
    )

    # Assert
    after = out.read_bytes()
    assert after.startswith(before)
    assert after[len(before):] == (
        b"2026-09-02 00:00:00,66103.0,66103.0,66103.0,66103.0,1.0,0.0,0.0,60\n"
    )


def test_appending_without_a_point_keeps_the_existing_bytes_and_adds_no_spread(tmp_path) -> None:
    """point 無し build → append(point=None): 接頭辞保存・spread 無し（既存データ書換の回帰遮断）。"""
    # Arrange
    _put_golden_day(tmp_path, "2026-09-01")
    out = tick_m1.build_m1_from_ticks(
        pd.Timestamp("2026-09-01"), pd.Timestamp("2026-09-01"),
        symbol=_SYMBOL, ref="append_none", data_dir=tmp_path, point=None,
    )
    before = out.read_bytes()
    _put_golden_day(tmp_path, "2026-09-02")

    # Act
    tick_m1.append_m1_from_ticks(
        pd.Timestamp("2026-09-01"), pd.Timestamp("2026-09-02"),
        symbol=_SYMBOL, ref="append_none", data_dir=tmp_path, point=None,
    )

    # Assert: 既存 bytes は接頭辞・全文は 2 日分の golden（ヘッダに spread 無し）。
    after = out.read_bytes()
    assert after.startswith(before)
    assert after == _GOLDEN_MID_CSV


def test_append_m1_rows_refuses_spread_rows_on_a_file_without_spread(tmp_path) -> None:
    """spread 無しのファイルへ spread 付き行を積まない（ValueError・ファイルは無傷）。"""
    # Arrange
    _put_golden_day(tmp_path, "2026-09-01")
    out = tick_m1.build_m1_from_ticks(
        pd.Timestamp("2026-09-01"), pd.Timestamp("2026-09-01"),
        symbol=_SYMBOL, ref="rows_refuse", data_dir=tmp_path,
    )
    before = out.read_bytes()
    new_rows = tick_m1.ticks_to_m1(_ticks(_GOLDEN_DAYS["2026-09-02"]), point=_POINT)

    # Act / Assert
    with pytest.raises(ValueError):
        tick_m1.append_m1_rows(new_rows, out)
    assert out.read_bytes() == before


# =====================================================================
# 計算量（Test Spy・発行 − 使用 = 0・回数は焼き込まない）
# =====================================================================

def _spy_spread_computation(monkeypatch) -> "list[int]":
    """``quote_spread.minute_spread_points`` を包み、発行ごとの戻り長（気配幅の数）を記録する。"""
    qs = importlib.import_module("marketdata.quote_spread")
    real = qs.minute_spread_points
    issued: "list[int]" = []

    def spy(*args, **kwargs):
        out = real(*args, **kwargs)
        issued.append(len(out))
        return out

    monkeypatch.setattr(qs, "minute_spread_points", spy)
    return issued


def _minutes_of_ticks(n_minutes: int, per_minute: int) -> pd.DataFrame:
    """``n_minutes`` 分 × 1 分あたり ``per_minute`` 本のティック（bid < ask）。"""
    rows = []
    for m in range(n_minutes):
        base = pd.Timestamp("2026-09-01 00:00:00") + pd.Timedelta(minutes=m)
        for i in range(per_minute):
            t = base + pd.Timedelta(milliseconds=i * (60_000 // per_minute))
            bid = 66000.0 + i * 0.1
            rows.append((t, bid, bid + 7.0 + (i % 3) * 0.1))
    return _ticks(rows)


def _put_days(data_dir, n_days: int) -> "list[pd.Timestamp]":
    """``n_days`` 日 × 2 分 × 3 本のティックを tick 木へ置き、日の列を返す。"""
    days = [pd.Timestamp("2026-09-01") + pd.Timedelta(days=d) for d in range(n_days)]
    for day in days:
        rows = [
            (day + pd.Timedelta(minutes=m, seconds=s), 66000.0 + s * 0.1,
             66007.0 + s * 0.1 + m * 0.1)
            for m in range(2) for s in (5, 30, 55)
        ]
        _put_day(data_dir, day, _ticks(rows))
    return days


@pytest.mark.parametrize("n_days", [2, 4])
@pytest.mark.parametrize("basis", ["mid", "bid"])
def test_no_spread_is_computed_without_a_point(tmp_path, monkeypatch, basis, n_days) -> None:
    """CX: point 無しでは気配幅を 1 つも計算しない（出力に spread が無い＝使用 0）。"""
    # Arrange
    issued = _spy_spread_computation(monkeypatch)
    days = _put_days(tmp_path, n_days)

    # Act
    out = tick_m1.build_m1_from_ticks(
        days[0], days[-1], symbol=_SYMBOL, ref="cx_none", data_dir=tmp_path,
        price_basis=basis, point=None,
    )

    # Assert: 使用 = 0（spread 列が無い）ゆえ発行も 0 でなければ浪費。
    header = list(pd.read_csv(out, nrows=0).columns)
    used = 0
    assert "spread" not in header
    assert sum(issued) - used == 0


def test_every_computed_minute_spread_is_used_in_the_m1(monkeypatch) -> None:
    """CX: 発行した気配幅の数 − M1 の行数 = 0（作ってから捨てない）。"""
    # Arrange
    issued = _spy_spread_computation(monkeypatch)

    # Act
    m1 = tick_m1.ticks_to_m1(_minutes_of_ticks(3, 12), point=_POINT)

    # Assert
    assert len(m1) > 0  # 空振り防止
    assert sum(issued) - len(m1) == 0


def test_computed_spreads_do_not_grow_with_ticks_per_minute(monkeypatch) -> None:
    """CX（オーダー）: 3 分 × 12 本と 3 分 × 120 本で発行数が等しく、両点で 発行 − 使用 = 0。"""
    results = []
    for per_minute in (12, 120):
        issued = _spy_spread_computation(monkeypatch)
        m1 = tick_m1.ticks_to_m1(_minutes_of_ticks(3, per_minute), point=_POINT)
        results.append((sum(issued), len(m1)))

    (small_issued, small_used), (large_issued, large_used) = results
    assert small_used > 0 and large_used > 0  # 空振り防止
    assert small_issued - small_used == 0
    assert large_issued - large_used == 0
    assert large_issued == small_issued, (
        f"1 分あたりのティックを 10 倍にしたら気配幅の発行が {small_issued} → {large_issued}"
        " へ増えました（ティック数に比例する計算が入り込んでいます）。"
    )


def test_computed_spreads_in_a_build_match_the_written_rows(tmp_path, monkeypatch) -> None:
    """CX（オーダー）: build 2 日 / 4 日の両点で 発行した気配幅の数 − CSV の行数 = 0。"""
    for n_days in (2, 4):
        # Arrange
        data_dir = tmp_path / f"days{n_days}"
        issued = _spy_spread_computation(monkeypatch)
        days = _put_days(data_dir, n_days)

        # Act
        out = tick_m1.build_m1_from_ticks(
            days[0], days[-1], symbol=_SYMBOL, ref="cx_build", data_dir=data_dir,
            point=_POINT,
        )

        # Assert
        rows = len(out.read_text(encoding="utf-8").splitlines()) - 1
        assert rows > 0  # 空振り防止
        assert sum(issued) - rows == 0, f"{n_days} 日: 発行 {sum(issued)} − 行数 {rows} ≠ 0"


# =====================================================================
# 計算量: 行の選択（>last_date・until・外れ分除去）の後で気配幅を計算する（R-1）
# =====================================================================

def _spy_spread_io(monkeypatch) -> "list[tuple[int, int]]":
    """``quote_spread.minute_spread_points`` を包み、発行ごとに (入力ティック数, 出力分数) を記録する。"""
    qs = importlib.import_module("marketdata.quote_spread")
    real = qs.minute_spread_points
    calls: "list[tuple[int, int]]" = []

    def spy(*args, **kwargs):
        bid = args[0] if args else kwargs["bid"]
        out = real(*args, **kwargs)
        calls.append((len(bid), len(out)))
        return out

    monkeypatch.setattr(qs, "minute_spread_points", spy)
    return calls


def _minute_rows(day: pd.Timestamp, minutes, *, bid0: float = 66000.0, per_minute: int = 3):
    """``minutes``（日の先頭からの分番号）ごとに ``per_minute`` 本の ``(時刻, bid, ask)``。"""
    rows = []
    for m in minutes:
        base = day + pd.Timedelta(minutes=m)
        for j in range(per_minute):
            bid = bid0 + m * 0.1 + j * 0.1
            rows.append((base + pd.Timedelta(seconds=5 + j * 20), bid, bid + 7.0 + j * 0.1))
    return rows


def _ticks_in_minutes(ticks: pd.DataFrame, minutes) -> int:
    """fixture のうち ``minutes``（naive UTC の分）に属するティック数。"""
    floor = pd.to_datetime(ticks["timestamp"]).dt.tz_convert("UTC").dt.tz_localize(None).dt.floor("min")
    return int(floor.isin(pd.DatetimeIndex(minutes)).sum())


def _csv_minutes(lines: "list[str]") -> "list[pd.Timestamp]":
    """ヘッダ無しの CSV 行から date を取り出す。"""
    return [pd.Timestamp(line.split(",", 1)[0]) for line in lines if line]


def test_append_computes_spreads_only_for_the_appended_minutes(tmp_path, monkeypatch) -> None:
    """CX（R-1）: 追記は既存最終分より後の分だけ気配幅を計算する（当日を丸ごと計算しない）。

    当日 n 分のうち n − 1 分を build 済みにし、残り 1 分を append する。n = 60 と 600 の 2 点で
    発行 − 追記行数 = 0・入力ティック − 追記分のティック = 0、かつ発行が n に依らないこと。
    """
    day = pd.Timestamp("2026-09-01")
    issued_minutes = []
    for n in (60, 600):
        # Arrange: 当日 n 分 × 3 本。build は until で最終分を形成中として落とす（n − 1 行）。
        data_dir = tmp_path / f"n{n}"
        ticks = _ticks(_minute_rows(day, range(n)))
        _put_day(data_dir, day, ticks)
        out = tick_m1.build_m1_from_ticks(
            day, day, symbol=_SYMBOL, ref="cx_append", data_dir=data_dir,
            until=day + pd.Timedelta(minutes=n - 1), point=_POINT,
        )
        before = out.read_text(encoding="utf-8")
        calls = _spy_spread_io(monkeypatch)

        # Act
        tick_m1.append_m1_from_ticks(
            day, day, symbol=_SYMBOL, ref="cx_append", data_dir=data_dir, point=_POINT,
        )

        # Assert
        after = out.read_text(encoding="utf-8")
        assert after.startswith(before)
        appended = _csv_minutes(after[len(before):].splitlines())
        issued_in = sum(c[0] for c in calls)
        issued_out = sum(c[1] for c in calls)
        assert len(appended) > 0  # 空振り防止
        assert issued_out - len(appended) == 0, (
            f"n={n}: 気配幅を {issued_out} 分計算し {len(appended)} 行だけ追記しました。"
        )
        assert issued_in - _ticks_in_minutes(ticks, appended) == 0, (
            f"n={n}: 追記しない分のティック {issued_in - _ticks_in_minutes(ticks, appended)} 本を読みました。"
        )
        issued_minutes.append(issued_out)

    assert issued_minutes[0] == issued_minutes[1], (
        f"当日の分数を 60 → 600 にしたら気配幅の発行が {issued_minutes[0]} → {issued_minutes[1]}"
        " へ増えました（追記しない分まで計算しています）。"
    )


def _outlier_and_forming_day(day: pd.Timestamp, n_forming: int) -> pd.DataFrame:
    """正常 5 分（0,1,3,4,5）＋外れ 1 分（2・日内 close 中央値から約 −55%）＋形成中 n_forming 分（6..）。"""
    rows = (
        _minute_rows(day, [0, 1, 3, 4, 5])
        + _minute_rows(day, [2], bid0=30000.0)  # ±30% 超＝outlier_policy.repair_day_outliers が除去
        + _minute_rows(day, range(6, 6 + n_forming))
    )
    return _ticks(sorted(rows))


def test_build_computes_spreads_only_for_the_written_minutes(tmp_path, monkeypatch) -> None:
    """CX（R-1）: build は外れ分除去と until の後に残る分だけ気配幅を計算する。

    書かれる行はどちらも 5。形成中 2 分と 50 分の 2 点で 発行 − 行数 = 0・
    入力ティック − 書いた分のティック = 0、かつ発行が形成中の分数に依らないこと。
    """
    day = pd.Timestamp("2026-09-01")
    until = day + pd.Timedelta(minutes=6)
    issued_minutes = []
    for n_forming in (2, 50):
        # Arrange
        data_dir = tmp_path / f"forming{n_forming}"
        ticks = _outlier_and_forming_day(day, n_forming)
        _put_day(data_dir, day, ticks)
        # 前提: 集計した分のうち書かれない分が実在する（Spy 前・point 無しで発行 0）。
        m1_all = tick_m1.ticks_to_m1(ticks)
        calls = _spy_spread_io(monkeypatch)

        # Act
        out = tick_m1.build_m1_from_ticks(
            day, day, symbol=_SYMBOL, ref="cx_build_drop", data_dir=data_dir,
            until=until, point=_POINT,
        )

        # Assert
        written = _csv_minutes(out.read_text(encoding="utf-8").splitlines()[1:])
        assert len(m1_all) - len(written) > 0  # 捨てる分が実在する（空振り防止）
        assert len(written) > 0
        issued_in = sum(c[0] for c in calls)
        issued_out = sum(c[1] for c in calls)
        assert issued_out - len(written) == 0, (
            f"形成中 {n_forming} 分: 気配幅を {issued_out} 分計算し {len(written)} 行だけ書きました。"
        )
        assert issued_in - _ticks_in_minutes(ticks, written) == 0, (
            f"形成中 {n_forming} 分: 書かない分のティック"
            f" {issued_in - _ticks_in_minutes(ticks, written)} 本を読みました。"
        )
        issued_minutes.append(issued_out)

    assert issued_minutes[0] == issued_minutes[1], (
        f"形成中の分を 2 → 50 にしたら気配幅の発行が {issued_minutes[0]} → {issued_minutes[1]}"
        " へ増えました（書かない分まで計算しています）。"
    )


def test_no_spread_is_computed_when_every_minute_is_forming(tmp_path, monkeypatch) -> None:
    """CX（R-1・境界）: 全分が形成中（until が最初の分より前）なら気配幅を 1 つも計算しない。"""
    # Arrange
    day = pd.Timestamp("2026-09-01")
    _put_day(tmp_path, day, _ticks(_minute_rows(day, range(3))))
    calls = _spy_spread_io(monkeypatch)

    # Act
    out = tick_m1.build_m1_from_ticks(
        day, day, symbol=_SYMBOL, ref="cx_all_forming", data_dir=tmp_path,
        until=day - pd.Timedelta(minutes=1), point=_POINT,
    )

    # Assert: 書いた行 0 ＝使用 0。
    written = len(out.read_text(encoding="utf-8").splitlines()) - 1
    assert written == 0
    assert sum(c[1] for c in calls) - written == 0


@pytest.mark.parametrize(
    ("point", "expected"),
    [
        (_POINT, b"date,open,high,low,close,volume,up,dn,spread\n"),
        (None, b"date,open,high,low,close,volume,up,dn\n"),
    ],
    ids=["with_point", "without_point"],
)
def test_a_build_whose_every_minute_is_forming_writes_only_the_header(
    tmp_path, point, expected
) -> None:
    """境界（回帰固定）: 全分が形成中の build はヘッダ 1 行だけを書く（point 有無で spread 列の有無）。"""
    # Arrange
    day = pd.Timestamp("2026-09-01")
    _put_day(tmp_path, day, _ticks(_minute_rows(day, range(3))))

    # Act
    out = tick_m1.build_m1_from_ticks(
        day, day, symbol=_SYMBOL, ref="all_forming", data_dir=tmp_path,
        until=day - pd.Timedelta(minutes=1), point=point,
    )

    # Assert
    assert out.read_bytes() == expected


# =====================================================================
# 行選択の順序（外れ分除去 → after/until）の回帰固定（Y-1）
# =====================================================================

def _append_then_build(data_dir, point) -> "tuple[bytes, bytes]":
    """当日 5 分を build 済み → 外れ 1 分（−55%）を足して append した全文と、同じ範囲の一括 build の全文。"""
    day = pd.Timestamp("2026-09-01")
    _put_day(data_dir, day, _ticks(_minute_rows(day, range(5))))
    appended = tick_m1.build_m1_from_ticks(
        day, day, symbol=_SYMBOL, ref="y1_append", data_dir=data_dir, point=point,
    )
    # 外れ分（分 5）は既存最終分（分 4）より後＝ after で残る行。日内 close 中央値は正常 5 分が決める。
    _put_day(data_dir, day, _ticks(sorted(
        _minute_rows(day, range(5)) + _minute_rows(day, [5], bid0=30000.0)
    )))
    tick_m1.append_m1_from_ticks(
        day, day, symbol=_SYMBOL, ref="y1_append", data_dir=data_dir, point=point,
    )
    built = tick_m1.build_m1_from_ticks(
        day, day, symbol=_SYMBOL, ref="y1_build", data_dir=data_dir, point=point,
    )
    return appended.read_bytes(), built.read_bytes()


@pytest.mark.parametrize("point", [_POINT, None], ids=["with_point", "without_point"])
def test_append_removes_an_outlier_minute_against_the_whole_day_like_build(tmp_path, point) -> None:
    """回帰固定（Y-1）: 外れ分除去は当日の全分で判定してから after/until で選ぶ（append == build）。

    行選択を除去より前へ移すと、追記候補の外れ 1 分だけで中央値を取るため除去されず追記され、
    一括 build と食い違う。
    """
    # Arrange / Act
    appended, built = _append_then_build(tmp_path, point)

    # Assert
    assert appended == built


# =====================================================================
# 計算量: 日境界 00:00:00 の重複分（ISSUE-521・既知浪費の機械的検査・Y-5）
# =====================================================================

_Y5_DAY1 = pd.Timestamp("2026-09-01")
_Y5_DAY2 = pd.Timestamp("2026-09-02")


def _put_boundary_duplicated_days(data_dir) -> None:
    """2 日の tick 木。前日ファイルに翌日 00:00:00 ちょうどのティックも入れる（境界分が 2 ファイルに重複）。"""
    day1 = _minute_rows(_Y5_DAY1, range(3)) + [(_Y5_DAY2, 66000.3, 66007.3)]
    _put_day(data_dir, _Y5_DAY1, _ticks(day1))
    _put_day(data_dir, _Y5_DAY2, _ticks(_minute_rows(_Y5_DAY2, range(3))))


def _files_holding_minute(data_dir, minute: pd.Timestamp) -> int:
    """tick 木の日ファイルのうち ``minute`` のティックを持つものの数。"""
    files = tick_m1.day_parquet_files(_Y5_DAY1, _Y5_DAY2, symbol=_SYMBOL, data_dir=data_dir)
    return sum(_ticks_in_minutes(pd.read_parquet(p), [minute]) > 0 for p in files)


def _build_boundary_days(data_dir) -> "list[str]":
    """境界重複 fixture を point 付きで build し、ヘッダを除く CSV 行を返す。"""
    out = tick_m1.build_m1_from_ticks(
        _Y5_DAY1, _Y5_DAY2, symbol=_SYMBOL, ref="y5_boundary", data_dir=data_dir, point=_POINT,
    )
    return out.read_text(encoding="utf-8").splitlines()[1:]


def _rows_at(rows: "list[str]", minute: pd.Timestamp) -> int:
    """CSV 行（ヘッダ無し）のうち date が ``minute`` の行数。"""
    return _csv_minutes(rows).count(minute)


def test_the_boundary_fixture_puts_one_minute_in_two_day_files_and_writes_it_once(tmp_path) -> None:
    """前提（Y-5 の空振り防止）: 境界分は 2 ファイルに在り、CSV には 1 行だけ書かれる（ISSUE-167）。"""
    # Arrange
    _put_boundary_duplicated_days(tmp_path)

    # Act
    rows = _build_boundary_days(tmp_path)

    # Assert
    assert _files_holding_minute(tmp_path, _Y5_DAY2) == 2
    assert _rows_at(rows, _Y5_DAY2) == 1


@pytest.mark.xfail(strict=True, reason="ISSUE-521: 日境界の重複分の spread を dedupe 前に計算している")
def test_no_spread_is_computed_for_the_duplicated_day_boundary_minute(tmp_path, monkeypatch) -> None:
    """CX（ISSUE-521）: 境界分が 2 日ファイルに重複しても 発行した気配幅の分数 − CSV 行数 = 0。

    現行は日ごとに気配幅を計算してから concat 後の dedupe で重複行を捨てるため、捨てる行の
    気配幅を計算している（期待どおり失敗＝xfail）。直れば XPASS となり strict で落ちる
    ＝本 xfail の解除が強制される。
    """
    # Arrange
    _put_boundary_duplicated_days(tmp_path)
    issued = _spy_spread_computation(monkeypatch)

    # Act
    rows = _build_boundary_days(tmp_path)

    # Assert
    assert len(rows) > 0  # 空振り防止
    assert sum(issued) - len(rows) == 0, f"発行 {sum(issued)} − 行数 {len(rows)} ≠ 0"
