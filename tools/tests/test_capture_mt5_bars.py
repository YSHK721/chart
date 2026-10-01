"""``tools/capture_mt5_bars.py`` の単体検定（MetaTrader5 不在の Linux で全緑）。

本スクリプトは依頼者が Windows VM（OANDA-Japan MT5 Live にログイン済みの端末）で実行し、
MT5 の上位足を既存エクスポートと同じ書式の CSV へ落とす（ISSUE-511 前提 (b)・段階 1）。
コンテナには端末が無いので、端末は fake を ``main(argv, mt5=..., now=...)`` へ注入して固定する。

固定する不変条件:

- R-1 書式は既存エクスポートの実物（参照実装）と **byte 一致**する（見出し・CRLF・BOM・桁）。
- R-2 価格の桁は ``symbol_info().digits`` で決まる（桁のリテラルを持たない）。
- R-3 書いた CSV を読み側 ``Mt5CsvOHLCRepository`` で読み戻すと元の値に戻る。
- R-4 端末へ渡す datetime の作り方は ``tools/mt5_tick_feed.py`` と同じ規則である
  （規則を複製した側を検定で縛る）。
- R-5/R-6 前提が崩れたら終了コード 2 で止まり、何も書かない。サーバ違いは足を 1 本も取らない。
- R-7/R-8 出力パスの規約・既存ファイルの保護・manifest の sha256 と行数。
- R-9 実行計画が ISSUE-511 の仕様（3 期間・時間足・J-2 の終端）と一致する。
- R-10 構造（端末 API は許可集合の中だけ・属性名を組み立てない・発注系を参照しない・
  ``getattr`` 以外の動的属性アクセスを使わない・import は stdlib のみ・本体が施行を名乗る
  項目の宣言と走査の登録簿と検出力の実測が一致する）。
- R-11 CSV と manifest が git の改行変換を受けない（J-3）。
- CX-1〜4 計算量（取った足はすべて書く・組ごとに 1 回だけ取る・接続は組数に比例しない・
  書き出すバイト列を組ごとに 1 回だけ作る）。
"""
from __future__ import annotations

import ast
import hashlib
import json
import os
import subprocess
import sys
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from simulator.adapter.repository.ohlc_mt5_csv import Mt5CsvOHLCRepository
from tools import capture_mt5_bars as cap
from tools import mt5_tick_feed as feed

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SOURCE = _REPO_ROOT / "tools" / "capture_mt5_bars.py"
_EXPORTS = _REPO_ROOT / "simulator" / "tests" / "fixtures" / "mt5" / "ma_slope_jp225_202501" / "input"
_BARS_FIXTURE_DIR = "simulator/tests/fixtures/mt5_bars"

_AT = datetime(2026, 9, 15, 12, 0, 0, tzinfo=timezone.utc)
_LIVE_SERVER = "OANDA-Japan MT5 Live"
_HEADER = b"<DATE>\t<TIME>\t<OPEN>\t<HIGH>\t<LOW>\t<CLOSE>\t<TICKVOL>\t<VOL>\t<SPREAD>\r\n"

#: 仕様（ISSUE-511 前提 (b)・J-2 で終端を含む＝半開区間 [from, to) のサーバラベル）。
_NINE = ("M1", "M5", "M15", "M30", "H1", "H4", "D1", "W1", "MN1")
_SPEC = (
    ("20241229_20250201", (2024, 12, 29), (2025, 2, 1), _NINE),
    ("20260323_20260501", (2026, 3, 23), (2026, 5, 1), _NINE),
    ("20200501_20260901", (2020, 5, 1), (2026, 9, 1), ("D1", "W1", "MN1")),
)

#: MT5 が返す足の構造化配列の列（公式ドキュメントの列名。型は推論）。
_RATES_DTYPE = [
    ("time", "<i8"), ("open", "<f8"), ("high", "<f8"), ("low", "<f8"), ("close", "<f8"),
    ("tick_volume", "<u8"), ("spread", "<i4"), ("real_volume", "<u8"),
]


def _label(y, mo, d, h=0, mi=0, s=0) -> int:
    """サーバ時刻ラベルを UTC とみなした epoch 秒（端末の戻り値 ``time`` と同じ意味）。"""
    return int(datetime(y, mo, d, h, mi, s, tzinfo=timezone.utc).timestamp())


def _bar(t, o=100.0, h=None, lo=None, c=None, tv=1, rv=0, sp=0) -> dict:
    return {
        "time": t, "open": o, "high": o if h is None else h, "low": o if lo is None else lo,
        "close": o if c is None else c, "tick_volume": tv, "real_volume": rv, "spread": sp,
    }


def _terminal_epoch(naive: datetime) -> int:
    """MetaTrader5 パッケージと同じ規則で naive datetime を epoch 秒へ戻す（V-1 実測）。"""
    return int(time.mktime(naive.timetuple()))


def _evenly(count: int):
    """要求窓 [from, last] に ``count`` 本を等間隔で置く足の供給規則。"""
    def rates(index, from_s, last_s):
        step = (last_s - from_s + 1) // count
        return [_bar(from_s + i * step, o=100.0 + i * 0.1) for i in range(count)]
    return rates


class FakeTerminal:
    """MetaTrader5 モジュールの代役（読み取りのみ・端末に触れない）。

    ``rates(index, from_s, last_s)`` が ``copy_rates_range`` の戻り値を決める。
    ``index`` は何回目の呼出か、``from_s`` / ``last_s`` は端末が受け取った datetime を
    パッケージと同じ規則で epoch 秒へ戻した値である。
    """

    TIMEFRAME_M1, TIMEFRAME_M5, TIMEFRAME_M15, TIMEFRAME_M30 = 1, 5, 15, 30
    TIMEFRAME_H1, TIMEFRAME_H4, TIMEFRAME_D1 = 16385, 16388, 16408
    TIMEFRAME_W1, TIMEFRAME_MN1 = 32769, 49153

    def __init__(self, rates=None, *, server=_LIVE_SERVER, digits=1, initialize_ok=True,
                 select_ok=True, symbol_info_ok=True, account_ok=True, build=5200,
                 version="5.0.5120"):
        self._rates = rates or _evenly(1)
        self._server = server
        self._digits = digits
        self._initialize_ok = initialize_ok
        self._select_ok = select_ok
        self._symbol_info_ok = symbol_info_ok
        self._account_ok = account_ok
        self._build = build
        self.__version__ = version
        self.calls: "list[str]" = []
        #: 端末へ渡された (時間足, from, to)。
        self.requested: "list[tuple]" = []
        #: 端末が返した足の本数。
        self.served: "list[int]" = []

    def count(self, name: str) -> int:
        return self.calls.count(name)

    def initialize(self):
        self.calls.append("initialize")
        return self._initialize_ok

    def shutdown(self):
        self.calls.append("shutdown")

    def last_error(self):
        return (1, "Success")

    def account_info(self):
        self.calls.append("account_info")
        return SimpleNamespace(server=self._server) if self._account_ok else None

    def terminal_info(self):
        self.calls.append("terminal_info")
        return SimpleNamespace(build=self._build, name="OANDA MetaTrader 5")

    def symbol_select(self, symbol, enable=True):
        self.calls.append("symbol_select")
        return self._select_ok

    def symbol_info(self, symbol):
        self.calls.append("symbol_info")
        return SimpleNamespace(name=symbol, digits=self._digits) if self._symbol_info_ok else None

    def copy_rates_range(self, symbol, timeframe, date_from, date_to):
        index = self.count("copy_rates_range")
        self.calls.append("copy_rates_range")
        self.requested.append((timeframe, date_from, date_to))
        rates = self._rates(index, _terminal_epoch(date_from), _terminal_epoch(date_to))
        self.served.append(0 if rates is None else len(rates))
        return rates


