"""開発パスの正規登録（ISSUE-087 🟡-3・sys.path 実行時 insert の撤去）。

venv の site-packages へ .pth（標準の恒久パス登録機構）を書き、衝突しない固有名
トップパッケージのみを全プロセスで解決可能にする。登録するパスの一覧は
``tools/dev_paths.txt``（唯一源）から導出する（ここに書き写さない）。
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


def path_entries(root: Path) -> "list[Path]":
    """``tools/dev_paths.txt``（唯一源）を ``root`` 起点の絶対パスへ解決する（ISSUE-279）。

    値をここに書き写さない。台帳へ 1 行足せば .pth / serve.sh / pytest の 3 経路すべてへ伝播する
    （一致は ``tools/tests/test_dev_paths_single_source.py`` が強制）。
    """
    ledger = Path(__file__).resolve().parent / "dev_paths.txt"
    out: "list[Path]" = []
    for raw in ledger.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        out.append(root if line == "." else root / line)
    return out


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
