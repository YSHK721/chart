"""Composition Root（表示層つき sim core・main 層・CLEAN_ARCH §8・Phase 4 F-7）。

Phase 3 の `composition_root_indicators.build_sim_indicator_app`（配信面 ＋ ジョブ実行系
＋ 指標一覧）を**包む**。結線をここへ書き写さない。写した瞬間に「Phase 3 の既定値を
変えたのに Phase 4 だけ古い」という食い違いが生まれる。

結線（表示層の配信根）:
    /report-js     → `simulator/report_ui/web/js`   （linkage / table / format / chart の実体）
    /report-css    → `simulator/report_ui/web/css`  （style.css の実体）
    /report-vendor → `simulator/report_ui/web/vendor` の **chart.umd.js 1 ファイルだけ**

vendor 根には Chart.js **v4.4.1**（比較・判定タブが要る・Phase 5 R-1 で承認）と
lightweight-charts **v4.1.3** が同居している。統合 UI が読み込む lightweight-charts は
共有根（`indigators/indicator_ui/web/vendor`）の **v5.2.0** ただ 1 つでなければならず、
2 つのバージョンが同じページへ載る経路を作らない（NFR-07）。よって根をそのまま配信せず、
`AllowlistFileRoutes` で **許可した 1 ファイル以外は内側の配信器へ渡さない**。到達不能を
「経路が無いこと」で担保する。許可集合は実 HTTP 検定
（`tests/integration/test_serve_sim_display{,_vendor}.py`）と本モジュールで二重に固定する。

symlink で report_ui の資産を sim の web 根へ引き込む案は採らない。`StaticFileServer` は
resolve() 後の実パスで許可根を判定するため、許可根の外を指す symlink は 404 になる
（実測済み）。根が違うものは根ごと別の配信器を立てる。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from simulator.replay_ui.framework.static_file_server import StaticFileServer
from simulator.sim_ui.adapter.ea_registry_series_catalog import EaRegistrySeriesCatalog
from simulator.sim_ui.adapter.ea_series_api_controller import EaSeriesApiController
from simulator.sim_ui.framework.allowlist_file_routes import AllowlistFileRoutes
from simulator.sim_ui.adapter.run_options_api_controller import RunOptionsApiController
from simulator.sim_ui.adapter.symbol_spec_catalog import SymbolSpecCatalog
from simulator.sim_ui.framework.serve_sim_display import SimDisplayApp
from simulator.sim_ui.framework.serve_sim_ea_series import SimEaSeriesApp
from simulator.sim_ui.framework.serve_sim_run_options import SimRunOptionsApp
from simulator.sim_ui.main.composition_root_indicators import build_sim_indicator_app
from simulator.sim_ui.usecase.list_run_options import ListRunOptionsInteractor

# repo 根 = simulator/sim_ui/main/composition_root_display.py の parents[3]。
_REPO_ROOT = Path(__file__).resolve().parents[3]

#: report_ui の JS 実体を引く prefix（sim core は prefix 除去後のパスを受ける）。
REPORT_JS_PREFIX = "/report-js"
#: report_ui の CSS 実体を引く prefix。
REPORT_CSS_PREFIX = "/report-css"
#: report_ui の vendor 実体を引く prefix（許可した 1 ファイルだけが通る）。
REPORT_VENDOR_PREFIX = "/report-vendor"
#: vendor 根から配信を許すファイル（R-1・依頼者承認）。Chart.js v4.4.1 ただ 1 つ。
#: 同じ根に同居する lightweight-charts v4.1.3 は**載せない**——統合ページが読む vendor は
#: 共有根の v5.2.0 だけであり（NFR-07）、2 つの版が同じページへ載る経路を作らない。
REPORT_VENDOR_ALLOWED = frozenset({"/chart.umd.js"})


def build_sim_display_app(
    *,
    repo_root: Any = None,
    web_dir: Any = None,
    shared_js_root: Any = None,
    data_root: Any = None,
) -> SimDisplayApp:
    """配信面・ジョブ実行系・指標一覧・表示層の配信根を結線した :class:`SimDisplayApp` を返す。

    引数の規約は Phase 3 の `build_sim_indicator_app` と同一（そのまま素通しする）。
    """
    root = Path(repo_root).resolve() if repo_root is not None else _REPO_ROOT
    inner = build_sim_indicator_app(
        repo_root=repo_root,
        web_dir=web_dir,
        shared_js_root=shared_js_root,
        data_root=data_root,
    )
    # Phase 6 F-8（名前空間結線・依頼者承認 2026-08-12）: 実行指示パネルの指標候補源となる
    # `GET /ea-series/{ea_name}`（その EA の registry 系列名・単一ソース EaRegistrySeriesCatalog）
    # を委譲で 1 本足す。既存の配信面・API 面は素通し（OCP・byte 不変）。
    inner = SimEaSeriesApp(
        inner=inner,
        controller=EaSeriesApiController(catalog=EaRegistrySeriesCatalog()),
    )
    # Phase 6 拡張（run config フォーム結線・依頼者承認 2026-08-12）: 実行指示フォームの選択肢
    # `GET /run-options`（データセット別プロファイル＋ea_name 一覧・単一ソース SymbolSpecCatalog）
    # を委譲でもう 1 本足す。既存の配信面・API 面は素通し（OCP・byte 不変）。
    inner = SimRunOptionsApp(
        inner=inner,
        controller=RunOptionsApiController(
            options=ListRunOptionsInteractor(port=SymbolSpecCatalog())
        ),
    )
    report_web = root / "simulator" / "report_ui" / "web"
    return SimDisplayApp(
        inner=inner,
        static_routes={
            # 第 2 引数（shared_js_root）は None。各根は自分のサブツリーだけを許可する
            # （最小権限）。vendor 根は**含めない**＝v4 バンドルへの経路が存在しない。
            REPORT_JS_PREFIX: StaticFileServer((report_web / "js").resolve(), None),
            REPORT_CSS_PREFIX: StaticFileServer((report_web / "css").resolve(), None),
            # vendor 根は**許可した 1 ファイルだけ**を通す（R-1）。何を出すかの方針は
            # 合成根が持ち、配信機構（`AllowlistFileRoutes`）はリテラルを持たない。
            REPORT_VENDOR_PREFIX: AllowlistFileRoutes(
                StaticFileServer((report_web / "vendor").resolve(), None),
                allowed=REPORT_VENDOR_ALLOWED,
            ),
        },
    )
