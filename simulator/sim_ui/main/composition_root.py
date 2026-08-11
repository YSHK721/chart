"""Composition Root — sim_ui バックエンドの DI 結線（CLEAN_ARCH §8・main 層）。

`SimApp`（framework）へ配信面の設定を注入する。パスは引数で受け、cwd 非依存の絶対パスで
解決する（replay_ui の `build_replay_app` と同一規約）。

Phase 1 の sim コアは静的配信だけを持つため、注入するのは配信根 2 つに限る。
ジョブ台帳 Port・ジョブ起動 Port は Phase 2（F-3）で、実在の変更要因とともに足す
（§11.4 YAGNI: 先に空の Port を定義しない）。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from simulator.sim_ui.framework.serve_sim import SimApp

# repo 根 = simulator/sim_ui/main/composition_root.py の parents[3]。
_REPO_ROOT = Path(__file__).resolve().parents[3]


def build_sim_app(
    *,
    repo_root: Any = None,
    web_dir: Any = None,
    shared_js_root: Any = None,
) -> SimApp:
    """配信面を結線した ``SimApp`` を返す。

    ``web_dir``: sim フロントの配信根（`simulator/sim_ui/web`）。None で静的配信無効
    （replay と同一規約。起動スクリプトが明示的に渡す）。
    ``shared_js_root``: 単一ソース共有のフォールバック根（既定
    ``<repo>/indigators/indicator_ui/web``）。ただし配信を許可するのは本根の
    ``js/``・``css/``・``vendor/`` サブツリーのみで、build.mjs / package.json / data /
    tests / node_modules 等は露出しない（最小権限＝StaticFileServer が許可根を限定する）。
    ``repo_root``: 既定値の導出元。差し替えると shared_js_root の既定も追随する
    （既定値の出所を 1 つに保つ）。
    """
    root = Path(repo_root).resolve() if repo_root is not None else _REPO_ROOT
    shared_js = (
        Path(shared_js_root).resolve()
        if shared_js_root is not None
        else root / "indigators" / "indicator_ui" / "web"
    )
    return SimApp(web_dir=web_dir, shared_js_root=shared_js)
