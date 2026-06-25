// 価格チャート（多窓: ローソク足 + Balance + Drawdown）＋論理レンジ/クロスヘア同期。
// 試作 index.html:414-561 準拠・パリティ点 1,2,3,4,7。
// vendor lightweight-charts v4.1.3: addCandlestickSeries / addAreaSeries / addBaselineSeries /
//   subscribeVisibleLogicalRangeChange / setVisibleLogicalRange / subscribeCrosshairMove /
//   setCrosshairPosition / clearCrosshairPosition（試作実挙動＝v4.1.3 API で可）。
//
// 純ロジック（DOM/vendor 非依存・node:test 被覆）:
//   balanceForwardFill（balance_curve をバー時刻へ前方補完）/ drawdownSeries（peak からの
//   下落＝アンダーウォーター）/ byTimeResolve（time→value 索引・クロスヘア同期入力）。

let _chart = null, _balChart = null, _ddChart = null;
let _candle = null, _balSeries = null, _ddSeries = null;
let _markerHoverCb = null; // chart→linkage 通知のコールバック注入（直接 import を作らない）
let _rows = []; // 直近の trades 行（マーカー再描画用）
let _barTimes = [], _barsNormal = [], _barsDim = [], _candlesDimmed = false;
const DIM_ALPHA = 0.15; // 非 hover ペアの減光アルファ（試作 DIM_ALPHA=0.15）
const MARKER_CAP = 700;
const EXIT_COLOR = "#6b7785";
const DEFAULT_DEPOSIT = 10000;

