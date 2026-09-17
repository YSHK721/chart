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
    4. 第 2 定義の再出現を落とす AST 走査（台帳 ``_KNOWN_UNFIXED`` は現在**空**）。
    5. ``usecases`` の確定境界（§E）と ``rebuild`` の日窓（§F）の characterization。
       この 2 件は D-15 の是正中に見つかった同型の第 2 定義であり、2026-09-07 に
       ``server_clock`` への委譲へ置き換えた。§E / §F は置き換え**前**に書いて Green を
       確認してあり、是正の前後で挙動が 1 ビットも変わっていないことの証拠である。

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
from marketdata.mt5_ticks import (
    archive_ingest,
    fakes,
    ingest,
    journal,
    m1_chain,
    rebuild,
    server_clock,
    usecases,
)

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


#: **未是正**の第 2 定義。現在は空集合＝残債ゼロ（ISSUE-502 D-15 後続・2026-09-07）。
#:
#: 台帳（D-15）は ``ingest`` ⇔ ``archive_ingest`` の 2 実装だけを挙げていたが、走査すると
#: 同じ「UTC 日の真夜中」を自前で組む箇所がもう 2 つあった。両方とも
#: ``server_clock.utc_day_start_ms`` / ``utc_day_end_ms`` への委譲へ置き換え済みである:
#:     - ``usecases.py``  日確定の境界（``datetime.combine(day + 1日, 00:00, utc)``）→
#:       ``fromtimestamp(utc_day_end_ms(day) // 1000, utc)``。反転点の bit 等価は §E が固定する。
#:     - ``rebuild.py``   日窓 ``[真夜中, +1日)``（``pd.Timestamp(datetime.combine(...))``）→
#:       ``pd.Timestamp(utc_day_start_ms/end_ms, unit="ms")``。帰属の bit 等価は §F が固定する。
#:
#: これは免除リストではなく**台帳**である: 1 つでも増えれば Red、是正して減っても Red になり、
#: 表の更新（＝債務の明示的な解消）を強制する。
_KNOWN_UNFIXED: "set[str]" = set()


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


@pytest.mark.parametrize(
    "module, required",
    [
        ("usecases.py", {"utc_day_end_ms"}),
        ("rebuild.py", {"utc_day_start_ms", "utc_day_end_ms"}),
    ],
)
def test_the_former_second_definitions_now_call_the_shared_day_boundary(
    module: str, required: "set[str]"
) -> None:
    """委譲が**結線されている**（``_KNOWN_UNFIXED`` を空にしただけ、を落とす）。

    識別力: どちらかが自前の真夜中へ戻ると、``combine`` / ``time(0, 0)`` の走査（上の検定）と
    本検定の両方が Red になる。
    """
    # Arrange
    tree = ast.parse((_MT5_PKG / module).read_text(encoding="utf-8"))

    # Act
    called = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }

    # Assert
    assert required <= called, (
        f"{module} が {sorted(required - called)} を呼んでいません（第 2 定義が復活した疑い）"
    )


# =====================================================================
# E. usecases の確定境界（characterization・是正の前後で 1 ビットも変えない）
# =====================================================================

def _legacy_finalize_boundary(day: dt.date, grace_seconds: int) -> dt.datetime:
    """是正前に ``FinalizeDay.is_closed`` が自前で組んでいた式（**写し**）。

    期待値は実装から読み直さず、ここに保存した式から作る。実装と同じ関数を呼んで期待値を
    作れば、式が変わっても Green になる。本ファイルは AST 走査の対象外（走査は
    ``mt5_ticks/*.py`` のみ）なので、ここに写しを置いても第 2 定義とは数えない。
    """
    return dt.datetime.combine(
        day + dt.timedelta(days=1), dt.time(0, 0), tzinfo=dt.timezone.utc
    ) + dt.timedelta(seconds=grace_seconds)


_FINALIZE_DAYS = [
    dt.date(2020, 6, 2),    # 夏
    dt.date(2021, 1, 5),    # 冬
    dt.date(2026, 1, 2),
    dt.date(2026, 8, 25),
    dt.date(2026, 3, 29),   # DST 切替日（緩和しない）
    dt.date(2026, 10, 25),  # DST 切替日（緩和しない）
]

