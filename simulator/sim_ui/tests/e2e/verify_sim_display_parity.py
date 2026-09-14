"""パリティ 12 点の二画面突合（Playwright・Phase 4 F-11）。

**同一 payload を移植元 report_ui の画面と sim 表示層の画面へ与え、同じ観測点で
同じ値が出ることを実証する**。移植元は vendor v4.1.3・sim は v5.2.0 で、series の作り方と
マーカー API が違う（`addSeries` / `createSeriesMarkers`）。違うのは**その 2 つだけ**である、
というのが Phase 4 の主張であり、本検定はその主張を観測値で固定する。

fixture は移植元 `report_ui/tests/e2e/verify_parity.py` の 2 区間ペイロードを import する
（sim 用に別のダミーを作らない＝ずれの余地を残さない）。sim 側の初期区間は
`Object.keys(segments)[0]`＝"is" で、移植元の `selectSegment("is")` と同じ区間になる。

観測点（骨格 12 点）:
    1  Balance 窓          #paneBal に canvas
    2  Drawdown 窓         #paneDD に canvas
    3  3 窓の論理レンジ同期  3 つの独立チャート枠（同期の規則は node:test が被覆）
    4  クロスヘア同期       同上（移植元 verify_parity.py:158-162 と同じ観測の当て方）
    7  chartBadge          "N trades in view"
    16 hSel 連動ラベル      マーカー hover 起動後のラベル文字列
    S1 取引履歴 12 列       th[data-k] の順序・キー・ラベル
    S2 マーカー hover→行 .hl  chart→table 方向
    S3 行 hover→マーカー強調   table→chart 方向（size=1.4 / text="#id" / 他 α=DIM_ALPHA）
    S4 区間外ローソク減光     window.__candlesDimmed ＋ 区間 [entry_time, exit_time]
    S5 マーカー id           "e"+id / "x"+id
    S6 MARKER_CAP            700

点 3 / 4 は移植元 E2E と同じく**枠の存在**で当てる（同期そのものは DOM に出ないため）。
点 S3 / S5 / S6 は両画面で表示規則モジュールを実際に import して呼び、戻り値を突き合わせる
（両者が同一実体を読んでいることの直接証拠）。

chromium / playwright 不在環境では skip（移植元 verify.py の規約準拠）。
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[4]
# 移植元 e2e は `from e2e import _harness` を使う。その解決点を明示的に載せる
# （pytest の rootdir 挿入に依存すると、単体起動と pytest 起動で挙動が変わる）。
_REPORT_TESTS = _REPO / "simulator" / "report_ui" / "tests"
if str(_REPORT_TESTS) not in sys.path:
    sys.path.insert(0, str(_REPORT_TESTS))

from e2e import _harness  # noqa: E402
from e2e import verify_parity  # noqa: E402  (2 区間 fixture の唯一源)

pytestmark = pytest.mark.e2e

REPORT_WEB = _REPO / "simulator" / "report_ui" / "web"
SIM_WEB = _REPO / "simulator" / "sim_ui" / "web"
SHARED_VENDOR = _REPO / "indigators" / "indicator_ui" / "web" / "vendor" / "lightweight-charts.js"

JOB_ID = "parityjob"


def _build_sim_web_root(tmp_path: Path) -> Path:
    """sim 表示層の配信面（/sim/report-js・/sim/report-css・/sim/js・/sim/vendor・/sim/data）を再現する。

    実運用の経路は `composition_root_display.build_sim_display_app` の prefix ルートだが、
    ここでは静的ハーネスで同じ**URL 構造**を用意する（front の絶対パス import は URL に
    しか依存しない）。配信面そのものの検定は `tests/integration/test_serve_sim_display.py`。

    開くのは**製品そのもの**の子文書 `/sim/report_view.html` である（裁定 B）。検定用の
    ページを別に書かない——書けば「fixture では動くが製品では動かない」を作れてしまう。
    """
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
    data_dir = sim / "data" / JOB_ID
    data_dir.mkdir(parents=True)
    (data_dir / "report.json").write_text(
        json.dumps(verify_parity._payload(), ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    return root


# --- 観測（両画面で同じ問いを投げる）-------------------------------------------

_PANES = """() => ({
  price: document.querySelectorAll('#price-chart canvas').length > 0,
  bal: document.querySelectorAll('#paneBal canvas').length > 0,
  dd: document.querySelectorAll('#paneDD canvas').length > 0,
  frames: ['#price-chart', '#paneBal', '#paneDD'].filter((s) => document.querySelector(s)).length,
})"""

# 窓が「在る」だけでは足りない（実測 2026-08-12: 器の高さが決まらず 3 窓が 2px に潰れても
# canvas は存在し、件数だけ見る検定は素通りした。その状態ではマーカーを hover できない）。
_PANE_HEIGHTS = """() => {
  const h = (s) => { const e = document.querySelector(s);
    return e ? Math.round(e.getBoundingClientRect().height) : 0; };
  return {price: h('#price-chart'), bal: h('#paneBal'), dd: h('#paneDD')};
}"""

_COLUMNS = """() => ({
  keys: [...document.querySelectorAll('#tradeTable thead th')].map((t) => t.dataset.k),
  labels: [...document.querySelectorAll('#tradeTable thead th')].map((t) => t.textContent),
  rows: document.querySelectorAll('#tradeTable tbody tr').length,
})"""

_HL_ROWS = """() => [...document.querySelectorAll('#tradeTable tbody tr.hl')].map((r) => +r.dataset.id)"""

# ヘッダは移植元 style.css:26-35 の `#topbar` / `#topbar h1` を**実体のまま**使う。
# id を別名にすると id セレクタが 1 つも当たらず、見た目だけが静かにずれる
# （実測差分: display block／h1 24px／hSel が 2 行目へ落ちる／ヘッダ高 66px）。
_HEADER_STYLE = """() => {
  const h = document.querySelector('#topbar');
  if (!h) return null;
  const cs = getComputedStyle(h);
  const h1 = h.querySelector('h1');
  const h1cs = h1 ? getComputedStyle(h1) : null;
  return {
    display: cs.display, padding: cs.padding, alignItems: cs.alignItems, gap: cs.gap,
    height: Math.round(h.getBoundingClientRect().height),
    h1FontSize: h1cs ? h1cs.fontSize : null,
    h1Margin: h1cs ? h1cs.margin : null,
    // hSel が h1 と**同じ行に居るか**（2 行目へ落ちていないか）を、両者の縦位置の
    //   重なりで判定する。行位置の絶対値はヘッダの高さ（＝載っている部品の数）で変わる
    //   ので、画面間で等値にはならない。
    sameRow: (() => {
      const s = document.querySelector('#hSel');
      if (!s || !h1) return null;
      const a = s.getBoundingClientRect(), b = h1.getBoundingClientRect();
      return a.top < b.bottom && b.top < a.bottom;
    })(),
  };
}"""

# 表示規則を**その画面が読んでいる実体**から呼ぶ。URL だけが違う（同一ファイル）。
_RULES = """async (chartUrl) => {
  const m = await import(chartUrl);
  const trades = [
    {id: 1, side: 'buy', profit: 50, entry_time: 100, exit_time: 300, entry_price: 10, exit_price: 12},
    {id: 2, side: 'sell', profit: -20, entry_time: 200, exit_time: 400, entry_price: 12, exit_price: 13},
  ];
  const bars = [100, 200, 300, 400, 500].map((t) => ({time: t, open: 1, high: 2, low: 0, close: 2}));
  const barTimes = bars.map((b) => b.time);
  const normal = bars.map((b) => ({...b, tag: 'n'}));
  const dim = m.buildDimBars(bars);
  const merged = m.mergeDimBarsForTrade(barTimes, normal, dim, trades[0]);
  return {
    MARKER_CAP: m.MARKER_CAP,
    DIM_ALPHA: m.DIM_ALPHA,
    markersPlain: m.buildTradeMarkers(trades, null),
    markersHovered: m.buildTradeMarkers(trades, 1),
    dimColors: dim.map((b) => b.color),
    mergedTags: merged.map((b) => b.tag || 'dim'),
    badge: [m.chartBadgeText(3), m.chartBadgeText(m.MARKER_CAP + 1)],
  };
}"""


def _report_ui_page(tmp_path: Path):
    """移植元 report_ui の画面（vendor v4.1.3）を立てる。"""
    p, browser, page, httpd = _harness.launch(verify_parity._build_web_root, tmp_path / "ref")
    return p, browser, page, httpd


def _sim_page(playwright, browser, tmp_path: Path):
    """sim 表示層の画面（vendor v5.2.0・製品の子文書そのもの）を同一 payload で立てる。"""
    root = _build_sim_web_root(tmp_path / "sim")
    port = _harness.free_port()
    httpd = _harness.serve(str(root), port)
    page = browser.new_page(viewport={"width": 1600, "height": 1000})
    errors: list = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(f"http://127.0.0.1:{port}/sim/report_view.html?job={JOB_ID}")
    page.wait_for_function("window.__simReportViewReady === true", timeout=15000)
    page.wait_for_function(
        "() => document.querySelectorAll('#tradeTable tbody tr').length > 0", timeout=15000
    )
    return page, httpd, errors


def test_sim_display_parity_12_points(tmp_path: Path) -> None:
    """12 点を 1 セッションで突き合わせる（点番号 → 同値 assertion）。"""
    p, browser, ref, ref_httpd = _report_ui_page(tmp_path)
    sim = sim_httpd = None
    sim_errors: list = []
    try:
        ref.set_viewport_size({"width": 1600, "height": 1000})
        sim, sim_httpd, sim_errors = _sim_page(p, browser, tmp_path)

        # 点1 / 点2 / 点3 / 点4: 3 窓の枠と canvas。
        ref_panes = ref.evaluate(_PANES)
        sim_panes = sim.evaluate(_PANES)
        assert ref_panes["bal"] and sim_panes["bal"], f"点1 Balance 窓: {ref_panes} vs {sim_panes}"
        assert ref_panes["dd"] and sim_panes["dd"], f"点2 Drawdown 窓: {ref_panes} vs {sim_panes}"
        assert ref_panes == sim_panes, f"点3/4 3 窓の枠: {ref_panes} vs {sim_panes}"

        # 点1/2: 窓は**操作できる高さ**を持つ（潰れていれば hover もズームもできない）。
        ref_h = ref.evaluate(_PANE_HEIGHTS)
        sim_h = sim.evaluate(_PANE_HEIGHTS)
        for pane in ("price", "bal", "dd"):
            assert ref_h[pane] > 20, f"点1/2 移植元の {pane} 窓が潰れています: {ref_h}"
            assert sim_h[pane] > 20, f"点1/2 sim の {pane} 窓が潰れています: {sim_h}"

        # ヘッダ: 移植元の #topbar 規則が両画面に同じく当たっている（見た目のパリティ）。
        ref_head = ref.evaluate(_HEADER_STYLE)
        sim_head = sim.evaluate(_HEADER_STYLE)
        assert ref_head is not None and sim_head is not None, "ヘッダ #topbar が無い"
        assert ref_head["display"] == "flex", f"移植元のヘッダ規則が当たっていない: {ref_head}"
        for key in ("display", "padding", "alignItems", "gap", "h1FontSize", "h1Margin"):
            assert ref_head[key] == sim_head[key], (
                f"ヘッダ {key}: {ref_head[key]!r} vs {sim_head[key]!r}（#topbar 規則の当たり方が違う）"
            )
        assert ref_head["sameRow"] is True, "移植元で hSel が h1 と同じ行に無い"
        assert sim_head["sameRow"] is True, "sim で hSel が 2 行目へ落ちている（#topbar の flex 未適用）"
        # 高さは**等値にしない**: 移植元のヘッダは区間トグル・判定バッジ・meta-line を持ち、
        #   sim は持たない（Phase 5 の範囲・YAGNI）。中身が違えば高さは違って当然である。
        #   規則が当たっていれば「部品の少ない sim が移植元より高くなることはない」。
        #   id を取り違えていた実測では sim 66px > 移植元 38px だった（本 assertion で捕まる）。
        assert sim_head["height"] <= ref_head["height"], (
            f"ヘッダ高: sim {sim_head['height']} > 移植元 {ref_head['height']}"
            "（#topbar 規則が当たっていない疑い）"
        )

        # 点S1: 取引履歴 12 列（順序・キー・ラベル）＋ 行数。
        ref_cols = ref.evaluate(_COLUMNS)
        sim_cols = sim.evaluate(_COLUMNS)
        assert len(ref_cols["keys"]) == 12, f"点S1 列数: {ref_cols['keys']}"
        assert ref_cols == sim_cols, f"点S1 明細 12 列: {ref_cols} vs {sim_cols}"

        # 点7: chartBadge の可視件数 readout。
        ref_badge = ref.inner_text("#chartBadge")
        sim_badge = sim.inner_text("#chartBadge")
        assert "trades in view" in ref_badge, f"点7 badge 文言: {ref_badge!r}"
        assert ref_badge == sim_badge, f"点7 chartBadge: {ref_badge!r} vs {sim_badge!r}"

        # 点S2: マーカー hover（chart→table）→ 該当行 .hl。
        #   グリフ画素の hover は移植元 E2E と同じく hover 起動フックで代理する。
        ref.evaluate("() => window.__chartEmitMarkerHover(1)")
        sim.evaluate("() => window.__simEmitMarkerHover(1)")
        ref.wait_for_timeout(150)
        sim.wait_for_timeout(150)
        ref_hl = ref.evaluate(_HL_ROWS)
        sim_hl = sim.evaluate(_HL_ROWS)
        assert ref_hl == [1], f"点S2 移植元の .hl: {ref_hl}"
        assert ref_hl == sim_hl, f"点S2 マーカー hover→行 .hl: {ref_hl} vs {sim_hl}"

        # 点16: 連動選択ラベル（hover 中の trade を 1 行で示す）。
        ref_hsel = ref.inner_text("#hSel")
        sim_hsel = sim.inner_text("#hSel")
        assert "#1" in ref_hsel, f"点16 移植元 hSel: {ref_hsel!r}"
        assert ref_hsel == sim_hsel, f"点16 hSel ラベル: {ref_hsel!r} vs {sim_hsel!r}"

        # 点S4: 区間外ローソクの減光（hover 中は減光・解除で戻る）。
        assert ref.evaluate("() => window.__candlesDimmed") is True, "点S4 移植元が減光していない"
        assert sim.evaluate("() => window.__candlesDimmed") is True, "点S4 sim が減光していない"
        ref.evaluate("() => window.__chartEmitMarkerHover(null)")
        sim.evaluate("() => window.__simEmitMarkerHover(null)")
        ref.wait_for_timeout(150)
        sim.wait_for_timeout(150)
        assert ref.evaluate("() => window.__candlesDimmed") is False, "点S4 移植元が復帰しない"
        assert sim.evaluate("() => window.__candlesDimmed") is False, "点S4 sim が復帰しない"
        assert ref.evaluate(_HL_ROWS) == sim.evaluate(_HL_ROWS) == [], "点S2 hover 解除で .hl が残る"

        # 点S3: 行 hover（table→chart）→ 該当行 .hl ＋ 選択ラベル ＋ 減光。
        #   移植元は明細をタブの裏に置く（既定は「比較・判定」）ので、実 hover の前にタブを開く。
        #   sim にタブは無い（Phase 5 の範囲・YAGNI）＝開く操作そのものが無い。
        #   タブは**表示の器**であって表示規則ではないため、突合対象は開いた後の観測値である。
        ref.click('.mv-tab[data-tab="detail"]')
        ref.wait_for_timeout(150)
        ref.hover('#tradeTable tbody tr[data-id="2"]')
        sim.hover('#tradeTable tbody tr[data-id="2"]')
        ref.wait_for_timeout(200)
        sim.wait_for_timeout(200)
        assert ref.evaluate(_HL_ROWS) == [2], "点S3 移植元の行 hover が効かない"
        assert ref.evaluate(_HL_ROWS) == sim.evaluate(_HL_ROWS), "点S3 行 hover→強調"
        assert ref.inner_text("#hSel") == sim.inner_text("#hSel"), "点S3 行 hover のラベル"
        assert ref.evaluate("() => window.__candlesDimmed") is True
        assert sim.evaluate("() => window.__candlesDimmed") is True

        # 点S3 / S5 / S6: 表示規則そのものを両画面で呼んで突き合わせる。
        #   （size=1.4・text="#id"・他ペア α=DIM_ALPHA・id "e"+id/"x"+id・cap 700）
        ref_rules = ref.evaluate(_RULES, "/js/chart.js")
        sim_rules = sim.evaluate(_RULES, "/sim/report-js/chart.js")
        assert ref_rules["MARKER_CAP"] == 700, f"点S6 MARKER_CAP: {ref_rules['MARKER_CAP']}"
        assert ref_rules["DIM_ALPHA"] == 0.15, f"点S4 DIM_ALPHA: {ref_rules['DIM_ALPHA']}"
        ids = [m["id"] for m in ref_rules["markersPlain"]]
        assert ids == ["e1", "e2", "x1", "x2"], f"点S5 マーカー id: {ids}"
        hot = [m for m in ref_rules["markersHovered"] if m["id"] in ("e1", "x1")]
        assert all(m["size"] == 1.4 for m in hot), f"点S3 hover サイズ: {hot}"
        assert ref_rules["markersHovered"][0]["text"] == "#1", "点S3 hover ラベル"
        assert ref_rules["mergedTags"] == ["n", "n", "n", "dim", "dim"], (
            f"点S4 区間 [entry,exit]（hi=bisectLeft(exit+1)）: {ref_rules['mergedTags']}"
        )
        assert ref_rules == sim_rules, "点S3/S5/S6 表示規則が両画面で一致しない"

        # 全体: どちらの画面でも JS エラーが出ていない。
        assert sim_errors == [], f"sim 画面の JS エラー: {sim_errors}"
    finally:
        if sim is not None:
            sim.close()
        if sim_httpd is not None:
            sim_httpd.shutdown()
        browser.close()
        ref_httpd.shutdown()
        p.stop()