def _run(tmp_path, fake, capsys=None):
    rc = cap.main(["--out-root", str(tmp_path)], mt5=fake, now=_AT)
    err = capsys.readouterr().err if capsys is not None else ""
    return rc, err


def _written(tmp_path) -> "list[Path]":
    return sorted(p for p in tmp_path.rglob("*") if p.is_file())


def _data_rows(path: Path) -> int:
    return len(path.read_bytes().split(b"\r\n")) - 2


def _expected_relative_paths() -> "set[str]":
    return {
        f"mt5_bars/{pid}/JP225_{tf}_{pid}.csv" for pid, _, _, tfs in _SPEC for tf in tfs
    } | {"mt5_bars/manifest.json"}


# =====================================================================
# R-1 / R-2 / R-3 書式
# =====================================================================

def _rates_from_export(data: bytes) -> "list[dict]":
    """既存エクスポートの実物を、端末が返す足（ラベル epoch 秒）へ読み戻す。"""
    rows = []
    for line in data.split(b"\r\n")[1:-1]:
        d, t, o, h, lo, c, tv, rv, sp = line.decode("ascii").split("\t")
        stamp = datetime.strptime(f"{d} {t}", "%Y.%m.%d %H:%M:%S").replace(tzinfo=timezone.utc)
        rows.append(_bar(int(stamp.timestamp()), float(o), float(h), float(lo), float(c),
                         int(tv), int(rv), int(sp)))
    return rows


def _as_structured(rows: "list[dict]") -> np.ndarray:
    arr = np.zeros(len(rows), dtype=_RATES_DTYPE)
    for i, row in enumerate(rows):
        for name, _ in _RATES_DTYPE:
            arr[i][name] = row[name]
    return arr


@pytest.mark.parametrize("container", ["dicts", "structured_array"])
@pytest.mark.parametrize(
    "export", ["JP225_M1_202501.csv", "JP225_M1_202412230100_202501302359.csv"]
)
def test_format_rows_reproduces_the_existing_mt5_export_byte_for_byte(export, container):
    """R-1: 参照実装（端末の既存エクスポート）と 1 byte も違わない。

    端末の実物は numpy の構造化配列を返すので、dict 列と構造化配列の両方で固定する。
    """
    expected = (_EXPORTS / export).read_bytes()
    rows = _rates_from_export(expected)
    rates = {"dicts": rows, "structured_array": _as_structured(rows)}[container]

    produced = cap.format_rows(rates, 1)

    assert produced == expected


@pytest.mark.parametrize(
    "digits, prices",
    [(2, b"100.50\t101.25\t100.00\t101.00"), (3, b"100.500\t101.250\t100.000\t101.000")],
)
def test_price_digits_come_from_the_terminal_symbol_info(tmp_path, monkeypatch, digits, prices):
    """R-2: 桁は ``symbol_info().digits`` から来る。VOL は real_volume、SPREAD は spread。"""
    monkeypatch.setattr(cap, "PLAN", cap.PLAN[:1])
    start = _label(2024, 12, 29)
    fake = FakeTerminal(
        lambda i, f, l: [_bar(start, 100.5, 101.25, 100.0, 101.0, tv=3, rv=4, sp=7)],
        digits=digits,
    )

    rc, _ = _run(tmp_path, fake)

    out = tmp_path / "mt5_bars" / "20241229_20250201" / "JP225_M1_20241229_20250201.csv"
    assert rc == 0
    assert out.read_bytes() == _HEADER + b"2024.12.29\t00:00:00\t" + prices + b"\t3\t4\t7\r\n"


def test_written_rows_read_back_through_the_simulator_mt5_reader(tmp_path):
    """R-3: 読み側（``Mt5CsvOHLCRepository``）で読み戻すと元の値に戻る。"""
    rows = [
        _bar(_label(2025, 1, 2, 1, 0), 39400.5, 39447.0, 39400.5, 39447.0, tv=9, sp=100),
        _bar(_label(2025, 1, 2, 23, 59), 38961.7, 38966.7, 38956.7, 38961.7, tv=26, sp=50),
        _bar(_label(2025, 1, 3), 39402.0, 39402.0, 39402.0, 39402.0, tv=1, sp=480),
    ]
    path = tmp_path / "bars.csv"
    path.write_bytes(cap.format_rows(rows, 1))

    bars = Mt5CsvOHLCRepository().load(path)

    assert [(b.time, b.open, b.high, b.low, b.close, b.volume, b.spread) for b in bars] == [
        (np.datetime64("2025-01-02T01:00:00"), 39400.5, 39447.0, 39400.5, 39447.0, 9.0, 100),
        (np.datetime64("2025-01-02T23:59:00"), 38961.7, 38966.7, 38956.7, 38961.7, 26.0, 50),
        (np.datetime64("2025-01-03T00:00:00"), 39402.0, 39402.0, 39402.0, 39402.0, 1.0, 480),
    ]


# =====================================================================
# R-4 端末へ渡す時刻の規則（tools/mt5_tick_feed.py と同じ）
# =====================================================================

@contextmanager
def _local_timezone(name: str):
    """プロセスのローカル tz を据える（環境の TZ に判定を依存させないため）。"""
    previous = os.environ.get("TZ")
    os.environ["TZ"] = name
    time.tzset()
    try:
        yield
    finally:
        os.environ.pop("TZ", None)
        os.environ.update({} if previous is None else {"TZ": previous})
        time.tzset()


class _TickFeedTerminal:
    """``read_tick_window`` が端末へ渡す datetime を記録するだけの代役。"""

    COPY_TICKS_INFO = 1

    def __init__(self):
        self.requested: "list[datetime]" = []

    def symbol_select(self, symbol, enable=True):
        return True

    def copy_ticks_from(self, symbol, frm, count, flags):
        self.requested.append(frm)
        return np.zeros(0, dtype=[("time_msc", "<i8")])

    def last_error(self):
        return (1, "Success")


def _feed_delivers(label_s: int) -> datetime:
    terminal = _TickFeedTerminal()
    feed.read_tick_window(
        terminal, symbol="JP225", from_msc=label_s * 1000, to_msc=None, max_rows=1
    )
    return terminal.requested[0]


@pytest.mark.parametrize("tz", ["UTC", "Asia/Tokyo", "America/New_York"])
def test_terminal_datetime_follows_the_tick_feed_rule(tz):
    """R-4: 計画の全境界（from と to−1 秒）で、tick feed が端末へ渡す datetime と一致する。"""
    edges = sorted({e for j in cap.PLAN for e in (j.from_s, j.to_s - 1)})

    with _local_timezone(tz):
        ours = [cap.terminal_datetime(e) for e in edges]
        theirs = [_feed_delivers(e) for e in edges]

    assert len(edges) == 6
    assert ours == theirs