#: 境界からのずらし幅と、そのときの期待値（``now >= boundary``）。
#: マイクロ秒 1 つで反転することを固定する（ms へ丸める実装に退化すると Red）。
_FINALIZE_DELTAS = [
    (dt.timedelta(days=-1), False),
    (dt.timedelta(milliseconds=-1), False),
    (dt.timedelta(microseconds=-1), False),
    (dt.timedelta(0), True),
    (dt.timedelta(microseconds=1), True),
    (dt.timedelta(milliseconds=1), True),
]


@pytest.mark.parametrize("grace", [0, 300])
@pytest.mark.parametrize("day", _FINALIZE_DAYS)
@pytest.mark.parametrize("delta, expected", _FINALIZE_DELTAS)
def test_is_closed_flips_exactly_at_the_legacy_utc_day_boundary(
    tmp_path: Path, day: dt.date, grace: int, delta: dt.timedelta, expected: bool
) -> None:
    """``is_closed`` の反転点が、是正前の式が与える瞬間と 1 マイクロ秒も違わない。"""
    # Arrange
    clock = fakes.FixedClock(_legacy_finalize_boundary(day, grace) + delta)
    finalize = usecases.FinalizeDay(
        token=_TOKEN, data_dir=tmp_path, clock=clock, grace_seconds=grace
    )

    # Act
    got = finalize.is_closed(day)

    # Assert
    assert got is expected


def test_the_finalize_boundary_equals_the_legacy_expression_on_every_day() -> None:
    """委譲後の境界式が、是正前の式と **全 domain で** 同じ瞬間を指す。

    ``is_closed`` の検定は代表 6 日しか見ない。日ごとに食い違う欠陥はそこをすり抜けるので、
    式そのものの一致を 1970..2079 の全日で走査する（``//`` の丸め・``fromtimestamp`` の
    解釈が特定の日でずれる、を落とす）。
    """
    # Arrange
    epoch_day = dt.date(1970, 1, 1)

    # Act / Assert
    for i in range(0, 40_000):
        day = epoch_day + dt.timedelta(days=i)
        delegated = dt.datetime.fromtimestamp(
            server_clock.utc_day_end_ms(day) // 1000, dt.timezone.utc
        )
        assert delegated == _legacy_finalize_boundary(day, 0)


@pytest.mark.parametrize("delta, expected", _FINALIZE_DELTAS)
def test_a_naive_clock_is_read_as_utc_at_the_same_boundary(
    tmp_path: Path, delta: dt.timedelta, expected: bool
) -> None:
    """naive な時計（検定用の固定時計）でも同じ瞬間に反転する（UTC としての読み替え）。"""
    # Arrange
    day = dt.date(2026, 8, 25)
    aware = _legacy_finalize_boundary(day, 300) + delta
    clock = fakes.FixedClock(aware.replace(tzinfo=None))
    finalize = usecases.FinalizeDay(token=_TOKEN, data_dir=tmp_path, clock=clock)

    # Act / Assert
    assert finalize.is_closed(day) is expected


# =====================================================================
# F. rebuild の日窓 ``[真夜中, +1 日)``（characterization）
# =====================================================================
#
# 窓は ``rebuild_day`` のローカル変数なので、**観測できる形**で固定する: 当日 00:00 の外れ値は
# 是正で消え（＝真夜中は当日の内側）、翌日 00:00 の外れ値は残る（＝終端は含まない）。前日の
# 外れ値も残る（＝始端より前は触らない）。式を読み直さず、帰属そのものを見る。

_RB_REF = "jp225_mt5_boundary"
_RB_DAY = dt.date(2026, 8, 25)
_RB_PREV = dt.date(2026, 8, 24)
_RB_NEXT = dt.date(2026, 8, 26)
#: 2026-08 は夏（EEST = UTC+3）。ラベル ms ＝ UTC ms + 3h（DST を跨がない日だけを使う）。
_RB_SUMMER_OFFSET_MS = 3 * 3600 * 1000
#: ISSUE-107 と同型の配信欠損ファントム帯（日内 close 中央値から大きく外れる）。
_RB_PHANTOM_PRICE = 15100.0
_RB_CLEAN_PRICE = 66000.0


