"""``unified_ui/serve.sh`` の同一ツリー入れ替え（``--restart``）と古いコードの検出を固定する（ISSUE-531）。

なぜ必要か:
    同じツリーでコードを更新しても、serve.sh は「既に起動済みです」で exit 0 し、``--takeover`` は
    別ツリーのときだけ停止へ入った。静的ファイルは要求ごとに読み直されるが Python は起動時の
    まま——画面の一部だけ新しく、サーバ側の判断は古いまま動く（2026-10-01 に実 UI 確認が
    3 回止まり、依頼者が手で再起動した）。

固定する不変条件:
    1. 同一ツリーで ``--restart`` なら、既存の停止手順（router へ INT）を通ってから起動へ進む。
    2. 同一ツリーで配信中のコードが今のツリーと同じなら、従来どおり何もせず exit 0。
    3. 同一ツリーで違う・または確かめられないなら、黙って exit 0 せず、違いを示して
       ``--restart`` を案内し、占有側を**落とさない**（判断は人に残す）。
    4. ``--restart`` は別ツリーを落とさない（別ツリーの引き継ぎは ``--takeover`` の判断）。
    5. 停止しようとするスタックが MT5 ティック供給を動かしているのに、この端末に秘密
       （MT5_BRIDGE_SECRET）が無ければ、止める**前に**中止する。止めた後で供給だけが
       起動し直されない形（ISSUE-524 の 8 日間の欠測と同じ「黙って止まる」）を作らない。

方式（実サーバ 8000 系には触れない）:
    ``test_unified_serve_takeover.py`` の ``pids_with`` 検定と同じく、serve.sh から関数を抜き出して
    実プロセスに対して評価する。占有者は**偽の router**（一時ポートで ``/__serving_root`` と
    ``/__serving_code`` を答える python プロセス・別セッション）であり、配信元は一時 git
    リポジトリである。core の内部ポートには使われていない一時ポートを渡すので、停止手順の
    core 側は「既に落ちている」経路を通る。

計算量: 判定が占有者へ送る HTTP 要求の数は、ツリーのファイル数を 2 点変えても増えない
    （ツリーの走査は手元の git だけで済み、問い合わせに比例させない）。回数は焼き込まず、
    2 点の比較で表明する。
"""
from __future__ import annotations

import os
import signal
import socket
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_SERVE = _ROOT / "unified_ui" / "serve.sh"
_SERVING_CODE_PY = _ROOT / "unified_ui" / "serving_code.py"

#: serve.sh から抜き出す関数（占有者の判定と停止手順に要る分）。
_FUNCS = (
    "ancestor_pids",
    "pids_with",
    "wait_down",
    "stop_core_if_up",
    "dashboard_argv_of",
    "stop_dashboard_core_if_up",
    "stop_sim_core_if_up",
    "ensure_supply_restartable",
    "stop_stack",
    "serving_code_matches",
    "resolve_occupant",
)

_FAKE_ROUTER = textwrap.dedent(
    """
    import signal
    import sys
    from http.server import BaseHTTPRequestHandler, HTTPServer

    # 起動元が INT を無視していても、本物の router と同じく INT で KeyboardInterrupt にする。
    signal.signal(signal.SIGINT, signal.default_int_handler)

    args = dict(zip(sys.argv[1::2], sys.argv[2::2]))
    log = args["--log"]

    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            with open(log, "a", encoding="utf-8") as f:
                f.write(self.path + "\\n")
            if self.path == "/__serving_root":
                body = (args["--serving-root"] + "\\n").encode()
            elif self.path == "/__serving_code":
                if not args.get("--serving-code"):
                    self.send_response(404); self.end_headers(); return
                body = (args["--serving-code"] + "\\n").encode()
            else:
                body = b"ok\\n"
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    with open(args["--port-file"], "w") as f:
        f.write(str(srv.server_address[1]))
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    """
)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def _make_repo(tmp_path: Path, name: str, n_files: int = 3) -> Path:
    repo = tmp_path / name
    (repo / "pkg").mkdir(parents=True)
    for i in range(n_files):
        (repo / "pkg" / f"m{i}.py").write_text(f"VALUE = {i}\n", encoding="utf-8")
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@example.invalid")
    _git(repo, "config", "user.name", "t")
    _git(repo, "add", "pkg")
    _git(repo, "commit", "-q", "-m", "init")
    return repo