def test_the_terminal_receives_the_window_from_to_minus_one_second(tmp_path):
    """要求窓は [切り下げた from, to−1 秒]（J-2: 終端の日を含み、次の期間の先頭を含まない）。"""
    fake = FakeTerminal()

    rc, _ = _run(tmp_path, fake)

    delivered = [(_terminal_epoch(f), _terminal_epoch(t)) for _, f, t in fake.requested]
    assert rc == 0
    assert delivered == [(_aligned_from(j), j.to_s - 1) for j in cap.PLAN]


# =====================================================================
# R-12 要求窓の始端を足のラベル境界へ切り下げる
# =====================================================================

#: 切り下げ後の要求始端（期待値。実装の関数を使わずに literal で固定する）。
#: 曜日の実測: 2024-12-29=日曜・2026-03-23=月曜・2020-05-01=金曜。
_ALIGNED = {
    ("20241229_20250201", "MN1"): _label(2024, 12, 1),
    ("20260323_20260501", "W1"): _label(2026, 3, 22),
    ("20260323_20260501", "MN1"): _label(2026, 3, 1),
    ("20200501_20260901", "W1"): _label(2020, 4, 26),
}


def _aligned_from(job) -> int:
    """その組の要求始端（切り下げが要らない組は期間の始端のまま）。"""
    return _ALIGNED.get((job.period_id, job.timeframe), job.from_s)


def _job(period_id: str, timeframe: str):
    return next(j for j in cap.PLAN if (j.period_id, j.timeframe) == (period_id, timeframe))