@pytest.fixture(autouse=True)
def _registered_rb_ref(monkeypatch, tmp_path: Path):
    """合成 ref を台帳へ一時登録する（価格基準の唯一源は台帳・ISSUE-511 段階 3 の段階 6）。

    増分経路（``m1_chain``）も権威経路（``rebuild``）も基準を渡さず ref から引くようになった
    ので、登録が無いと「台帳に無い ref では price_basis が必須」で止まる。実在の MT5 系列と同じ
    基準を名乗らせる（本節が測るのは日窓の帰属であって基準ではない）。
    """
    from marketdata import dataset_registry
    from marketdata.dataset_registry import REGISTRY, DatasetDescriptor

    monkeypatch.setitem(REGISTRY, _RB_REF, DatasetDescriptor(
        path=tmp_path / f"{_RB_REF}_m1.csv", symbol="JP225", tick=True,
        price_basis=dataset_registry.tick_price_basis("jp225_mt5"), vendor="mt5",
    ))


def _rb_label_ms(utc: dt.datetime) -> int:
    """UTC の壁時計 → サーバ時刻ラベル ms（検定入力の生成のみ）。"""
    epoch = dt.datetime(1970, 1, 1, tzinfo=dt.timezone.utc)
    delta = utc.replace(tzinfo=dt.timezone.utc) - epoch
    return delta // dt.timedelta(milliseconds=1) + _RB_SUMMER_OFFSET_MS


def _rb_rows(day: dt.date, minutes: "list[int]", phantom_minutes: "set[int]"):
    """``day`` の指定 UTC 分に 20 tick ずつ置く。戻り値は ``(rows, until)``。"""
    base = dt.datetime(day.year, day.month, day.day)
    rows = []
    for minute in minutes:
        for i in range(20):
            when = base + dt.timedelta(minutes=minute, seconds=i * 3)
            price = (
                _RB_PHANTOM_PRICE if minute in phantom_minutes
                else _RB_CLEAN_PRICE + minute * 0.5 + i * 0.1
            )
            rows.append((_rb_label_ms(when), price, price + 10.0))
    until = (base + dt.timedelta(minutes=max(minutes) + 1)).replace(
        tzinfo=dt.timezone.utc
    )
    return rows, until


def _rb_publish(day: dt.date, minutes, phantom_minutes, tmp_path: Path) -> None:
    """増分経路で受け取った状態にする（ジャーナル → 確定 → 増分 M1 追記）。"""
    rows, until = _rb_rows(day, minutes, phantom_minutes)
    journal.append(day, rows, symbol=_TOKEN, data_dir=tmp_path)
    journal.finalize(day, symbol=_TOKEN, data_dir=tmp_path)
    m1_chain.append_m1_for_closed_minutes(
        rows, ref=_RB_REF, data_dir=tmp_path, until=until
    )


def _rb_closes(tmp_path: Path) -> "dict[str, float]":
    """M1 CSV を ``date -> close`` で読む。"""
    import pandas as pd  # 遅延 import: 本節だけが pandas を要る。

    frame = pd.read_csv(tick_m1.m1_csv_path(ref=_RB_REF, data_dir=tmp_path))
    return {str(d): float(c) for d, c in zip(frame["date"], frame["close"])}


@pytest.fixture()
def rebuilt_boundary_store(tmp_path: Path) -> "dict[str, float]":
    """前日 23:59 / 当日 00:00 / 翌日 00:00 に外れ値を置き、**当日だけ**再構築した結果。"""
    # Arrange: 各日は「清浄 9 分 + 外れ値 1 分」＝中央値が清浄側に決まる構成。
    _rb_publish(_RB_PREV, list(range(1430, 1440)), {1439}, tmp_path)   # 23:50..23:59
    _rb_publish(_RB_DAY, list(range(0, 5)) + list(range(1435, 1440)), {0}, tmp_path)
    _rb_publish(_RB_NEXT, list(range(0, 10)), {0}, tmp_path)

    # Act
    rebuild.rebuild_day(
        _RB_DAY, symbol=_TOKEN, ref=_RB_REF, data_dir=tmp_path, update_rollups=False
    )
    return _rb_closes(tmp_path)


