"""開発パスの正規登録（ISSUE-087 🟡-3・sys.path 実行時 insert の撤去）。

venv の site-packages へ .pth（標準の恒久パス登録機構）を書き、衝突しない固有名
トップパッケージのみを全プロセスで解決可能にする。登録するパスの一覧は
``tools/dev_paths.txt``（唯一源）から導出する（ここに書き写さない）。台帳の**読み手**は
中立核 :func:`common.dev_paths.path_entries` が所有し、本モジュールは呼ぶだけである
（ISSUE-502 C-1: 読み手を運用スクリプト層に置くと simulator ⇄ tools の循環になる）。
汎用名パッケージ（indicator_ui api の adapter/framework/domain、replay_ui の同名群）は
スライス間で名前衝突するため .pth へ載せず、各エントリポイント（server.py / bridge）が
自スライスの root だけを結線する。

**位置づけ（ISSUE-279・重要）**: 本 .pth が指すのは「install を実行したチェックアウト」の
絶対パスであり、venv を共有する git worktree から起動しても main の実装が読まれる。
したがって .pth は**権威ではなくフォールバック**（主にメインチェックアウトでの対話シェル用）。
実行時（serve.sh）とテスト時（pytest）は、それぞれ自分の位置から解決する:
  - serve.sh 群 → ``tools/dev_paths.sh`` を source（PYTHONPATH は .pth より先に解決される）
  - pytest      → ``pyproject.toml`` の ``pythonpath``（rootdir 相対）

備考: editable install（pip install -e）は venv に setuptools が無くオフラインのため不採用。
.pth は同等の正規機構（site モジュール標準）で、ビルドバックエンド不要。

実行: <venv>/bin/python tools/install_dev_paths.py
"""
from __future__ import annotations

import site
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PTH_NAME = "jp225_chart_paths.pth"

# ブートストラップ（本モジュールだけの例外・ISSUE-502 C-1）:
#   本モジュールは「解決機構（.pth）そのものを設置する側」であり、**まだ設置されていない
#   環境で起動される**のが正常系である（新しい venv・新しいコンテナ・setup_worktree.sh の
#   初回実行）。したがって自分が要る import を .pth に頼ってはならない。素の
#   `python tools/install_dev_paths.py` は sys.path 先頭へ `tools/` を置くだけなので、
#   チェックアウト根を明示的に足してから中立核を読む。
#   実測（2026-09-07）: この 2 行が無い状態で `python -I -S tools/install_dev_paths.py`
#   相当を起動すると ModuleNotFoundError: No module named 'common' で死ぬ。
#   これは ISSUE-479 Wave2 2-7 が撤去した「実行時 sys.path 書き換え」とは別物である。
#   撤去対象は台帳で解決できるのに自前で書き換えていた CLI 群であり、本モジュールは
#   その台帳を届ける機構を設置する当のものだから、自己充足でなければならない。
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# 台帳（tools/dev_paths.txt）の読み手は中立核が所有する（ISSUE-502 C-1）。読み手には
# 運用スクリプト層（本モジュール）と simulator の sim_ui（子プロセスの PYTHONPATH）という
# 別アクターの消費者が居る。実体をここに置くと sim_ui → tools の辺が生まれ、tools → simulator
# （ops が product を駆動する既存の向き）と合わせて循環になる。本モジュールが持つのは
# 「.pth を書く」という運用行為だけであり、読み取り規則は持たない。
from common.dev_paths import path_entries  # noqa: E402  (上のブートストラップの後でなければ解決しない)


LINES = [str(p) for p in path_entries(ROOT)]


def main() -> None:
    sp = site.getsitepackages()
    if not sp:
        print("site-packages が見つかりません", file=sys.stderr)
        raise SystemExit(1)
    target = Path(sp[0]) / PTH_NAME
    content = "\n".join(LINES) + "\n"
    if target.exists() and target.read_text() == content:
        print(f"最新: {target}")
        return
    target.write_text(content, encoding="utf-8")
    print(f"登録: {target}\n" + content)


if __name__ == "__main__":
    main()
