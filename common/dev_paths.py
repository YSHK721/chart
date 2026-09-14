"""チェックアウトの import パス台帳の**読み手**（アクター非依存の中立核・ISSUE-502 C-1）。

「1 つのチェックアウトを構成する import パス」の唯一源は ``tools/dev_paths.txt`` である。
本モジュールはその台帳を読み、与えられたチェックアウト根を起点とする絶対パス列へ解決する
だけの純粋関数を持つ（値をここに書き写さない）。

なぜ中立核が所有するか（循環 C-1 の除去）:
    読み手には**別アクターの 2 つの消費者**がいる。
      - 運用スクリプト層（tools/install_dev_paths.py）: venv へ .pth を書く。
      - simulator の sim_ui（composition_root_jobs）: 計算の子プロセスへ渡す PYTHONPATH。
    実体を運用スクリプト層に置いたままだと、後者から前者への辺（simulator → tools）が生まれる。
    運用スクリプト層は元々 simulator を import する側（ops が product を駆動する向き）なので、
    この辺は **循環**になる。規則の実体は stdlib だけで書かれた汎用抽象であり、どちらの
    アクターにも属さない。よって中立核である本パッケージが所有し、両アクターがここを参照する。
    ``common.watch_loop``（循環 C-2 の是正・ISSUE-479 F-3）と同一の解である。

向きの固定は ``tools/tests/test_no_cycle_tools_simulator.py``（AST 走査）が強制する。

依存方向: 本モジュールは **stdlib のみ**に依存する（common の中立核規約）。台帳ファイルの
在り処はチェックアウトの配置に関する事実であり、運用スクリプト層への import 辺ではない。
"""
from __future__ import annotations

from pathlib import Path

#: チェックアウト根（``common/`` の親）。
_CHECKOUT_ROOT = Path(__file__).resolve().parents[1]

#: import パス台帳の唯一源。値ではなく**場所**だけを持つ（中身は台帳が持つ）。
LEDGER_PATH = _CHECKOUT_ROOT / "tools" / "dev_paths.txt"


def path_entries(root: Path) -> "list[Path]":
    """``tools/dev_paths.txt``（唯一源）を ``root`` 起点の絶対パスへ解決する（ISSUE-279）。

    値をここに書き写さない。台帳へ 1 行足せば .pth / serve.sh / pytest の 3 経路すべてへ伝播する
    （一致は ``tools/tests/test_dev_paths_single_source.py`` が強制）。

    台帳そのものは**本モジュールが属するチェックアウト**から読み、行の解決だけを ``root``
    起点で行う（ISSUE-279 の「起動しているツリーから解決する」規約。旧所在
    ``tools/install_dev_paths.path_entries`` と同一の意味で、1 バイトも挙動を変えていない）。
    """
    out: "list[Path]" = []
    for raw in LEDGER_PATH.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        out.append(root if line == "." else root / line)
    return out
