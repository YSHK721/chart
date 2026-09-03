"""子プロセスの import パスを台帳から導出させる（ISSUE-479 Wave2 2-2 追補）。

固定する仕様:
    `SubprocessJobLauncher` が子へ渡す PYTHONPATH は、`tools/dev_paths.txt`（唯一源）が
    定める全エントリを含む。値を launcher 側に書き写さない。

なぜこれが要るのか（実測された事故）:
    launcher は「repo 根」だけを PYTHONPATH へ置いていた。それでも動いていたのは、
    共有 MA 実装を読む adapter が import 時に自分で sys.path を書き換えていたからである。
    Wave2 2-2 でその書き換えを撤去した（解決先が import 順に依存するため）ところ、
    子プロセスが `ModuleNotFoundError: No module named 'moving_averages'` で落ちた
    ——sim_ui の統合検定 8 件が赤になって初めて露見した。

    つまり launcher は「1 つのチェックアウトを構成する import パス」の 4 人目の消費者で
    ありながら、台帳から導出せず自分の値を持っていた。ISSUE-279 で潰したはずの
    「値の書き写し」がここに 1 つ残っていたということである。片方だけ腐る典型なので、
    値ではなく**導出**に直し、一致を機械的に強制する。
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from simulator.sim_ui.adapter.subprocess_job_launcher import SubprocessJobLauncher

_REPO = Path(__file__).resolve().parents[4]


def _ledger_entries() -> "list[str]":
    """台帳の有効行を絶対パスへ解決する（検定側も値を書き写さない）。"""
    out: "list[str]" = []
    for raw in (_REPO / "tools" / "dev_paths.txt").read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line and not line.startswith("#"):
            out.append(str(_REPO if line == "." else _REPO / line))
    return out


def _child_path(monkeypatch, existing: "str | None") -> "list[str]":
    if existing is None:
        monkeypatch.delenv("PYTHONPATH", raising=False)
    else:
        monkeypatch.setenv("PYTHONPATH", existing)
    launcher = SubprocessJobLauncher(job_dir_of=lambda _id: _REPO, repo_root=_REPO)
    return launcher._child_env()["PYTHONPATH"].split(os.pathsep)


class TestTheChildInheritsTheLedgerPaths:
    """子が台帳の全エントリを受け取ること。"""

    def test_every_ledger_entry_reaches_the_child(self, monkeypatch):
        got = _child_path(monkeypatch, None)
        missing = [e for e in _ledger_entries() if e not in got]
        assert missing == [], (
            f"台帳のエントリが子へ届いていません: {missing}\n  子の PYTHONPATH: {got}"
        )

    def test_the_ledger_entries_come_first(self, monkeypatch):
        """呼び出し側の既存 PYTHONPATH より台帳が優先される（起動元ツリーが勝つ）。"""
        got = _child_path(monkeypatch, "/keep/me")
        assert got[: len(_ledger_entries())] == _ledger_entries()

    def test_the_existing_pythonpath_is_preserved(self, monkeypatch):
        """呼び出し側の意図を捨てない（台帳を前置するだけ）。"""
        got = _child_path(monkeypatch, "/keep/me")
        assert "/keep/me" in got

    def test_no_entry_is_duplicated(self, monkeypatch):
        """既に台帳が入っている環境（serve.sh 経由）でも二重に積まない。"""
        got = _child_path(monkeypatch, os.pathsep.join(_ledger_entries()))
        assert len(got) - len(set(got)) == 0, got

    @pytest.mark.parametrize("existing", [None, "", "/keep/me"], ids=["unset", "empty", "set"])
    def test_the_shared_ma_library_is_resolvable_from_the_child_path(
        self, monkeypatch, existing
    ):
        """共有 MA 実装（indigators 配下）が子の探索パスから解決できる。

        これが崩れると子は `ModuleNotFoundError` で死に、呼び出し側には
        「終了コード 1」としか見えない（原因が 1 段隠れる）。
        """
        got = _child_path(monkeypatch, existing)
        resolvable = [p for p in got if (Path(p) / "moving_averages").is_dir()]
        assert resolvable != [], got


class TestTheLauncherDoesNotCopyTheLedgerValues:
    """値の書き写しを構文で禁じる（片方だけ腐るのを防ぐ）。"""

    def test_the_launcher_source_has_no_ledger_value_written_into_it(self):
        src = (
            _REPO / "simulator" / "sim_ui" / "adapter" / "subprocess_job_launcher.py"
        ).read_text(encoding="utf-8")
        copied = [
            line
            for line in ("indigators/market_profile/api", "/indigators")
            if line in src
        ]
        assert copied == [], (
            f"台帳の値が launcher へ書き写されています: {copied}\n"
            "  tools/dev_paths.txt から導出してください。"
        )


class TestTheChildEnvDoesNotWasteWork:
    """計算量検定（Test Spy・発行 − 使用 = 0）。測るのは時間ではなく回数。"""

    @pytest.mark.parametrize("calls", [1, 8], ids=["build_1", "build_8"])
    def test_the_ledger_is_read_once_per_env_build(self, monkeypatch, calls):
        """env 構築 1 回 / 8 回の 2 点で「台帳の読込 − env 構築 = 0」。

        読込回数を焼き込まず、1 構築につき読み捨てが 0 であることを固定する。
        """
        import simulator.sim_ui.adapter.subprocess_job_launcher as mod

        reads: "list[Path]" = []
        original = mod.ledger_path_entries

        def spy(root):
            reads.append(root)
            return original(root)

        monkeypatch.setattr(mod, "ledger_path_entries", spy)
        monkeypatch.delenv("PYTHONPATH", raising=False)

        launcher = SubprocessJobLauncher(job_dir_of=lambda _id: _REPO, repo_root=_REPO)
        built = [launcher._child_env() for _ in range(calls)]

        assert len(built) == calls
        assert len(reads) - calls == 0, (reads, calls)

    def test_the_entry_count_does_not_grow_with_repeated_builds(self, monkeypatch):
        """同じ env を作り直してもエントリが増殖しない（前置の積み重ねが起きない）。"""
        monkeypatch.delenv("PYTHONPATH", raising=False)
        launcher = SubprocessJobLauncher(job_dir_of=lambda _id: _REPO, repo_root=_REPO)
        first = launcher._child_env()["PYTHONPATH"].split(os.pathsep)
        monkeypatch.setenv("PYTHONPATH", os.pathsep.join(first))
        second = launcher._child_env()["PYTHONPATH"].split(os.pathsep)
        assert len(second) - len(first) == 0, (first, second)
