// 比較・判定タブ（IS vs OOS・区間非依存）。詳細設計 §11・試作 index.html:1027-1135 準拠。
//
// 責務:
//   - 純ロジック（DOM 非依存・node:test 被覆）: 判定バナー文言（verdictLabel）・report 値の
//     数値抽出（parseReportNum）・劣化比較セル（compareCell: ratio=OOS/IS・delta=OOS−IS・
//     null/inf 規約）・劣化比較表モデル（buildCompareRows: REPORT_GROUPS 章立て）。
//   - DOM/Chart（buildCompare・e2e 被覆）: 判定バナー・主要7指標カード・劣化比較表・
//     右グラフ（エクイティ IS/OOS 重畳＋純損益内訳）を #pane-compare へ描画する。
//
// 依存方向（アーキ指針）: compare.js → glossary（静的 import）/ compare.js → vendor Chart.js
//   （UMD global）。chart/graphs/linkage への直接 import は持たない（report.json のみ消費）。
//   区間非依存: IS/OOS 両方を常に参照し init で 1 回構築する（selectSegment 非依存）。
//   Chart.js は cmpCharts（graphs の activeCharts と別インスタンス空間）で隔離し、
//   タブ遷移時は resize のみ（destroy しない）。

import { REPORT_GROUPS, LABELS_JA } from "./glossary.js";
import { cfmtLocale, signClass, fmtT } from "./format.js";

// --- 純ロジック（DOM 非依存・テスト容易） ---------------------------------------

// verdict.result → ホワイトチェック判定バナー文言（R-2）。未知は空文字。
const _VERDICT_LABEL = { fail: "過剰最適化", warn: "要注意", pass: "合格" };
export function verdictLabel(result) {
  return _VERDICT_LABEL[result] || "";
}

// report 値（文字列）から先頭数値を抽出する。% と末尾 "(...)" を除去し、純数値のみ採用。
//   非数値（識別子・期間・"inf"）は null（＝比/差を算出しない）。
export function parseReportNum(s) {
  if (s === null || s === undefined) return null;
  let t = String(s).replace(/[\s ]/g, "").replace(/\(.*\)$/, "").replace(/%$/, "");
  return /^-?\d+(\.\d+)?$/.test(t) ? parseFloat(t) : null;
}

// IS/OOS の report 値ペアから比較セルを作る。数値項目のみ ratio=OOS/IS（IS===0 で null）・
//   delta=OOS−IS を算出する。非数値は is/oos/ratio/delta すべて null。
export function compareCell(isVal, oosVal) {
  const ip = parseReportNum(isVal);
  const op = parseReportNum(oosVal);
  if (ip === null || op === null) {
    return { is: null, oos: null, ratio: null, delta: null, deltaClass: "" };
  }
  const ratio = ip === 0 ? null : op / ip;
  const delta = op - ip;
  return { is: ip, oos: op, ratio, delta, deltaClass: signClass(delta) };
}

// 劣化比較表モデルを REPORT_GROUPS 章立てで構築する。
//   返り値: [{type:"group", title} | {type:"metric", key, labelJa, isRaw, oosRaw,
//            is, oos, ratio, delta, deltaClass}]。該当キーが無い章は出さない（試作準拠）。
//   どの章にも属さない report キーは末尾「その他」章へ。
export function buildCompareRows(isReport, oosReport) {
  const isR = isReport || {};
  const oosR = oosReport || {};
  const rows = [];
  const used = new Set();
  const rowFor = (k) => {
    used.add(k);
    const c = compareCell(isR[k], oosR[k]);
    return {
      type: "metric", key: k, labelJa: LABELS_JA[k] || k,
      isRaw: k in isR ? isR[k] : null,
      oosRaw: k in oosR ? oosR[k] : null,
      is: c.is, oos: c.oos, ratio: c.ratio, delta: c.delta, deltaClass: c.deltaClass,
    };
  };
  for (const [title, keys] of REPORT_GROUPS) {
    const present = keys.filter((k) => (k in isR) || (k in oosR));
    if (!present.length) continue;
    rows.push({ type: "group", title });
    for (const k of present) rows.push(rowFor(k));
  }
  const rest = Object.keys(isR).filter((k) => !used.has(k));
  if (rest.length) {
    rows.push({ type: "group", title: "その他" });
    for (const k of rest) rows.push(rowFor(k));
  }
  return rows;
}

