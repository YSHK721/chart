"""MT5 供給常駐の単一書き手ロック（ISSUE-530）。

2026-09-23、取り込み常駐が二重に起動し、**受信の一次記録**（ndjson ジャーナル）へ同時に追記して
壊した（重複 1,225 行・ms 逆行 292 件）。原因は「二重起動を機械的に禁止する仕組みが 1 つも
無い」ことである（grep -nE "lock|flock" tools/mt5_tick_watch.py の一致は clock だけだった）。
indicator UI の serve.sh（常駐を起動する側）を実行し直すだけで再発する。

ライブ供給側は ISSUE-488 で同じ欠陥を根治済みであり、錠の実体は中立核
``common/writer_lock.py`` が単一定義で持つ（手書き複製をしない）。本検定が固定するのは、
MT5 側が**その錠を、自分の系列を守る別の名前で、正しい順序で**使うことである:

    1. 1 本目が持っている間、2 本目は即時に拒まれる（案内付き・書込 0）。
    2. 案内に先行 PID とロックのパスが載る。
    3. 錠はライブ側とは別ファイル（守る対象が別の木・別の系列）。
    4. 順序: 列形照合（読むだけ）→ ロック取得 → 端末を叩く／台帳へ書くどの経路よりも先。
    5. 計算量: ロック取得は起動時 1 回であり、周期数に比例して増えない（発行 − 使用 = 0）。

実ネットワークにも MetaTrader5 にも依存しない（供給元は注入する）。データは tmp_path に閉じる。
"""
from __future__ import annotations

import datetime as dt
import subprocess
import sys
from pathlib import Path

import pytest

from marketdata import tick_m1
from marketdata.mt5_ticks import fakes
from tools import live_tick_watch
from tools import mt5_tick_watch as watch

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SECRET = "single-writer-test-secret"
_SUMMER_OFFSET_MS = 3 * 3600 * 1000
_START = dt.datetime(2026, 8, 25, 9, 0)
_NOW = dt.datetime(2026, 8, 25, 9, 30, tzinfo=dt.timezone.utc)
_FROM = "2026-08-25 12:00:00"


def _label_ms(utc: dt.datetime) -> int:
    return int(utc.replace(tzinfo=dt.timezone.utc).timestamp() * 1000) + _SUMMER_OFFSET_MS