def _identity(repo: Path) -> str:
    proc = subprocess.run(
        ["python3", str(_SERVING_CODE_PY), "identity", str(repo)],
        capture_output=True, text=True, timeout=60, check=True,
    )
    return proc.stdout.strip()


class _FakeRouter:
    """占有者の代役。argv に ``--web-root <root>/unified_ui/web`` を持つ（停止手順が引く断片）。"""

    def __init__(self, tmp_path: Path, serving_root: Path, serving_code: str) -> None:
        script = tmp_path / "fake_router.py"
        script.write_text(_FAKE_ROUTER, encoding="utf-8")
        self.log = tmp_path / f"requests_{time.monotonic_ns()}.log"
        self.log.write_text("", encoding="utf-8")
        port_file = tmp_path / f"port_{time.monotonic_ns()}"
        argv = [
            sys.executable, str(script),
            "--web-root", f"{serving_root}/unified_ui/web",
            "--serving-root", str(serving_root),
            "--serving-code", serving_code,
            "--log", str(self.log),
            "--port-file", str(port_file),
        ]
        # 別セッション（実際の占有スタックと同じく、判定側とは別プロセスグループ）。
        self.proc = subprocess.Popen(argv, start_new_session=True)
        deadline = time.monotonic() + 10
        while not port_file.exists() or not port_file.read_text():
            if time.monotonic() > deadline:
                raise RuntimeError("偽 router が起動しなかった")
            time.sleep(0.05)
        self.port = int(port_file.read_text())

    def alive(self) -> bool:
        return self.proc.poll() is None

    def requests(self) -> list[str]:
        return [line for line in self.log.read_text(encoding="utf-8").splitlines() if line]

    def close(self) -> None:
        if self.alive():
            self.proc.kill()
            self.proc.wait(timeout=10)


