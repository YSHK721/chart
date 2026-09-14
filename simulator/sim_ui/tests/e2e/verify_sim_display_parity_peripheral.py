"""パリティ 周辺点の二画面突合（Playwright・Phase 5 F-7）。

Phase 4 の `verify_sim_display_parity.py`（骨格 12 点）の続きで、Phase 5 で移植した
**周辺の表示**（タブ・区間トグル・抽出ピル・ヒートマップ・比較グラフ・判定バナー・用語集・
接点マーカー）を、移植元 report_ui の画面と sim 表示層の画面へ**同一 payload**を与えて
同じ観測点で突き合わせる。移植元は vendor v4.1.3・sim は v5.2.0 で、series 生成と
マーカー API だけが違う——その主張を周辺点でも観測値で固定する。

fixture は移植元 `report_ui/tests/e2e/verify_parity.py` の 2 区間 payload を源にし、
**接点（agg.contacts）を各区間へ足す**（移植元の payload は接点キーを持たないため）。
verify_parity.py 自体は 1 文字も変えない（別テストが自分用に payload を augment する）。

採用点（一致率 100% の分母・基本設計書 §13 パリティ点）:
    P1  タブ帯が宣言（`SIM_TAB_NAMES`）と過不足なく一致し、移植元から流用すべきタブを
        取り落としていない（graph/report は流用しない）。**期待値は宣言と移植元の実タブ
        から導出する**——リテラルの集合は書かない（工程 5 レビュー 🔴-1）
    P19 分析タブの面が実際に生えている（合成根が `mountTraceAnalysis` を呼んでいる）。
        ISSUE-291 型「受け口はあるのに呼ばれない」を捕らえる唯一の検査（静的検査では
        到達可能性を証明できない・工程 5 実測）
    P2  ヒートマップ 5 ビュー全セル（2 区間なので IS/OOS 損益差ビューも出る）
    P3  セルクリック → 抽出連動（activeFilter・#tradeTable dim・#chartBadge）
    P4  contactsToMarkers 戻り値突合（両画面で同一実体を import して呼ぶ）
    P5  接点トグル独立性（setContactsVisible が売買マーカーを変えない）
    P6  判定バナー（#cmpVerdict の文言）
    P7  7 指標カード（#cmpBasic）
    P8  劣化比較表（#cmpTable 行）
    9   用語集 + tip（gcard/gitem 数・data-gg hover の #tip 発火）
    12/13/14  比較グラフ 3 種（window.__cmpCharts 経由のデータ突合）
    15  区間トグル（#segSel・segbtn 2 個・is/oos）
    17  サマリーカード非存在（sim は report タブを持たない）
    18  抽出ピル（#clearFilter 可視・#detailCount 件数）
    U-1 接点グリフ y アンカー（両画面とも透明 LineSeries value=close＋position で一致）

Phase 4 の 12 点は同一セッションで回帰 0 件（表示規則の実体突合を再掲）。
chromium / playwright 不在環境では skip（移植元 verify.py の規約準拠）。
"""
from __future__ import annotations

import copy
import json
import shutil
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[4]
_REPORT_TESTS = _REPO / "simulator" / "report_ui" / "tests"
if str(_REPORT_TESTS) not in sys.path:
    sys.path.insert(0, str(_REPORT_TESTS))

from e2e import _harness  # noqa: E402
from e2e import verify_parity  # noqa: E402  (2 区間 fixture の唯一源)

pytestmark = pytest.mark.e2e

REPORT_WEB = _REPO / "simulator" / "report_ui" / "web"
SIM_WEB = _REPO / "simulator" / "sim_ui" / "web"
SHARED_VENDOR = _REPO / "indigators" / "indicator_ui" / "web" / "vendor" / "lightweight-charts.js"

JOB_ID = "parityperi"
_TS = verify_parity._TS


def _payload_with_contacts() -> dict:
    """移植元 2 区間 payload に接点（agg.contacts）を各区間へ足したもの。

    接点は区間の bars 範囲（``_TS`` .. ``_TS+180``）内・close(=105) 近傍に置く。両画面とも
    透明 LineSeries(value=close) へ position(aboveBar/belowBar) で描くので、price 値は
    アンカーに使われない（U-1: グリフ y は close に対する上下位置で一意）。
    """
    payload = copy.deepcopy(verify_parity._payload())
    payload["segments"]["is"]["agg"]["contacts"] = [
        {"time": _TS + 60, "price": 105.0, "dir": "up"},
        {"time": _TS + 120, "price": 105.0, "dir": "down"},
    ]
    payload["segments"]["oos"]["agg"]["contacts"] = [
        {"time": _TS + 60, "price": 105.0, "dir": "down"},
    ]
    return payload