def _tape(start: dt.datetime, *, minutes: int, per_minute: int = 4):
    return [
        (_label_ms(start + dt.timedelta(minutes=m, seconds=i * (60 // per_minute))),
         66000.0 + m + i * 0.1, 66010.0 + m + i * 0.1)
        for m in range(minutes) for i in range(per_minute)
    ]


@pytest.fixture()
def secret(monkeypatch):
    monkeypatch.setenv(watch.SECRET_ENV, _SECRET)
    return _SECRET


def _settings(data_dir: Path, *extra: str):
    return watch.settings_from(watch.build_parser().parse_args(
        ["--data-dir", str(data_dir), "--from", _FROM, *extra]
    ))


def _tree(root: Path) -> "dict[str, bytes]":
    return {
        str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()
    }


def _child_holding(data_dir: Path) -> subprocess.Popen:
    """別プロセスに MT5 常駐の錠を握らせる（``locked`` 印字で獲得済み）。"""
    child = subprocess.Popen(
        [sys.executable, "-c", (
            "import sys, time; sys.path.insert(0, sys.argv[1]);"
            "from common.writer_lock import acquire_writer_lock;"
            "from tools.mt5_tick_watch import WRITER_LOCK_FILENAME;"
            "h = acquire_writer_lock(sys.argv[2], filename=WRITER_LOCK_FILENAME,"
            " name='mt5_tick_watch', hint='');"
            "print('locked', flush=True); time.sleep(60)"
        ), str(_REPO_ROOT), str(data_dir)],
        stdout=subprocess.PIPE, text=True,
    )
    assert child.stdout.readline().strip() == "locked"
    return child


# =====================================================================
# 1. 2 本目が拒まれる
# =====================================================================
def test_a_second_daemon_is_refused_while_the_first_holds(tmp_path, secret, capsys):
    """TC-M1: 先行の常駐が居るとき、2 本目は周期を 1 つも回さずに終わる。

    ジャーナルへ 1 行も追記しない（ISSUE-530 の被害はまさにこの追記だった）。
    """
    # Arrange
    child = _child_holding(tmp_path)
    source = fakes.CountingTickSource(_tape(_START, minutes=3))
    before = _tree(tmp_path)

    # Act
    try:
        code = watch.run(
            _settings(tmp_path), source=source, clock=fakes.FixedClock(_NOW),
            cycles=1, sleep=lambda _s: None,
        )
    finally:
        child.kill()

    # Assert
    assert code != watch.EXIT_OK
    assert source.cycle_fetches == 0
    assert _tree(tmp_path) == before        # 1 バイトも書いていない


def test_the_refusal_names_the_prior_pid_and_the_lock_path(tmp_path, secret, capsys):
    """TC-M2: 案内は先行 PID とロックのパスを載せる（運用者が次の一手を打てる）。"""
    # Arrange
    child = _child_holding(tmp_path)

    # Act
    try:
        watch.run(
            _settings(tmp_path), source=fakes.CountingTickSource(_tape(_START, minutes=3)),
            clock=fakes.FixedClock(_NOW), cycles=1, sleep=lambda _s: None,
        )
    finally:
        child.kill()

    # Assert
    err = capsys.readouterr().err
    assert str(child.pid) in err, f"先行 PID が案内に無い: {err}"
    assert str(tmp_path / watch.WRITER_LOCK_FILENAME) in err, f"ロックのパスが案内に無い: {err}"
    assert "Traceback" not in err


def test_a_second_daemon_exits_with_the_documented_code(tmp_path, secret):
    """TC-M3: 二重起動は既定で即時に止まり、終了コードで運用者へ伝わる（未知の落ち方にしない）。

    ライブ供給（``tools/live_tick_watch.py``）が二重起動に使っている値と同じ 2 を返す
    ＝運用者が 2 本の常駐を同じ読み方で扱える。
    """
    # Arrange
    child = _child_holding(tmp_path)

    # Act
    try:
        code = watch.main(
            ["--data-dir", str(tmp_path), "--once", "--from", _FROM, "--quiet"],
            source=fakes.CountingTickSource(_tape(_START, minutes=3)),
            clock=fakes.FixedClock(_NOW),
        )
    finally:
        child.kill()

    # Assert
    assert code == watch.EXIT_USAGE


# =====================================================================
# 2. 順序（照合 → ロック → 端末・台帳）
# =====================================================================
def test_the_lock_is_taken_before_the_terminal_is_probed(tmp_path, secret):
    """TC-M4: 拒まれる 2 本目は端末を 1 度も叩かない（どのみち書けない相手を叩かない）。"""
    # Arrange
    child = _child_holding(tmp_path)
    source = fakes.CountingTickSource(_tape(_START, minutes=3))

    # Act
    try:
        watch.run(
            _settings(tmp_path), source=source, clock=fakes.FixedClock(_NOW),
            cycles=1, sleep=lambda _s: None,
        )
    finally:
        child.kill()

    # Assert
    assert (source.token_probes, source.cycle_fetches) == (0, 0)


def test_the_column_form_check_runs_before_the_lock(tmp_path, secret):
    """TC-M5: 列形の食い違いで止まるときは錠を取らない（読むだけの検査が先）。

    先に錠を取ってから止まると、``takeover`` を持つ側では先行の書き手を退去させたうえで
    自分も終わることになる。順序はライブ側（ISSUE-488）と同じ規律に揃える。
    """
    # Arrange: 台帳が spread を宣言していない系列に、spread 付きの既存 CSV を置く。
    path = tick_m1.m1_csv_path(ref=watch.DEFAULT_REF, data_dir=tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("date,open,high,low,close,spread\n", encoding="utf-8")

    # Act
    code = watch.run(
        _settings(tmp_path), source=fakes.CountingTickSource(_tape(_START, minutes=3)),
        clock=fakes.FixedClock(_NOW), cycles=1, sleep=lambda _s: None,
    )

    # Assert
    assert code == watch.EXIT_FAIL_STOP
    assert not (tmp_path / watch.WRITER_LOCK_FILENAME).exists()


# =====================================================================
# 3. 錠はライブ側と別（守る対象が別の木・別の系列）
# =====================================================================
def test_the_mt5_lock_is_a_different_file_from_the_live_lock(tmp_path, secret):
    """TC-M6: ライブ供給が同じ data_dir で稼働していても MT5 の起動は止まらない。

    止めたいのは「同じ系列への 2 本目」であって、別系列の常駐ではない。
    """
    # Arrange
    live_handle = live_tick_watch.acquire_writer_lock(tmp_path)
    source = fakes.CountingTickSource(_tape(_START, minutes=3))

    # Act
    try:
        code = watch.run(
            _settings(tmp_path), source=source, clock=fakes.FixedClock(_NOW),
            cycles=1, sleep=lambda _s: None,
        )
    finally:
        live_handle.close()

    # Assert
    assert code == watch.EXIT_OK
    assert source.cycle_fetches == 1
    assert watch.WRITER_LOCK_FILENAME != live_tick_watch._WRITER_LOCK_FILENAME
    assert (tmp_path / watch.WRITER_LOCK_FILENAME).is_file()


def test_the_lock_is_released_when_the_prior_holder_dies(tmp_path, secret):
    """TC-M7: 先行が死んでいれば起動は止まらない（錠はカーネルが持つ・偽陽性が無い）。"""
    # Arrange
    child = _child_holding(tmp_path)
    child.kill()
    child.wait(timeout=10)
    source = fakes.CountingTickSource(_tape(_START, minutes=3))

    # Act
    code = watch.run(
        _settings(tmp_path), source=source, clock=fakes.FixedClock(_NOW),
        cycles=1, sleep=lambda _s: None,
    )

    # Assert
    assert (code, source.cycle_fetches) == (watch.EXIT_OK, 1)


# =====================================================================
# 4. 計算量（発行 − 使用 = 0）
# =====================================================================
class _LockSpy:
    """ロック取得の Test Spy（獲得した錠と、その時点で開いている錠を数える）。"""

    def __init__(self, target):
        self.target = target
        self.handles = []

    def __call__(self, *args, **kwargs):
        handle = self.target(*args, **kwargs)
        self.handles.append(handle)
        return handle

    @property
    def issued(self) -> int:
        """発行した錠の数。"""
        return len(self.handles)

    def open_now(self) -> int:
        """いま保持している（＝実際に書込を守っている）錠の数。"""
        return sum(1 for h in self.handles if not h.closed)


class _ObservingSource(fakes.CountingTickSource):
    """周期の取得ごとに「そのとき保持されていた錠の数」を記録する供給元。"""

    def __init__(self, tape, spy: _LockSpy):
        super().__init__(tape)
        self.spy = spy
        self.guarded: "list[int]" = []

    def fetch(self, *, symbol, from_msc, to_msc, max_rows):
        if max_rows != 1:                 # 周期の取得だけを観測する（トークン探りは除く）
            self.guarded.append(self.spy.open_now())
        return super().fetch(
            symbol=symbol, from_msc=from_msc, to_msc=to_msc, max_rows=max_rows
        )


def _measure(tmp_path: Path, monkeypatch, *, cycles: int):
    """``cycles`` 周期を回し、錠の発行数と「各周期で保持されていた数」を返す。"""
    data_dir = tmp_path / f"cycles{cycles}"
    data_dir.mkdir(parents=True, exist_ok=True)
    spy = _LockSpy(watch.acquire_writer_lock)
    monkeypatch.setattr(watch, "acquire_writer_lock", spy)
    source = _ObservingSource(_tape(_START, minutes=20), spy)

    code = watch.run(
        _settings(data_dir), source=source, clock=fakes.FixedClock(_NOW),
        cycles=cycles, sleep=lambda _s: None,
    )
    assert code == watch.EXIT_OK
    assert spy.issued, "錠が 1 つも発行されていない（本検定が恒真式に退化している）"
    return spy.issued, source.guarded, source.cycle_fetches


def test_every_issued_lock_guards_the_writes_none_is_discarded(tmp_path, secret, monkeypatch):
    """CX: 発行した錠 − 書込を守った錠 = 0（取っては捨てる錠を作らない）。

    回数そのものは期待値に焼き込まない（焼き込むと浪費が仕様へ昇格する）。固定するのは
    「発行した錠がすべて周期の書込を守っていた」＝無駄の不在である。
    """
    # Arrange / Act
    issued, guarded, fetches = _measure(tmp_path, monkeypatch, cycles=3)

    # Assert
    assert guarded == [issued] * fetches
    assert fetches == 3                  # 対照: 仕事そのものは周期数どおり発行されている


@pytest.mark.parametrize(("small", "large"), [(2, 6)])
def test_acquiring_the_lock_does_not_grow_with_the_number_of_cycles(
    tmp_path, secret, monkeypatch, small, large
):
    """CX: 錠の取得は起動時 1 回。周期数（2 点）を変えても発行が増えない（オーダーの表明）。"""
    # Arrange / Act
    issued_small, _, fetches_small = _measure(tmp_path, monkeypatch, cycles=small)
    issued_large, _, fetches_large = _measure(tmp_path, monkeypatch, cycles=large)

    # Assert
    assert issued_small == issued_large
    assert (fetches_small, fetches_large) == (small, large)   # 対照: 周期は実際に増えている
