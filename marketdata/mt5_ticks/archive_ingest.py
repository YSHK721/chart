"""OANDA 月別アーカイブ（月 zip）の取り込み（adapter E）。

段階 1 の HTTP 経路とは**別の供給経路**である（ISSUE-447 供給経路 B）。入力は OANDA
マイページが配る月別 zip（中身は TAB 区切りテキスト 1 本）で、時刻は **MT5 サーバ時刻
ラベル**である。UTC 化・日分割・列整形・パスは既存の権威へ委譲し、本モジュールは
「アーカイブという入れ物の読み方」だけを持つ。

依存宣言（``test_mt5_module_dependency_declarations.py`` が AST で強制）:
    stdlib（zipfile 含む）/ :mod:`marketdata.tick_m1` / :mod:`marketdata.mt5_ticks` 下位。

走査範囲の端は「完成品」ではない:
    月 zip は前月末日の途中から始まり、最後の UTC 日は次の月 zip へ続く。よって走査の
    先頭日と末尾日は**書かない**（:func:`ingest_months` の docstring に理由）。書いてしまうと
    既存日スキップがその欠けを恒久化する。

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
from typing import Any, Iterable, Iterator, List, Optional, Protocol, Sequence, Tuple

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


#: 月別アーカイブの命名（``ticks_JP225_2020-05.zip``）。**銘柄も年月も名前から読む**。
#:
#: 銘柄を名前から読む理由（工程 5 🔴-3）: 取り込み先の木は ``symbol_token`` が決める。
#: 名前の銘柄と突き合わせなければ、ある銘柄のアーカイブを別銘柄の木へ書き込める。
_ARCHIVE_NAME = re.compile(
    r"^ticks_(?P<pair>[A-Z0-9]+)_(?P<year>\d{4})-(?P<month>\d{2})\.zip$"
)

_MIDNIGHT = dt.time(0, 0)


def _name_parts(zip_path: Any) -> "re.Match":
    name = Path(zip_path).name
    matched = _ARCHIVE_NAME.match(name)
    if matched is None:
        raise Mt5SupplyError(
            f"月別アーカイブの命名 ``ticks_<PAIR>_YYYY-MM.zip`` に一致しません: {name!r}"
        )
    return matched


def month_key(zip_path: Any) -> str:
    """月 zip のファイル名から ``YYYY-MM`` を読む（読む順序を決める唯一の判断）。

    月の識別を中身の先頭行から取らない理由: 先頭行のラベルは**前月末日**である
    （実測 T8・``ticks_JP225_2020-05.zip`` の先頭は ``2020.04.30``）。中身で並べると
    月順が 1 つずれる。
    """
    matched = _name_parts(zip_path)
    return f"{matched.group('year')}-{matched.group('month')}"


def pair_of(zip_path: Any) -> str:
    """月 zip のファイル名から**銘柄**を読む（``ticks_JP225_2020-05.zip`` の銘柄部）。"""
    return _name_parts(zip_path).group("pair")


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
            yield _decoded(stream, source=str(zip_path))


def _decoded(stream: "Iterable[bytes]", *, source: str) -> "Iterator[str]":
    """バイト行を ASCII で読む。読めないバイトも Fail-Stop の型で報せる。

    実測では全 76 か月が ASCII である。厳格に読むのは、文字集合が変わったことを
    「置換文字で静かに通す」のではなく供給の変化として止めるためである。
    """
    for number, line in enumerate(stream, 1):
        try:
            yield line.decode("ascii")
        except UnicodeDecodeError as exc:
            raise Mt5SupplyError(
                f"アーカイブの {number} 行目が ASCII ではありません（{source}）: {exc}"
            ) from exc


@dataclass(frozen=True)
class MonthProgress:
    """月 1 本ぶんの経過（CLI が 1 行ずつ報せるための素材・規則は持たない）。

    月ごとに出す理由: 76 か月の一括実行は数十分かかる。全部終わってから 1 行だけ出す
    報告は、途中で落ちたときに「どこまで進んだか」を何も残さない。
    """

    month: str
    rows_read: int
    days_written: int
    rows_carried: int


@dataclass(frozen=True)
class IngestReport:
    """取り込み 1 回の結果（**読んだ行の行方が全部書いてある**）。

    不変条件は
    ``rows_read == rows_written + rows_skipped_existing + rows_head_dropped + rows_carried``
    である（:attr:`rows_unaccounted` が 0 でなければ、どこかで行を捨てている）。
    切り落とし・持ち越しも「行方」であり、報告に出さなければ黙って捨てたのと同じである。
    """

    months: "Tuple[str, ...]"
    rows_read: int
    rows_written: int
    rows_skipped_existing: int
    days_written: int
    days_skipped_existing: int
    days_empty: int
    head_dropped_day: "Optional[dt.date]"
    rows_head_dropped: int
    days_carried: int
    rows_carried: int
    month_progress: "Tuple[MonthProgress, ...]"

    @property
    def rows_unaccounted(self) -> int:
        """行方の説明がつかない行数（0 でなければ捨てている）。"""
        return (
            self.rows_read
            - self.rows_written
            - self.rows_skipped_existing
            - self.rows_head_dropped
            - self.rows_carried
        )


class Writer(Protocol):
    """日別 parquet と ``.empty`` の書き手（:func:`ingest_months` が依存する抽象）。

    取り込みの側は「書いた / 書かなかった」を数えるだけで、**どう書くか**を知らない。
    dry-run が「本番と同じだけ計算して捨てる」実装に化けないための境界である。
    """

    def write_day(self, rows: "Sequence[Row]", path: Path) -> None: ...

    def write_marker(self, path: Path) -> None: ...


class DayWriter:
    """日別 parquet と ``.empty`` を書く既定の writer（原子置換は段階 1 の実装を共有する）。

    行 → DataFrame の変換を**書く直前**に置いてあるのは、書かない writer（dry-run）が
    フレームを 1 つも作らないようにするためである。作ってから捨てる経路を残さない。
    """

    def write_day(self, rows: "Sequence[Row]", path: Path) -> None:
        journal.write_parquet_atomically(ingest.rows_to_frame(rows), path)

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


def _assert_pairs_match_token(paths: "Sequence[Path]", symbol_token: str) -> None:
    """入力 zip の銘柄が 1 つに揃い、書込先トークンの銘柄部と一致することを確かめる。

    2 つを別々に見る理由（工程 5 🔴-3）:
        - 銘柄が揃っていない入力を 1 パスで読むと、時刻の単調性検査が別銘柄の並びに対して
          働き、**別銘柄の行が同じ日 partition へ混ざる**。
        - 揃っていても、トークンが別銘柄を指していれば別銘柄の木へ書き込める。入力と
          書込先を突き合わせる場所がここ以外に無い（CLI は合成点であり規則を持たない）。

    銘柄の綴りはファイル名（供給側）とトークン（書込先）の 2 つの独立した出所を持つ。
    一致を要求することで、片方だけ間違えた指定が**必ず**止まる。
    """
    if not paths:
        return
    pairs = sorted({pair_of(p) for p in paths})
    if len(pairs) != 1:
        raise Mt5SupplyError(
            f"銘柄の違う月別アーカイブが混ざっています: {pairs}。"
            " 1 回の取り込みが書けるのは 1 銘柄ぶんの木だけです。"
        )
    expected = str(symbol_token).partition(ingest.TOKEN_SEPARATOR)[0]
    if pairs[0] != expected:
        raise Mt5SupplyError(
            f"アーカイブの銘柄と書込先トークンの銘柄部が違います:"
            f" アーカイブ {pairs[0]!r} に対しトークン {symbol_token!r}（銘柄部 {expected!r}）。"
            " 別銘柄の木へ書き込む指定になっています。"
        )


def _assert_existing_day_matches(
    day: dt.date, rows: "Sequence[Row]", parquet: Path
) -> None:
    """既存の日別 parquet が、いま読んだ行と**同じ日**であることを確かめる。

    「在るからスキップ」では、既存が何であってもスキップする。壊れた日・欠けた日が在っても
    黙って通り、以後の実行がそれを恒久化する（工程 5 🔴-2）。照合するのは行数と先頭 / 末尾の
    UTC ms である。全行の突合は月全体を 2 度読むことになり、判定に見合わない。

    一致しなければ Fail-Stop にする理由:
        上書きも黙認もできない。上書きは「既に在る台帳を書き換えない」方針に反し、黙認は
        欠けた日を恒久化する。どちらが正しいかは入力からは決まらないので、人が決めるまで止める。
    """
    existing = journal.day_parquet_extent(parquet)
    incoming = (
        len(rows),
        server_clock.to_utc_ms(rows[0][0]),
        server_clock.to_utc_ms(rows[-1][0]),
    )
    if existing != incoming:
        raise Mt5SupplyError(
            f"既存の日別 parquet と入力が一致しません: {day} —"
            f" 既存 {existing[0]} 行（UTC ms {existing[1]}〜{existing[2]}）に対し"
            f" 入力 {incoming[0]} 行（UTC ms {incoming[1]}〜{incoming[2]}）。"
            f" 上書きはしないため中止します（{parquet}）。"
        )


def _utc_midnight_ms(day: dt.date) -> int:
    """UTC 日の 00:00 の epoch ms（日境界の比較点）。"""
    return (dt.datetime.combine(day, _MIDNIGHT, tzinfo=dt.timezone.utc) - _EPOCH) // _MILLISECOND


def ingest_months(
    zip_paths: "Iterable[Any]",
    *,
    symbol_token: str,
    data_dir: Any,
    writer: "Optional[Writer]" = None,
) -> IngestReport:
    """月 zip を年月順に **1 パス**で読み、UTC 日別 parquet を書く。

    **日を閉じて書くのは「次の UTC 日の行が到来したとき」だけ**である。月跨ぎの UTC 日
    （実測 T8: 53/75 境界）は、日が変わるまで書かずに持ち越して**1 つの parquet へ統合**
    する。持ち越しはファイル境界を跨いで生き続ける。

    範囲の端にある 2 つの日を書かない理由（工程 5 🔴-1）:
        末尾（``days_carried`` / ``rows_carried``）
            走査が終わった時点で開いている日は、次の UTC 日の行を見ていない。まだ増える
            かもしれない日を「完成品」として書くと、既存日スキップがその欠けを恒久化する
            （実測: 月分割実行で 2020-06-30 が 4,173 行のまま凍結・一括なら 5,673 行）。
        先頭（``head_dropped_day`` / ``rows_head_dropped``）
            月 zip は前月末日の途中から始まる（実測: ``ticks_JP225_2020-05.zip`` の先頭
            ラベルは ``2020.04.30``・その UTC 日は 9,289 行しかない）。走査全体の最初の
            UTC 日は**頭が欠けている可能性を排除できない**ため、末尾と同じ理由で書かない。
            端が欠けていないことを入力から示す手段が無い以上、「書かない」が唯一の
            Fail-Safe である（欠けた日を書いてから直す経路は存在しない）。

    先頭日と末尾日が同一（走査に UTC 日が 1 つしか無い）の場合は**先頭として 1 度だけ**
    数える（同じ行を 2 度数えると :attr:`IngestReport.rows_unaccounted` が壊れる）。
    """
    writer = writer or DayWriter()
    paths = sorted((Path(p) for p in zip_paths), key=month_key)
    _assert_pairs_match_token(paths, symbol_token)

    rows_read = 0
    rows_written = 0
    rows_skipped = 0
    days_written = 0
    days_skipped = 0
    rows_head_dropped = 0
    head_dropped_day: "Optional[dt.date]" = None
    days_carried = 0
    rows_carried = 0
    day: "Optional[dt.date]" = None
    head_day: "Optional[dt.date]" = None
    day_rows: "List[Row]" = []
    day_end_ms = 0
    last_utc_ms: "Optional[int]" = None
    seen_days: "List[dt.date]" = []
    progress: "List[MonthProgress]" = []

    def close_day(*, final: bool) -> None:
        """開いている日を閉じる。端の日（先頭 / 走査終了時点）は**書かない**。"""
        nonlocal rows_written, rows_skipped, days_written, days_skipped, day_rows
        nonlocal rows_head_dropped, head_dropped_day, days_carried, rows_carried
        if day is None or not day_rows:
            return
        # 気配の実在性・型・ラベル単調は段階 1 の権威が持つ（第 2 実装を作らない）。
        # 書かない日にも通すのは、壊れた行を「端だから」で見逃さないためである。
        ingest.validate_rows(day_rows, from_msc=day_rows[0][0])
        if day == head_day:
            head_dropped_day = day
            rows_head_dropped += len(day_rows)
            day_rows = []
            return
        if final:
            days_carried += 1
            rows_carried += len(day_rows)
            day_rows = []
            return
        seen_days.append(day)
        parquet = tick_m1.day_parquet_path(day, symbol=symbol_token, data_dir=data_dir)
        if parquet.is_file():
            # 既存の日は**上書きしない**（ISSUE-447 方針: 既に在る台帳を書き換えない）。
            # ただしスキップしてよいのは**同じものが在るとき**だけである。
            # 行は月ファイル単位でしか読めないため、読むこと自体は避けられない。
            # よって「読んだが書かなかった行」を報告へ出す（捨てた行を黙らせない）。
            _assert_existing_day_matches(day, day_rows, parquet)
            rows_skipped += len(day_rows)
            days_skipped += 1
        else:
            writer.write_day(day_rows, parquet)
            rows_written += len(day_rows)
            days_written += 1
        day_rows = []

    for path in paths:
        month_rows = 0
        month_days = days_written
        with open_month_zip(path) as lines:
            for row in iter_rows(lines, source=path.name):
                rows_read += 1
                month_rows += 1
                utc_ms = server_clock.to_utc_ms(row[0])
                if last_utc_ms is not None and utc_ms < last_utc_ms:
                    raise Mt5SupplyError(
                        f"UTC の時刻が戻っています: {last_utc_ms} の次に {utc_ms}"
                        f"（{path.name}・ラベル {row[0]}）。"
                    )
                last_utc_ms = utc_ms
                if utc_ms >= day_end_ms:
                    close_day(final=False)
                    day = server_clock.utc_day_of(row[0])
                    if head_day is None:
                        head_day = day
                    day_end_ms = _utc_midnight_ms(day + dt.timedelta(days=1))
                day_rows.append(row)
        progress.append(MonthProgress(
            month=month_key(path),
            rows_read=month_rows,
            days_written=days_written - month_days,
            rows_carried=len(day_rows),
        ))
    close_day(final=True)

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
        head_dropped_day=head_dropped_day,
        rows_head_dropped=rows_head_dropped,
        days_carried=days_carried,
        rows_carried=rows_carried,
        month_progress=tuple(progress),
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
