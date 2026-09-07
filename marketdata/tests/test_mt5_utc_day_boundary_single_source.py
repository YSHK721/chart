"""UTC 日境界の**唯一源**を固定する（ISSUE-502 D-15）。

何が壊れていたか（SOLID 精査 2026-09-06 の実測）:
    「行がどの UTC 日に属すか」を決める規則が 2 実装あった。
    ``mt5_ticks/ingest.py`` の :func:`~marketdata.mt5_ticks.ingest.split_by_utc_day` は
    :func:`~marketdata.mt5_ticks.server_clock.utc_day_of` の返す日を逐次比較して群を切り、
    ``mt5_ticks/archive_ingest.py`` は自前の ``_utc_midnight_ms``（真夜中 epoch ms）を閾値に
    して切っていた。**群の作り方**（逐次比較 vs 閾値）は経路の都合で違ってよいが、
    **日境界の定義**（真夜中 ms・比較の向き・境界値の帰属）が 2 つあると、片方だけを直した
    日に日 partition がまるごと 1 日ずれる。ずれた台帳は「取れていない」のか「壊れている」のか
    後から区別できない。

是正前の実測（2026-09-07・境界値の帰属は 2 実装で一致していた）:
    ちょうど UTC 真夜中の tick は両実装とも**新しい日**へ、真夜中 -1ms は両実装とも**前日**へ
    帰属した（夏 2020-06-02 / 冬 2021-01-05 / 2026-01-02 / 2026-08-04 の 4 点）。
    半開区間 ``[start, end)`` の内側 7,200 probe（2020-01-01 から 2,400 日）でも
    ``utc_day_of`` との不一致は 0 件。よって統合は挙動を変えない。

本検定が固定するもの:
    1. 境界値の帰属（characterization）— 真夜中ちょうどは新しい日、その 1ms 前は前日。
    2. 半開区間と ``utc_day_of`` の恒等式 — 定義が 1 つであることの表明。
    3. 2 経路の日 partition 一致 — 実 ``ingest_months`` の書込単位と
       ``split_by_utc_day`` の群が同じであること。
    4. 第 2 定義の再出現を落とす AST 走査。

書込は必ず ``tmp_path`` の下だけで行う（``data/`` へは 1 バイトも書かない）。
構造: Arrange-Act-Assert（AAA）。
"""
from __future__ import annotations

import ast
import datetime as dt
import zipfile
from pathlib import Path

import pytest

from marketdata import tick_m1
from marketdata.mt5_ticks import archive_ingest, ingest, server_clock

_MT5_PKG = Path(__file__).resolve().parents[1] / "mt5_ticks"

_HEADER = "<DATE>\t<TIME>\t<BID>\t<ASK>\t<LAST>\t<VOLUME>"
_TOKEN = "JP225@OANDA-Japan-MT5-Live"

#: 冬（EET = UTC+2）の実測固定点に基づくラベル。``server_clock`` の固定点検定
#: （``test_mt5_server_clock.py``: 2021-01 = 17:00 ラベル = 冬 UTC+2）と同じ季節を使う。
#: ここではラベルと**期待する UTC 日**を手で書き下す（実装から読み直さない＝独立な期待値）。
_WINTER_ROWS = [
    ("2021.01.04 02:00:00.000", dt.date(2021, 1, 4)),   # UTC 2021-01-04 00:00:00 ちょうど
    ("2021.01.04 12:00:00.000", dt.date(2021, 1, 4)),
    ("2021.01.05 01:59:59.999", dt.date(2021, 1, 4)),   # 真夜中 -1ms は前日
    ("2021.01.05 02:00:00.000", dt.date(2021, 1, 5)),   # 真夜中ちょうどは新しい日
    ("2021.01.05 12:00:00.000", dt.date(2021, 1, 5)),
    ("2021.01.06 02:00:00.000", dt.date(2021, 1, 6)),
    ("2021.01.06 23:30:00.000", dt.date(2021, 1, 6)),
    ("2021.01.07 02:00:00.000", dt.date(2021, 1, 7)),
]


def _line(label: str, bid: float, ask: float) -> str:
    """アーカイブ 1 行（``LAST`` / ``VOLUME`` は実ファイルと同じく空）。"""
    return f"{label.replace(' ', chr(9))}\t{bid}\t{ask}\t\t"


def _write_month(directory: Path, month: str, labels: "list[str]") -> Path:
    """``ticks_JP225_<month>.zip`` を実ファイルと同じ形式で書く（検定入力）。"""
    path = directory / f"ticks_JP225_{month}.zip"
    body = [_HEADER] + [
        _line(label, 19900.0 + i, 19910.0 + i) for i, label in enumerate(labels)
    ]
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(f"ticks_JP225_{month}.csv", "\n".join(body) + "\n")
    return path


