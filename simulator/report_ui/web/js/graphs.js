// SPEC#4 インタラクティブグラフ（entries/pl 比較棒・相関散布・保有時間棒）と チャート連動。
// 対象（アーキ指針 §2/§3/§4・SPEC#4・試作 index.html:719-847 buildGraphs 参照）:
//   IS青(#3b82f6)/OOS橙(#f0843b) 並置の棒（entries hour/wday/month・pl hour/wday/month・hold）と
//   相関散布2系列（profit×mfe / profit×mae）を host へ描画する。
//   Balance/Drawdown は①lwc チャートで充足済のため graphs では二重実装しない（比較棒/散布のみ）。
//
// 連動方式（アーキ指針 §3）: グラフ要素クリック→純関数で id Set を作り linkage.applyFilter へ渡す。
//   chart/table への直接 import は作らず、購読登録は main.js が行う（コールバック注入）。
//   filter 純関数（filterIdsBy*/scatterIds）は DOM 非依存で export しテスト容易にする。
//
// R-2/単一規約: hour=entry の UTC hour / wday=(getUTCDay()+6)%7（Mon=0）/ hold=hold_sec バケット。
//   trades は data.segments[seg].trades を読む（フラット DATA.trades 参照は移植しない）。

import { aggOf } from "./data.js";

// wday インデックス規約（Mon=0..Sun=6）。back derive.WEEK / heatmap.js WEEKORDER と一致。
const WEEKORDER = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

// hold バケット境界（back derive._HBUCK と同一・[lo,hi) 半開区間）。
const HB = [
  [0, 60, "<1m"], [60, 120, "1-2m"], [120, 300, "2-5m"], [300, 600, "5-10m"],
  [600, 1800, "10-30m"], [1800, 3600, "30-60m"], [3600, 1e9, ">1h"],
];

// entry_time(秒・UTC) の hour（R-2 規約: getUTCHours）。
function _entryHour(t) {
  return new Date(t.entry_time * 1000).getUTCHours();
}

// entry_time(秒・UTC) の wday ラベル（R-2 規約: (getUTCDay()+6)%7・Mon=0）。
function _entryWday(t) {
  return WEEKORDER[(new Date(t.entry_time * 1000).getUTCDay() + 6) % 7];
}

// --- filter 純関数（DOM 非依存・テスト容易・linkage.applyFilter 入力） -----------------

// entry の UTC hour が h に一致する trade id の Set。
export function filterIdsByHour(trades, h) {
  const ids = new Set();
  for (const t of trades || []) if (_entryHour(t) === h) ids.add(t.id);
  return ids;
}

// entry の wday（(getUTCDay()+6)%7・Mon=0）が w に一致する trade id の Set。
export function filterIdsByWday(trades, w) {
  const ids = new Set();
  for (const t of trades || []) if (_entryWday(t) === w) ids.add(t.id);
  return ids;
}

// hold_sec が hold バケット lab（[lo,hi)）に属する trade id の Set。
export function filterIdsByHold(trades, lab) {
  const b = HB.find((x) => x[2] === lab);
  const ids = new Set();
  if (!b) return ids;
  for (const t of trades || []) if (t.hold_sec >= b[0] && t.hold_sec < b[1]) ids.add(t.id);
  return ids;
}

// 散布点列（[{x,y,id}]）→ trade id の Set。
export function scatterIds(points) {
  const ids = new Set();
  for (const p of points || []) ids.add(p.id);
  return ids;
}

// 散布クリック解決（🟡-2）: Chart.js v4 onClick 要素の {datasetIndex,index} から
//   正しい系列（datasetIndex=0→IS arrA / =1→OOS arrB）の点を選び trade id を返す。
//   範囲外・点欠落時は null（誤 id 抽出を防ぐ）。DOM 非依存・テスト容易。
export function scatterIdAt(arrA, arrB, datasetIndex, index) {
  const arr = datasetIndex === 0 ? (arrA || []) : (arrB || []);
  const p = arr[index];
  return p ? p.id : null;
}

