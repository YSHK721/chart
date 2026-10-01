"""配信中のコードの同一性（ISSUE-531）の判定規則を固定する。

なぜ必要か:
    同じツリーでコードを更新しても、``unified_ui/serve.sh`` は「既に起動済みです」で exit 0 した。
    静的ファイルは要求ごとに読み直されるが Python は起動時のまま——画面の一部だけ新しく、
    サーバ側の判断は古いまま動く（2026-09-25 実測・2026-10-01 に実 UI 確認が 3 回止まった）。
    「どのツリーか」（``/__serving_root``・ISSUE-348）だけでなく、「そのツリーの**どの内容**を
    起動時に読んだか」をプロセス外から照合できなければ、黙って古いコードを見続ける。

固定する規則（判定の根拠は ``unified_ui/serving_code.py`` の docstring）:
    1. 同一性は**内容**で決める。HEAD の移動・コミットだけでは変わらない（内容が同じなら同じ）。
    2. 対象は「ツリーのすべて − 配信プロセスが読まないと理由つきで宣言したもの」。
       誤りの向きを安全側に倒す（「同じ」と誤ると古いコードを黙って配信する＝ISSUE-531 そのもの）。
       Python 以外の起動時の入力（JSON の台帳・serve.sh・dev_paths.local.sh）の変更は差になる。
       宣言した除外（テスト・文書・.claude・prototype・要求ごとに読み直される静的ファイル）の
       変更は差にならない。
    3. 未コミットの変更・未追跡・削除は内容の差として数える。``.gitignore`` で無視された
       ファイルは数えない。ただし起動時に読まれる dev_paths.local.sh は明示して数える
       （値は出力しない＝秘密を含む）。
    3. 差があるとき、起動後に更新された Python ファイルを示す（違いを黙らない）。

計算量（CLAUDE.md 計算量テスト規約）:
    観測の境界は ``code_manifest`` の git 引数（宣言された注入点）と ``compare`` の
    mtime 引数である。実装の内部名は差し替えない。
    - 内容を読む（``hash-object``）のは**変わったファイルだけ**であり、読んだ内容はすべて
      目録に使われる（発行 − 使用 = 0）。期待値はテストが自分で変えたファイルの集合から導く。
    - git の呼び出し回数は**ファイル数を 2 点変えても増えない**（オーダーの表明・回数は焼き込まない）。
    - 更新時刻を見るのは目録に載ったファイルだけ（無視されたディレクトリを走査しない）。
"""
from __future__ import annotations

import importlib.util
import os
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_MODULE_PATH = _ROOT / "unified_ui" / "serving_code.py"


