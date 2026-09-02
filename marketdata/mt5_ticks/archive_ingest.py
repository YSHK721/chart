"""OANDA 月別アーカイブ（月 zip）の取り込み（adapter E）。

段階 1 の HTTP 経路とは**別の供給経路**である（ISSUE-447 供給経路 B）。入力は OANDA
マイページが配る月別 zip（中身は TAB 区切りテキスト 1 本）で、時刻は **MT5 サーバ時刻
ラベル**である。UTC 化・日分割・列整形・パスは既存の権威へ委譲し、本モジュールは
「アーカイブという入れ物の読み方」だけを持つ。

依存宣言（``test_mt5_module_dependency_declarations.py`` が AST で強制）:
    stdlib（zipfile 含む）/ :mod:`marketdata.tick_m1` / :mod:`marketdata.mt5_ticks` 下位。

実測（2026-09-02・全 76 か月 75,082,747 行を走査）:
    全行が 6 列 TAB で ``<TIME>`` のミリ秒は **3 桁**（75,082,747 / 75,082,747）。
    それでも 1〜3 桁を受けるのは、桁数ではなく**小数として**読むためである
    （``.5`` は 500ms）。桁数を前提にした読み方は、桁が変わった日に静かに 1000 倍ずれる。
"""
from __future__ import annotations

import contextlib
import datetime as dt
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Iterator, List, Optional, Sequence, Tuple

from marketdata import tick_m1
from marketdata.mt5_ticks import ingest, journal, server_clock
from marketdata.mt5_ticks.port import Mt5SupplyError

#: ティック 1 行 = ``(サーバ時刻ラベル ms, bid, ask)``（``ingest.Row`` と同一の形）。
Row = Tuple[int, float, float]

#: アーカイブのヘッダ行（実ファイルの 1 行目・全 76 か月で同一）。
HEADER_LINE = "<DATE>\t<TIME>\t<BID>\t<ASK>\t<LAST>\t<VOLUME>"

#: 1 行の列数。``<LAST>`` / ``<VOLUME>`` は全行空だが、**列としては読む**
#: （列が消えた・増えた形式変更を Fail-Stop で捕まえるため）。
_FIELD_COUNT = 6

_EPOCH = dt.datetime(1970, 1, 1, tzinfo=dt.timezone.utc)
_MILLISECOND = dt.timedelta(milliseconds=1)


def _label_ms(date_text: str, time_text: str) -> int:
    """``2020.04.30`` + ``18:00:00.613`` を「壁時計 ms」へ（UTC 変換はしない）。

    ここが返すのは**サーバ時刻ラベル**であり UTC ではない。ラベル → UTC の規則は
    :mod:`marketdata.mt5_ticks.server_clock` が唯一の権威である（本所では触れない）。
    """
    second_text, _, fraction = time_text.partition(".")
    if not fraction or len(fraction) > 3 or not fraction.isdigit():
        raise ValueError(f"ミリ秒が 1〜3 桁の数字ではありません: {time_text!r}")
    year, month, day = (int(part) for part in date_text.split("."))
    hour, minute, second = (int(part) for part in second_text.split(":"))
    stamp = dt.datetime(
        year, month, day, hour, minute, second, tzinfo=dt.timezone.utc
    )
    return (stamp - _EPOCH) // _MILLISECOND + int(fraction.ljust(3, "0"))


def iter_rows(lines: "Iterable[str]", *, source: str = "") -> "Iterator[Row]":
    """行テキストを 1 行ずつ ``Row`` へ変換して**流す**（月全体を溜めない）。

    :func:`parse_lines` と本関数を分けてあるのは、1 か月が最大 474 万行あり、月を丸ごと
    リストへ載せない経路が要るためである（:func:`ingest_months` はこちらを使う）。
    """
    for number, raw in enumerate(lines, 1):
        line = raw.rstrip("\r\n")
        if number == 1 and line == HEADER_LINE:
            continue
        fields = line.split("\t")
        if len(fields) != _FIELD_COUNT:
            raise Mt5SupplyError(
                f"アーカイブの {number} 行目の列数が {_FIELD_COUNT} ではありません"
                f"（{len(fields)} 列・{source or '入力'}）: {line!r}"
            )
        try:
            yield (_label_ms(fields[0], fields[1]), float(fields[2]), float(fields[3]))
        except ValueError as exc:
            raise Mt5SupplyError(
                f"アーカイブの {number} 行目を解釈できません（{source or '入力'}）:"
                f" {line!r} — {exc}"
            ) from exc


