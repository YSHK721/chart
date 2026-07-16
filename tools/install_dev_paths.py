"""開発パスの正規登録（ISSUE-087 🟡-3・sys.path 実行時 insert の撤去）。

venv の site-packages へ .pth（標準の恒久パス登録機構）を書き、衝突しない固有名
トップパッケージのみを全プロセスで解決可能にする:
  - リポジトリ根（marketdata / common / common_view / api_shared / tools）
  - indigators/market_profile/api（market_profile_api）
汎用名パッケージ（indicator_ui api の adapter/framework/domain、replay_ui の同名群）は
スライス間で名前衝突するため .pth へ載せず、各エントリポイント（server.py / bridge）が
自スライスの root だけを結線する。

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
LINES = [str(ROOT), str(ROOT / "indigators" / "market_profile" / "api")]


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
