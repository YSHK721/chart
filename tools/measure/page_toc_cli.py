"""公開済みページの HTML ファイルに目次を差し込む（生成元がリポジトリに無いページ用）。

実行: lightweight-charts-python-main/.venv/bin/python -m tools.measure.page_toc_cli <入力 html> <出力 html>
出力: 「見出し N 件」。N < 2 なら差し込まず、出力ファイルは作らない（終了コード 3）。
"""
from __future__ import annotations

import sys
from pathlib import Path

from tools.measure.page_toc import headings, inject_toc


def main(src: str, dst: str) -> int:
    html = Path(src).read_text(encoding="utf-8")
    _, heads = headings(html)
    print(f"見出し {len(heads)} 件")
    if len(heads) < 2:
        return 3
    Path(dst).write_text(inject_toc(html), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2]))
