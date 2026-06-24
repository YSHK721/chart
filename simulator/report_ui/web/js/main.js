// エントリ（詳細設計 §11.1 main.js・F-1 最小サブセット）。
// report.json を fetch → DATA → 区間切替で各パネル再描画（チャート＋サマリーカード）。

import { fmtMoney, cfmt, signClass } from "./format.js";
import { renderChart, renderMarkers, onMarkerHover, currentRows, emitMarkerHover } from "./chart.js";
import { createLinkage } from "./linkage.js";
import { buildTradeTable } from "./table.js";

let DATA = null;
const linkage = createLinkage();

// サマリーカードに表示する指標（key, label, formatter）。
const SUMMARY_FIELDS = [
  ["net", "純損益", (v) => fmtMoney(v)],
  ["final_balance", "最終残高", (v) => fmtMoney(v)],
  ["trades", "取引数", (v) => String(v)],
  ["win_rate", "勝率%", (v) => cfmt(v, 2)],
  ["profit_factor", "PF", (v) => cfmt(v, 3)],
  ["expectancy", "期待値", (v) => cfmt(v, 2)],
  ["payoff", "ペイオフ", (v) => cfmt(v, 3)],
  ["return_pct", "リターン%", (v) => cfmt(v, 2)],
  ["max_dd_pct", "最大DD%", (v) => cfmt(v, 2)],
];

function renderSummary(seg) {
  const s = DATA.summary[seg];
  const host = document.getElementById("summary-cards");
  host.innerHTML = "";
  for (const [key, label, fmt] of SUMMARY_FIELDS) {
    const v = s[key];
    const card = document.createElement("div");
    card.className = "card";
    const sc = (key === "net" || key === "return_pct") ? signClass(v) : "";
    card.innerHTML = `<div class="k">${label}</div><div class="v ${sc}">${fmt(v)}</div>`;
    host.appendChild(card);
  }
}

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
  if (!host) return;
  buildTradeTable(host, DATA.segments[seg], linkage);
}

function selectSegment(seg) {
  renderMeta(seg);
  renderSummary(seg);
  renderChart("price-chart", DATA.segments[seg]);
  renderTable(seg);
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

    // 双方向ハイライト結線（一方向 import: chart→linkage はコールバック注入）。
    onMarkerHover((id) => linkage.setHover(id, "chart")); // マーカー hover → 行ハイライト
    linkage.subscribe((id) =>
      renderMarkers(currentRows(), { hoverId: id, filter: linkage.activeFilter })); // hover → マーカー強調

    const sel = document.getElementById("seg-select");
    sel.addEventListener("change", () => selectSegment(sel.value));
    selectSegment(sel.value || "is");

    // E2E フック（双方向結線の検証用。本番表示には不使用）。
    // __chartEmitMarkerHover は chart モジュールの登録済み hover コールバック（= onMarkerHover で
    // 注入した linkage.setHover）を駆動する。よって chart→linkage 結線が外れると行ハイライトも止まる。
    window.__linkage = linkage;
    window.__chartEmitMarkerHover = (id) => emitMarkerHover(id);

    window.__READY = true;
  } catch (e) {
    showError("レポート読込エラー: " + e.message);
    window.__READY = true; // エラーでも描画結線の検証は完了させる
  }
}

boot();