def test_midnight_of_the_day_is_inside_the_rebuilt_window(
    rebuilt_boundary_store: "dict[str, float]",
) -> None:
    """当日 00:00 の外れ値バーは是正で消える（真夜中ちょうどは当日の内側）。"""
    assert "2026-08-25 00:00:00" not in rebuilt_boundary_store


def test_midnight_of_the_next_day_is_outside_the_rebuilt_window(
    rebuilt_boundary_store: "dict[str, float]",
) -> None:
    """翌日 00:00 の外れ値バーは残る（終端は含まない＝半開区間）。"""
    assert rebuilt_boundary_store["2026-08-26 00:00:00"] == _RB_PHANTOM_PRICE


def test_the_last_minute_of_the_previous_day_is_outside_the_rebuilt_window(
    rebuilt_boundary_store: "dict[str, float]",
) -> None:
    """前日 23:59 の外れ値バーは残る（始端より前は触らない）。"""
    assert rebuilt_boundary_store["2026-08-24 23:59:00"] == _RB_PHANTOM_PRICE


def test_the_clean_minutes_of_the_day_survive_the_rebuild(
    rebuilt_boundary_store: "dict[str, float]",
) -> None:
    """当日の清浄バーは残る（窓ごと消していないことの反証）。"""
    assert "2026-08-25 00:01:00" in rebuilt_boundary_store
    assert "2026-08-25 23:59:00" in rebuilt_boundary_store


def test_the_rebuild_window_equals_the_legacy_expression_on_every_day() -> None:
    """委譲後の日窓が、是正前の式と **全 domain で** 同じ 2 点を指す。

    ``rebuild_day`` を通す検定は 1 日しか見ない。また委譲後の ``Timestamp`` は解像度がミリ秒
    （是正前はマイクロ秒）になるため、**値の一致**と、**より細かい解像度の index に対する
    半開判定が変わらないこと**の両方を固定する（pandas が解像度跨ぎの比較規則を変えたら Red）。
    """
    import pandas as pd  # 遅延 import: 本節だけが pandas を要る。

    # Arrange
    epoch_day = dt.date(1970, 1, 1)

    # Act / Assert
    for i in range(0, 40_000, 7):
        day = epoch_day + dt.timedelta(days=i)
        legacy_start = pd.Timestamp(
            dt.datetime.combine(pd.Timestamp(day).date(), dt.time(0, 0))
        )
        legacy_end = legacy_start + pd.Timedelta(days=1)
        start = pd.Timestamp(server_clock.utc_day_start_ms(day), unit="ms")
        end = pd.Timestamp(server_clock.utc_day_end_ms(day), unit="ms")
        assert (start, end) == (legacy_start, legacy_end)

    # 半開判定そのもの: 始端の 1 マイクロ秒前は外、始端ちょうどは内、終端ちょうどは外。
    day = dt.date(2026, 8, 25)
    start = pd.Timestamp(server_clock.utc_day_start_ms(day), unit="ms")
    end = pd.Timestamp(server_clock.utc_day_end_ms(day), unit="ms")
    index = pd.DatetimeIndex(
        [
            start - pd.Timedelta(microseconds=1),
            start,
            end - pd.Timedelta(microseconds=1),
            end,
        ]
    ).as_unit("us")
    assert list((index >= start) & (index < end)) == [False, True, True, False]


# =====================================================================
# G. 計算量検定（絶対命令）— 委譲が「作ってから捨てる」計算を持ち込んでいない
# =====================================================================
#
# 固定するのは**無駄の不在**であって回数そのものではない。回数を期待値に焼き込むと、
# 浪費が仕様へ昇格する（ISSUE-450）。よってここでは
#   (1) 発行した日境界計算 − 判定に使った日境界計算 = 0
#   (2) 入力（保有日数・M1 行数）を増やしても発行が増えない（2 点でオーダーを表明）
# の 2 つだけを述べる。

