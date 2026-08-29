"""静的品質検定の走査範囲（本リポジトリ固有・**唯一の定義**）。

`declaration_integrity.py` / `test_quality.py` は汎用ツールであり、リポジトリ固有の
除外を持たない。本モジュールがその 1 か所であり、pytest 入口・Stop フックの
どちらもここを読む（同じ内容を 2 か所に書かない）。

除外の根拠（実測 2026-08-29・py ファイル数）:

  .claude/worktrees        24,226 本  作業ブランチの完全な複製 23 個。他ブランチの違反を拾う
                                      （ツール側の DEFAULT_EXCLUDE に `.claude` として入れた）
  lightweight-charts-python-main
                            2,277 本  vendored な第三者ライブラリ（upstream の LICENSE/README/
                                      egg-info あり）。我々が直す対象ではない

除外前後: 走査 2 分 48 秒 → 11.5 秒 / 検出 28,792 件 → 2,309 件。
"""

from __future__ import annotations

#: リポジトリ固有に走査から外すディレクトリ名。
PROJECT_EXCLUDE: frozenset[str] = frozenset({
    "lightweight-charts-python-main",
})


def apply(*modules) -> None:
    """渡した検定モジュールの ``DEFAULT_EXCLUDE`` へ本リポジトリの除外を足す。"""
    for m in modules:
        m.DEFAULT_EXCLUDE.update(PROJECT_EXCLUDE)