// 残高曲線（[{time,value}]）から最大DD（残高ベース・アンダーウォーター）系列を作る（点12）。
//   試作 ddSet 準拠: 起点 {x:bc[0].time-1,y:0} ＋ 各点で連続ピークからの下落額 d（≤0）。
//   返り値: { points:[{x,y}], maxDrawdown, maxDdPct }（maxDrawdown=最深額・maxDdPct=最深%）。
export function underwaterCurve(balanceCurve, initDeposit = DEFAULT_DEPOSIT) {
  const bc = balanceCurve || [];
  if (!bc.length) return { points: [], maxDrawdown: 0, maxDdPct: 0 };
  let peak = initDeposit, mm = 0, mp = 0;
  const points = [{ x: bc[0].time - 1, y: 0 }];
  for (const p of bc) {
    if (p.value > peak) peak = p.value;
    const d = p.value - peak;
    if (d < mm) { mm = d; mp = peak === 0 ? 0 : (d / peak) * 100; }
    points.push({ x: p.time, y: +d.toFixed(0) });
  }
  return { points, maxDrawdown: mm, maxDdPct: mp };
}

// レーダー/劣化バー軸（試作 RM）。hi=true は「高いほど良い」軸（OOS/IS 維持率）、
//   hi=false（低DD）は絶対値の逆数（低DD ほど高い）。
export const RADAR_METRICS = [
  { k: "profit_factor", l: "PF", hi: true },
  { k: "win_rate", l: "勝率", hi: true },
  { k: "payoff", l: "ペイオフ", hi: true },
  { k: "expectancy", l: "期待値", hi: true },
  { k: "return_pct", l: "リターン", hi: true },
  { k: "max_dd_pct", l: "低DD", hi: false },
];

// 1 指標の OOS/IS 維持率（試作 ret）。hi=true: ov/iv（iv=0 で 0）/ hi=false: |iv|/|ov|（低DD 逆数）。
//   summary は max_dd_pct が負値でも符号非依存（Math.abs）で維持率を出す（設計書 §0 注）。
export function metricRetention(metric, isSummary, oosSummary) {
  const iv = isSummary[metric.k], ov = oosSummary[metric.k];
  if (metric.hi) return iv !== 0 ? ov / iv : 0;
  return Math.abs(iv) > 0 ? Math.abs(iv) / Math.abs(ov || 1e-9) : 0;
}

// 全レーダー軸の維持率配列（点13/14 共通入力）。順序は RADAR_METRICS。
export function metricRetentionAll(isSummary, oosSummary) {
  return RADAR_METRICS.map((m) => +metricRetention(m, isSummary, oosSummary).toFixed(3));
}

// 劣化バーの値と色（試作 dv/dcol・点14）。v>=0.95 緑 / >=0.7 黄 / それ未満 赤。
export function degradationBars(isSummary, oosSummary) {
  const values = metricRetentionAll(isSummary, oosSummary);
  const colors = values.map((v) => (v >= 0.95 ? "#26a69a" : v >= 0.7 ? "#e3b341" : "#ef5350"));
  const labels = RADAR_METRICS.map((m) => m.l);
  return { labels, values, colors };
}

// レーダークランプ（試作 clamp）: 0..1.3 に丸める（OOS が中心へ縮む/外へ伸びるを抑制）。
export function radarClamp(v) {
  return Math.max(0, Math.min(1.3, v));
}

// --- DOM/Chart（buildCompare・区間非依存・init 1回・e2e 被覆） ---------------------

// IS青/OOS橙の系列色（graphs.js と同一の配色規約・周辺コード粒度に合わせる）。
const IS_COLOR = "#3b82f6", OOS_COLOR = "#f0843b";
// report.meta.initial_deposit 欠落時のエクイティ起点フォールバック（UC INITIAL と同値）。
const DEFAULT_DEPOSIT = 10000;

// 主要7指標カード（degradation キー・試作 CMP_NAMES）。
const CMP_NAMES = {
  net: "純損益 (JPY)", profit_factor: "プロフィットファクタ", win_rate: "勝率 (%)",
  expectancy: "期待値/取引 (JPY)", payoff: "ペイオフ比", return_pct: "リターン (%)",
  max_dd_pct: "最大DD (%)",
};