// 散布 dataset ソース（🟡-1）: 棒の _pair と同じく dataset0=IS / dataset1=OOS を
//   seg に依らず返す（cur 依存を排し OOS 区間で IS/OOS が二重表示にならない）。
//   kind="mfe"|"mae"。{a:IS点列, b:OOS点列}。
export function scatterPairSources(data, kind) {
  const key = "scatter_" + kind;
  const isA = aggOf(data, "is")[key] || [];
  const oosB = aggOf(data, "oos")[key] || [];
  return { a: isA, b: oosB };
}

// 保有時間棒 dataset ソース（🟡-1）: dataset0=IS / dataset1=OOS を seg 非依存で返す。
//   ラベルは IS の hold_pl キー順を基準とし、a/b を同ラベルで並置する（二重表示なし）。
export function holdPairSources(data) {
  const isPl = aggOf(data, "is").hold_pl || {};
  const oosPl = aggOf(data, "oos").hold_pl || {};
  const labels = Object.keys(isPl);
  return {
    labels,
    a: labels.map((k) => isPl[k] || 0),
    b: labels.map((k) => oosPl[k] || 0),
  };
}

// --- グラデ・ヘルパー（試作 index.html:721-767 準拠・僅かな陰影で高彩度を保つ） -----------
const GRAD_LIGHTEN = 42;     // 棒: 先端を明るく
const GRAD_PER_BAR = 1;      // 棒: 1 本進むごとの明度変化(%)
const _hexRgb = (h) => [parseInt(h.slice(1, 3), 16), parseInt(h.slice(3, 5), 16), parseInt(h.slice(5, 7), 16)];
const _lift = (rgb, pct) => { const t = pct > 0 ? 255 : 0, p = Math.abs(pct) / 100; return rgb.map((v) => Math.round((t - v) * p + v)); };
function _gradBar(ctx, base) {
  const i = ctx.dataIndex || 0;
  const b = _lift(_hexRgb(base), i * GRAD_PER_BAR);
  const ch = ctx.chart, ys = ch.scales && ch.scales.y, a = ch.chartArea;
  if (!a || !ys) return `rgb(${b.join(",")})`;
  const val = ctx.dataset.data[i];
  let yTip = ys.getPixelForValue(val), yBase = ys.getPixelForValue(0);
  if (!isFinite(yTip) || !isFinite(yBase)) return `rgb(${b.join(",")})`;
  if (Math.abs(yTip - yBase) < 1) yTip = yBase - 1;
  const g = ch.ctx.createLinearGradient(0, yTip, 0, yBase);
  g.addColorStop(0, `rgb(${_lift(b, GRAD_LIGHTEN).join(",")})`);
  g.addColorStop(1, `rgb(${b.join(",")})`);
  return g;
}

const IS_COLOR = "#3b82f6", OOS_COLOR = "#f0843b";
// 散布点色（IS_COLOR/OOS_COLOR と同色相・半透明）。点が重なっても密度が見えるよう alpha を持たせる。
const IS_DOT = "rgba(59,130,246,0.45)", OOS_DOT = "rgba(240,132,59,0.5)";

function _card(id, title) {
  return `<div class="card"><h4>${title}</h4><div class="cv"><canvas id="${id}"></canvas></div></div>`;
}

// IS/OOS 並置棒の dataset ペア（共通化は pair に留める・汎用ファクトリ禁止）。
function _pair(da, db) {
  return [
    { label: "IS", data: da, backgroundColor: (ctx) => _gradBar(ctx, IS_COLOR) },
    { label: "OOS", data: db, backgroundColor: (ctx) => _gradBar(ctx, OOS_COLOR) },
  ];
}