def _probe(tmp_path: Path, *, repo_root: Path, router: _FakeRouter, restart: int,
           takeover: int, env_extra: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    """serve.sh の関数を抜き出し、占有者の判定を 1 回評価する（起動の手前で PROCEED を出す）。"""
    source = _SERVE.read_text(encoding="utf-8")
    funcs = []
    for name in _FUNCS:
        start = source.index(f"{name}() {{")
        end = source.index("\n}\n", start) + len("\n}\n")
        funcs.append(source[start:end])
    base = f"http://127.0.0.1:{router.port}"
    header = textwrap.dedent(
        f"""
        set -euo pipefail
        REPO_ROOT="{repo_root}"
        TAKEOVER={takeover}
        RESTART={restart}
        PUBLIC_PORT={router.port}
        LIVE_PORT={_free_port()}
        REPLAY_PORT={_free_port()}
        SIM_PORT={_free_port()}
        DASHBOARD_PORT={_free_port()}
        DASHBOARD_MODULE="dashboard_ui.main.serve"
        SERVING_CODE_PY="{_SERVING_CODE_PY}"
        PUBLIC_URL="{base}/"
        SERVING_ROOT_URL="{base}/__serving_root"
        SERVING_CODE_URL="{base}/__serving_code"
        """
    )
    script = tmp_path / f"probe_{time.monotonic_ns()}.sh"
    script.write_text(header + "\n".join(funcs) + "\nresolve_occupant\necho PROCEED\n",
                      encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if k != "MT5_BRIDGE_SECRET"}
    env.update(env_extra or {})
    return subprocess.run(["bash", str(script)], capture_output=True, text=True,
                          timeout=120, env=env)


@pytest.fixture()
def work(tmp_path_factory) -> Path:
    """ASCII だけのパスの作業場所。

    ps は非 ASCII の argv を ``?`` へ置き換えて出す（実測: pytest の ``tmp_path`` はテスト名の
    日本語を含み、pids_with の照合が外れた）。実運用の配信元は ASCII のパスなので、検定も
    ASCII のパスで評価する。
    """
    return tmp_path_factory.mktemp("restart")


@pytest.fixture()
def routers():
    made: list[_FakeRouter] = []
    yield made
    for r in made:
        r.close()


# ---------------------------------------------------------------- 同一ツリー


def test_同一ツリーでコードが同じなら何もせず正常終了する(work: Path, routers) -> None:
    repo = _make_repo(work, "tree")
    router = _FakeRouter(work, repo, _identity(repo))
    routers.append(router)

    proc = _probe(work, repo_root=repo, router=router, restart=0, takeover=0)

    assert proc.returncode == 0, proc.stderr
    assert "既に起動済み" in proc.stdout
    assert "PROCEED" not in proc.stdout
    assert router.alive()


def test_同一ツリーでコードが違えば違いを示してrestartを案内し落とさない(work: Path, routers) -> None:
    repo = _make_repo(work, "tree")
    router = _FakeRouter(work, repo, _identity(repo))
    routers.append(router)
    (repo / "pkg" / "m1.py").write_text("VALUE = 'new'\n", encoding="utf-8")

    proc = _probe(work, repo_root=repo, router=router, restart=0, takeover=0)

    assert proc.returncode != 0, "古いコードを配信中なのに正常終了してはならない"
    assert "--restart" in proc.stderr
    assert "pkg/m1.py" in proc.stderr, f"違いを示していない: {proc.stderr}"
    assert "PROCEED" not in proc.stdout
    assert router.alive(), "人の判断なしに占有側を落としてはならない"


def test_同一ツリーで配信中のコードを確かめられなければ黙って通さない(work: Path, routers) -> None:
    """申告口を持たない旧ルータ。「たぶん同じだろう」と仮定して exit 0 しない（ISSUE-348 と同じ規律）。"""
    repo = _make_repo(work, "tree")
    router = _FakeRouter(work, repo, "")
    routers.append(router)

    proc = _probe(work, repo_root=repo, router=router, restart=0, takeover=0)

    assert proc.returncode != 0
    assert "--restart" in proc.stderr
    assert router.alive()


def test_同一ツリーでrestartなら停止手順を通って起動へ進む(work: Path, routers) -> None:
    repo = _make_repo(work, "tree")
    router = _FakeRouter(work, repo, _identity(repo))
    routers.append(router)

    proc = _probe(work, repo_root=repo, router=router, restart=1, takeover=0)

    assert proc.returncode == 0, proc.stderr
    assert "PROCEED" in proc.stdout
    router.proc.wait(timeout=10)
    # Ctrl-C 相当（INT）で止めたこと＝親 serve.sh の trap cleanup が core を止める経路。
    assert router.proc.returncode in (0, -signal.SIGINT), router.proc.returncode


# ---------------------------------------------------------------- 別ツリー


def test_restartは別ツリーを落とさずtakeoverを案内する(work: Path, routers) -> None:
    mine = _make_repo(work, "mine")
    other = _make_repo(work, "other")
    router = _FakeRouter(work, other, _identity(other))
    routers.append(router)

    proc = _probe(work, repo_root=mine, router=router, restart=1, takeover=0)

    assert proc.returncode != 0
    assert "--takeover" in proc.stderr
    assert router.alive()


def test_takeoverは別ツリーを同じ停止手順で止めて起動へ進む(work: Path, routers) -> None:
    mine = _make_repo(work, "mine")
    other = _make_repo(work, "other")
    router = _FakeRouter(work, other, _identity(other))
    routers.append(router)

    proc = _probe(work, repo_root=mine, router=router, restart=0, takeover=1)

    assert proc.returncode == 0, proc.stderr
    assert "PROCEED" in proc.stdout
    router.proc.wait(timeout=10)


# ---------------------------------------------------------------- MT5 供給


@pytest.fixture()
def fake_mt5_watch(work: Path):
    """argv に ``<tree>/tools/mt5_tick_watch.py`` を持つ常駐の代役（別セッション）。"""
    started: list[subprocess.Popen] = []

    def start(tree: Path) -> subprocess.Popen:
        proc = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(120)", f"{tree}/tools/mt5_tick_watch.py"],
            start_new_session=True,
        )
        started.append(proc)
        return proc

    yield start
    for p in started:
        if p.poll() is None:
            p.kill()
            p.wait(timeout=10)