// cmpCharts: graphs の activeCharts と別インスタンス空間（隔離・destroy しない）。
const cmpCharts = {};
export function cmpChartInstances() {
  return cmpCharts;
}

// 分割の縦線プラグイン（試作 splitLine・cmpEquity/cmpDD/cmpDeg で共用・点12）。
// Chart.register は vendor ロード時に 1 回だけ実行する（多重登録回避）。
let _splitLineRegistered = false;
function _ensureSplitLine() {
  if (_splitLineRegistered || typeof Chart === "undefined") return;
  _splitLineRegistered = true;
  Chart.register({
    id: "splitLine",
    afterDraw(ch, _a, opts) {
      if (opts.x == null) return;
      const xs = ch.scales.x, a = ch.chartArea, px = xs.getPixelForValue(opts.x);
      if (!isFinite(px)) return;
      const c = ch.ctx;
      c.save();
      c.strokeStyle = "#8b949e"; c.setLineDash([5, 4]); c.lineWidth = 1;
      c.beginPath(); c.moveTo(px, a.top); c.lineTo(px, a.bottom); c.stroke();
      c.setLineDash([]); c.fillStyle = "#8b949e"; c.font = "10px system-ui";
      c.fillText(opts.label || "", px + 4, a.top + 11);
      c.restore();
    },
  });
}

// 面塗りグラデ（試作 aGrad・cmpDD/cmpRadar の fill 用・上濃→下薄）。
function _aGrad(ctx, base, top = 0.22, bot = 0.02) {
  const _rgb = (c) => {
    if (typeof c === "string" && c[0] === "#" && c.length === 7) {
      return [parseInt(c.slice(1, 3), 16), parseInt(c.slice(3, 5), 16), parseInt(c.slice(5, 7), 16)];
    }
    const m = String(c).match(/\d+/g);
    return m ? m.slice(0, 3).map(Number) : [0, 0, 0];
  };
  const a = ctx.chart && ctx.chart.chartArea;
  const rgb = _rgb(base);
  if (!a) return `rgba(${rgb.join(",")},${top})`;
  const g = ctx.chart.ctx.createLinearGradient(0, a.top, 0, a.bottom);
  g.addColorStop(0, `rgba(${rgb.join(",")},${top})`);
  g.addColorStop(1, `rgba(${rgb.join(",")},${bot})`);
  return g;
}

// split 日付（meta.split・"YYYY-MM-DD"）を UNIX 秒へ（試作 splitTs）。欠落時は null。
function _splitTs(split) {
  if (!split) return null;
  const t = Date.parse(split + "T00:00:00Z");
  return Number.isNaN(t) ? null : Math.floor(t / 1000);
}

// 表示整形（劣化比較表の IS/OOS 列・試作 disp 準拠）。桁区切りは format.cfmtLocale を共用。
function _disp(v) {
  if (v === null || v === undefined) return "—";
  const s = String(v).trim(), t = s.replace(/[\s ]/g, "");
  if (/^-?\d+(\.0+)?$/.test(t)) return cfmtLocale(parseFloat(t), 0);
  if (/^-?\d+\.\d+$/.test(t)) {
    const dp = t.split(".")[1].length;
    return cfmtLocale(parseFloat(t), dp > 3 ? 3 : dp);
  }
  return s;
}

function _renderVerdict(data) {
  const vd = data.verdict || { result: "", reasons: [] };
  const lab = verdictLabel(vd.result);
  const host = document.getElementById("cmpVerdict");
  if (!host) return;
  host.className = "cmp-verdict " + (vd.result || "");
  host.innerHTML =
    `<div class="big vbadge ${vd.result}">${lab}</div>` +
    `<div><div class="cmp-verdict-title">ホワイトチェック判定：${lab}</div>` +
    `<ul>${(vd.reasons || []).map((r) => `<li>${r}</li>`).join("")}</ul></div>`;
}

