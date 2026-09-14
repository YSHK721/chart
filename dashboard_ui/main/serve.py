"""main 層のプロセス起動口 — `python -m dashboard_ui.main.serve <port> [--repo-root <path>]`。

ここが持つのは 3 つだけである。どれも **束縛（どの具象を、どの方針で組むか）** に属する:

    1. argv の解釈（待受けポート・配信元ツリー）
    2. 本番既定（確定素材の持ち越し persist と起動時の温め warmup）
    3. Composition Root の呼出と、組んだアプリの待受けへの引き渡し

なぜ framework でなく main か（ISSUE-502 段階 3・台帳 C-3 / F-11 / F-13）:
    起動口が `framework/serve_dashboard.py` にあった間、束縛のために framework が
    `dashboard_ui.main.composition_root` を **関数の中で** import していた。関数内 import は
    循環を「実行時まで遅らせる」だけで消しはしない（main → framework → main）。同時に
    「HTTP の口の形」と「本番の運用方針」という**別アクター**が 1 モジュールに同居していた
    （SRP）。所有者を main へ移すと、両方が 1 手で消える。framework 側は HTTP の殻
    （`serve_dashboard.DashboardApp` / `serve_dashboard.make_handler` /
    `serve_dashboard.make_server` / `serve_dashboard.serve`）だけを残し、`dashboard_ui` の
    中身を一切 import しない。再発は機械的検査
    （`tests/unit/test_dashboard_import_direction.py`）が遮断する。

`--repo-root` は**配信元ツリーの絶対パス**である。省略時は Composition Root が自分のファイル
位置から解決する（既定の挙動は不変）。統合 UI の起動スクリプト（unified_ui/serve.sh）は必ず
渡す: PYTHONPATH は ps の argv に現れないため、これが無いと停止側が「8481 を握っているのが
どのツリーの core か」を判定できず、別ツリーの残骸を掴んだまま起動する（ISSUE-348 /
ISSUE-355 と同型の「他人のコードを自分のものとして見る」事故）。

呼び出しはモジュール経由（`serve_dashboard.serve(...)` / `composition_root.build_...`）で行う。
名前を手元へ束縛すると、待受けの実体が本モジュールの属性としても現れて呼び名が 2 つになる。
"""
from __future__ import annotations

import sys

from dashboard_ui.framework import serve_dashboard
from dashboard_ui.main import composition_root

#: 既定の待受けポート（統合 UI の `DASHBOARD_PORT` と同値）。
DEFAULT_PORT = 8481

#: 配信元ツリーを明示する起動引数（ISSUE-348 の規律）。
REPO_ROOT_OPTION = "--repo-root"


def main(argv: "list[str] | None" = None) -> None:
    """argv を読み、アプリを 1 つ組んで待受けへ渡す（ブロッキング）。"""
    port, repo_root = _parse(list(sys.argv[1:] if argv is None else argv))
    serve_dashboard.serve(
        composition_root.build_dashboard_app(
            repo_root=repo_root,
            # 確定素材の持ち越しと起動時の温め（ISSUE-501 段階 2・依頼者承認 2026-09-06）。
            #   本番の起動口だけが有効化する（テスト・in-process 計測は既定 OFF＝隔離）。
            persist=True,
            warmup=True,
        ),
        port=port,
    )


def _parse(arguments: "list[str]") -> "tuple[int, str | None]":
    """`<port> [--repo-root <path>]` を `(port, repo_root)` へ。"""
    port = DEFAULT_PORT
    repo_root: "str | None" = None
    rest = list(arguments)
    while rest:
        token = rest.pop(0)
        if token == REPO_ROOT_OPTION and rest:
            repo_root = rest.pop(0)
            continue
        if not token.startswith("-"):
            port = int(token)
    return port, repo_root


if __name__ == "__main__":
    main()
