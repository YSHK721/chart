"""起動時の可視の告知が「鳴るが拒否しない」ことの検定（ISSUE-526 段 3）。

なぜ拒否にしないか: 供給が止まっているときに UI の起動まで止めると「供給が止まっているから
画面も開けない」になり、原因を調べる手段ごと失う。告知は**気づける形**であって門ではない。

なぜログだけにしないか: unified_ui/serve.sh の core 起動は子の出力を捨てており、失敗の理由が
残らない（ISSUE-527 の実在する非対称）。同じ形を新設すると、告知を足したのに誰も見ない。
よって告知は起動時の標準出力・標準エラーへ出し、判定を得られなかった場合もその理由を捨てない。

方式: 告知は tools/supply_health_notice.sh が単体で起動可能な形に切り出してあるため、
**実際に走らせて終了コードと出力を測る**（起動スクリプト本体は 4 つの core を立ててしまうので
実行できないが、告知だけは実行して測れる）。判定を出す側は差し替え可能な python として渡す。

構造: Arrange-Act-Assert（AAA）。
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from marketdata import supply_health

_REPO_ROOT = Path(__file__).resolve().parents[2]
_NOTICE_SH = _REPO_ROOT / "tools" / "supply_health_notice.sh"
_SERVE_SH = _REPO_ROOT / "unified_ui" / "serve.sh"

#: 告知が鳴ったことの目印（人が読む語であり、鳴らないときは出ない）。
_WARNING_MARK = "警告"


def _stub_python(tmp_path: Path, *, stdout: str, exit_code: int = 0, stderr: str = "") -> Path:
    """判定を出す側の代役（渡された引数を無視して固定の出力を返す実行可能ファイル）。"""
    path = tmp_path / "stub_python"
    path.write_text(
        "#!/usr/bin/env bash\n"
        f"printf '%s' {shell_quote(stdout)}\n"
        f"printf '%s' {shell_quote(stderr)} >&2\n"
        f"exit {exit_code}\n",
        encoding="utf-8",
    )
    path.chmod(0o755)
    return path


def shell_quote(text: str) -> str:
    """shell の単一引用符で囲む（中身の引用符を壊さない）。"""
    return "'" + text.replace("'", "'\\''") + "'"


def _run_notice(python_path, cwd: Path) -> subprocess.CompletedProcess:
    """告知を 1 回走らせ、終了コードと出力をそのまま返す。"""
    return subprocess.run(
        [str(_NOTICE_SH), str(python_path), str(_REPO_ROOT)],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ},
    )


@pytest.mark.parametrize("verdict", sorted(supply_health.VERDICTS))
def test_the_notice_warns_exactly_for_the_alarm_verdicts(verdict, tmp_path):
    """異常な判定のときだけ鳴る（判定不能で鳴らすと、起動のたびに鳴って誰も見なくなる）。"""
    # Arrange
    report = f"{verdict}\ndukascopy {verdict}\nmt5 {verdict}\n"
    stub = _stub_python(tmp_path, stdout=report)

    # Act
    done = _run_notice(stub, tmp_path)
    warned = _WARNING_MARK in (done.stdout + done.stderr)

    # Assert
    assert warned == (verdict in supply_health.ALARM_VERDICTS)


@pytest.mark.parametrize("verdict", sorted(supply_health.VERDICTS))
def test_the_notice_never_refuses_the_startup(verdict, tmp_path):
    """どの判定でも終了コードは 0（起動を続ける＝門にしない）。"""
    # Arrange
    report = f"{verdict}\ndukascopy {verdict}\nmt5 {verdict}\n"
    stub = _stub_python(tmp_path, stdout=report)

    # Act
    done = _run_notice(stub, tmp_path)

    # Assert
    assert done.returncode == 0


def test_the_notice_shows_which_supply_is_abnormal(tmp_path):
    """供給ごとの行も出す（総合判定だけでは、どちらを起こし直すか分からない）。"""
    # Arrange
    report = (
        f"{supply_health.STOPPED}\n"
        f"dukascopy {supply_health.HEALTHY}\n"
        f"mt5 {supply_health.STOPPED}\n"
    )
    stub = _stub_python(tmp_path, stdout=report)

    # Act
    done = _run_notice(stub, tmp_path)
    shown = done.stdout + done.stderr

    # Assert
    assert ("mt5 " + supply_health.STOPPED) in shown
    assert ("dukascopy " + supply_health.HEALTHY) in shown


def test_the_notice_keeps_the_reason_when_the_verdict_cannot_be_obtained(tmp_path):
    """判定を得られなかったときも、理由を捨てずに出す（ISSUE-527 の非対称を作らない）。"""
    # Arrange
    stub = _stub_python(
        tmp_path, stdout="", exit_code=1, stderr="ModuleNotFoundError: no such module")

    # Act
    done = _run_notice(stub, tmp_path)
    shown = done.stdout + done.stderr

    # Assert
    assert (done.returncode, _WARNING_MARK in shown) == (0, True)
    assert "ModuleNotFoundError" in shown


def test_the_notice_answers_with_the_real_supply_health(tmp_path):
    """出荷の結線そのままで走る（読み取りのみ・1 バイトも書かない）。

    値は供給の実状態で変わるため焼き込まない。固定するのは「終了コード 0 で、語彙の中の
    判定が 1 つ出る」ことである。
    """
    # Arrange / Act
    done = _run_notice(sys.executable, tmp_path)
    shown = done.stdout + done.stderr

    # Assert
    assert done.returncode == 0, f"stderr={done.stderr[-800:]}"
    assert [v for v in sorted(supply_health.VERDICTS) if v in shown] != []


def test_serve_sh_runs_the_notice_before_it_starts_the_router():
    """起動スクリプトが告知を**ルータ起動より前に**走らせる。

    受け口を作っても、起動側が呼ばなければ無言で死ぬ（ISSUE-291 の実測と同型）。起動後に
    置いても意味が無い: ルータは foreground 起動で、その後ろの行は走らない。
    """
    # Arrange
    text = _SERVE_SH.read_text(encoding="utf-8")

    # Act
    notice_at = text.find("supply_health_notice.sh")
    # ルータの起動行そのものを探す（同じ変数は起動前の存在確認でも使われるため、
    #   変数名だけを探すと「起動より前」の判定が無意味になる）。
    router_at = text.find('python3 "$ROUTER_PY"')

    # Assert
    # di-ok(C2): serve.sh は起動スクリプトで、実行以外に観測手段が無い（本文が検査対象）。
    #   実行すると 4 つの core を立ててしまうため、結線の観測はこの形しか存在しない。
    assert notice_at > 0, "serve.sh が告知を呼んでいない（告知が死んでいる）"
    assert notice_at < router_at
