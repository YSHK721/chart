"""run_job の report.json 書出し結線（Phase 4 F）の結合検定。

固定する不変条件:
    1. **成功した run のときだけ**書き出す（`exit_code == 0` かつ result あり）。
    2. 失敗 run（非 0）・result 不在では書き出さない。失敗した run の結果を表示面へ
       出すと、古い/壊れた結果が「今の結果」に見える。
    3. 書出しは job-dir と result を渡すだけ（写像は adapter/UC 側の責務）。
    4. **書出しに失敗しても run の終了コードを変えない**。バックテスト自体は成功して
       おり（stats.json / report.md は既に出ている）、表示用ペイロードの失敗で
       ジョブを失敗扱いにすると、成功した計算を捨てることになる。
    5. 子は状態ファイルを書かない（既存設計の維持）。

方式: `run_backtest` と `report_payload_writer` を差し替えて**渡された引数**を観測する
（重い実データを回さない・既存 test_run_job.py と同方式）。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from simulator.sim_ui.main import run_job


@pytest.fixture
def job_dir(tmp_path: Path) -> Path:
    d = tmp_path / "0123456789abcdef0123456789abcdef"
    d.mkdir()
    (d / "spec.json").write_text(
        json.dumps({"backtest": {"ea_name": "PRO_fit_Band_EA", "symbol": "JP225"}}),
        encoding="utf-8",
    )
    return d


class _FakeRunBacktest:
    def __init__(self, exit_code: int = 0, result: object = "RESULT") -> None:
        self.exit_code = exit_code
        self.result = result

    def __call__(self, **kwargs):
        return self.exit_code, self.result


class _SpyWriter:
    def __init__(self, boom: bool = False) -> None:
        self.calls: list = []
        self.boom = boom

    def write(self, job_dir, result, **kwargs):
        self.calls.append((job_dir, result))
        if self.boom:
            raise OSError("disk full")
        return Path(job_dir) / "report.json"


def _run(monkeypatch, job_dir: Path, *, exit_code=0, result="RESULT", boom=False):
    spy = _SpyWriter(boom=boom)
    monkeypatch.setattr(run_job, "run_backtest", _FakeRunBacktest(exit_code, result))
    monkeypatch.setattr(run_job, "report_payload_writer", spy)
    code = run_job.main(["--job-dir", str(job_dir)])
    return code, spy


# --- 1. 成功 run では書き出す --------------------------------------------------

def test_成功runでreport_jsonを書き出す(job_dir: Path, monkeypatch) -> None:
    _code, spy = _run(monkeypatch, job_dir)
    assert len(spy.calls) == 1


def test_書出しにはjob_dirとresultを渡す(job_dir: Path, monkeypatch) -> None:
    _code, spy = _run(monkeypatch, job_dir)
    passed_dir, passed_result = spy.calls[0]
    assert Path(passed_dir) == job_dir
    assert passed_result == "RESULT"


def test_成功runの終了コードは0のまま(job_dir: Path, monkeypatch) -> None:
    code, _spy = _run(monkeypatch, job_dir)
    assert code == 0


# --- 2. 失敗 run では書き出さない ----------------------------------------------

@pytest.mark.parametrize("exit_code", [1, 2, 3])
def test_失敗runでは書き出さない(job_dir: Path, monkeypatch, exit_code: int) -> None:
    code, spy = _run(monkeypatch, job_dir, exit_code=exit_code, result="RESULT")
    assert spy.calls == []
    assert code == exit_code


def test_resultが無ければ書き出さない(job_dir: Path, monkeypatch) -> None:
    """`run_backtest` は失敗時に result=None を返す（ConfigError 等）。"""
    _code, spy = _run(monkeypatch, job_dir, exit_code=0, result=None)
    assert spy.calls == []


# --- 4. 書出し失敗は run の終了コードを変えない ---------------------------------

def test_書出しが失敗しても終了コードは変わらない(job_dir: Path, monkeypatch) -> None:
    code, spy = _run(monkeypatch, job_dir, boom=True)
    assert len(spy.calls) == 1
    assert code == 0


def test_書出し失敗でも状態ファイルは書かない(job_dir: Path, monkeypatch) -> None:
    _run(monkeypatch, job_dir, boom=True)
    assert not (job_dir / "state.json").exists()


# --- 5. 書出し失敗は job-dir に残す（観測可能にする）---------------------------
# 子の stdout / stderr は launcher が DEVNULL に固定している
# （`adapter/subprocess_job_launcher.py:75-76`）。したがって print だけでは
# **どこにも届かない**。書出しに失敗すると「ジョブは完了なのに結果が出ない」状態に
# なるので、理由を job-dir へ残す（終了コードは変えない＝承認済みの制約は維持）。

def test_書出し失敗の理由をjob_dirへ残す(job_dir: Path, monkeypatch) -> None:
    _run(monkeypatch, job_dir, boom=True)
    note = job_dir / "report_payload_error.json"
    assert note.exists(), "書出し失敗が job-dir に残っていない（stderr は DEVNULL で消える）"
    assert "disk full" in json.loads(note.read_text(encoding="utf-8"))["message"]


def test_書出し失敗は失敗runと混同しない(job_dir: Path, monkeypatch) -> None:
    """run 自体は成功している。`failure.json` を書くと失敗 run と区別できなくなる。"""
    _run(monkeypatch, job_dir, boom=True)
    assert not (job_dir / "failure.json").exists()


def test_書出しに成功したら理由ファイルは作らない(job_dir: Path, monkeypatch) -> None:
    _run(monkeypatch, job_dir)
    assert not (job_dir / "report_payload_error.json").exists()


def test_失敗runでは理由ファイルを作らない(job_dir: Path, monkeypatch) -> None:
    """書き出しに到達していない（＝表示用ペイロードの失敗ではない）。"""
    _run(monkeypatch, job_dir, exit_code=1)
    assert not (job_dir / "report_payload_error.json").exists()
