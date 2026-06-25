// エントリ（試作 index.html:266-1198 init/selectSegment 準拠・パリティ完全準拠）。
// report.json を fetch → DATA → 区間トグルで各パネル再描画。比較/用語は区間非依存（init 1回）。

import { fmtMoney } from "./format.js";
import {
  renderChart, renderMarkers, onMarkerHover, currentRows, emitMarkerHover,
  dimCandlesForTrade, restoreCandles, focusTime, resizeChart,
} from "./chart.js";
import { createLinkage } from "./linkage.js";
import { buildTradeTable } from "./table.js";
import { buildHeatmap } from "./heatmap.js";
import { buildGraphs, activeCharts } from "./graphs.js";
import { buildCompare, resizeCompareCharts, cmpChartInstances } from "./compare.js";
import { buildReport } from "./report.js";
import { buildGlossary, wireTips } from "./glossary.js";
import { wireResizers, wireMaximize } from "./layout.js";

let DATA = null;
let CUR_SEG = "is"; // 現在表示中の区間
const linkage = createLinkage();

function renderMeta(seg) {
  const m = DATA.segments[seg].meta;
  document.getElementById("meta-line").textContent =
    `${m.symbol} ${m.timeframe} / ${m.strategy} / bars=${m.bars} / trades=${m.trades} / ${m.period}`;
}

function renderVerdict() {
  const v = DATA.verdict || { result: "", reasons: [] };
  const badge = document.getElementById("verdict-badge");
  badge.textContent = v.result ? v.result.toUpperCase() : "";
  badge.className = "badge " + (v.result || "");
  badge.title = (v.reasons || []).join(" / ");
}

function renderTable(seg) {
  const host = document.getElementById("tradeTable");
  if (host) buildTradeTable(host, DATA.segments[seg], linkage);
}

function renderHeatmap(seg) {
  const host = document.getElementById("heatHost");
  if (host) buildHeatmap(host, DATA, seg, linkage, focusTime);
}

function renderGraphs(seg) {
  const host = document.getElementById("graphHost");
  if (!host) return;
  buildGraphs(host, DATA, seg, linkage, focusTime);
  window.__graphsCharts = activeCharts();
}

// 点8: サマリー(Report)タブを区間別 report で再描画する。
function renderReport(seg) {
  const host = document.getElementById("reportGrid");
  if (host) buildReport(host, DATA.segments[seg].report);
}

// 区間切替（試作 selectSegment）。多窓チャート destroy→再生成・各パネル再構築。
function selectSegment(seg) {
  CUR_SEG = seg;
  linkage.applyFilter(null, ""); // 区間切替でフィルタ解除
  document.querySelectorAll(".segbtn").forEach((b) => b.classList.toggle("on", b.dataset.seg === seg));
  renderMeta(seg);
  renderChart("price-chart", DATA.segments[seg], { initialDeposit: DATA.meta && DATA.meta.initial_deposit });
  renderTable(seg);
  renderHeatmap(seg);
  renderGraphs(seg);
  renderReport(seg);
  setTimeout(() => resizeChart(), 30);
}

// 点15: 区間トグルボタン（select 廃止）。クリックで selectSegment。
function buildSegToggle() {
  document.querySelectorAll("#segSel .segbtn").forEach((b) => {
    b.onclick = () => { if (b.dataset.seg !== CUR_SEG) selectSegment(b.dataset.seg); };
  });
}

// 点16: 連動選択ラベル hSel を hover 中 trade で更新する（試作 setHover の hSel 部）。
function updateHSel(id) {
  const el = document.getElementById("hSel");
  if (!el) return;
  if (id == null) { el.textContent = ""; restoreCandles(); return; }
  const rows = currentRows();
  const t = rows.find((r) => r.id === id);
  if (!t) { el.textContent = ""; restoreCandles(); return; }
  el.innerHTML =
    `▶ #${t.id} ${String(t.side).toUpperCase()} @${t.entry_price} → ${t.exit_price} ` +
    `<b style="color:${t.profit > 0 ? "#26a69a" : "#ef5350"}">${fmtMoney(t.profit)} JPY</b> · MFE ${t.mfe} / MAE ${t.mae}`;
  dimCandlesForTrade(t); // ペア区間外のローソク足を減光
}

// マルチビュータブ切替（6 タブ）。表示ペインのみ可視化し、各 Chart を resize する。
function wireTabs() {
  const tabs = document.querySelectorAll(".mv-tab");
  tabs.forEach((tab) => {
    tab.addEventListener("click", () => {
      const name = tab.dataset.tab;
      tabs.forEach((t) => t.classList.toggle("active", t === tab));
      document.querySelectorAll(".mv-pane").forEach((pane) => {
        pane.classList.toggle("hidden", pane.dataset.pane !== name);
      });
      if (name === "compare") resizeCompareCharts();
      setTimeout(() => { resizeChart(); resizeCompareCharts(); }, 30);
    });
  });
}

function showError(msg) {
  const el = document.getElementById("error-banner");
  el.textContent = msg;
  el.classList.remove("hidden");
}

async function boot() {
  try {
    const res = await fetch("data/report.json?v=" + Date.now(), { cache: "no-store" });
    if (!res.ok) throw new Error(`report.json fetch failed: ${res.status}`);
    DATA = await res.json();

    renderVerdict();

    // 双方向ハイライト結線（chart→linkage はコールバック注入）。
    onMarkerHover((id) => linkage.setHover(id, "chart"));
    // hover 購読: マーカー強調＋点16 hSel ラベル＋ローソク減光（DOM 副作用は購読者側）。
    linkage.subscribe((id) => {
      renderMarkers(currentRows(), { hoverId: id, filter: linkage.activeFilter });
      updateHSel(id);
    });

    // 点18: 抽出フィルタ購読（ピル表示＋件数＋ズーム＋ chart/table 連動）。
    linkage.subscribeFilter((filter, label) => {
      renderMarkers(currentRows(), { hoverId: linkage.hoverTradeId, filter });
      renderTable(CUR_SEG); // dim を反映
      const cf = document.getElementById("clearFilter");
      if (cf) cf.style.display = filter ? "inline-block" : "none";
      const dc = document.getElementById("detailCount");
      if (dc) dc.textContent = filter ? ` · 抽出 ${filter.size} 件 (${label || ""})` : "";
    });
    // 点18: ✕ クリックでフィルタ解除。
    const cfBtn = document.getElementById("clearFilter");
    if (cfBtn) cfBtn.onclick = () => linkage.applyFilter(null, "");

    wireTabs();

    // 比較・判定／用語は区間非依存（init で 1 回構築）。
    buildCompare(DATA);
    window.__cmpCharts = cmpChartInstances();
    buildGlossary(document.getElementById("glossHost")); // 点9
    wireTips(); // 点9 hover tip

    // レイアウト（点5/6/10/11）: リサイザ・最大化を結線（resize は chart/cmp 両方）。
    const onResize = () => { resizeChart(); resizeCompareCharts(); };
    wireResizers(onResize);
    wireMaximize(onResize);

    buildSegToggle();      // 点15
    selectSegment("is");   // マルチビュー各パネルを IS で初期描画

    // E2E フック（双方向結線・各 Chart の検証用）。
    window.__linkage = linkage;
    window.__chartEmitMarkerHover = (id) => emitMarkerHover(id);

    window.__READY = true;
  } catch (e) {
    showError("レポート読込エラー: " + e.message);
    window.__READY = true;
  }
}

boot();