def _load():
    spec = importlib.util.spec_from_file_location("serving_code_under_test", _MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def sc():
    return _load()


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


#: 起動時に読まれうる Python 以外の入力（変えると指紋が変わるべきもの）。
_STARTUP_INPUTS = ("common/topology.json", "pkg/serve.sh")

#: 配信プロセスが読まないと宣言されたもの（変えても指紋が変わらないべきもの）。
_DECLARED_NOT_READ = (
    "pkg/tests/test_a.py",
    "pkg/test_b.py",
    "docs/guide.md",
    ".doc/design.md",
    ".claude/notes.txt",
    "prototype_260101-01/a.py",
    "pkg/web/js/app.js",
    "pkg/web/index.html",
    "pkg/web/css/a.css",
    "pkg/web/js/m.mjs",
)

_EXTRA_FILES = {rel: "v1\n" for rel in (*_STARTUP_INPUTS, *_DECLARED_NOT_READ)}


def _make_repo(tmp_path: Path, n_files: int) -> Path:
    """Python ファイル ``n_files`` 本・文書 1 本・無視ディレクトリ 1 つを持つリポジトリを作る。"""
    repo = tmp_path / f"repo_{n_files}"
    (repo / "pkg").mkdir(parents=True)
    for i in range(n_files):
        (repo / "pkg" / f"m{i}.py").write_text(f"VALUE = {i}\n", encoding="utf-8")
    (repo / "README.md").write_text("doc\n", encoding="utf-8")
    for rel, text in _EXTRA_FILES.items():
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text(text, encoding="utf-8")
    (repo / ".gitignore").write_text("ignored/\ndev_paths.local.sh\n", encoding="utf-8")
    (repo / "ignored").mkdir()
    for i in range(n_files):
        (repo / "ignored" / f"x{i}.py").write_text("X = 1\n", encoding="utf-8")
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@example.invalid")
    _git(repo, "config", "user.name", "t")
    _git(repo, "add", "pkg", "README.md", ".gitignore", *_EXTRA_FILES)
    _git(repo, "commit", "-q", "-m", "init")
    return repo


class _GitSpy:
    """宣言された注入点（``git`` 引数）に挟む Test Spy。実 git へ委譲し、呼び出しを記録する。"""

    def __init__(self, inner):
        self._inner = inner
        self.calls: list[tuple[str, ...]] = []
        self.hashed: list[str] = []

    def __call__(self, args, stdin=None):
        self.calls.append(tuple(args))
        if "hash-object" in args and stdin:
            self.hashed.extend(p for p in stdin.decode("utf-8").split("\n") if p)
        return self._inner(args, stdin)


def _set_mtime_of_worktree_files(repo: Path, when: float) -> None:
    """作業ツリーの全ファイル（.git を除く）の更新時刻を ``when`` にする。"""
    for path in repo.rglob("*"):
        if path.is_file() and ".git" not in path.relative_to(repo).parts:
            os.utime(path, (when, when))


def _fp(sc, repo: Path) -> str:
    return sc.fingerprint(sc.code_manifest(sc.run_git(repo)))


# ---------------------------------------------------------------- 規則 1: 内容で決める


def test_追跡中のPythonを未コミットで変えると指紋が変わる(sc, tmp_path: Path) -> None:
    repo = _make_repo(tmp_path, 3)
    before = _fp(sc, repo)
    (repo / "pkg" / "m1.py").write_text("VALUE = 'changed'\n", encoding="utf-8")
    assert _fp(sc, repo) != before


def test_変更をコミットしても内容が同じなら指紋は変わらない(sc, tmp_path: Path) -> None:
    """HEAD の移動は内容の差ではない（コミットしただけで再起動を求めない）。"""
    repo = _make_repo(tmp_path, 3)
    (repo / "pkg" / "m1.py").write_text("VALUE = 'changed'\n", encoding="utf-8")
    edited = _fp(sc, repo)
    _git(repo, "add", "pkg/m1.py")
    _git(repo, "commit", "-q", "-m", "edit")
    assert _fp(sc, repo) == edited


@pytest.mark.parametrize("rel", _STARTUP_INPUTS)
def test_Python以外の起動時の入力を変えると指紋が変わる(sc, tmp_path: Path, rel: str) -> None:
    """JSON の台帳（lru_cache・import 時決定）や serve.sh は起動時に 1 回だけ読まれる。"""
    repo = _make_repo(tmp_path, 3)
    before = _fp(sc, repo)
    (repo / rel).write_text("v2\n", encoding="utf-8")
    assert _fp(sc, repo) != before


@pytest.mark.parametrize("rel", (*_DECLARED_NOT_READ, "README.md"))
def test_読まないと宣言したものを変えても指紋は変わらない(sc, tmp_path: Path, rel: str) -> None:
    """不要な restart（＝実行中のバックテストの停止）を招かない。"""
    repo = _make_repo(tmp_path, 3)
    before = _fp(sc, repo)
    (repo / rel).write_text("v2\n", encoding="utf-8")
    assert _fp(sc, repo) == before


def test_除外の宣言に無いものは含める側に倒す(sc, tmp_path: Path) -> None:
    """拡張子の許可リストではない: 未知の種類のファイルも差として数える。"""
    repo = _make_repo(tmp_path, 3)
    before = _fp(sc, repo)
    (repo / "pkg" / "table.unknownext").write_text("x\n", encoding="utf-8")
    assert _fp(sc, repo) != before


def test_dev_paths_localは無視されていても数え値は出力しない(sc, tmp_path: Path) -> None:
    repo = _make_repo(tmp_path, 3)
    secret = "s3cr3t-value-should-not-appear"
    local = repo / "dev_paths.local.sh"
    local.write_text(f"export MT5_BRIDGE_SECRET={secret}\n", encoding="utf-8")
    git = sc.run_git(repo)
    line = sc.identity_line(git, now=1_000.0)
    local.write_text("export MT5_BRIDGE_SECRET=other\n", encoding="utf-8")
    proc = subprocess.run(
        ["python3", str(_MODULE_PATH), "compare", str(repo), line],
        capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 1
    assert "dev_paths.local.sh" in proc.stdout
    assert secret not in proc.stdout + proc.stderr + line


# ---------------------------------------------------------------- 規則 2: 未追跡・削除・無視


def test_未追跡のPythonを足すと指紋が変わる(sc, tmp_path: Path) -> None:
    repo = _make_repo(tmp_path, 3)
    before = _fp(sc, repo)
    (repo / "pkg" / "new_mod.py").write_text("NEW = 1\n", encoding="utf-8")
    assert _fp(sc, repo) != before


def test_追跡中のPythonを消すと指紋が変わる(sc, tmp_path: Path) -> None:
    repo = _make_repo(tmp_path, 3)
    before = _fp(sc, repo)
    (repo / "pkg" / "m2.py").unlink()
    assert _fp(sc, repo) != before


def test_無視されたPythonの変更では指紋は変わらない(sc, tmp_path: Path) -> None:
    repo = _make_repo(tmp_path, 3)
    before = _fp(sc, repo)
    (repo / "ignored" / "x0.py").write_text("X = 2\n", encoding="utf-8")
    (repo / "ignored" / "x_new.py").write_text("Y = 1\n", encoding="utf-8")
    assert _fp(sc, repo) == before


# ---------------------------------------------------------------- 規則 3: 照合と違いの提示


def test_照合_同じなら同じと答える(sc, tmp_path: Path) -> None:
    repo = _make_repo(tmp_path, 3)
    git = sc.run_git(repo)
    line = sc.identity_line(git, now=1_000.0)
    result = sc.compare(repo, git, line)
    assert result.same is True


def test_照合_違えば起動後に更新されたファイルを示す(sc, tmp_path: Path) -> None:
    repo = _make_repo(tmp_path, 3)
    git = sc.run_git(repo)
    started = 1_000_000.0
    line = sc.identity_line(git, now=started)
    # 起動前に作られたファイルは起動時刻より古い更新時刻を持つ（実運用と同じ順序を固定する）。
    _set_mtime_of_worktree_files(repo, started - 60)
    edited = repo / "pkg" / "m0.py"
    edited.write_text("VALUE = 'x'\n", encoding="utf-8")
    os.utime(edited, (started + 5, started + 5))
    result = sc.compare(repo, git, line)
    assert result.same is False
    assert result.newer == ["pkg/m0.py"]


def test_照合_読めない申告は判定不能として区別する(sc, tmp_path: Path) -> None:
    """居ないことと分からないことを同じ値にしない（判定不能を「同じ」へ倒さない）。"""
    repo = _make_repo(tmp_path, 1)
    with pytest.raises(ValueError):
        sc.compare(repo, sc.run_git(repo), "not-a-valid-line")


def test_CLI_同じなら0_違えば1_読めなければ2(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path, 2)

    def cli(*args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["python3", str(_MODULE_PATH), *args], capture_output=True, text=True, timeout=60
        )

    ident = cli("identity", str(repo))
    assert ident.returncode == 0, ident.stderr
    line = ident.stdout.strip()
    assert cli("compare", str(repo), line).returncode == 0
    (repo / "pkg" / "m1.py").write_text("VALUE = 'y'\n", encoding="utf-8")
    differ = cli("compare", str(repo), line)
    assert differ.returncode == 1
    assert "pkg/m1.py" in differ.stdout
    assert cli("compare", str(repo), "garbage").returncode == 2
    # git が失敗する（リポジトリでない）ときも「確かめられない」へ寄せる。
    assert cli("compare", str(tmp_path / "not_a_repo"), line).returncode == 2


# ---------------------------------------------------------------- 計算量


def _measure(sc, tmp_path: Path, n_files: int):
    """``n_files`` 本の木で、2 本を変え 1 本を足して目録を作り、注入点で観測する。"""
    repo = _make_repo(tmp_path, n_files)
    changed = {"pkg/m0.py", "pkg/m1.py"}
    added = {"pkg/added.py"}
    for rel in changed:
        (repo / rel).write_text("VALUE = 'changed'\n", encoding="utf-8")
    for rel in added:
        (repo / rel).write_text("ADDED = 1\n", encoding="utf-8")
    spy = _GitSpy(sc.run_git(repo))
    manifest = sc.code_manifest(spy)
    return spy, manifest, changed | added


def test_計算量_内容を読むのは変わったファイルだけで読んだ内容はすべて使う(sc, tmp_path: Path) -> None:
    spy, manifest, touched = _measure(sc, tmp_path, 20)
    hashed = set(spy.hashed)
    # 発行 − 使用 = 0: 読んだ内容はすべて目録に載る。
    assert hashed - set(manifest) == set()
    # 変わっていないファイルの内容を読まない（集合はテストが変えたものから導く）。
    assert hashed <= touched
    # 変わったファイルは漏れなく読む（読まなければ差を検出できない）。
    assert touched <= hashed


def test_計算量_gitの呼び出し回数はファイル数で増えない(sc, tmp_path: Path) -> None:
    small, _, _ = _measure(sc, tmp_path, 5)
    large, _, _ = _measure(sc, tmp_path, 60)
    assert len(large.calls) == len(small.calls)


def test_計算量_更新時刻は目録に載ったファイルだけを見る(sc, tmp_path: Path) -> None:
    repo = _make_repo(tmp_path, 10)
    git = sc.run_git(repo)
    line = sc.identity_line(git, now=1_000.0)
    (repo / "pkg" / "m3.py").write_text("VALUE = 'z'\n", encoding="utf-8")
    seen: list[str] = []

    def mtime_spy(path: str) -> float:
        seen.append(path)
        return 2_000.0

    result = sc.compare(repo, git, line, mtime=mtime_spy)
    manifest_paths = {str(repo / rel) for rel in sc.code_manifest(git)}
    assert result.same is False
    assert set(seen) <= manifest_paths
    assert not any("/ignored/" in p for p in seen)
