// エントリ（詳細設計 §11.1 main.js・F-1 最小サブセット）。
// report.json を fetch → DATA → 区間切替で各パネル再描画（チャート＋サマリーカード）。

import { fmtMoney, cfmt, signClass } from "./format.js";
import { renderChart } from "./chart.js";

let DATA = null;

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

function selectSegment(seg) {
  renderMeta(seg);
  renderSummary(seg);
  renderChart("price-chart", DATA.segments[seg]);
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
    const sel = document.getElementById("seg-select");
    sel.addEventListener("change", () => selectSegment(sel.value));
    selectSegment(sel.value || "is");

    window.__READY = true;
  } catch (e) {
    showError("レポート読込エラー: " + e.message);
    window.__READY = true; // エラーでも描画結線の検証は完了させる
  }
}

boot();