// IS/OOS 散布の dataset ペア（棒の _pair と対をなす・点列専用。汎用ファクトリ禁止）。
function _scatPair(da, db) {
  return [
    { label: "IS", data: da, backgroundColor: IS_DOT, pointRadius: 2 },
    { label: "OOS", data: db, backgroundColor: OOS_DOT, pointRadius: 2 },
  ];
}

function _baseOpt(onClick) {
  return {
    responsive: true, maintainAspectRatio: false, animation: false,
    plugins: { legend: { labels: { color: "#8b949e", boxWidth: 10 } } },
    scales: { x: { ticks: { color: "#8b949e" }, grid: { color: "#1b2230" } },
              y: { ticks: { color: "#8b949e" }, grid: { color: "#1b2230" } } },
    onClick,
  };
}

function _scatOpt(xt) {
  return {
    responsive: true, maintainAspectRatio: false, animation: false,
    plugins: { legend: { labels: { color: "#8b949e", boxWidth: 10 } } },
    scales: {
      x: { title: { display: true, text: xt, color: "#8b949e" }, ticks: { color: "#8b949e" }, grid: { color: "#1b2230" } },
      y: { title: { display: true, text: "Profit", color: "#8b949e" }, ticks: { color: "#8b949e" }, grid: { color: "#1b2230" } },
    },
  };
}

// Chart.js インスタンス保持（R-3: 再描画時 destroy→再構築で "Canvas is already in use" を防ぐ）。
let _charts = {};

// E2E フック用アクセサ（本番表示には不使用・registered Chart の destroy/再構築を検証するため）。
export function activeCharts() {
  return _charts;
}

function _ctx(id) {
  const el = document.getElementById(id);
  return el ? el.getContext("2d") : null;
}

