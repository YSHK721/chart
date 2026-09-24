"""MT5 供給常駐の引き継ぎ口（``--takeover``）— ライブ供給との対称性。

なぜ必要か（実測・2026-09-24）:
    ISSUE-530 の是正で ``tools/mt5_tick_watch.py`` に単一書き手ロックを結線した。錠の実体は
    中立核 ``common/writer_lock.py`` が単一定義で持ち、``takeover`` を既に受け取れる。ところが
    MT5 側は**引き継ぎ口を作らなかった**ため、起動スクリプト（indicator UI の serve.sh）
    を実行し直すと先行が生きている限り ``EXIT_USAGE``（2）で止まる。ライブ供給
    （``tools/live_tick_watch.py``）は ``--takeover`` を持ち、同じ serve.sh が
    live_tick_watch.py --stream --takeover で起動している＝**2 本の常駐が非対称**だった。

本検定が固定する不変条件:
    1. ``--takeover`` 付きなら、先行保持者を SIGTERM で退去させて引き継ぐ（周期が回る）。
    2. **既定（``--takeover`` なし）は従来どおり即時拒否**（引き継ぎは明示指定のときだけ）。
    3. 拒否の案内はライブ側と同じ「次の一手」を載せる（``--takeover`` を名指す）。
    4. 起動スクリプトが MT5 常駐へ ``--takeover`` を渡す（ライブ側と対称・両分岐）。
    5. 計算量: 引き継ぎ経路でも錠の取得は起動時 1 回で、周期数に比例して増えない。

実ネットワークにも MetaTrader5 にも依存しない（供給元は注入する）。データは tmp_path に閉じる。
"""
from __future__ import annotations

import datetime as dt
import subprocess
import sys
from pathlib import Path

import pytest

from marketdata.mt5_ticks import fakes
from tools import live_tick_watch
from tools import mt5_tick_watch as watch

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SERVE_SH = _REPO_ROOT / "indigators" / "indicator_ui" / "serve.sh"
_SECRET = "takeover-test-secret"
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


def _child_holding(data_dir: Path) -> subprocess.Popen:
    """別プロセスに MT5 常駐の錠を握らせる（``locked`` 印字で獲得済み）。

    SIGTERM で素直に終わる子である（``--takeover`` が退去させられることの前提）。
    """
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
# 1. 引き継ぎ口（明示指定のときだけ働く）
# =====================================================================
def test_takeover_stops_the_prior_daemon_and_acquires(tmp_path, secret):
    """TC-T1: ``--takeover`` 付きなら先行を退去させて引き継ぐ（周期が実際に回る）。

    正規の起動経路（serve.sh）が、出所不明の残存プロセスを確実に退去させるための口である。
    """
    # Arrange
    child = _child_holding(tmp_path)
    source = fakes.CountingTickSource(_tape(_START, minutes=3))

    # Act
    try:
        code = watch.run(
            _settings(tmp_path, "--takeover"), source=source, clock=fakes.FixedClock(_NOW),
            cycles=1, sleep=lambda _s: None,
        )
    finally:
        child.kill()

    # Assert
    assert code == watch.EXIT_OK
    assert source.cycle_fetches == 1            # 引き継いだ側が実際に供給している
    assert child.wait(timeout=10) is not None   # 先行は退去した（SIGTERM を受けて終了）


def test_the_default_still_refuses_the_second_daemon(tmp_path, secret):
    """TC-T2: 既定（``--takeover`` なし）は従来どおり即時拒否・先行は生き残る。

    引き継ぎは**明示指定のときだけ**である（既定が先行を殺すようになったら、運用者の
    意図しない退去が黙って起きる）。
    """
    # Arrange
    child = _child_holding(tmp_path)
    source = fakes.CountingTickSource(_tape(_START, minutes=3))

    # Act
    try:
        code = watch.run(
            _settings(tmp_path), source=source, clock=fakes.FixedClock(_NOW),
            cycles=1, sleep=lambda _s: None,
        )
        alive = child.poll()
    finally:
        child.kill()

    # Assert
    assert code == watch.EXIT_USAGE
    assert source.cycle_fetches == 0
    assert alive is None, "既定の起動が先行プロセスを止めてしまっている"


def test_the_cli_exposes_the_takeover_flag_and_defaults_to_off(secret):
    """TC-T3: CLI の面（ライブ側の同名フラグを固定している検定と同型）。"""
    assert watch.build_parser().parse_args(["--takeover"]).takeover is True
    assert watch.build_parser().parse_args([]).takeover is False


def test_the_refusal_points_at_the_takeover_flag(tmp_path, secret, capsys):
    """TC-T4: 拒否の案内は「次の一手」として ``--takeover`` を名指す（ライブ側と同じ意味）。"""
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
    assert "--takeover" in err, f"引き継ぎ口の案内が無い: {err}"
    assert "--takeover" in live_tick_watch._WRITER_LOCK_HINT   # 対照: ライブ側も同じ流儀