class _SpyWriter:
    """書いた (path, rows) をそのまま溜める writer（本物は書かない）。"""

    def __init__(self) -> None:
        self.days: "list[tuple[Path, list]]" = []
        self.markers: "list[Path]" = []

    def write_day(self, rows, path: Path) -> None:
        self.days.append((path, list(rows)))

    def write_marker(self, path: Path) -> None:
        self.markers.append(path)


# =====================================================================
# A. 境界値の帰属（characterization・是正の前後で 1 ビットも変えない）
# =====================================================================

@pytest.mark.parametrize("label, expected_day", _WINTER_ROWS)
def test_the_utc_day_of_a_label_matches_the_hand_written_expectation(
    label: str, expected_day: dt.date
) -> None:
    """真夜中ちょうど・その 1ms 前を含む代表点で、UTC 日の帰属が期待値と一致する。"""
    # Arrange
    rows = archive_ingest.parse_lines([_HEADER, _line(label, 1.0, 2.0)])

    # Act
    got = server_clock.utc_day_of(rows[0][0])

    # Assert
    assert got == expected_day


def test_midnight_belongs_to_the_new_day_and_one_millisecond_before_belongs_to_the_old() -> None:
    """境界値の帰属を 1 つの検定として明示する（半開区間 ``[start, end)`` の表明）。"""
    # Arrange
    rows = archive_ingest.parse_lines(
        [_HEADER]
        + [_line(label, 1.0, 2.0) for label, _ in _WINTER_ROWS]
    )
    before = rows[2][0]     # 2021.01.05 01:59:59.999
    exactly = rows[3][0]    # 2021.01.05 02:00:00.000

    # Act
    day_before = server_clock.utc_day_of(before)
    day_exactly = server_clock.utc_day_of(exactly)

    # Assert: 期待値は独立な式（1970-01-01 からの日数 × 86,400,000）で作る。
    midnight_ms = (dt.date(2021, 1, 5) - dt.date(1970, 1, 1)).days * 86_400_000
    assert day_before == dt.date(2021, 1, 4)
    assert day_exactly == dt.date(2021, 1, 5)
    assert server_clock.to_utc_ms(exactly) == midnight_ms
    assert server_clock.utc_day_start_ms(day_exactly) == midnight_ms
    assert server_clock.to_utc_ms(before) == midnight_ms - 1
    assert server_clock.utc_day_end_ms(day_before) == midnight_ms


# =====================================================================
# B. 半開区間の定義（唯一源の恒等式）
# =====================================================================

def test_the_day_window_is_derived_from_the_epoch_without_a_second_formula() -> None:
    """``utc_day_start_ms`` が「1970-01-01 からの日数 × 86,400,000」と一致する。

    期待値は**独立な式**（日数の掛け算）で作る。実装と同じ式で期待値を作れば、
    式が間違っても Green になる。
    """
    # Arrange
    epoch_day = dt.date(1970, 1, 1)

    # Act / Assert
    for i in range(0, 21_000, 37):
        day = epoch_day + dt.timedelta(days=i)
        assert server_clock.utc_day_start_ms(day) == i * 86_400_000
        assert server_clock.utc_day_end_ms(day) == (i + 1) * 86_400_000


def test_every_label_falls_inside_the_half_open_window_of_its_own_day() -> None:
    """``utc_day_of(l) == d`` ⟺ ``start(d) <= to_utc_ms(l) < end(d)``（定義が 1 つである表明）。"""
    # Arrange: 2020..2026 を 7 時間刻みで走査（DST 切替を毎年 2 回跨ぐ）。
    start_label = (dt.datetime(2020, 1, 1, tzinfo=dt.timezone.utc)
                   - dt.datetime(1970, 1, 1, tzinfo=dt.timezone.utc)) // dt.timedelta(milliseconds=1)
    step = 7 * 3600 * 1000
    labels = [start_label + step * i for i in range(0, 8_800)]

    # Act / Assert
    for label_ms in labels:
        day = server_clock.utc_day_of(label_ms)
        utc_ms = server_clock.to_utc_ms(label_ms)
        assert server_clock.utc_day_start_ms(day) <= utc_ms < server_clock.utc_day_end_ms(day)


# =====================================================================
# C. 2 経路の日 partition が一致する（外形は違ってよいが境界は同じ）
# =====================================================================