// グラフ群を host(#graphHost 内 #graphGrid) へ描画し、要素クリック→linkage.applyFilter を結線する。
// 引数: host=<div id=graphHost>, data=DATA（segments）, seg=選択区間, linkage,
//        onFocus(optional)=最初の該当 trade へズームするコールバック（main.js が注入）。
export function buildGraphs(host, data, seg, linkage, onFocus) {
  if (!host || typeof window === "undefined" || !window.Chart) return;
  // R-3: 既存 Chart を破棄してから再構築（区間切替の二重バインド例外を防ぐ）。
  Object.values(_charts).forEach((c) => c && c.destroy());
  _charts = {};

  const grid = host.querySelector("#graphGrid") || host;
  grid.innerHTML =
    _card("gEH", "Entries by hours") +
    _card("gEW", "Entries by weekdays") +
    _card("gEM", "Entries by months") +
    _card("gPH", "Profits and losses by hours") +
    _card("gPW", "Profits and losses by weekdays") +
    _card("gPM", "Profits and losses by months") +
    _card("gCF", "Correlation (Profits, MFE)") +
    _card("gCA", "Correlation (Profits, MAE)") +
    _card("gHT", "Position holding time（保有時間別損益）");

  const A = aggOf(data, "is");
  const B = aggOf(data, "oos");
  const cur = aggOf(data, seg);
  const W = cur.weekorder || WEEKORDER;
  const trades = data.segments[seg].trades || [];
  const Chart = window.Chart;

  const focusFirst = (ids) => {
    if (!onFocus) return;
    let first = null;
    for (const t of trades) if (ids.has(t.id) && (first === null || t.entry_time < first.entry_time)) first = t;
    if (first) onFocus(first.entry_time);
  };
  const emit = (ids, label) => { linkage.applyFilter(ids, label); focusFirst(ids); };

  const hours = [...Array(24).keys()];
  const eh = (A.entries_hour) || {}, ehB = (B.entries_hour) || {};
  const ph = (A.pl_hour) || {}, phB = (B.pl_hour) || {};
  const ew = (A.entries_wday) || {}, ewB = (B.entries_wday) || {};
  const pw = (A.pl_wday) || {}, pwB = (B.pl_wday) || {};
  const em = (A.entries_month) || {}, emB = (B.entries_month) || {};
  const pm = (A.pl_month) || {}, pmB = (B.pl_month) || {};

  const onHour = (e, els) => { if (els[0]) emit(filterIdsByHour(trades, els[0].index), `hour ${els[0].index}:00`); };
  const onWday = (e, els) => { if (els[0]) emit(filterIdsByWday(trades, W[els[0].index]), W[els[0].index]); };

  _charts.eh = new Chart(_ctx("gEH"), { type: "bar", data: { labels: hours, datasets: _pair(hours.map((h) => eh[h] || 0), hours.map((h) => ehB[h] || 0)) }, options: _baseOpt(onHour) });
  _charts.ew = new Chart(_ctx("gEW"), { type: "bar", data: { labels: W, datasets: _pair(W.map((w) => ew[w] || 0), W.map((w) => ewB[w] || 0)) }, options: _baseOpt(onWday) });
  const months = [...new Set([...Object.keys(em), ...Object.keys(emB)])].sort();
  _charts.em = new Chart(_ctx("gEM"), { type: "bar", data: { labels: months, datasets: _pair(months.map((m) => em[m] || 0), months.map((m) => emB[m] || 0)) }, options: _baseOpt(() => {}) });
  _charts.ph = new Chart(_ctx("gPH"), { type: "bar", data: { labels: hours, datasets: _pair(hours.map((h) => ph[h] || 0), hours.map((h) => phB[h] || 0)) }, options: _baseOpt(onHour) });
  _charts.pw = new Chart(_ctx("gPW"), { type: "bar", data: { labels: W, datasets: _pair(W.map((w) => pw[w] || 0), W.map((w) => pwB[w] || 0)) }, options: _baseOpt(onWday) });
  const pmonths = [...new Set([...Object.keys(pm), ...Object.keys(pmB)])].sort();
  _charts.pm = new Chart(_ctx("gPM"), { type: "bar", data: { labels: pmonths, datasets: _pair(pmonths.map((m) => pm[m] || 0), pmonths.map((m) => pmB[m] || 0)) }, options: _baseOpt(() => {}) });

  // 🟡-2: Chart.js v4 onClick 要素 {datasetIndex,index} で正しい系列の点を解決する
  //   （常に IS 配列を参照する誤りを排し、OOS 点クリックで正しい OOS trade id を抽出）。
  const onScatter = (arrA, arrB) => (e, els) => {
    if (!els[0]) return;
    const id = scatterIdAt(arrA, arrB, els[0].datasetIndex, els[0].index);
    if (id != null) emit(new Set([id]), `trade #${id}`);
  };
  // 🟡-1: dataset0=IS / dataset1=OOS を seg 非依存で並置（cur 依存の二重表示を排す）。
  const scf = scatterPairSources(data, "mfe");
  const sca = scatterPairSources(data, "mae");
  _charts.cf = new Chart(_ctx("gCF"), {
    type: "scatter",
    data: { datasets: _scatPair(scf.a, scf.b) },
    options: { ..._scatOpt("MFE (JPY)"), onClick: onScatter(scf.a, scf.b) },
  });
  _charts.ca = new Chart(_ctx("gCA"), {
    type: "scatter",
    data: { datasets: _scatPair(sca.a, sca.b) },
    options: { ..._scatOpt("MAE (JPY)"), onClick: onScatter(sca.a, sca.b) },
  });

  // 🟡-1: 保有時間棒も dataset0=IS / dataset1=OOS を seg 非依存で並置。
  const hp = holdPairSources(data);
  _charts.ht = new Chart(_ctx("gHT"), {
    type: "bar",
    data: { labels: hp.labels, datasets: _pair(hp.a, hp.b) },
    options: _baseOpt((e, els) => { if (els[0]) emit(filterIdsByHold(trades, hp.labels[els[0].index]), `hold ${hp.labels[els[0].index]}`); }),
  });
}