def _text(label_s: int) -> str:
    return datetime.fromtimestamp(label_s, timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _month_labels(start_s: int, to_s: int) -> "list[int]":
    out, moment = [], datetime.fromtimestamp(start_s, timezone.utc)
    while int(moment.timestamp()) < to_s:
        out.append(int(moment.timestamp()))
        moment = datetime(moment.year + (moment.month == 12), moment.month % 12 + 1, 1,
                          tzinfo=timezone.utc)
    return out


def _label_grid(timeframe: str, start_s: int, to_s: int) -> "list[int]":
    """その時間足の足ラベルの並び（切り下げ後の始端から期間の終端の手前まで）。"""
    if timeframe == "MN1":
        return _month_labels(start_s, to_s)
    return list(range(start_s, to_s, 7 * 86400))


def _terminal_serving(labels, *, honours_date_from: bool):
    """端末の代役。``honours_date_from`` は公式の規則（open time >= date_from）に従う。

    従わせない側は「ラベルが切り下げ後の窓の中にある足を返す」端末（規則は未検証なので
    両方の挙動で成立させる）。
    """
    def rates(index, from_s, last_s):
        served = [t for t in labels if from_s <= t <= last_s] if honours_date_from else list(labels)
        return [_bar(t, o=100.0 + i * 0.1) for i, t in enumerate(served)]
    return rates


@pytest.mark.parametrize(
    "period_id, timeframe", sorted({(j.period_id, j.timeframe) for j in cap.PLAN})
)
def test_the_requested_start_is_floored_to_the_label_boundary(period_id, timeframe):
    """R-12: 21 組すべてで要求始端がラベル境界。揃っている組では期間の始端のまま。"""
    job = _job(period_id, timeframe)

    assert cap.aligned_from(timeframe, job.from_s) == _aligned_from(job)


@pytest.mark.parametrize(
    "timeframe, moment, expected",
    [
        ("M1", (2026, 3, 23, 10, 30, 45), (2026, 3, 23, 10, 30, 0)),
        ("M5", (2026, 3, 23, 10, 33, 45), (2026, 3, 23, 10, 30, 0)),
        ("M15", (2026, 3, 23, 10, 44, 59), (2026, 3, 23, 10, 30, 0)),
        ("M30", (2026, 3, 23, 10, 30, 1), (2026, 3, 23, 10, 30, 0)),
        ("H1", (2026, 3, 23, 10, 0, 1), (2026, 3, 23, 10, 0, 0)),
        ("H4", (2026, 3, 23, 10, 30, 45), (2026, 3, 23, 8, 0, 0)),
        ("H4", (2026, 3, 23, 8, 0, 0), (2026, 3, 23, 8, 0, 0)),
        ("H4", (2026, 3, 23, 23, 59, 59), (2026, 3, 23, 20, 0, 0)),
        ("D1", (2026, 3, 23, 23, 59, 59), (2026, 3, 23, 0, 0, 0)),
        ("W1", (2026, 3, 22, 0, 0, 0), (2026, 3, 22, 0, 0, 0)),
        ("W1", (2026, 3, 28, 23, 59, 59), (2026, 3, 22, 0, 0, 0)),
        ("MN1", (2026, 3, 31, 23, 59, 59), (2026, 3, 1, 0, 0, 0)),
        ("MN1", (2026, 1, 1, 0, 0, 0), (2026, 1, 1, 0, 0, 0)),
    ],
)
def test_flooring_puts_every_timeframe_on_its_own_grid(timeframe, moment, expected):
    """R-12 境界値: 各時間足の格子（H4 はサーバ日 00:00 起点・W1 は日曜・MN1 は 1 日）。"""
    assert cap.aligned_from(timeframe, _label(*moment)) == _label(*expected)


def test_an_unknown_timeframe_has_no_silent_flooring_rule():
    """R-12: 規則の無い時間足を黙って素通りさせない。"""
    with pytest.raises(cap.CaptureError):
        cap.aligned_from("M2", _label(2026, 3, 23))


@pytest.mark.parametrize(
    "honours_date_from", [True, False], ids=["honours_date_from", "serves_the_covering_bar"]
)
@pytest.mark.parametrize("period_id, timeframe", sorted(_ALIGNED))
def test_the_bar_covering_the_period_start_is_captured(
    tmp_path, monkeypatch, capsys, period_id, timeframe, honours_date_from
):
    """R-12: 始端がラベルの途中に当たる 4 組が、端末の 2 通りの挙動のどちらでも全行書かれる。"""
    job = _job(period_id, timeframe)
    monkeypatch.setattr(cap, "PLAN", (job,))
    labels = _label_grid(timeframe, _ALIGNED[(period_id, timeframe)], job.to_s)
    fake = FakeTerminal(_terminal_serving(labels, honours_date_from=honours_date_from))

    rc, err = _run(tmp_path, fake, capsys)

    root = tmp_path / "mt5_bars"
    out = root / period_id / f"JP225_{timeframe}_{period_id}.csv"
    rows = out.read_bytes().split(b"\r\n")[1:-1] if out.exists() else []
    entry = json.loads((root / "manifest.json").read_bytes())["files"][0] if rc == 0 else {}
    assert (rc, err) == (0, "")
    assert len(rows) == len(labels) >= 2
    assert rows[0].split(b"\t")[:2] == [
        datetime.fromtimestamp(labels[0], timezone.utc).strftime("%Y.%m.%d").encode(), b"00:00:00"
    ]
    assert sum(fake.served) - len(rows) == 0
    assert (entry["from_label"], entry["to_label_exclusive"]) == (_text(job.from_s), _text(job.to_s))
    assert (entry["request_from_label"], entry["request_to_label_exclusive"]) == (
        _text(labels[0]), _text(job.to_s)
    )


# =====================================================================
# R-5 / R-6 Fail-Stop（終了コード 2・何も書かない）
# =====================================================================

def _second_call(anomaly):
    """2 組目だけを異常にする（1 組目の正常な取得も書かれないことを同時に固定する）。"""
    def rates(index, from_s, last_s):
        return anomaly(from_s, last_s) if index == 1 else [_bar(from_s)]
    return rates


_ANOMALIES = {
    "bar_before_the_window": lambda f, l: [_bar(f - 60), _bar(f)],
    "bar_at_the_exclusive_end": lambda f, l: [_bar(f), _bar(l + 1)],
    "zero_bars": lambda f, l: [],
    "none": lambda f, l: None,
    "time_goes_backwards": lambda f, l: [_bar(f + 60), _bar(f)],
    "duplicate_time": lambda f, l: [_bar(f), _bar(f)],
}


@pytest.mark.parametrize("anomaly", sorted(_ANOMALIES))
def test_anomalous_rates_stop_with_exit_2_and_write_nothing(tmp_path, monkeypatch, capsys, anomaly):
    """R-5: 窓の外（捨てずに止まる）・0 本・None・非単調は終了コード 2、何も書かない。"""
    monkeypatch.setattr(cap, "PLAN", cap.PLAN[:2])
    fake = FakeTerminal(_second_call(_ANOMALIES[anomaly]))

    rc, err = _run(tmp_path, fake, capsys)

    assert rc == 2
    assert "[FAIL-STOP]" in err
    assert _written(tmp_path) == []
    assert fake.count("shutdown") - fake.count("initialize") == 0


def _without(column: str, container: str):
    """列 ``column`` を欠いた戻り値（dict 列はキー、構造化配列は ``dtype.names`` から欠く）。"""
    def anomaly(from_s, last_s):
        row = {k: v for k, v in _bar(from_s).items() if k != column}
        if container == "dicts":
            return [row]
        arr = np.zeros(1, dtype=[f for f in _RATES_DTYPE if f[0] != column])
        for name in row:
            arr[0][name] = row[name]
        return arr
    return anomaly


@pytest.mark.parametrize("container", ["dicts", "structured_array"])
@pytest.mark.parametrize("column", [name for name, _ in _RATES_DTYPE])
def test_a_missing_rate_column_stops_with_exit_2_and_names_it(
    tmp_path, monkeypatch, capsys, column, container
):
    """是正 2: 戻り値の列が 1 つでも欠ければ素の KeyError でなく [FAIL-STOP]・何も書かない。"""
    monkeypatch.setattr(cap, "PLAN", cap.PLAN[:2])
    fake = FakeTerminal(_second_call(_without(column, container)))

    rc, err = _run(tmp_path, fake, capsys)

    assert rc == 2
    assert "[FAIL-STOP]" in err
    assert column in err
    assert _written(tmp_path) == []


def test_bars_on_both_edges_of_the_window_are_kept(tmp_path, monkeypatch):
    """境界値: from ちょうどと to−1 秒ちょうどの足は窓の内側として書かれる。"""
    monkeypatch.setattr(cap, "PLAN", cap.PLAN[:1])
    fake = FakeTerminal(lambda i, f, l: [_bar(f), _bar(l)])

    rc, _ = _run(tmp_path, fake)

    out = tmp_path / "mt5_bars" / "20241229_20250201" / "JP225_M1_20241229_20250201.csv"
    assert rc == 0
    assert out.read_bytes().split(b"\r\n")[1:-1] == [
        b"2024.12.29\t00:00:00\t100.0\t100.0\t100.0\t100.0\t1\t0\t0",
        b"2025.01.31\t23:59:59\t100.0\t100.0\t100.0\t100.0\t1\t0\t0",
    ]


@pytest.mark.parametrize(
    "broken",
    [
        {"server": "OANDA-Japan MT5 Demo"},
        {"account_ok": False},
        {"initialize_ok": False},
        {"select_ok": False},
        {"symbol_info_ok": False},
    ],
    ids=["other_server", "no_account", "initialize_fails", "select_fails", "no_symbol_info"],
)
def test_broken_terminal_premises_stop_before_any_rates_are_read(tmp_path, capsys, broken):
    """R-6: サーバ違い（等）は ``copy_rates_range`` を 1 回も呼ばずに止まる。"""
    fake = FakeTerminal(**broken)

    rc, err = _run(tmp_path, fake, capsys)

    assert rc == 2
    assert "[FAIL-STOP]" in err
    assert fake.count("copy_rates_range") == 0
    assert _written(tmp_path) == []


# =====================================================================
# R-7 / R-8 出力
# =====================================================================

def test_every_planned_pair_is_written_to_its_conventional_path(tmp_path):
    """R-7: 21 組のパスは ``<out-root>/mt5_bars/<期間ID>/JP225_<足>_<期間ID>.csv``。"""
    rc, _ = _run(tmp_path, FakeTerminal())

    written = {p.relative_to(tmp_path).as_posix() for p in _written(tmp_path)}
    assert rc == 0
    assert written == _expected_relative_paths()
    assert len(written) == 22


def test_an_existing_output_file_is_never_overwritten(tmp_path, capsys):
    """R-7: 既存ファイルがあれば失敗し、その中身は変わらない。"""
    existing = tmp_path / "mt5_bars" / "20241229_20250201" / "JP225_M1_20241229_20250201.csv"
    existing.parent.mkdir(parents=True)
    existing.write_bytes(b"keep")

    rc, err = _run(tmp_path, FakeTerminal(), capsys)

    assert rc == 2
    assert "[FAIL-STOP]" in err
    assert existing.read_bytes() == b"keep"


def test_a_late_collision_leaves_the_earlier_csv_and_no_manifest(tmp_path, monkeypatch, capsys):
    """R-7: 書き出しの段の後段で衝突したときに残る状態（本体 docstring の主張の実証）。

    上の衝突検定は 1 組目で衝突するので部分書き出しが起きない。5 組目（H1）で衝突させて
    「先行 CSV は残る・manifest は作られない・衝突したファイルは無傷・終了コード 2」を固定する。
    """
    monkeypatch.setattr(cap, "PLAN", cap.PLAN[:6])
    period = "20241229_20250201"
    collided = tmp_path / "mt5_bars" / period / f"JP225_H1_{period}.csv"
    collided.parent.mkdir(parents=True)
    collided.write_bytes(b"keep")

    rc, err = _run(tmp_path, FakeTerminal(), capsys)

    written = {p.relative_to(tmp_path).as_posix() for p in _written(tmp_path)}
    assert (rc, "[FAIL-STOP]" in err) == (2, True)
    assert written == {
        f"mt5_bars/{period}/JP225_{tf}_{period}.csv" for tf in ("M1", "M5", "M15", "M30", "H1")
    }
    assert not (tmp_path / "mt5_bars" / cap.MANIFEST_NAME).exists()
    assert collided.read_bytes() == b"keep"


def test_manifest_records_the_bytes_that_were_written(tmp_path):
    """R-8: manifest の sha256・行数・最初と最後のラベルが、書いたバイト列と一致する。"""
    fake = FakeTerminal(_evenly(3), build=5200, version="5.0.5120")

    rc, _ = _run(tmp_path, fake)

    root = tmp_path / "mt5_bars"
    manifest = json.loads((root / "manifest.json").read_bytes())
    recorded = {e["path"]: (e["sha256"], e["rows"]) for e in manifest["files"]}
    actual = {
        p.relative_to(root).as_posix(): (hashlib.sha256(p.read_bytes()).hexdigest(), _data_rows(p))
        for p in root.rglob("*.csv")
    }
    assert rc == 0
    assert recorded == actual
    assert len(recorded) == 21


def test_manifest_records_the_session_and_each_requested_window(tmp_path, monkeypatch):
    """R-8: server・端末 build・パッケージ版・digits・取得時刻と、組ごとの要求窓・ラベル。"""
    monkeypatch.setattr(cap, "PLAN", cap.PLAN[:1])
    start = _label(2024, 12, 29)
    fake = FakeTerminal(
        lambda i, f, l: [_bar(start), _bar(start + 60), _bar(start + 120)],
        digits=1, build=5200, version="5.0.5120",
    )

    rc, _ = _run(tmp_path, fake)

    manifest = json.loads((tmp_path / "mt5_bars" / "manifest.json").read_bytes())
    entry = manifest["files"][0]
    assert rc == 0
    assert {k: v for k, v in manifest.items() if k != "files"} == {
        "generator": "tools/capture_mt5_bars.py",
        "symbol": "JP225",
        "server": _LIVE_SERVER,
        "terminal_build": 5200,
        "mt5_package_version": "5.0.5120",
        "digits": 1,
        "captured_at_utc": "2026-09-15T12:00:00Z",
    }
    assert {k: v for k, v in entry.items() if k != "sha256"} == {
        "path": "20241229_20250201/JP225_M1_20241229_20250201.csv",
        "period_id": "20241229_20250201",
        "timeframe": "M1",
        "from_label": "2024-12-29 00:00:00",
        "to_label_exclusive": "2025-02-01 00:00:00",
        "request_from_label": "2024-12-29 00:00:00",
        "request_to_label_exclusive": "2025-02-01 00:00:00",
        "rows": 3,
        "first_label": "2024-12-29 00:00:00",
        "last_label": "2024-12-29 00:02:00",
    }


# =====================================================================
# R-9 実行計画
# =====================================================================

def test_the_plan_matches_the_issue_511_specification():
    """R-9: 3 期間・時間足・J-2 の終端（半開区間の to）が仕様どおり。"""
    expected = [
        (pid, tf, _label(*frm), _label(*to)) for pid, frm, to, tfs in _SPEC for tf in tfs
    ]

    assert [(j.period_id, j.timeframe, j.from_s, j.to_s) for j in cap.PLAN] == expected
    assert len(expected) == 21


def test_the_plan_rejects_a_duplicated_period_and_timeframe_pair():
    """R-9: 組 (期間ID, 足) の重複は拒む（同じ取得を 2 回しない）。"""
    job = cap.PLAN[0]

    with pytest.raises(cap.CaptureError):
        cap.build_plan([job, job])


# =====================================================================
# R-10 構造（宣言だけを残さない）
# =====================================================================

def _tree() -> ast.Module:
    return ast.parse(_SOURCE.read_bytes())


def _terminal_attrs(tree: ast.AST) -> "set[str]":
    """端末オブジェクト（本体での変数名は mt5）への属性参照の集合。"""
    return {
        n.attr for n in ast.walk(tree)
        if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name) and n.value.id == "mt5"
    }


