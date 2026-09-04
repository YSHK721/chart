"""``unified_ui/serve.sh`` が sim core を結線していることを固定する（基本設計書 §11.3 = D-3）。

なぜ必要か（実測された壊れ方）:
    受け口だけを作って**呼び出し側が送らない**という壊れ方は、この構成で実際に起きている
    （ISSUE-291: サーバ側に分岐を作っても front が送らなければ無言で死ぬ）。今回はさらに
    router の CLI が変わる（``--live-upstream`` / ``--replay-upstream`` を廃し
    ``--upstream <mode>=<url>`` の繰り返し指定にする）。``serve.sh`` を同時に直さないと、
    router は**旧フラグを受け付けないまま起動に失敗する**か、既定値で黙って動く。
    どちらも「自分の変更が効いていない UI を見る」ことにつながる（ISSUE-348 と同型）。

固定する不変条件:
    1. sim core の内部ポートは 8381（§11.1 裁定 1・統合配下のみ）。
    2. sim core を起動・待機・停止（cleanup の PGID kill）の各段に載せている。
    3. router へ 3 モードの上流を新 CLI 形式で渡している。
    4. 廃止した旧フラグを残していない（router 側と食い違わない）。
    5. sim core は venv python で起動する（生 python 起動禁止・detached 起動禁止）。

方式: 起動せずにスクリプト本文を読む（8000/8001/8281/8381 を実際に掴まない）。
      併せて ``bash -n`` で構文を確かめる。
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_SERVE = _ROOT / "unified_ui" / "serve.sh"
_SRC = _SERVE.read_text(encoding="utf-8")

# コメント行を除いた実行文だけを見る（注記としての言及に反応しないため）。
_CODE = "\n".join(
    line for line in _SRC.splitlines() if not line.lstrip().startswith("#")
)


def test_構文が壊れていない() -> None:
    proc = subprocess.run(
        ["bash", "-n", str(_SERVE)], capture_output=True, text=True, timeout=30
    )
    assert proc.returncode == 0, f"bash -n が失敗した: {proc.stderr}"


def test_sim_coreの内部ポートは8381() -> None:
    """§11.1 裁定 1: 統合配下の sim コアは 8381（8380 は単独起動用に予約）。"""
    assert re.search(r"^SIM_PORT=8381$", _CODE, re.MULTILINE), (
        "SIM_PORT=8381 の宣言が無い（既存 LIVE_PORT / REPLAY_PORT と同じ場所に置く）"
    )


def test_sim_coreを起動して待機する() -> None:
    """起動と生存待ちの両方に載っていること（起動だけして待たないと router が先に上がる）。"""
    assert "SIM_PGID=" in _CODE, "sim core の起動が無い"
    assert re.search(r'wait_up\s+"http://127\.0\.0\.1:\$\{SIM_PORT\}/"', _CODE), (
        "sim core の起動待ちが無い"
    )


def test_sim_coreを停止経路に載せている() -> None:
    """cleanup（trap EXIT/INT/TERM）でプロセスグループごと止めること。"""
    cleanup = _CODE.split("cleanup()", 1)[1].split("}", 1)[0]
    assert "SIM_PGID" in cleanup, "cleanup で sim core を停止していない（起動しっぱなしになる）"


def test_sim_coreはvenv_pythonで起動する() -> None:
    """生 python 起動禁止（NFR-08）。venv には pandas 等の依存が入っている。"""
    start = _CODE.split("start_sim_core()", 1)[1].split("\n}", 1)[0]
    assert '"$VENV_PY"' in start, "sim core を venv python で起動していない（生 python 起動）"
    assert "build_sim_app" in start, "sim core の Composition Root を呼んでいない"
    assert "nohup" not in start, "detached 起動は禁止（Ctrl-C 停止を壊す）"
    assert "setsid" in start, (
        "プロセスグループ起動でないと cleanup のグループ kill が届かない"
    )


def test_routerへ3モードの上流を新CLI形式で渡す() -> None:
    """§11.1 裁定 6: `--upstream <mode>=<url>` の繰り返し指定。"""
    for mode, port in (("live", "LIVE_PORT"), ("replay", "REPLAY_PORT"), ("sim", "SIM_PORT")):
        pattern = rf'--upstream\s+"{mode}=http://127\.0\.0\.1:\$\{{{port}\}}"'
        assert re.search(pattern, _CODE), f"router へ {mode} の上流を新形式で渡していない"


def test_廃止した旧フラグを残していない() -> None:
    """router 側で廃止した引数を渡し続けると起動時に落ちる（argparse が未知引数を拒む）。"""
    assert "--live-upstream" not in _CODE
    assert "--replay-upstream" not in _CODE


def test_venv_pythonの不在を即時に明示エラーにする() -> None:
    """venv が無いことを起動前に言う（🟡-1）。

    言わないと sim core だけがサイレントに死に、`wait_up` の 60 秒タイムアウトで
    「シミュレーション core が起動しませんでした」とだけ出る。原因（venv 不在）が
    メッセージに現れないため、切り分けに時間を取られる。両 core の serve.sh
    （`simulator/replay_ui/serve.sh:41-44`）は既に同じ検査を持つ。
    """
    assert re.search(r'if\s+\[\s+!\s+-x\s+"\$VENV_PY"\s+\]', _CODE), (
        "VENV_PY の実行可能検査が無い（replay/indicator の serve.sh と同流儀で置く）"
    )
    # 検査は「必須ファイル検査ループ」の直後、つまり core 起動より前に置く。
    assert _CODE.index('! -x "$VENV_PY"') < _CODE.index("start_sim_core"), (
        "venv 検査が sim core 起動より後にある（起動を試みてから落ちる）"
    )


def test_非takeover経路でも8381の占有を配信元つきで判定する() -> None:
    """8381 が別ツリーの sim core に握られたまま起動しない（🟡-3・ISSUE-348 同型）。

    8000 の判定だけでは足りない: 8000 が空いていても 8381 に他ツリーの sim core が
    残っていると、自分の sim core が bind できずに死に、router は**別ツリーの sim core** を
    proxy する。自分のコードが 1 行も入っていない sim を自分のものとして検証してしまう。
    自ツリー由来（argv にこのツリーの web 根を持つ）なら止めて起動し、他ツリーなら中断する。
    """
    # 判定ブロックが在ること（takeover 経路の外＝core 起動の前）。
    assert re.search(r"SIM_PORT.*占有|占有.*SIM_PORT|ensure_sim_port_free", _CODE), (
        "非 takeover 経路に 8381 の占有判定が無い"
    )
    block = _CODE.split("ensure_sim_port_free()", 1)
    assert len(block) == 2, "占有判定は名前の付いた関数に閉じる（呼び出し位置を検定できるように）"
    body = block[1].split("\n}", 1)[0]
    # 自ツリー判定は takeover 経路と同じ手掛かり（このツリーの web 根の絶対パス）で行う。
    assert 'pids_with "${REPO_ROOT}/simulator/sim_ui/web"' in body, (
        "自ツリー由来かを argv のツリー絶対パスで判定していない"
    )
    # 自ツリーなら停止（takeover 経路の停止手続きを再利用する＝手順を二重に持たない）。
    assert "stop_sim_core_if_up" in body, "自ツリーの残存を停止していない"
    # 他ツリーなら中断し、何が占有しているかを示す。
    assert "exit 1" in body, "他ツリー占有時に中断していない"
    # core 起動より前に呼ぶ（起動を試みてから気付くのでは遅い）。
    assert _CODE.index("ensure_sim_port_free\n") < _CODE.index('SIM_PGID="$(start_sim_core)"'), (
        "占有判定が sim core 起動より後にある"
    )


def test_frontのモード集合とrouter上流のモード名が一致する() -> None:
    """front（モード定義表）と back（serve.sh の --upstream）でモード名が食い違わない。

    注記（TDD の誠実性）: 本ケースは追加時点で既に緑である（front / back とも先行 Cycle で
    同じ名前に揃えた）。Red ではなく**回帰壁**として置く。理由は、この 2 つが食い違うと
    front は `/sim/*` を出すのに router に `/sim` の上流が無い＝**404 になるだけで
    エラーメッセージが出ない**からである（無音の失敗）。検出力は変異注入で確認済み:
    serve.sh の `sim=` を別名にすると本ケースが落ちる。
    """
    table = (_ROOT / "unified_ui" / "web" / "js" / "mode_table.js").read_text(encoding="utf-8")
    front_modes = set(re.findall(r"^\s*id:\s*'([a-z0-9_]+)',", table, re.MULTILINE))
    back_modes = set(re.findall(r'--upstream\s+"([a-z0-9_]+)=', _CODE))
    assert front_modes, "モード定義表からモード名を取れていない（表の書式が変わった）"
    assert front_modes == back_modes, (
        f"front のモード集合 {sorted(front_modes)} と router 上流 {sorted(back_modes)} が食い違う"
        "（front が出す /<mode>/* に上流が無いと、無音の 404 になる）"
    )


def test_占有引き継ぎでもsim_coreを止める() -> None:
    """別ツリーのスタックを引き継ぐとき、sim core を残すと 8381 の bind に失敗する。"""
    stop_stack = _CODE.split("stop_stack()", 1)[1].split("\n}", 1)[0]
    assert re.search(r"stop_sim_core_if_up\s", stop_stack), (
        "stop_stack で sim core を止めていない（残ると自分の sim core が bind できず、"
        "router が別ツリーの sim core を proxy する＝ISSUE-348 と同型の事故）"
    )
    # 停止対象は**そのツリーの** sim core に限る（他ツリーの同名プロセスに触れない）。
    helper = _CODE.split("stop_sim_core_if_up()", 1)[1].split("\n}", 1)[0]
    assert "SIM_PORT" in helper
    assert "simulator/sim_ui/web" in helper, (
        "argv 照合にツリーの絶対パスを含めていない（どのツリーの sim core か一意に決まらない）"
    )