def parse_lines(lines: "Iterable[str]", *, source: str = "") -> "List[Row]":
    """行テキストを ``Row`` のリストへ（``<LAST>`` / ``<VOLUME>`` は読まない）。"""
    return list(iter_rows(lines, source=source))


#: 月別アーカイブのファイル名に埋まっている年月（``ticks_JP225_2020-05.zip``）。
_MONTH_IN_NAME = re.compile(r"_(?P<year>\d{4})-(?P<month>\d{2})\.zip$")

_MIDNIGHT = dt.time(0, 0)


def month_key(zip_path: Any) -> str:
    """月 zip のファイル名から ``YYYY-MM`` を読む（読む順序を決める唯一の判断）。

    月の識別を中身の先頭行から取らない理由: 先頭行のラベルは**前月末日**である
    （実測 T8・``ticks_JP225_2020-05.zip`` の先頭は ``2020.04.30``）。中身で並べると
    月順が 1 つずれる。
    """
    name = Path(zip_path).name
    matched = _MONTH_IN_NAME.search(name)
    if matched is None:
        raise Mt5SupplyError(
            f"月別アーカイブの命名 ``*_YYYY-MM.zip`` に一致しません: {name!r}"
        )
    return f"{matched.group('year')}-{matched.group('month')}"


@contextlib.contextmanager
def open_month_zip(zip_path: Any) -> "Iterator[Iterator[str]]":
    """月 zip を**1 回だけ**開き、中身のテキストを行で流す。

    zip を開く操作を本関数 1 つに閉じてあるのは、計算量検定が「各 zip をちょうど 1 回
    開いたか」を Test Spy で数えられるようにするためである（開き直しは読み直しであり、
    月数に比例しない発行を生む）。
    """
    with zipfile.ZipFile(zip_path) as archive:
        names = archive.namelist()
        if len(names) != 1:
            raise Mt5SupplyError(
                f"月別アーカイブの中身が 1 ファイルではありません: {names!r}（{zip_path}）"
            )
        with archive.open(names[0]) as stream:
            yield (line.decode("ascii") for line in stream)


@dataclass(frozen=True)
class IngestReport:
    """取り込み 1 回の結果（**読んだ行の行方が全部書いてある**）。

    ``rows_read == rows_written + rows_skipped_existing`` が不変条件である
    （:attr:`rows_unaccounted` が 0 でなければ、どこかで行を捨てている）。
    """

    months: "Tuple[str, ...]"
    rows_read: int
    rows_written: int
    rows_skipped_existing: int
    days_written: int
    days_skipped_existing: int
    days_empty: int

    @property
    def rows_unaccounted(self) -> int:
        """行方の説明がつかない行数（0 でなければ捨てている）。"""
        return self.rows_read - self.rows_written - self.rows_skipped_existing