def _holds_the_terminal(value: ast.expr) -> bool:
    """その式の値に端末そのもの（本体での変数名は mt5）が混ざるか。

    右辺全体を ``ast.walk`` で見る（``mt5, 1`` の Tuple・``{"t": mt5}`` のコンテナも拾う）。
    ただし属性参照の受け手（``mt5.account_info()``）と関数へ渡した実引数
    （``capture(mt5, PLAN)``）は端末そのものを取り出さないので数えない。
    """
    consumed: "set[int]" = set()
    for node in ast.walk(value):
        if isinstance(node, ast.Attribute):
            consumed.add(id(node.value))
        elif isinstance(node, ast.Call):
            consumed |= {id(a) for a in [node.func, *node.args]}
            consumed |= {id(k.value) for k in node.keywords}
    return any(
        isinstance(node, ast.Name) and node.id == "mt5" and id(node) not in consumed
        for node in ast.walk(value)
    )


def _terminal_aliases(tree: ast.AST) -> "set[str]":
    """端末（本体での変数名は mt5）を別の名前へ束縛している箇所の、その束縛先の集合。

    ``_terminal_attrs`` は変数名 mt5 への属性参照しか見ないので、``terminal = mt5`` を 1 行
    入れるだけで許可集合の施行を抜けられる。別名を辿るのではなく **別名の不在** を施行する
    （辿る側は漏れるため、施行範囲を実際より広く名乗ることになる）。

    施行の範囲は **代入の右辺と関数の既定引数**（``terminal = mt5``・``a, b = mt5, 1``・
    ``holder = {"t": mt5}``・``def g(term=mt5)``）。端末を実引数として渡した先までは追わない。
    その先で属性名を組み立てる最悪形は、受け手に依らない
    ``test_no_getattr_composes_an_attribute_name`` が閉じる。
    """
    def bound(target: ast.expr) -> "set[str]":
        if isinstance(target, (ast.Tuple, ast.List)):
            return {name for element in target.elts for name in bound(element)}
        return {ast.unparse(target)}

    names: "set[str]" = set()
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            positional = [*n.args.posonlyargs, *n.args.args]
            pairs = list(zip(positional[len(positional) - len(n.args.defaults):], n.args.defaults))
            pairs += list(zip(n.args.kwonlyargs, n.args.kw_defaults))
            names |= {a.arg for a, d in pairs if d is not None and _holds_the_terminal(d)}
            continue
        targets = (
            list(n.targets) if isinstance(n, ast.Assign)
            else [n.target] if isinstance(n, (ast.AnnAssign, ast.NamedExpr)) else []
        )
        if targets and getattr(n, "value", None) is not None and _holds_the_terminal(n.value):
            names |= {name for target in targets for name in bound(target)}
    return names - {"mt5"}


def _composed_getattr_names(tree: ast.AST) -> "list[str]":
    """第 2 引数が定数でない ``getattr(...)`` 呼出の、その第 2 引数（受け手に依らない）。"""
    return [
        ast.unparse(n.args[1]) for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "getattr"
        and len(n.args) >= 2 and not isinstance(n.args[1], ast.Constant)
    ]


def _getattr_on_terminal(tree: ast.AST) -> "list[ast.expr]":
    """``getattr(mt5, 名前, ...)`` の名前の式。"""
    return [
        n.args[1] for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "getattr"
        and len(n.args) >= 2 and isinstance(n.args[0], ast.Name) and n.args[0].id == "mt5"
    ]


def _import_roots(tree: ast.AST) -> "set[str]":
    roots = set()
    for n in ast.walk(tree):
        roots |= {a.name.split(".")[0] for a in n.names} if isinstance(n, ast.Import) else set()
        roots |= {n.module.split(".")[0]} if isinstance(n, ast.ImportFrom) and n.module else set()
    return roots