def test_MT5供給が動いているのに秘密が無ければ止める前に中止する(work: Path, routers, fake_mt5_watch) -> None:
    repo = _make_repo(work, "tree")
    router = _FakeRouter(work, repo, _identity(repo))
    routers.append(router)
    watch = fake_mt5_watch(repo)

    proc = _probe(work, repo_root=repo, router=router, restart=1, takeover=0)

    assert proc.returncode != 0
    assert "MT5_BRIDGE_SECRET" in proc.stderr
    assert router.alive(), "止めた後で供給だけ起動し直されない形を作ってはならない"
    assert watch.poll() is None


def test_MT5供給が動いていて秘密があれば入れ替えへ進む(work: Path, routers, fake_mt5_watch) -> None:
    repo = _make_repo(work, "tree")
    router = _FakeRouter(work, repo, _identity(repo))
    routers.append(router)
    fake_mt5_watch(repo)

    proc = _probe(work, repo_root=repo, router=router, restart=1, takeover=0,
                  env_extra={"MT5_BRIDGE_SECRET": "x"})

    assert proc.returncode == 0, proc.stderr
    assert "PROCEED" in proc.stdout


# ---------------------------------------------------------------- 計算量


def test_計算量_占有者への問い合わせはツリーのファイル数で増えない(work: Path, routers) -> None:
    counts = []
    for n in (3, 60):
        repo = _make_repo(work, f"tree_{n}", n_files=n)
        router = _FakeRouter(work, repo, _identity(repo))
        routers.append(router)
        proc = _probe(work, repo_root=repo, router=router, restart=0, takeover=0)
        assert proc.returncode == 0, proc.stderr
        counts.append(len(router.requests()))
    assert counts[0] == counts[1], counts


# ---------------------------------------------------------------- CLI・結線


def test_helpにrestartが載っている() -> None:
    proc = subprocess.run(["bash", str(_SERVE), "--help"], capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stderr
    assert "--restart" in proc.stdout


def test_起動時の申告をcoreの起動より前に作りrouterへ渡す() -> None:
    """core が読むコードより新しい申告を作らない（作る→core 起動の順なら、隙間の更新は「違う」側へ倒れる）。"""
    code = "\n".join(
        line for line in _SERVE.read_text(encoding="utf-8").splitlines()
        if not line.lstrip().startswith("#")
    )
    identity_at = code.index('identity "$REPO_ROOT"')  # di-ok(C2): 起動スクリプトは実行以外に観測手段が無い
    first_core_at = code.index('LIVE_PGID="$(start_core')
    assert identity_at < first_core_at  # di-ok(C2): 同上
    assert '--serving-code "$SERVING_CODE"' in code  # di-ok(C2): 同上


def test_停止の手順は1か所にしか無い() -> None:
    """takeover と restart で同じ停止手順を 2 か所に書かない（router への INT は stop_stack だけ）。"""
    code = _SERVE.read_text(encoding="utf-8")
    body_start = code.index("stop_stack() {")
    body_end = code.index("\n}\n", body_start)
    outside = code[:body_start] + code[body_end:]
    assert "kill -INT" not in outside  # di-ok(C2): 起動スクリプトは実行以外に観測手段が無い
