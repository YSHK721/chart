"""Composition Root（指標一覧つき sim core・main 層・CLEAN_ARCH §8・Phase 3 F-5）。

Phase 2 の `composition_root_jobs.build_sim_job_app`（配信面 ＋ ジョブ実行系）を**包む**。
結線をここへ書き写さない。写した瞬間に「Phase 2 の既定値を変えたのに Phase 3 だけ古い」
という食い違いが生まれる。

結線（DIP: usecase は抽象にのみ依存し、実装の選択はここだけが知る）:
    IndicatorCausalityLedgerPort → FileIndicatorCausalityLedger（FS 台帳）

因果性台帳の根は**ジョブ台帳の根をそのまま使う**（`data_root` の既定値を二重定義しない）。

起動時の自動再検定はしない（YAGNI・§11.4）。検定は CLI（`verify_indicator_causality_cli`）
の責務で、sim core は台帳を**読むだけ**である。台帳が無ければ `/indicators` は 503 を返す
（fail-closed）。
"""
from __future__ import annotations

from typing import Any

from simulator.sim_ui.adapter.file_indicator_causality_ledger import (
    FileIndicatorCausalityLedger,
)
from simulator.sim_ui.adapter.indicator_api_controller import IndicatorApiController
from simulator.sim_ui.framework.serve_sim_indicators import SimIndicatorApp
from simulator.sim_ui.main.composition_root_jobs import build_sim_job_app


def build_sim_indicator_app(
    *,
    repo_root: Any = None,
    web_dir: Any = None,
    shared_js_root: Any = None,
    data_root: Any = None,
) -> SimIndicatorApp:
    """配信面・ジョブ実行系・指標一覧を結線した :class:`SimIndicatorApp` を返す。

    引数の規約は Phase 2 の `build_sim_job_app` と同一（そのまま素通しする）。
    """
    inner = build_sim_job_app(
        repo_root=repo_root,
        web_dir=web_dir,
        shared_js_root=shared_js_root,
        data_root=data_root,
    )
    # 台帳の根は Phase 2 が解決した値をそのまま使う（既定値の出所を 1 つに保つ）。
    ledger = FileIndicatorCausalityLedger(data_root=inner.ledger.data_root)
    return SimIndicatorApp(
        inner=inner, controller=IndicatorApiController(ledger=ledger)
    )