def _top_level_metatrader_imports(tree: ast.Module) -> "list[str]":
    return [
        name for node in tree.body
        for name in (
            [a.name for a in node.names] if isinstance(node, ast.Import)
            else [node.module or ""] if isinstance(node, ast.ImportFrom) else []
        )
        if name.startswith("MetaTrader5")
    ]


#: 発注系の名前の接頭辞。**この語の唯一の出所**（属性参照を見る ``_order_refs`` と、定数名の
#: getattr を見る ``_order_named_getattr`` の両方がここだけを参照する＝2 通りに割れない）。
_ORDER_PREFIXES = ("order_",)


def _order_refs(tree: ast.AST) -> "set[str]":
    return {
        n.attr for n in ast.walk(tree)
        if isinstance(n, ast.Attribute) and n.attr.startswith(_ORDER_PREFIXES)
    }


def _order_named_getattr(tree: ast.AST) -> "set[str]":
    """``getattr(受け手, "order_...")`` が引く名前の集合（受け手の名前に依らない）。

    第 2 引数が定数なので ``_composed_getattr_names`` には掛からず、属性参照ではないので
    ``_order_refs`` にも掛からない（実測）。素の ``terminal.order_send({})`` だけを塞いでも、
    定数名の getattr という抜け道が残る。
    """
    return {
        n.args[1].value for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "getattr"
        and len(n.args) >= 2 and isinstance(n.args[1], ast.Constant)
        and isinstance(n.args[1].value, str) and n.args[1].value.startswith(_ORDER_PREFIXES)
    }


#: ``getattr`` 以外で属性を動的に引く語。**この語の唯一の出所**。
#: 本体はこの 4 語を 1 つも使わない（実測）ので、出現 0 件を誤検出なしで施行できる。
_DYNAMIC_ATTRIBUTE_NAMES = frozenset({"__getattribute__", "__dict__", "vars", "attrgetter"})


def _dynamic_attribute_access(tree: ast.AST) -> "set[str]":
    """``getattr`` 以外の動的属性アクセスの名前（受け手の名前に依らない）。

    見るのは識別子（``ast.Attribute`` の属性名と ``ast.Name``）と、getattr の定数第 2 引数の
    3 か所だけで、散文の文字列は見ない。散文まで見ると、これらの語を説明に挙げた docstring が
    自分で掛かる（本体の安全性節がまさにこの 4 語を名指す）。"vars" と "attrgetter" は受け手が
    引数側に来るので ``ast.Name`` で、"__getattribute__" と "__dict__" は属性名で拾う。
    同じ 4 語を定数文字列で綴った getattr(terminal, "__dict__")["order_send"]({}) の形は、
    識別子として現れないので前 2 者に掛からない（実測: 3 か所目を足す前は、どの走査も沈黙した）。
    3 か所目がこれを閉じる。
    """
    found: "set[str]" = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Attribute) and n.attr in _DYNAMIC_ATTRIBUTE_NAMES:
            found.add(n.attr)
        elif isinstance(n, ast.Name) and n.id in _DYNAMIC_ATTRIBUTE_NAMES:
            found.add(n.id)
        elif (isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
              and n.func.id == "getattr" and len(n.args) >= 2
              and isinstance(n.args[1], ast.Constant) and isinstance(n.args[1].value, str)
              and n.args[1].value in _DYNAMIC_ATTRIBUTE_NAMES):
            found.add(n.args[1].value)
    return found


class _AnyNodeOfKind:
    """ノードの種類だけを突き合わせる期待値（AST ノードは同一性でしか等しくならない）。"""

    def __init__(self, kind: str):
        self.kind = kind

    def __eq__(self, other) -> bool:
        return type(other).__name__ == self.kind

    def __repr__(self) -> str:
        return f"<{self.kind} ノード>"


#: 本体が施行を名乗る項目（:data:`cap.ENFORCED_CHECKS`）と、それを実際に施行する走査の対応。
#: 散文で「施行は次の N つ」と数え上げると誇大が入り込むので、識別子の集合の一致
#: （``test_the_enforced_checks_and_the_scanner_registry_agree``）と、どの走査にも検出力の
#: 実測があること（``test_every_enforced_scanner_has_a_detection_power_case``）で固定する。
_ENFORCED_SCANNERS = {
    "terminal_attrs_within_the_allowlist": _terminal_attrs,
    "getattr_on_the_terminal_is_literal_and_allowed": _getattr_on_terminal,
    "no_composed_getattr_name": _composed_getattr_names,
    "no_terminal_bound_to_another_name": _terminal_aliases,
    "no_order_attribute_reference": _order_refs,
    "no_order_named_getattr": _order_named_getattr,
    "no_dynamic_attribute_access": _dynamic_attribute_access,
}


def test_the_module_does_not_import_metatrader5_at_top_level():
    assert _top_level_metatrader_imports(_tree()) == []


def test_the_module_never_references_order_apis():
    """接続先は実弾のライブ口座。発注系 API を 1 つも参照しない。"""
    assert _order_refs(_tree()) == set()


def test_terminal_access_stays_inside_the_allowed_apis():
    """端末 API は許可集合の中だけ。取得の口は実際に走査に掛かっている（空振りしない）。"""
    attrs = _terminal_attrs(_tree())

    assert attrs <= cap.ALLOWED_TERMINAL_APIS, sorted(attrs - cap.ALLOWED_TERMINAL_APIS)
    assert {"initialize", "shutdown", "account_info", "symbol_info", "copy_rates_range",
            "TIMEFRAME_M1", "TIMEFRAME_MN1"} <= attrs


def test_the_allowlist_matches_the_design():
    """許可集合を勝手に広げない（広げるときは設計と一緒に変える）。

    先例 ``tools/tests/test_mt5_tick_feed.py::test_the_allowlist_matches_the_design`` と同型。
    走査（``test_terminal_access_stays_inside_the_allowed_apis``）は本体の宣言を参照するので、
    宣言を広げるだけでは走査は落ちない。集合を広げる変更は必ずこのリテラルに現れる。
    """
    assert cap.ALLOWED_TERMINAL_APIS == frozenset({
        "initialize", "shutdown", "last_error", "account_info", "terminal_info",
        "symbol_select", "symbol_info", "copy_rates_range",
        "TIMEFRAME_M1", "TIMEFRAME_M5", "TIMEFRAME_M15", "TIMEFRAME_M30",
        "TIMEFRAME_H1", "TIMEFRAME_H4", "TIMEFRAME_D1", "TIMEFRAME_W1", "TIMEFRAME_MN1",
    })


def test_the_terminal_is_never_bound_to_another_name():
    """端末を別名へ束縛しない（``terminal = mt5`` の 1 行で上の走査を抜けられるため）。

    許可集合の施行は変数名 mt5 への属性参照を見る走査であり、別名の先までは追わない。
    追う側へ広げず、別名そのものを禁じることで走査の範囲＝施行の範囲にする。
    """
    assert _terminal_aliases(_tree()) == set()


