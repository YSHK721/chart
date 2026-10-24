"""公開済みページの HTML ファイルに目次を差し込む（生成元がリポジトリに無いページ用）。

実行: lightweight-charts-python-main/.venv/bin/python -m tools.measure.page_toc_cli <入力 html> <出力 html> [要素 class]
    h2 を使わないページは、見出しにする要素と class を渡す（例: div ftitle）。
出力: 「見出し N 件」。N < 2 なら差し込まず、出力ファイルは作らない（終了コード 3）。
"""
from __future__ import annotations

import sys
from pathlib import Path

from tools.measure.page_toc import headings, inject_toc, unwrap_skeleton


def main(src: str, dst: str, tag: str = "h2", cls: str | None = None) -> int:
    html = unwrap_skeleton(Path(src).read_text(encoding="utf-8"))
    _, heads = headings(html, tag, cls)
    print(f"見出し {len(heads)} 件")
    if len(heads) < 2:
        return 3
    Path(dst).write_text(inject_toc(html, tag=tag, cls=cls), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:5]))