function _renderCards(data) {
  const deg = data.degradation || {};
  const host = document.getElementById("cmpBasic");
  if (!host) return;
  let html = "";
  for (const k of Object.keys(CMP_NAMES)) {
    const r = deg[k];
    if (!r) continue;
    const dec = (k === "profit_factor" || k === "payoff") ? 3 : (k === "net" ? 0 : 2);
    const dcls = signClass(r.delta);
    html +=
      `<div class="bcard" data-metric="${k}"><div class="bk">${CMP_NAMES[k]}</div>` +
      `<div class="bdelta ${dcls}"><span class="bl">差</span>` +
      `${(r.delta > 0 ? "+" : "") + cfmtLocale(r.delta, dec)}</div>` +
      `<div class="bratio"><span class="bl">比</span>` +
      `${r.ratio == null ? "—" : cfmtLocale(r.ratio, 3)}</div>` +
      `<div class="bvals"><span class="bv is"><span class="lbl">IS</span>${cfmtLocale(r.is, dec)}</span>` +
      `<span class="bv oos"><span class="lbl">OOS</span>${cfmtLocale(r.oos, dec)}</span></div></div>`;
  }
  host.innerHTML = html;
}

function _renderTable(data) {
  const isR = (data.segments && data.segments.is && data.segments.is.report) || {};
  const oosR = (data.segments && data.segments.oos && data.segments.oos.report) || {};
  const head = document.querySelector("#cmpTable thead");
  const body = document.querySelector("#cmpTable tbody");
  if (!head || !body) return;
  head.innerHTML =
    '<tr><th>指標</th><th class="col-is">IS</th><th class="col-oos">OOS</th>' +
    "<th>比</th><th>差</th></tr>";
  const rows = buildCompareRows(isR, oosR);
  let html = "";
  for (const r of rows) {
    if (r.type === "group") {
      html += `<tr class="grp"><td colspan="5">${r.title}</td></tr>`;
      continue;
    }
    const sgnI = (parseReportNum(r.isRaw) ?? 0) < 0 ? "neg" : "";
    const sgnO = (parseReportNum(r.oosRaw) ?? 0) < 0 ? "neg" : "";
    const ratio = r.ratio == null ? "—" : cfmtLocale(r.ratio, 3);
    let delta = "—";
    if (r.delta != null) {
      const dec = (Number.isInteger(r.is) && Number.isInteger(r.oos)) ? 0 : 2;
      delta = (r.delta > 0 ? "+" : "") + cfmtLocale(r.delta, dec);
    }
    html +=
      `<tr><td class="lab" data-gk="${r.key}">${r.labelJa}` +
      `<span class="en">${r.key}</span></td>` +
      `<td class="col-is num ${sgnI}">${_disp(r.isRaw)}</td>` +
      `<td class="col-oos num ${sgnO}">${_disp(r.oosRaw)}</td>` +
      `<td class="num">${ratio}</td>` +
      `<td class="num ${r.deltaClass}">${delta}</td></tr>`;
  }
  body.innerHTML = html;
}