def test_getattr_on_the_terminal_uses_only_literal_allowed_names():
    """時間足定数を文字列組み立てで引かない（許可集合の検定を迂回させない）。"""
    names = _getattr_on_terminal(_tree())

    assert [n for n in names if not isinstance(n, ast.Constant)] == []
    assert {n.value for n in names} <= cap.ALLOWED_TERMINAL_APIS | cap.ALLOWED_PACKAGE_ATTRS


def test_the_package_attr_allowlist_matches_the_design():
    """端末 API ではないモジュール属性の許可集合を勝手に広げない（宣言と施行の一致）。

    上の検定は本体の宣言を参照するので、宣言を広げるだけでは落ちない。集合を広げる変更は
    必ずこのリテラルに現れる（``test_the_allowlist_matches_the_design`` と同型）。
    """
    assert cap.ALLOWED_PACKAGE_ATTRS == frozenset({"__version__"})


def test_no_getattr_composes_an_attribute_name():
    """属性名を組み立てる ``getattr`` が本体に 1 つも無い（受け手の名前に依らない）。

    ``_terminal_attrs`` も ``_getattr_on_terminal`` も受け手が名前 mt5 のときしか見ないので、
    端末を別名の仮引数へ渡した先で ``getattr(terminal, "order_" + n)`` と名前を組み立てる形は、
    検定で実行されない分岐（``except`` 節など）に置くと全検定が緑のまま通る（実測）。
    受け手を問わず「名前を組み立てない」ことを施行して、この最悪形を閉じる。
    """
    assert _composed_getattr_names(_tree()) == []


def test_no_getattr_names_an_order_api_on_any_receiver():
    """発注系の名前を **定数で** 引く ``getattr`` が 1 つも無い（受け手の名前に依らない）。

    ``getattr(terminal, "order_send")({})`` は第 2 引数が定数なので上の検定に掛からず、
    属性参照でないので ``_order_refs`` にも掛からず、受け手が mt5 でないので
    ``_getattr_on_terminal`` にも掛からない（実測: この形を検出する施行走査は本検定の
    ``_order_named_getattr`` だけで、他の施行走査はどれも沈黙する）。
    名前の組み立てを塞ぐだけでは実弾口座の安全境界に穴が残るので、重ねて施行する。
    """
    assert _order_named_getattr(_tree()) == set()


def test_the_module_never_uses_dynamic_attribute_access_besides_getattr():
    """``getattr`` 以外の動的属性アクセスが本体に 1 つも無い（受け手の名前に依らない）。

    ``terminal.__getattribute__("order_send")({})``・``terminal.__dict__["order_send"]({})``・
    ``operator.attrgetter("order_send")(terminal)({})``・``vars(terminal)["order_send"]({})``
    は、いずれも別名レシーバ＋定数名なので先行するどの走査も無検出である（実測: 本体へ差し込んでも
    先行する施行走査の出力が正当な本体と変わらない。``operator`` は stdlib なので import 走査も
    緑のまま）。
    本体は ``getattr`` 以外の動的属性アクセスを使わないので、この 4 語の出現 0 件を施行できる。
    """
    assert _dynamic_attribute_access(_tree()) == set()


def test_the_module_imports_only_the_standard_library():
    """VM へは 1 ファイルだけ持ち込む。MetaTrader5 以外は stdlib（numpy も使わない）。"""
    roots = _import_roots(_tree())

    assert roots - {"MetaTrader5"} <= set(sys.stdlib_module_names), sorted(
        roots - {"MetaTrader5"} - set(sys.stdlib_module_names)
    )
    assert "MetaTrader5" in roots


#: 検出力の実測（合成した違反を走査が検出する）。source / 走査 / 期待値。
#: ``_ENFORCED_SCANNERS`` の走査がすべてこの表に現れることを
#: ``test_every_enforced_scanner_has_a_detection_power_case`` が施行する。
_DETECTION_CASES = [
    ("import MetaTrader5 as mt5\n", _top_level_metatrader_imports, ["MetaTrader5"]),
    ("def f(mt5):\n    mt5.order_send({})\n", _order_refs, {"order_send"}),
    ("def f(mt5):\n    mt5.copy_ticks_range(1, 2, 3, 4)\n", _terminal_attrs,
     {"copy_ticks_range"}),
    ("import numpy\n", _import_roots, {"numpy"}),
    ("def f(mt5):\n    terminal = mt5\n    terminal.positions_get()\n",
     _terminal_aliases, {"terminal"}),
    ("def f(mt5):\n    self.kept = mt5\n", _terminal_aliases, {"self.kept"}),
    ("def f():\n    mt5 = _default_mt5()\n    return mt5\n", _terminal_aliases, set()),
    ("def f(mt5):\n    a, b = mt5, 1\n    return a, b\n", _terminal_aliases, {"a", "b"}),
    ('def f(mt5):\n    holder = {"t": mt5}\n    return holder\n',
     _terminal_aliases, {"holder"}),
    ("def g(term=mt5):\n    return term\n", _terminal_aliases, {"term"}),
    ("def f(mt5):\n    rates = mt5.copy_rates_range(1, 2, 3, 4)\n    return rates\n",
     _terminal_aliases, set()),
    ("def f(mt5):\n    out = capture(mt5, PLAN)\n    return out\n", _terminal_aliases, set()),
    ('def f(term, n):\n    getattr(term, "order_" + n)()\n',
     _composed_getattr_names, ["'order_' + n"]),
    ('def f(term):\n    getattr(term, "shutdown")()\n', _composed_getattr_names, []),
    ('def f(term):\n    getattr(term, "order_send")({})\n', _order_named_getattr, {"order_send"}),
    ('def f(mt5):\n    getattr(mt5, "__version__", None)\n', _order_named_getattr, set()),
    ('def f(terminal):\n    terminal.__getattribute__("order_send")({})\n',
     _dynamic_attribute_access, {"__getattribute__"}),
    ('def f(terminal):\n    terminal.__dict__["order_send"]({})\n',
     _dynamic_attribute_access, {"__dict__"}),
    ('import operator\ndef f(terminal):\n    operator.attrgetter("order_send")(terminal)({})\n',
     _dynamic_attribute_access, {"attrgetter"}),
    ('def f(terminal):\n    vars(terminal)["order_send"]({})\n',
     _dynamic_attribute_access, {"vars"}),
    ('def f(terminal):\n    getattr(terminal, "__dict__")["order_send"]({})\n',
     _dynamic_attribute_access, {"__dict__"}),
    ('def f(terminal):\n    getattr(terminal, "__getattribute__")("order_send")({})\n',
     _dynamic_attribute_access, {"__getattribute__"}),
    ('def f(mt5, tf):\n    getattr(mt5, "TIMEFRAME_" + tf)\n',
     _getattr_on_terminal, [_AnyNodeOfKind("BinOp")]),
]

_DETECTION_IDS = [
    "top_level_import", "order_api", "unlisted_terminal_api", "third_party_import",
    "terminal_alias", "terminal_kept_on_an_attribute", "supplying_the_terminal_is_not_an_alias",
    "terminal_in_a_tuple_right_hand_side", "terminal_in_a_container",
    "terminal_as_a_default_argument", "reading_an_attribute_is_not_an_alias",
    "passing_the_terminal_on_is_not_an_alias", "composed_getattr_on_any_receiver",
    "literal_getattr_on_any_receiver", "order_api_named_by_a_literal_getattr",
    "package_attr_by_a_literal_getattr", "order_api_by_getattribute",
    "order_api_by_instance_dict", "order_api_by_attrgetter", "order_api_by_vars",
    "instance_dict_named_by_a_literal_getattr", "getattribute_named_by_a_literal_getattr",
    "composed_timeframe_name_on_the_terminal",
]