# =====================================================================
# 2. 起動スクリプトが渡す（ライブ側と対称）
# =====================================================================
def _serve_sh_lines(needle: str) -> "list[str]":
    """serve.sh のうち ``needle`` を起動している行（コメントは除く）。"""
    return [
        line.strip()
        for line in _SERVE_SH.read_text(encoding="utf-8").splitlines()
        if needle in line and not line.strip().startswith("#")
    ]


def test_serve_sh_starts_the_mt5_daemon_with_takeover():
    """TC-T5: 起動スクリプトの MT5 起動は**どの分岐でも** ``--takeover`` を渡す。

    分岐は 2 つある（``MT5_TICK_WATCH_FROM`` の有無）。片方だけに付けると、その片方の
    起動経路でだけ ISSUE-530 の錠に弾かれて供給が上がらない。
    """
    # Arrange / Act
    lines = [line for line in _serve_sh_lines('"$MT5_WATCH_TOOL"') if "$VENV_PY" in line]

    # Assert
    assert len(lines) == 2, f"MT5 起動の分岐数が想定と違う: {lines}"
    for line in lines:
        assert "--takeover" in line, f"MT5 起動に --takeover が無い: {line}"


def test_serve_sh_keeps_the_from_argument_untouched():
    """TC-T6: ``--from`` の扱いは変わらない（引き継ぎ口の追加が再開点の規律を動かさない）。"""
    # Arrange / Act
    lines = [line for line in _serve_sh_lines('"$MT5_WATCH_TOOL"') if "$VENV_PY" in line]

    # Assert
    assert sum(1 for line in lines if '--from "$MT5_TICK_WATCH_FROM"' in line) == 1
    assert sum(1 for line in lines if "--from" not in line) == 1


def test_serve_sh_starts_both_daemons_symmetrically():
    """TC-T7: ライブ供給と MT5 供給が同じ引き継ぎ規律で起動される（非対称を作らない）。"""
    # Arrange / Act
    live = [line for line in _serve_sh_lines('"$TICK_WATCH_TOOL"') if "$VENV_PY" in line]
    mt5 = [line for line in _serve_sh_lines('"$MT5_WATCH_TOOL"') if "$VENV_PY" in line]

    # Assert
    assert live, "ライブ供給の起動行が見つからない（対照が消えている）"
    assert all("--takeover" in line for line in live + mt5)


# =====================================================================
# 3. 計算量（引き継ぎ経路でも発行 − 使用 = 0）
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
        return len(self.handles)

    def open_now(self) -> int:
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


def _measure_takeover(tmp_path: Path, monkeypatch, *, cycles: int):
    """``--takeover`` 付きで ``cycles`` 周期を回し、錠の発行数と保持状況を返す。"""
    data_dir = tmp_path / f"cycles{cycles}"
    data_dir.mkdir(parents=True, exist_ok=True)
    spy = _LockSpy(watch.acquire_writer_lock)
    monkeypatch.setattr(watch, "acquire_writer_lock", spy)
    source = _ObservingSource(_tape(_START, minutes=20), spy)

    code = watch.run(
        _settings(data_dir, "--takeover"), source=source, clock=fakes.FixedClock(_NOW),
        cycles=cycles, sleep=lambda _s: None,
    )
    assert code == watch.EXIT_OK
    assert spy.issued, "錠が 1 つも発行されていない（本検定が恒真式に退化している）"
    return spy.issued, source.guarded, source.cycle_fetches


def test_takeover_issues_no_lock_that_guards_nothing(tmp_path, secret, monkeypatch):
    """CX-1: 引き継ぎ経路でも 発行した錠 − 書込を守った錠 = 0（取っては捨てる錠を作らない）。"""
    # Arrange / Act
    issued, guarded, fetches = _measure_takeover(tmp_path, monkeypatch, cycles=3)

    # Assert
    assert guarded == [issued] * fetches
    assert fetches == 3                  # 対照: 仕事そのものは周期数どおり発行されている


@pytest.mark.parametrize(("small", "large"), [(2, 6)])
def test_takeover_acquisition_does_not_grow_with_the_number_of_cycles(
    tmp_path, secret, monkeypatch, small, large
):
    """CX-2: 引き継ぎ経路でも錠の取得は起動時 1 回（周期数 2 点でオーダーを表明）。"""
    # Arrange / Act
    issued_small, _, fetches_small = _measure_takeover(tmp_path, monkeypatch, cycles=small)
    issued_large, _, fetches_large = _measure_takeover(tmp_path, monkeypatch, cycles=large)

    # Assert
    assert issued_small == issued_large
    assert (fetches_small, fetches_large) == (small, large)   # 対照: 周期は実際に増えている