function _withAlpha(hex, a) {
  if (typeof hex !== "string" || hex[0] !== "#" || hex.length !== 7) return hex;
  const n = parseInt(hex.slice(1), 16);
  return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},${a})`;
}

function _bisectLeft(a, x) {
  let lo = 0, hi = a.length;
  while (lo < hi) { const m = (lo + hi) >> 1; if (a[m] < x) lo = m + 1; else hi = m; }
  return lo;
}

// --- 純ロジック（DOM/vendor 非依存・テスト容易） --------------------------------

// balance_curve（[{time,value}]・非単調/重複あり）を time 昇順で重複排除する（試作 dedupe）。
export function dedupeCurve(curve) {
  const m = new Map();
  for (const p of curve || []) m.set(p.time, p.value);
  return [...m.entries()].sort((a, b) => a[0] - b[0]).map(([time, value]) => ({ time, value }));
}

// balance_curve を各バー時刻へ前方補完し、Balance/Drawdown の同一時間ドメイン系列を作る。
//   返り値: { balData:[{time,value}], ddData:[{time,value}] }。
//   ddData は連続ピークからの下落額（≤0・アンダーウォーター）。試作 index.html:436-447 準拠。
export function balanceForwardFill(barTimes, balanceCurve, initDeposit = DEFAULT_DEPOSIT) {
  const bc = dedupeCurve(balanceCurve);
  let j = 0, cur = initDeposit, peak = initDeposit;
  const balData = [], ddData = [];
  for (const t of barTimes || []) {
    while (j < bc.length && bc[j].time <= t) { cur = bc[j].value; j++; }
    balData.push({ time: t, value: cur });
    if (cur > peak) peak = cur;
    ddData.push({ time: t, value: +(cur - peak).toFixed(0) });
  }
  return { balData, ddData };
}

// time→value の索引 Map（クロスヘア同期で他窓の同時刻値を引く・試作 *ByTime）。
export function byTimeResolve(series) {
  return new Map((series || []).map((p) => [p.time, p.value]));
}

// --- DOM/vendor（buildChart 多窓・同期・e2e 被覆） -------------------------------

function _common() {
  return {
    layout: { background: { color: "#0e1117" }, textColor: "#c9d1d9" },
    grid: { vertLines: { color: "#1b2230" }, horzLines: { color: "#1b2230" } },
    rightPriceScale: { borderColor: "#272d38" },
    crosshair: { mode: LightweightCharts.CrosshairMode.Normal },
    autoSize: true,
  };
}

// 3 チャートの可視論理レンジを相互同期（試作 syncCharts・点3）。
function _syncLogicalRange(list) {
  let lock = false;
  for (const src of list) {
    src.timeScale().subscribeVisibleLogicalRangeChange((lr) => {
      if (lock || !lr) return;
      lock = true;
      for (const o of list) if (o !== src) { try { o.timeScale().setVisibleLogicalRange(lr); } catch (e) { /* noop */ } }
      lock = false;
    });
  }
}

// クロスヘア（縦線）を全窓で同期（試作 crosshairSync・点4）。
function _crosshairSync(items) {
  let lock = false;
  for (const src of items) {
    src.chart.subscribeCrosshairMove((param) => {
      if (lock) return;
      lock = true;
      for (const o of items) {
        if (o === src) continue;
        if (param.time === undefined || param.point === undefined) {
          o.chart.clearCrosshairPosition();
        } else {
          const v = o.byTime.get(param.time);
          o.chart.setCrosshairPosition(v === undefined ? 0 : v, param.time, o.series);
        }
      }
      lock = false;
    });
  }
}

// 区間切替で 3 窓を破棄する（試作 selectSegment の destroy 相当）。
function _destroyCharts() {
  for (const c of [_chart, _balChart, _ddChart]) { if (c) { try { c.remove(); } catch (e) { /* noop */ } } }
  _chart = _balChart = _ddChart = null;
  _candle = _balSeries = _ddSeries = null;
  _crosshairWired = false;
  _candlesDimmed = false;
}

// 多窓チャート（ローソク足 + Balance + Drawdown）を構築する（点1,2,3,4,7）。
// segment.bars をローソク足へ、segment.agg.balance_curve を前方補完して Balance/DD 窓へ流す。
export function renderChart(containerId, segment, opts) {
  const elC = document.getElementById(containerId);
  const elB = document.getElementById("paneBal");
  const elD = document.getElementById("paneDD");
  if (!elC) return;
  _destroyCharts();

  const baseTs = { timeVisible: true, secondsVisible: false, borderColor: "#272d38", minBarSpacing: 0.004 };
  _chart = LightweightCharts.createChart(elC, { ..._common(), timeScale: { ...baseTs, visible: false } });
  if (elB) _balChart = LightweightCharts.createChart(elB, { ..._common(), timeScale: { ...baseTs, visible: false } });
  if (elD) _ddChart = LightweightCharts.createChart(elD, { ..._common(), timeScale: { ...baseTs, visible: true } });

  _candle = _chart.addCandlestickSeries({
    upColor: "#26a69a", downColor: "#ef5350",
    borderVisible: false, wickUpColor: "#26a69a", wickDownColor: "#ef5350",
  });
  _barsNormal = (segment.bars || []).map((b) => ({ time: b.time, open: b.open, high: b.high, low: b.low, close: b.close }));
  _barsDim = (segment.bars || []).map((b) => {
    const up = b.close >= b.open;
    const c = up ? _withAlpha("#26a69a", DIM_ALPHA) : _withAlpha("#ef5350", DIM_ALPHA);
    return { time: b.time, open: b.open, high: b.high, low: b.low, close: b.close, color: c, wickColor: c, borderColor: c };
  });
  _barTimes = (segment.bars || []).map((b) => b.time);
  _candle.setData(_barsNormal);

  const initDep = (opts && opts.initialDeposit) || (segment.meta && segment.meta.initial_deposit) || DEFAULT_DEPOSIT;
  const curve = (segment.agg && segment.agg.balance_curve) || [];
  const { balData, ddData } = balanceForwardFill(_barTimes, curve, initDep);

  if (_balChart) {
    // 点1 Balance: エリア系列（フィル＋縦グラデ・低不透明度）。
    _balSeries = _balChart.addAreaSeries({
      lineColor: "rgba(59,130,246,0.9)",
      topColor: "rgba(59,130,246,0.40)", bottomColor: "rgba(59,130,246,0.03)",
      lineWidth: 2, priceLineVisible: false, lastValueVisible: true,
    });
    _balSeries.setData(balData);
  }
  if (_ddChart) {
    // 点2 Drawdown: ベースライン 0 基準（アンダーウォーター・下方フィル）。
    _ddSeries = _ddChart.addBaselineSeries({
      baseValue: { type: "price", price: 0 },
      topLineColor: "rgba(239,83,80,0)", topFillColor1: "rgba(239,83,80,0)", topFillColor2: "rgba(239,83,80,0)",
      bottomLineColor: "rgba(239,83,80,0.9)", bottomFillColor1: "rgba(239,83,80,0.05)", bottomFillColor2: "rgba(239,83,80,0.42)",
      lineWidth: 2, priceLineVisible: false, lastValueVisible: true,
    });
    _ddSeries.setData(ddData);
  }

  // 点3 論理レンジ同期・点4 クロスヘア同期（存在する窓のみ連結）。
  const charts = [_chart, _balChart, _ddChart].filter(Boolean);
  _syncLogicalRange(charts);
  const items = [{ chart: _chart, series: _candle, byTime: byTimeResolve(_barsNormal.map((b) => ({ time: b.time, value: b.close }))) }];
  if (_balSeries) items.push({ chart: _balChart, series: _balSeries, byTime: byTimeResolve(balData) });
  if (_ddSeries) items.push({ chart: _ddChart, series: _ddSeries, byTime: byTimeResolve(ddData) });
  _crosshairSync(items);

  _rows = segment.trades || [];
  const t0 = _rows.length ? _rows[0].entry_time : (_barTimes[0] || 0);
  const t1 = _rows.length ? _rows[_rows.length - 1].exit_time : (_barTimes[_barTimes.length - 1] || 0);
  _chart.timeScale().setVisibleRange({ from: t0 - 600, to: t1 + 600 });
  _chart.timeScale().subscribeVisibleTimeRangeChange(() => renderMarkers(_rows, { hoverId: null, filter: null }));
  _ensureCrosshair();
  renderMarkers(_rows, { hoverId: null, filter: null });
}

// crosshair でマーカーグリフ命中時に注入コールバックへ trade id を通知。
let _crosshairWired = false;
function _ensureCrosshair() {
  if (_crosshairWired || !_chart) return;
  _crosshairWired = true;
  _chart.subscribeCrosshairMove((param) => {
    const oid = param && param.hoveredObjectId;
    if (typeof oid === "string" && (oid[0] === "e" || oid[0] === "x")) {
      const tid = Number(oid.slice(1));
      if (!Number.isNaN(tid) && _markerHoverCb) { _markerHoverCb(tid); return; }
    }
    if (_markerHoverCb) _markerHoverCb(null);
  });
}

export function onMarkerHover(cb) { _markerHoverCb = cb; }
export function emitMarkerHover(id) { if (_markerHoverCb) _markerHoverCb(id); }

// 可視レンジ内の trades のみを返す（試作 visibleTrades・chartBadge 件数の母集合）。
function _visibleTrades(rows) {
  if (!_chart) return rows || [];
  const r = _chart.timeScale().getVisibleRange();
  if (!r) return rows || [];
  return (rows || []).filter((t) => t.exit_time >= r.from && t.entry_time <= r.to);
}

// 売買マーカーを描画し、点7 chartBadge に可視取引件数を表示する。
export function renderMarkers(rows, opts) {
  if (!_candle) return;
  const { hoverId = null, filter = null } = opts || {};
  let vt = _visibleTrades(rows || _rows);
  if (filter) vt = vt.filter((t) => filter.has(t.id));
  const badge = typeof document !== "undefined" ? document.getElementById("chartBadge") : null;
  if (vt.length > MARKER_CAP) {
    _candle.setMarkers([]);
    if (badge) badge.textContent = `${vt.length} trades in view — ズームインでマーカー表示 (cap ${MARKER_CAP})`;
    return;
  }
  if (badge) badge.textContent = `${vt.length} trades in view`;
  const hovering = hoverId != null;
  const mk = [];
  for (const t of vt) {
    const win = t.profit > 0, hot = t.id === hoverId, dim = hovering && !hot;
    const ecol = win ? "#26a69a" : "#ef5350";
    mk.push({
      time: t.entry_time, position: t.side === "buy" ? "belowBar" : "aboveBar",
      color: dim ? _withAlpha(ecol, DIM_ALPHA) : ecol,
      shape: t.side === "buy" ? "arrowUp" : "arrowDown",
      size: hot ? 1.4 : 1, id: "e" + t.id, text: hot ? "#" + t.id : "",
    });
    mk.push({
      time: t.exit_time, position: t.side === "buy" ? "aboveBar" : "belowBar",
      color: dim ? _withAlpha(EXIT_COLOR, DIM_ALPHA) : EXIT_COLOR,
      shape: "circle", size: hot ? 1.4 : 0.6, id: "x" + t.id,
    });
  }
  mk.sort((a, b) => a.time - b.time);
  _candle.setMarkers(mk);
}

// ペア[entry,exit]区間外のローソク足を減光する（試作 dimCandlesForTrade）。
export function dimCandlesForTrade(t) {
  if (!_candle) return;
  if (!t || t.entry_price == null) { restoreCandles(); return; }
  const lo = _bisectLeft(_barTimes, t.entry_time), hi = _bisectLeft(_barTimes, t.exit_time + 1);
  const merged = _barsDim.slice();
  for (let i = lo; i < hi; i++) merged[i] = _barsNormal[i];
  _candle.setData(merged);
  _candlesDimmed = true;
}
export function restoreCandles() {
  if (_candlesDimmed && _candle) { _candle.setData(_barsNormal); _candlesDimmed = false; }
}

// 時刻 t を中心にチャートをズームする（試作 focusTime・グラフ/ヒート/明細クリック連動）。
export function focusTime(t, span = 3 * 3600) {
  if (_chart) _chart.timeScale().setVisibleRange({ from: t - span / 2, to: t + span / 2 });
}

// 3 窓を resize する（レイアウト変更/タブ遷移時に main/layout が呼ぶ）。
export function resizeChart() {
  for (const c of [_chart, _balChart, _ddChart]) { if (c) { try { c.resize ? c.resize() : c.applyOptions({}); } catch (e) { /* noop */ } } }
}

export function currentRows() { return _rows; }