@pytest.mark.parametrize("source, scanner, expected", _DETECTION_CASES, ids=_DETECTION_IDS)
def test_the_structure_scanners_have_detection_power(source, scanner, expected):
    """走査が恒真式に退化していない（違反を合成して検出できる）。"""
    assert scanner(ast.parse(source)) == expected


def test_the_enforced_checks_and_the_scanner_registry_agree():
    """本体が施行を名乗る項目と、実際に施行する走査の登録簿が一致する。

    本体の安全性節は散文で項目を並べるので、数え上げだけでは「施行は次の N つ」が実態から
    ずれても何も落ちない（本ブランチで同種の誇大が 6 件）。本体の宣言 ``ENFORCED_CHECKS`` と
    登録簿の鍵の一致を機械的に固定し、どちらか一方だけを増減させた変更を落とす。
    """
    assert set(cap.ENFORCED_CHECKS) == set(_ENFORCED_SCANNERS)


def test_every_enforced_scanner_has_a_detection_power_case():
    """施行を名乗るどの走査にも検出力の実測がある（恒真式の混入を防ぐ）。"""
    covered = {scanner for _, scanner, _ in _DETECTION_CASES}

    assert set(_ENFORCED_SCANNERS.values()) - covered == set()


def test_the_cli_help_succeeds_as_a_script():
    """``python3 tools/capture_mt5_bars.py --help`` がコンテナ（MetaTrader5 不在）で成功する。"""
    proc = subprocess.run(
        [sys.executable, str(_SOURCE), "--help"], capture_output=True, text=True, cwd=str(_REPO_ROOT),
    )

    assert proc.returncode == 0, proc.stderr
    assert "--out-root" in proc.stdout


def test_the_cli_keeps_its_option_surface_minimal():
    opts = {a for act in cap.build_parser()._actions for a in act.option_strings}

    assert opts == {"-h", "--help", "--out-root"}


# =====================================================================
# R-11 改行変換の対象外（J-3）
# =====================================================================

def test_bar_fixtures_are_excluded_from_line_ending_conversion():
    """VM（Windows）から git で運ぶ CSV と manifest を git が改行変換しない。"""
    paths = [
        f"{_BARS_FIXTURE_DIR}/20241229_20250201/JP225_M1_20241229_20250201.csv",
        f"{_BARS_FIXTURE_DIR}/20200501_20260901/JP225_MN1_20200501_20260901.csv",
        f"{_BARS_FIXTURE_DIR}/manifest.json",
    ]

    proc = subprocess.run(
        ["git", "check-attr", "text", "--", *paths],
        capture_output=True, text=True, cwd=str(_REPO_ROOT), check=True,
    )

    assert proc.stdout.splitlines() == [f"{p}: text: unset" for p in paths]


def test_the_usage_section_tells_where_to_write_and_how_to_bring_the_output_back():
    """VM 実行者に最終置き場（クローンの fixtures）と J-3 の持ち帰り手順が伝わる。

    出力先が ``fixtures/mt5_bars/`` でなければ上の ``.gitattributes`` が効かず、Windows の
    改行変換で manifest の sha256 が合わなくなる（原因が端末側にあるようにしか見えない）。
    本ファイルは VM へ 1 本だけ持ち込むので、手順の置き場は docstring しかない。
    """
    doc = cap.__doc__

    assert "--out-root <クローン>/simulator/tests/fixtures" in doc
    assert f"git add {_BARS_FIXTURE_DIR}/" in doc
    assert "``git add -A`` は使わない" in doc
    assert "専用ブランチ" in doc and "git push" in doc


# =====================================================================
# 計算量（Test Spy・回数を焼き込まない）
# =====================================================================

@pytest.mark.parametrize("pairs", [2, 21])
@pytest.mark.parametrize("bars_per_pair", [10, 1000])
def test_every_fetched_bar_is_written(tmp_path, monkeypatch, pairs, bars_per_pair):
    """CX-1: 取得した足の総数 − 書いたデータ行の総数 = 0（取って捨てる足が無い）。"""
    monkeypatch.setattr(cap, "PLAN", cap.PLAN[:pairs])
    fake = FakeTerminal(_evenly(bars_per_pair))

    rc, _ = _run(tmp_path, fake)

    written = sum(_data_rows(p) for p in (tmp_path / "mt5_bars").rglob("*.csv"))
    assert rc == 0
    assert sum(fake.served) - written == 0


@pytest.mark.parametrize("pairs", [2, 21])
def test_each_planned_pair_is_fetched_once(tmp_path, monkeypatch, pairs):
    """CX-2: 呼出数 − 計画の組数 = 0、かつ同じ (足, 窓) の呼出数の最大 − 1 = 0。"""
    monkeypatch.setattr(cap, "PLAN", cap.PLAN[:pairs])
    fake = FakeTerminal()

    rc, _ = _run(tmp_path, fake)

    per_request = {r: fake.requested.count(r) for r in fake.requested}
    assert rc == 0
    assert fake.count("copy_rates_range") - len(cap.PLAN) == 0
    assert max(per_request.values()) - 1 == 0


def test_session_calls_do_not_grow_with_the_plan(tmp_path, monkeypatch):
    """CX-3: 接続・銘柄照会（copy_rates_range 以外）の呼出数が 2 組と 21 組で等しい。"""
    full = cap.PLAN

    def session_calls(pairs):
        monkeypatch.setattr(cap, "PLAN", full[:pairs])
        fake = FakeTerminal()
        rc = cap.main(["--out-root", str(tmp_path / str(pairs))], mt5=fake, now=_AT)
        return rc, {c: fake.count(c) for c in fake.calls if c != "copy_rates_range"}

    small, large = session_calls(2), session_calls(21)

    assert small == large
    assert {"initialize", "symbol_info"} <= set(small[1])


@pytest.mark.parametrize("pairs", [2, 21])
def test_the_written_bytes_are_built_once_for_each_file(tmp_path, monkeypatch, pairs):
    """CX-4: ``format_rows`` の発行回数 − 書いた CSV の本数 = 0（同じバイト列を 2 度作らない）。

    manifest の sha256 と行数は書いたバイト列から取るので、``cap.file_entry`` へ渡す分をもう一度
    組み立てても出力は 1 byte も変わらない＝状態の検定では原理的に落ちない。発行を数えて
    無駄の不在を固定する。回数そのものは焼き込まず、出力（書いた CSV の本数）との差で表明する。
    """
    monkeypatch.setattr(cap, "PLAN", cap.PLAN[:pairs])
    issued: "list[int]" = []
    build = cap.format_rows

    def spy(rates, digits):
        issued.append(len(rates))
        return build(rates, digits)

    monkeypatch.setattr(cap, "format_rows", spy)

    rc, _ = _run(tmp_path, FakeTerminal(_evenly(3)))

    written = list((tmp_path / "mt5_bars").rglob("*.csv"))
    assert rc == 0
    assert len(issued) - len(written) == 0