function _renderCharts(data) {
  if (typeof Chart === "undefined") return; // vendor 未ロード時は描画スキップ
  _ensureSplitLine();
  const init = (data.meta && data.meta.initial_deposit) || DEFAULT_DEPOSIT;
  const split = data.meta && data.meta.split;
  const splitTs = _splitTs(split);
  const segData = (seg) => (data.segments && data.segments[seg]) || {};
  const curveOf = (seg) => ((segData(seg).agg && segData(seg).agg.balance_curve) || []);
  const _splitPlugin = splitTs != null ? { splitLine: { x: splitTs, label: "分割 " + split } } : {};

  // エクイティ IS/OOS 重畳（残高曲線・split 前 init から起点・分割縦線）。
  const eqEl = document.getElementById("cmpEquity");
  if (eqEl) {
    const mk = (seg, col) => {
      const bc = curveOf(seg);
      const pts = bc.length
        ? [{ x: bc[0].time - 1, y: init }].concat(bc.map((p) => ({ x: p.time, y: p.value })))
        : [];
      return {
        label: segData(seg).label || seg.toUpperCase(), data: pts, borderColor: col,
        backgroundColor: (ctx) => _aGrad(ctx, col, 0.16, 0.01),
        borderWidth: 1.4, pointRadius: 0, tension: 0, fill: true, parsing: false,
      };
    };
    cmpCharts.eq = new Chart(eqEl.getContext("2d"), {
      type: "line",
      data: { datasets: [mk("is", IS_COLOR), mk("oos", OOS_COLOR)] },
      options: {
        responsive: true, maintainAspectRatio: false, animation: false,
        interaction: { mode: "nearest", intersect: false },
        plugins: {
          legend: { labels: { color: "#8b949e", boxWidth: 10 } },
          ..._splitPlugin,
          tooltip: { callbacks: {
            title: (it) => fmtT(it[0].parsed.x).slice(5, 16),
            label: (it) => it.dataset.label + ": " + cfmtLocale(it.parsed.y, 0),
          } },
        },
        scales: {
          x: { type: "linear", ticks: { color: "#8b949e", maxTicksLimit: 9, callback: (v) => fmtT(v).slice(5, 10) }, grid: { color: "#1b2230" } },
          y: { ticks: { color: "#8b949e" }, grid: { color: "#1b2230" } },
        },
      },
    });
  }

  // 純損益内訳（総利益・総損失・純損益の IS/OOS 棒）。
  const pnlEl = document.getElementById("cmpPnl");
  if (pnlEl) {
    const summary = data.summary || {};
    const breakdown = (seg) => {
      const tr = (segData(seg).trades) || [];
      const gp = tr.filter((t) => (t.profit || 0) > 0).reduce((s, t) => s + t.profit, 0);
      const gl = tr.filter((t) => (t.profit || 0) < 0).reduce((s, t) => s + t.profit, 0);
      const net = (summary[seg] && summary[seg].net) || (gp + gl);
      return [Math.round(gp), Math.round(gl), Math.round(net)];
    };
    cmpCharts.pnl = new Chart(pnlEl.getContext("2d"), {
      type: "bar",
      data: {
        labels: ["総利益", "総損失", "純損益"],
        datasets: [
          { label: "IS", data: breakdown("is"), backgroundColor: IS_COLOR },
          { label: "OOS", data: breakdown("oos"), backgroundColor: OOS_COLOR },
        ],
      },
      options: {
        responsive: true, maintainAspectRatio: false, animation: false,
        plugins: { legend: { labels: { color: "#8b949e", boxWidth: 10 } } },
        scales: {
          x: { ticks: { color: "#8b949e" }, grid: { display: false } },
          y: { ticks: { color: "#8b949e" }, grid: { color: "#1b2230" } },
        },
      },
    });
  }

  // 点12 最大ドローダウン（残高ベース・アンダーウォーター JPY・IS/OOS 重畳＋分割縦線）。
  const ddEl = document.getElementById("cmpDD");
  if (ddEl) {
    const ddSet = (seg, col) => {
      const uw = underwaterCurve(curveOf(seg), init);
      const lab = `${segData(seg).label || seg.toUpperCase()}  最大 ${cfmtLocale(uw.maxDrawdown, 0)} (${uw.maxDdPct.toFixed(2)}%)`;
      return {
        label: lab, data: uw.points, borderColor: col,
        backgroundColor: (ctx) => _aGrad(ctx, col, 0.24, 0.02),
        borderWidth: 1.4, pointRadius: 0, tension: 0, fill: true, parsing: false,
      };
    };
    cmpCharts.dd = new Chart(ddEl.getContext("2d"), {
      type: "line",
      data: { datasets: [ddSet("is", IS_COLOR), ddSet("oos", OOS_COLOR)] },
      options: {
        responsive: true, maintainAspectRatio: false, animation: false,
        interaction: { mode: "nearest", intersect: false },
        plugins: {
          legend: { labels: { color: "#8b949e", boxWidth: 10 } },
          ..._splitPlugin,
          tooltip: { callbacks: {
            title: (it) => fmtT(it[0].parsed.x).slice(5, 16),
            label: (it) => it.dataset.label.split("  ")[0] + ": " + cfmtLocale(it.parsed.y, 0) + " JPY",
          } },
        },
        scales: {
          x: { type: "linear", ticks: { color: "#8b949e", maxTicksLimit: 9, callback: (v) => fmtT(v).slice(5, 10) }, grid: { color: "#1b2230" } },
          y: { ticks: { color: "#8b949e", callback: (v) => cfmtLocale(v, 0) }, grid: { color: "#1b2230" } },
        },
      },
    });
  }

  // 点13/14 共通入力: IS=1.0 基準の OOS 維持率（summary 6 指標・符号非依存）。
  const S = data.summary || {};
  const isS = S.is || {}, oosS = S.oos || {};

  // 点13 指標レーダー（IS=正六角形1.0・OOS=維持率・内側に縮む＝劣化）。
  const radarEl = document.getElementById("cmpRadar");
  if (radarEl) {
    const oosVals = RADAR_METRICS.map((m) => +radarClamp(metricRetention(m, isS, oosS)).toFixed(3));
    cmpCharts.radar = new Chart(radarEl.getContext("2d"), {
      type: "radar",
      data: {
        labels: RADAR_METRICS.map((m) => m.l),
        datasets: [
          { label: "IS", data: RADAR_METRICS.map(() => 1), borderColor: IS_COLOR, backgroundColor: (ctx) => _aGrad(ctx, IS_COLOR, 0.26, 0.05), borderWidth: 1.5, pointRadius: 2 },
          { label: "OOS", data: oosVals, borderColor: OOS_COLOR, backgroundColor: (ctx) => _aGrad(ctx, OOS_COLOR, 0.28, 0.05), borderWidth: 1.5, pointRadius: 2 },
        ],
      },
      options: {
        responsive: true, maintainAspectRatio: false, animation: false,
        plugins: {
          legend: { labels: { color: "#8b949e", boxWidth: 10 } },
          tooltip: { callbacks: { label: (it) => it.dataset.label + ": " + cfmtLocale(it.parsed.r, 2) } },
        },
        scales: {
          r: {
            min: 0, max: 1.3, ticks: { display: false, stepSize: 0.5 }, grid: { color: "#1b2230" },
            angleLines: { color: "#1b2230" }, pointLabels: { color: "#c9d1d9", font: { size: 11 } },
          },
        },
      },
    });
  }

  // 点14 劣化比バー（維持率・1.0 基準線・色=良し悪し）。
  const degEl = document.getElementById("cmpDeg");
  if (degEl) {
    const db = degradationBars(isS, oosS);
    cmpCharts.deg = new Chart(degEl.getContext("2d"), {
      type: "bar",
      data: {
        labels: db.labels,
        datasets: [{ label: "OOS/IS", data: db.values, backgroundColor: (ctx) => db.colors[ctx.dataIndex] }],
      },
      options: {
        indexAxis: "y", responsive: true, maintainAspectRatio: false, animation: false,
        plugins: {
          legend: { display: false },
          splitLine: { x: 1, label: "維持 1.0" },
          tooltip: { callbacks: { label: (it) => "維持率 " + cfmtLocale(it.parsed.x, 3) } },
        },
        scales: {
          x: { min: 0, ticks: { color: "#8b949e" }, grid: { color: "#1b2230" } },
          y: { ticks: { color: "#c9d1d9" }, grid: { display: false } },
        },
      },
    });
  }

  // タイトルに用語(data-gg)を付与（試作 CMAP・hover tip 連動）。
  const CMAP = { cmpEquity: "Equity IS/OOS", cmpPnl: "P/L breakdown", cmpDD: "Max Drawdown", cmpRadar: "Metrics radar", cmpDeg: "Degradation ratio" };
  document.querySelectorAll("#pane-compare .cmp-right .cmp-card").forEach((c) => {
    const cv = c.querySelector("canvas"), h = c.querySelector("h4");
    if (cv && h && CMAP[cv.id]) h.dataset.gg = CMAP[cv.id];
  });
}

// 比較・判定タブを #pane-compare へ描画する（区間非依存・init で 1 回呼ぶ）。
// IS/OOS の summary/degradation/report/segments を読み、selectSegment には依存しない。
export function buildCompare(data) {
  if (!data) return;
  _renderVerdict(data);
  _renderCards(data);
  _renderTable(data);
  _renderCharts(data);
}

// タブ遷移時に cmpCharts を resize する（destroy しない・隔離 init を維持）。
export function resizeCompareCharts() {
  for (const c of Object.values(cmpCharts)) {
    if (c && typeof c.resize === "function") c.resize();
  }
}