class DayWriter:
    """日別 parquet と ``.empty`` を書く既定の writer（原子置換は段階 1 の実装を共有する）。

    行 → DataFrame の変換を**書く直前**に置いてあるのは、書かない writer（dry-run）が
    フレームを 1 つも作らないようにするためである。作ってから捨てる経路を残さない。
    """

    def write_day(self, rows: "Sequence[Row]", path: Path) -> None:
        journal._write_parquet_atomically(ingest.rows_to_frame(rows), path)

    def write_marker(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("", encoding="utf-8")


class DryRunWriter:
    """何も書かない writer（``--dry-run``）。**フレームも作らない**。

    「書かないが作りはする」実装にしないための型である。作って捨てる経路を 1 つでも
    残すと、dry-run が本番と同じだけ計算する（ISSUE-450 と同型の浪費）。
    """

    def write_day(self, rows: "Sequence[Row]", path: Path) -> None:
        return None

    def write_marker(self, path: Path) -> None:
        return None


def _utc_midnight_ms(day: dt.date) -> int:
    """UTC 日の 00:00 の epoch ms（日境界の比較点）。"""
    return (dt.datetime.combine(day, _MIDNIGHT, tzinfo=dt.timezone.utc) - _EPOCH) // _MILLISECOND


def ingest_months(
    zip_paths: "Iterable[Any]",
    *,
    symbol_token: str,
    data_dir: Any,
    writer: "Optional[DayWriter]" = None,
) -> IngestReport:
    """月 zip を年月順に **1 パス**で読み、UTC 日別 parquet を書く。

    月跨ぎの UTC 日（実測 T8: 53/75 境界）は、日が変わるまで書かずに持ち越して
    **1 つの parquet へ統合**する。持ち越しはファイル境界を跨いで生き続ける。
    """
    writer = writer or DayWriter()
    paths = sorted((Path(p) for p in zip_paths), key=month_key)

    rows_read = 0
    rows_written = 0
    rows_skipped = 0
    days_written = 0
    days_skipped = 0
    day: "Optional[dt.date]" = None
    day_rows: "List[Row]" = []
    day_end_ms = 0
    last_utc_ms: "Optional[int]" = None
    seen_days: "List[dt.date]" = []

    def flush() -> None:
        nonlocal rows_written, rows_skipped, days_written, days_skipped, day_rows
        if day is None or not day_rows:
            return
        seen_days.append(day)
        # 気配の実在性・型・ラベル単調は段階 1 の権威が持つ（第 2 実装を作らない）。
        # **書く前に**通すことで、壊れた行を含む日を 1 バイトも書かない。
        ingest.validate_rows(day_rows, from_msc=day_rows[0][0])
        parquet = tick_m1.day_parquet_path(day, symbol=symbol_token, data_dir=data_dir)
        if parquet.is_file():
            # 既存の日は**上書きしない**（ISSUE-447 方針: 既に在る台帳を書き換えない）。
            # 行は月ファイル単位でしか読めないため、読むこと自体は避けられない。
            # よって「読んだが書かなかった行」を報告へ出す（捨てた行を黙らせない）。
            rows_skipped += len(day_rows)
            days_skipped += 1
        else:
            writer.write_day(day_rows, parquet)
            rows_written += len(day_rows)
            days_written += 1
        day_rows = []

    for path in paths:
        with open_month_zip(path) as lines:
            for row in iter_rows(lines, source=path.name):
                rows_read += 1
                utc_ms = server_clock.to_utc_ms(row[0])
                if last_utc_ms is not None and utc_ms < last_utc_ms:
                    raise Mt5SupplyError(
                        f"UTC の時刻が戻っています: {last_utc_ms} の次に {utc_ms}"
                        f"（{path.name}・ラベル {row[0]}）。"
                    )
                last_utc_ms = utc_ms
                if utc_ms >= day_end_ms:
                    flush()
                    day = server_clock.utc_day_of(row[0])
                    day_end_ms = _utc_midnight_ms(day + dt.timedelta(days=1))
                day_rows.append(row)
    flush()

    days_empty = _mark_empty_days(
        seen_days, symbol_token=symbol_token, data_dir=data_dir, writer=writer
    )

    return IngestReport(
        months=tuple(month_key(p) for p in paths),
        rows_read=rows_read,
        rows_written=rows_written,
        rows_skipped_existing=rows_skipped,
        days_written=days_written,
        days_skipped_existing=days_skipped,
        days_empty=days_empty,
    )


def _mark_empty_days(
    seen_days: "Sequence[dt.date]", *, symbol_token: str, data_dir: Any, writer: Any
) -> int:
    """走査した範囲の中で行が 1 つも無かった暦日に ``.empty`` を置き、その日数を返す。

    範囲を「走査した最初の UTC 日 〜 最後の UTC 日」に限るのは、走査していない日に
    ``.empty`` を置くと「取れていない」と「行が無い」が区別できなくなるためである
    （段階 1 :func:`journal.finalize` と同じ判断）。
    """
    if not seen_days:
        return 0
    present = set(seen_days)
    empty = 0
    day = seen_days[0]
    while day <= seen_days[-1]:
        if day not in present:
            empty += 1
            marker = tick_m1.day_empty_marker_path(
                day, symbol=symbol_token, data_dir=data_dir
            )
            parquet = tick_m1.day_parquet_path(day, symbol=symbol_token, data_dir=data_dir)
            if not marker.is_file() and not parquet.is_file():
                writer.write_marker(marker)
        day += dt.timedelta(days=1)
    return empty