def _build_ref_web_root(tmp_path: Path) -> Path:
    """移植元 report_ui の配信面（接点を足した payload）。verify_parity.py は変えない。"""
    root = tmp_path / "web"
    shutil.copytree(REPORT_WEB, root)
    (root / "data").mkdir(parents=True, exist_ok=True)
    (root / "data" / "report.json").write_text(
        json.dumps(_payload_with_contacts(), ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    return root


def _build_sim_web_root(tmp_path: Path) -> Path:
    """sim 表示層の配信面（接点を足した同一 payload・製品の子文書 report_view.html を開く）。"""
    root = tmp_path / "simweb"
    sim = root / "sim"
    (sim / "js" / "adapter").mkdir(parents=True)
    shutil.copytree(REPORT_WEB / "js", sim / "report-js")
    shutil.copytree(REPORT_WEB / "css", sim / "report-css")
    shutil.copytree(SIM_WEB / "js" / "adapter" / "front", sim / "js" / "adapter" / "front")
    shutil.copytree(SIM_WEB / "css", sim / "css")
    shutil.copy(SIM_WEB / "report_view.html", sim / "report_view.html")
    (sim / "vendor").mkdir()
    shutil.copy(SHARED_VENDOR, sim / "vendor" / "lightweight-charts.js")
    # Chart.js（比較グラフ用・移植元 vendor 無改変）を sim の report-vendor へ 1 ファイルだけ置く。
    (sim / "report-vendor").mkdir()
    shutil.copy(REPORT_WEB / "vendor" / "chart.umd.js", sim / "report-vendor" / "chart.umd.js")
    data_dir = sim / "data" / JOB_ID
    data_dir.mkdir(parents=True)
    (data_dir / "report.json").write_text(
        json.dumps(_payload_with_contacts(), ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    return root


# --- 観測式（両画面へ同じ問いを投げる）----------------------------------------

_TABS = """() => [...document.querySelectorAll('.mv-tab')].map((t) => t.dataset.tab)"""

#: タブ帯の**宣言そのもの**（`SIM_TAB_NAMES`）を配信中の実モジュールから読む。
#:
#: 期待値をテストへ書き写さない（工程 5 レビュー 🔴-1）。段階 4 以前は
#: 共通タブ名の集合リテラルを手書きしており、タブを 1 枚足したとき
#: 「実装は正しいのに検定が赤」になった——しかも本ファイルは `verify_*.py` 命名で
#: **既定の pytest 収集に載らない**（ISSUE-378 #2）ため、その赤は自動では見えなかった。
#: ソーステキストの解析でもなく、**実際に配信されている ES モジュールの export** を読む。
_SIM_TAB_DECLARATION = """async () => {
  const m = await import('/sim/js/adapter/front/sim_tabs_view.js');
  return [...m.SIM_TAB_NAMES];
}"""

#: 分析タブの名前の宣言（`sim_trace_view.js` が所有）。ここでも綴りを書き写さない。
_ANALYSIS_TAB_NAME = """async () => {
  const m = await import('/sim/js/adapter/front/sim_trace_view.js');
  return m.SIM_TRACE_TAB_NAME;
}"""

#: 分析ペインに面が**実際に生えているか**（合成根が mount を呼んだかの観測）。
#:
#: 静的検査では到達可能性を証明できない（`if (false)` で囲む変異が全ゲートを通ることを
#: 工程 5 が実測した）。実ブラウザなら識別できる——`mountTraceAnalysis` は await の前に
#: 同期で器を挿すため、分析 API が 404（本 e2e の配信面は静的のみ）でも面は生える。
#: したがってこの観測は**呼ばれたか**だけを見ており、API の到達性には依存しない。
_ANALYSIS_PANE = """(name) => {
  const pane = document.querySelector('.mv-pane[data-pane="' + name + '"]');
  if (!pane) return { pane: false, children: 0, hasRoot: false };
  return {
    pane: true,
    children: pane.children.length,
    hasRoot: !!pane.querySelector('.trace-analysis'),
  };
}"""

#: 移植元から**流用しない**タブ（YAGNI・doc §流用）。宣言はこの 1 箇所だけが持つ。
_NOT_PORTED = {"graph", "report"}

_HEAT_VIEWS = """() => ({
  views: document.querySelectorAll('#heatHost .heatBlock').length,
  cells: document.querySelectorAll('#heatHost td.cell').length,
  hasIsOos: [...document.querySelectorAll('#heatHost .heatTitle')]
    .some((t) => t.textContent.includes('IS vs OOS')),
})"""

# 比較グラフは compare.js の module-level `cmpCharts` を **その画面が読んでいる実体**から
#   引く（cmpChartInstances・URL は同一ファイル）。report_ui は main.js が window.__cmpCharts へ
#   写すが、sim は写さない（main.js を使わない）ので、両画面で共通に読める module 実体を使う。
_CMP_CHARTS = """async (compareUrl) => {
  const m = await import(compareUrl);
  const c = m.cmpChartInstances ? m.cmpChartInstances() : {};
  const shape = (k) => (c[k] && c[k].data)
    ? { labels: c[k].data.labels.length, ds: c[k].data.datasets.length } : null;
  return { keys: Object.keys(c).sort(),
           eq: shape('eq'), pnl: shape('pnl'), dd: shape('dd') };
}"""

_VERDICT = """() => { const v = document.querySelector('#cmpVerdict');
  return v ? v.textContent.replace(/\\s+/g, ' ').trim() : null; }"""

_CMP_BASIC = """() => document.querySelectorAll('#cmpBasic .bcard').length"""

_CMP_TABLE = """() => document.querySelectorAll('#cmpTable tbody tr').length"""

_SEG = """() => {
  const s = document.querySelector('#segSel');
  return { present: !!s,
    btns: s ? [...s.querySelectorAll('.segbtn')].map((b) => b.dataset.seg) : [] };
}"""

_GLOSS = """() => ({
  cards: document.querySelectorAll('#glossHost .gcard').length,
  items: document.querySelectorAll('#glossHost .gitem').length,
})"""

# 接点変換を**その画面が読んでいる実体**から呼ぶ（URL だけ違う同一ファイル）。
_CONTACT_RULES = """async (chartUrl) => {
  const m = await import(chartUrl);
  const contacts = [
    {time: 100, price: 105, dir: 'up'},
    {time: 200, price: 105, dir: 'down'},
    {time: 150, price: 105, dir: 'up'},
  ];
  const trades = [
    {id: 1, side: 'buy', profit: 50, entry_time: 100, exit_time: 300, entry_price: 10, exit_price: 12},
  ];
  return {
    CONTACT_UP_COLOR: m.CONTACT_UP_COLOR,
    CONTACT_DOWN_COLOR: m.CONTACT_DOWN_COLOR,
    CONTACT_MARKER_CAP: m.CONTACT_MARKER_CAP,
    markersVisible: m.contactsToMarkers(contacts, {visible: true}),
    markersHidden: m.contactsToMarkers(contacts, {visible: false}),
    inRange: m.contactsInRange(contacts, {from: 120, to: 260}).map((c) => c.time),
    // P5: 接点トグルは売買マーカー（buildTradeMarkers）に触れない＝接点入力に依らず不変。
    tradeMarkers: m.buildTradeMarkers(trades, null),
  };
}"""


def _ref_page(tmp_path: Path):
    root = _build_ref_web_root(tmp_path / "ref")
    port = _harness.free_port()
    httpd = _harness.serve(str(root), port)
    from playwright.sync_api import sync_playwright
    p = sync_playwright().start()
    browser = p.chromium.launch()
    page = browser.new_page(viewport={"width": 1600, "height": 1000})
    errors: list = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(f"http://127.0.0.1:{port}/index.html")
    page.wait_for_function("window.__READY === true", timeout=20000)
    return p, browser, page, httpd, errors


def _sim_page(browser, tmp_path: Path):
    root = _build_sim_web_root(tmp_path / "sim")
    port = _harness.free_port()
    httpd = _harness.serve(str(root), port)
    page = browser.new_page(viewport={"width": 1600, "height": 1000})
    errors: list = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(f"http://127.0.0.1:{port}/sim/report_view.html?job={JOB_ID}")
    page.wait_for_function("window.__simReportViewReady === true", timeout=20000)
    page.wait_for_function(
        "() => document.querySelectorAll('#tradeTable tbody tr').length > 0", timeout=20000
    )
    return page, httpd, errors


def test_sim_display_parity_peripheral(tmp_path: Path) -> None:
    """周辺点を 1 セッションで突き合わせる。採用点の一致率 100% を実証する。"""
    p, browser, ref, ref_httpd, ref_errors = _ref_page(tmp_path)
    sim = sim_httpd = None
    sim_errors: list = []
    passed: list = []
    try:
        sim, sim_httpd, sim_errors = _sim_page(browser, tmp_path)

        # P1: タブ帯が**宣言と過不足なく一致**し、移植元から流用すべきタブを取り落として
        #     いないこと。期待値は宣言（`SIM_TAB_NAMES`）と移植元の実タブから**導出**する
        #     ——リテラルの集合を書かない（工程 5 レビュー 🔴-1 の根本是正）。
        ref_tabs = ref.evaluate(_TABS)
        sim_tabs = sim.evaluate(_TABS)
        declared = set(sim.evaluate(_SIM_TAB_DECLARATION))
        assert declared, "SIM_TAB_NAMES を読めていない（宣言が空なら以下が恒真になる）"
        # 宣言 ⇔ 生成物（過不足なし）。
        assert set(sim_tabs) == declared, f"P1 sim タブ: {sim_tabs} vs 宣言 {declared}"
        # 移植元から流用すべきタブ（流用しない 2 つを除いた全部）を落としていない。
        inherited = set(ref_tabs) - _NOT_PORTED
        assert inherited, f"P1 reference タブ: {ref_tabs}"
        assert inherited <= set(sim_tabs), f"P1 流用漏れ: {inherited - set(sim_tabs)}"
        # 流用しない 2 つは出さない（P1 の元の意図）。
        assert _NOT_PORTED & set(sim_tabs) == set(), f"P1 sim に graph/report: {sim_tabs}"
        passed.append("P1")

        # P19: 分析タブの面が**実際に生えている**（合成根が mountTraceAnalysis を呼んで
        #      いる）。ISSUE-291 型（受け口はあるのに呼ばれない）を捕らえる唯一の検査で
        #      ある——静的検査では到達可能性を証明できない（工程 5 が `if (false)` で囲む
        #      変異が全ゲートを通ることを実測）。
        analysis_tab = sim.evaluate(_ANALYSIS_TAB_NAME)
        assert analysis_tab, "SIM_TRACE_TAB_NAME を読めていない"
        assert analysis_tab in declared, f"P19 宣言に {analysis_tab} が無い: {declared}"
        analysis = sim.evaluate(_ANALYSIS_PANE, analysis_tab)
        assert analysis["pane"] is True, f"P19 分析ペインが無い: {analysis}"
        assert analysis["hasRoot"] is True, (
            f"P19 分析ペインに面が生えていない＝合成根が mountTraceAnalysis を"
            f"呼んでいない（口はあるが呼ばれない・ISSUE-291 型）: {analysis}"
        )
        assert analysis["children"] >= 1, f"P19 分析ペインが空: {analysis}"
        # **陰性対照**（工程 5 再レビュー 🟡-A）: 面が生えているのは分析ペインだけである。
        #   これが無いと、観測式を定数へ書き換えるだけで production が壊れていても緑になる
        #   （実測: `hasRoot` と children を両方定数化すると `if (false)` でも 2 passed）。
        #   children は識別力が薄い（全ペインが 1 以上）ため、実質の観測は `hasRoot` 1 本で
        #   あり、その 1 本が定数化されていないことを対照で固定する。
        #   実測: analysis のみ True、detail / heat / compare / glossary はすべて False。
        others = declared - {analysis_tab}
        assert others, f"P19 陰性対照の対象が無い: {declared}"
        for other in sorted(others):
            probe = sim.evaluate(_ANALYSIS_PANE, other)
            assert probe["pane"] is True, f"P19 ペインが無い: {other} {probe}"
            assert probe["hasRoot"] is False, (
                f"P19 陰性対照が破れた——{other} にも面が生えている。観測式が定数化された"
                f"か、面が誤ったペインへ挿されている: {probe}"
            )
        # 移植元にはこのタブが無い（sim 固有の増分であることの対照）。
        assert analysis_tab not in set(ref_tabs), f"P19 reference に {analysis_tab}"
        passed.append("P19")

        # 17: サマリーカード（report タブ・#summaryCard）は sim に無い。
        assert sim.query_selector('[data-tab="report"]') is None, "17 sim に report タブ"
        assert sim.query_selector("#summaryCard") is None, "17 sim に #summaryCard"
        assert ref.query_selector('[data-tab="report"]') is not None, "17 reference に report タブが無い"
        passed.append("17")

        # 15: 区間トグル（2 区間 → #segSel・segbtn 2 個・is/oos）。
        ref_seg = ref.evaluate(_SEG)
        sim_seg = sim.evaluate(_SEG)
        assert sim_seg["present"], f"15 sim #segSel 不在: {sim_seg}"
        assert sim_seg["btns"] == ["is", "oos"], f"15 sim segbtn: {sim_seg['btns']}"
        assert ref_seg["btns"] == sim_seg["btns"], f"15 区間トグル: {ref_seg} vs {sim_seg}"
        passed.append("15")

        # P2: ヒートマップ 5 ビュー全セル（2 区間 → IS/OOS 損益差ビューも出る）。
        ref.click('.mv-tab[data-tab="heat"]')
        sim.click('.mv-tab[data-tab="heat"]')
        ref.wait_for_timeout(200)
        sim.wait_for_timeout(200)
        ref_heat = ref.evaluate(_HEAT_VIEWS)
        sim_heat = sim.evaluate(_HEAT_VIEWS)
        assert sim_heat["views"] == 5, f"P2 sim ビュー数: {sim_heat}"
        assert sim_heat["hasIsOos"] is True, f"P2 sim に IS/OOS 損益差ビューが無い: {sim_heat}"
        assert ref_heat == sim_heat, f"P2 ヒートマップ: {ref_heat} vs {sim_heat}"
        passed.append("P2")

        # P3: セルクリック → 抽出連動（activeFilter・#tradeTable dim・#chartBadge）。
        ref.eval_on_selector("#heatHost td.cell", "el => el.click()")
        sim.eval_on_selector("#heatHost td.cell", "el => el.click()")
        ref.wait_for_timeout(250)
        sim.wait_for_timeout(250)
        _linked = """() => ({
          badge: document.querySelector('#chartBadge').textContent,
          dim: document.querySelectorAll('#tradeTable tbody tr.dim').length,
          rows: document.querySelectorAll('#tradeTable tbody tr').length,
        })"""
        ref_link = ref.evaluate(_linked)
        sim_link = sim.evaluate(_linked)
        assert ref_link == sim_link, f"P3 セルクリック連動: {ref_link} vs {sim_link}"
        passed.append("P3")
        # 連動解除
        ref.evaluate("() => window.__linkage && window.__linkage.applyFilter(null,'')")
        sim.evaluate("() => window.__simLinkage.applyFilter(null,'')")

        # P4 / P5 / U-1: 接点変換規則を両画面の実体から呼んで突き合わせる。
        ref_c = ref.evaluate(_CONTACT_RULES, "/js/chart.js")
        sim_c = sim.evaluate(_CONTACT_RULES, "/sim/report-js/chart.js")
        assert ref_c["CONTACT_MARKER_CAP"] == 700, f"P4 CAP: {ref_c['CONTACT_MARKER_CAP']}"
        ids = [m["id"] for m in ref_c["markersVisible"]]
        assert ids == ["c0", "c1", "c2"], f"P4 接点 id: {ids}"
        # U-1: グリフ y アンカーは position で決まる（time 昇順 sort 後）。
        positions = [(m["position"], m["shape"]) for m in ref_c["markersVisible"]]
        assert positions == [("belowBar", "arrowUp"), ("belowBar", "arrowUp"), ("aboveBar", "arrowDown")], (
            f"U-1 接点 position/shape: {positions}"
        )
        assert ref_c["markersHidden"] == [], "P5 接点 OFF で [] にならない"
        assert ref_c == sim_c, "P4/P5/U-1 接点規則が両画面で一致しない"
        passed.extend(["P4", "P5", "U-1"])

        # 比較・判定タブ（P6/P7/P8/12/13/14）。
        ref.click('.mv-tab[data-tab="compare"]')
        sim.click('.mv-tab[data-tab="compare"]')
        ref.wait_for_timeout(300)
        sim.wait_for_timeout(300)

        # P6: 判定バナー（#cmpVerdict の文言）。
        ref_v = ref.evaluate(_VERDICT)
        sim_v = sim.evaluate(_VERDICT)
        assert sim_v and "優位性消失" in sim_v, f"P6 sim 判定文言: {sim_v!r}"
        assert ref_v == sim_v, f"P6 判定バナー: {ref_v!r} vs {sim_v!r}"
        passed.append("P6")

        # P7: 7 指標カード（#cmpBasic）。
        ref_basic = ref.evaluate(_CMP_BASIC)
        sim_basic = sim.evaluate(_CMP_BASIC)
        assert sim_basic == ref_basic and sim_basic > 0, f"P7 指標カード: {ref_basic} vs {sim_basic}"
        passed.append("P7")

        # P8: 劣化比較表（#cmpTable 行）。
        ref_tbl = ref.evaluate(_CMP_TABLE)
        sim_tbl = sim.evaluate(_CMP_TABLE)
        assert sim_tbl == ref_tbl and sim_tbl > 0, f"P8 劣化比較表 行数: {ref_tbl} vs {sim_tbl}"
        passed.append("P8")

        # 12/13/14: 比較グラフ 3 種（window.__cmpCharts のデータ形状突合）。
        ref_cc = ref.evaluate(_CMP_CHARTS, "/js/compare.js")
        sim_cc = sim.evaluate(_CMP_CHARTS, "/sim/report-js/compare.js")
        assert sim_cc["eq"] and sim_cc["pnl"] and sim_cc["dd"], f"12/13/14 sim cmpCharts: {sim_cc}"
        assert ref_cc == sim_cc, f"12/13/14 比較グラフ: {ref_cc} vs {sim_cc}"
        passed.extend(["12", "13", "14"])

        # 9: 用語集 + tip。
        ref.click('.mv-tab[data-tab="glossary"]')
        sim.click('.mv-tab[data-tab="glossary"]')
        ref.wait_for_timeout(200)
        sim.wait_for_timeout(200)
        ref_g = ref.evaluate(_GLOSS)
        sim_g = sim.evaluate(_GLOSS)
        assert sim_g["cards"] > 0 and sim_g["items"] > 0, f"9 sim 用語集: {sim_g}"
        assert ref_g == sim_g, f"9 用語集: {ref_g} vs {sim_g}"
        # tip: data-gg 上の mousemove で #tip 発火（heat タイトルで測る・子文書内）。
        for pg in (ref, sim):
            pg.click('.mv-tab[data-tab="heat"]')
            pg.wait_for_timeout(150)
            gg = pg.query_selector("#heatHost .heatTitle[data-gg]")
            box = gg.bounding_box()
            pg.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
            pg.wait_for_timeout(150)
        ref_tip = ref.evaluate("() => { const t=document.querySelector('#tip'); return t && getComputedStyle(t).display==='block' && t.textContent.trim().length>0; }")
        sim_tip = sim.evaluate("() => { const t=document.querySelector('#tip'); return t && getComputedStyle(t).display==='block' && t.textContent.trim().length>0; }")
        assert ref_tip is True and sim_tip is True, f"9 tip 発火: ref={ref_tip} sim={sim_tip}"
        passed.append("9")

        # 18: 抽出ピル（applyFilter で #clearFilter 可視・#detailCount 件数）。
        ref.click('.mv-tab[data-tab="detail"]')
        sim.click('.mv-tab[data-tab="detail"]')
        ref.wait_for_timeout(150)
        sim.wait_for_timeout(150)
        ref.evaluate("() => window.__linkage.applyFilter(new Set([1]), 'テスト')")
        sim.evaluate("() => window.__simLinkage.applyFilter(new Set([1]), 'テスト')")
        ref.wait_for_timeout(200)
        sim.wait_for_timeout(200)
        _pill = """() => ({
          clearVisible: getComputedStyle(document.querySelector('#clearFilter')).display !== 'none',
          count: document.querySelector('#detailCount') ? document.querySelector('#detailCount').textContent.trim() : null,
        })"""
        ref_pill = ref.evaluate(_pill)
        sim_pill = sim.evaluate(_pill)
        assert sim_pill["clearVisible"] is True, f"18 sim ピル非可視: {sim_pill}"
        assert ref_pill == sim_pill, f"18 抽出ピル: {ref_pill} vs {sim_pill}"
        passed.append("18")

        # 全体: sim 画面で JS エラーが出ていない（Chart.js + v5 lwc 同居で 0）。
        assert sim_errors == [], f"sim 画面の JS エラー: {sim_errors}"

        print("PARITY_PERIPHERAL_PASSED=" + ",".join(passed))
        print("PARITY_PERIPHERAL_COUNT=" + str(len(passed)))
    finally:
        if sim is not None:
            sim.close()
        if sim_httpd is not None:
            sim_httpd.shutdown()
        ref.close()
        browser.close()
        ref_httpd.shutdown()
        p.stop()