def test_the_archive_threshold_path_writes_the_same_day_groups_as_split_by_utc_day(
    tmp_path: Path,
) -> None:
    """閾値方式（archive_ingest）の書込単位が、逐次比較方式（ingest）の群と一致する。

    走査の端（先頭日・末尾日）は archive 側が意図的に書かないので、比較対象から外す。
    外すのは**端の日だけ**であり、境界の定義そのものは比較に残る。
    """
    # Arrange
    labels = [label for label, _ in _WINTER_ROWS]
    _write_month(tmp_path, "2021-01", labels)
    rows = archive_ingest.parse_lines([_HEADER] + [_line(x, 1.0, 2.0) for x in labels])
    expected = {day: chunk for day, chunk in ingest.split_by_utc_day(rows)}
    head, tail = dt.date(2021, 1, 4), dt.date(2021, 1, 7)
    writer = _SpyWriter()

    # Act
    report = archive_ingest.ingest_months(
        [tmp_path / "ticks_JP225_2021-01.zip"],
        symbol_token=_TOKEN,
        data_dir=tmp_path / "out",
        writer=writer,
    )

    # Assert: 書かれた日の集合が、端を除いた split_by_utc_day の群と一致する。
    written = {
        path: [(r[0]) for r in chunk] for path, chunk in writer.days
    }
    expected_paths = {
        tick_m1.day_parquet_path(day, symbol=_TOKEN, data_dir=tmp_path / "out"):
            [r[0] for r in chunk]
        for day, chunk in expected.items() if day not in (head, tail)
    }
    assert written == expected_paths, (
        "閾値方式と逐次比較方式で日 partition が食い違っています（境界の定義が 2 つある証拠）"
    )
    assert report.head_dropped_day == head
    assert report.days_carried == 1


# =====================================================================
# D. 第 2 定義の再出現を落とす（AST 走査）
# =====================================================================

def _mt5_modules() -> "list[Path]":
    return sorted(
        p for p in _MT5_PKG.glob("*.py")
        if p.name != "__init__.py"
    )


def _files_calling(attribute: str) -> "list[str]":
    """``*.<attribute>(...)`` を呼ぶモジュール名（``mt5_ticks`` 配下）。"""
    hits = []
    for path in _mt5_modules():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                    and node.func.attr == attribute:
                hits.append(path.name)
                break
    return sorted(set(hits))


def _files_constructing_midnight_time() -> "list[str]":
    """真夜中の時刻オブジェクト生成（全引数が定数 0 の time 呼び出し）を書くモジュール名。"""
    hits = []
    for path in _mt5_modules():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = node.func.attr if isinstance(node.func, ast.Attribute) else \
                getattr(node.func, "id", "")
            if name != "time" or not node.args:
                continue
            if all(isinstance(a, ast.Constant) and a.value == 0 for a in node.args):
                hits.append(path.name)
                break
    return sorted(set(hits))


#: **未是正**の第 2 定義（ISSUE-502 D-15 の是正中に新たに実測で見つかった 2 件）。
#:
#: 台帳（D-15）は ``ingest`` ⇔ ``archive_ingest`` の 2 実装だけを挙げていたが、走査すると
#: 同じ「UTC 日の真夜中」を自前で組む箇所がもう 2 つあった:
#:     - ``usecases.py``  日確定の境界を ``datetime.combine(day + 1日, 00:00, utc)`` で計算
#:     - ``rebuild.py``   日窓 ``[真夜中, +1日)`` を ``pd.Timestamp(datetime.combine(...))`` で計算
#: どちらも本タスク（D-14 / D-15 の是正）の担当範囲外のファイルであり、**勝手に触らない**。
#:
#: これは免除リストではなく**台帳**である: 1 つでも増えれば Red、是正して減っても Red になり、
#: 表の更新（＝債務の明示的な解消）を強制する。
_KNOWN_UNFIXED = {"usecases.py", "rebuild.py"}


def test_only_server_clock_computes_a_utc_midnight() -> None:
    """真夜中の組み立て（``datetime.combine`` / ``time(0, 0)``）が純層 1 箇所に集約されている。

    識別力: ``archive_ingest`` へ ``_utc_midnight_ms`` を書き戻すと Red になる。
    新しいモジュールが自前の真夜中を持っても Red になる（台帳が増えるため）。
    """
    # Act
    got = set(_files_calling("combine")) | set(_files_constructing_midnight_time())

    # Assert
    assert got == {"server_clock.py"} | _KNOWN_UNFIXED, (
        "UTC 日の真夜中を自前で組むモジュールの集合が変わりました。"
        " 増えたなら server_clock.utc_day_start_ms / utc_day_end_ms へ委譲してください。"
        " 減った（是正された）なら _KNOWN_UNFIXED から外してください。"
    )
    assert not ({"ingest.py", "archive_ingest.py"} & got), (
        "D-15 の 2 経路に日境界の第 2 定義が復活しています"
    )


def test_archive_ingest_no_longer_owns_a_day_boundary_helper() -> None:
    """``archive_ingest`` に日境界の私有ヘルパが残っていない（名前ごと消えている）。"""
    assert not hasattr(archive_ingest, "_utc_midnight_ms")
    assert not hasattr(archive_ingest, "_MIDNIGHT")


def test_archive_ingest_actually_calls_the_shared_day_boundary() -> None:
    """委譲が**結線されている**（受け口を作っただけで呼んでいない、を落とす）。"""
    # Arrange
    tree = ast.parse((_MT5_PKG / "archive_ingest.py").read_text(encoding="utf-8"))

    # Act
    called = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }

    # Assert
    assert "utc_day_end_ms" in called, (
        "archive_ingest が server_clock.utc_day_end_ms を呼んでいません（第 2 定義が復活した疑い）"
    )