def _spy_on_day_start(monkeypatch) -> "list[dt.date]":
    """``utc_day_start_ms`` に渡った日を溜める（``utc_day_end_ms`` は本関数へ委譲する）。"""
    issued: "list[dt.date]" = []
    real = server_clock.utc_day_start_ms

    def _spy(day):
        issued.append(day)
        return real(day)

    monkeypatch.setattr(server_clock, "utc_day_start_ms", _spy)
    return issued


@pytest.mark.parametrize("day_count", [2, 9])
def test_finalize_issues_one_day_boundary_per_day_it_actually_judges(
    tmp_path: Path, monkeypatch, day_count: int
) -> None:
    """確定判定は「判定した日」の数しか日境界を発行しない（保有日数を増やしても 1 日 1 回）。

    識別力: 判定のたびに全日ぶんの境界を組み直す実装へ退化すると、発行が日数の 2 乗で
    増えて Red になる。
    """
    # Arrange: どの日もまだ閉じていない時計（＝全日を判定するが 1 日も確定しない）。
    days = [dt.date(2026, 8, 1) + dt.timedelta(days=i) for i in range(day_count)]
    clock = fakes.FixedClock(dt.datetime(2026, 8, 1, 0, 0, tzinfo=dt.timezone.utc))
    finalize = usecases.FinalizeDay(token=_TOKEN, data_dir=tmp_path, clock=clock)
    issued = _spy_on_day_start(monkeypatch)

    # Act
    got = finalize(days=days)

    # Assert: 判定に使った日数 == 発行数（差 0）。確定は 0 件なので書込も 0。
    assert got == {}
    assert len(issued) == len(days)


def test_a_day_short_circuited_by_the_observed_day_issues_no_boundary_at_all(
    tmp_path: Path, monkeypatch
) -> None:
    """``latest_observed_day`` で即決できる日は日境界を **1 回も** 発行しない（無駄の不在）。"""
    # Arrange
    days = [dt.date(2026, 8, 1) + dt.timedelta(days=i) for i in range(5)]
    clock = fakes.FixedClock(dt.datetime(2026, 8, 1, 0, 0, tzinfo=dt.timezone.utc))
    finalize = usecases.FinalizeDay(token=_TOKEN, data_dir=tmp_path, clock=clock)
    issued = _spy_on_day_start(monkeypatch)

    # Act: 観測済みの日が全日より新しい＝時計を見るまでもなく全日が閉じている。
    finalize.is_closed(days[0], latest_observed_day=dt.date(2026, 9, 1))

    # Assert
    assert issued == []


@pytest.mark.parametrize("minutes", [5, 40])
def test_the_rebuild_window_is_issued_once_regardless_of_the_m1_length(
    tmp_path: Path, monkeypatch, minutes: int
) -> None:
    """再構築の日窓の発行数が M1 行数で増えない（行ごとに窓を組み直す実装を落とす）。

    回数リテラルは焼き込まない。固定するのは「発行した日境界が当日とその翌日に限られる」
    ことと、「入力長 5 分 / 40 分の 2 点で発行が変わらない」ことである。
    """
    # Arrange: 清浄日（差分なし＝書込 0 の経路）で窓の発行だけを測る。
    _rb_publish(_RB_DAY, list(range(minutes)), set(), tmp_path)
    issued = _spy_on_day_start(monkeypatch)

    # Act
    rebuild.rebuild_day(
        _RB_DAY, symbol=_TOKEN, ref=_RB_REF, data_dir=tmp_path, update_rollups=False
    )

    # Assert: 触れた日は当日と翌日だけ（半開区間の 2 端）。件数は入力長に依らない。
    assert set(issued) == {_RB_DAY, _RB_DAY + dt.timedelta(days=1)}
    assert len(issued) == len(set(issued))
