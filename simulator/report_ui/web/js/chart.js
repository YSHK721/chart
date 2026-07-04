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
// 接点マーカー専用系列（透明ライン）。売買マーカーと同一系列に setMarkers すると v4.1.3 は
// シリーズ単位で上書きになるため、接点は別系列へ分離して独立トグルを成立させる。
let _contactSeries = null, _contacts = [], _contactsVisible = true;
let _markerHoverCb = null; // chart→linkage 通知のコールバック注入（直接 import を作らない）
let _rows = []; // 直近の trades 行（マーカー再描画用）
let _barTimes = [], _barsNormal = [], _barsDim = [], _candlesDimmed = false;
const DIM_ALPHA = 0.15; // 非 hover ペアの減光アルファ（試作 DIM_ALPHA=0.15）
const MARKER_CAP = 700;
const EXIT_COLOR = "#6b7785";
const DEFAULT_DEPOSIT = 10000;

// 接点マーカー（コンタクトスキャン）配色・上限。売買マーカー（買=#26a69a 緑 / 売=#ef5350 赤）
// と区別できる別配色を用いる（up=琥珀 / down=藤）。cap は売買 MARKER_CAP と同流儀の可視間引き。
export const CONTACT_UP_COLOR = "#f5c542";   // 下→上クロス（arrowUp・belowBar）
export const CONTACT_DOWN_COLOR = "#c084fc"; // 上→下クロス（arrowDown・aboveBar）
export const CONTACT_MARKER_CAP = 700;

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

// 接点 1 件 {time, price, dir} を lwc マーカー 1 件へ変換する（up/down で shape/position/color 分離）。
// dir==="up"（下→上）: arrowUp を belowBar に。dir==="down"（上→下）: arrowDown を aboveBar に。
export function contactToMarker(c, idx) {
  const up = c.dir === "up";
  return {
    time: c.time,
    position: up ? "belowBar" : "aboveBar",
    color: up ? CONTACT_UP_COLOR : CONTACT_DOWN_COLOR,
    shape: up ? "arrowUp" : "arrowDown",
    id: "c" + idx,
  };
}

// 接点を可視レンジ [from,to] に絞る（cap 適用の前段）。売買マーカーの _visibleTrades と同流儀。
//   全件が cap を超えても、ズームイン後の可視件数が cap 以下なら表示できる（恒常非表示の回避）。
//   range が null（レンジ未確定）のときは全件を返す（防御的）。
export function contactsInRange(contacts, range) {
  if (!range) return contacts || [];
  return (contacts || []).filter((c) => c.time >= range.from && c.time <= range.to);
}

// agg.contacts[{time,price,dir}] を lwc マーカー配列へ変換する（time 昇順 sort・cap 間引き・トグル）。
//   opts.visible=false（トグル OFF）→ []。件数 > cap → [](売買 setMarkers と同流儀の可視間引き)。
//   売買マーカーとは別系列へ setMarkers するため、本配列は接点専用（統合しない・独立トグル要件）。
export function contactsToMarkers(contacts, opts) {
  const { visible = true, cap = CONTACT_MARKER_CAP } = opts || {};
  if (!visible) return [];
  const list = (contacts || []).slice().sort((a, b) => a.time - b.time);
  if (list.length > cap) return [];
  return list.map((c, i) => contactToMarker(c, i));
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
  _contactSeries = null;
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

  // 接点マーカー専用の透明ライン系列（バー close 上に重ねる・線は不可視）。売買マーカーの
  // setMarkers（_candle 系列）とは別系列にして独立トグルを成立させる（v4.1.3 setMarkers は
  // シリーズ単位＝同一系列だと上書きになるため分離）。接点データは agg.contacts から取得。
  _contactSeries = _chart.addLineSeries({
    color: "rgba(0,0,0,0)", lineWidth: 1,
    priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false,
  });
  _contactSeries.setData(_barsNormal.map((b) => ({ time: b.time, value: b.close })));
  _contacts = (segment.agg && segment.agg.contacts) || [];

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
  // 可視レンジ変更（pan/zoom・focusTime 含む）でマーカーを再描画する。直近の描画意図
  // （hoverId/filter）を保持して再描画し、focusTime のズームで選択ハイライトが消えないようにする。
  _chart.timeScale().subscribeVisibleTimeRangeChange(() => {
    renderMarkers(_rows, _lastMarkerOpts);
    renderContactMarkers(); // 接点も可視レンジに追従して再描画（pan/zoom で cap 内なら表示）
  });
  _ensureCrosshair();
  renderMarkers(_rows, { hoverId: null, filter: null });
  renderContactMarkers(); // 接点マーカー（別系列・現在のトグル state を反映）
  // E2E フック（行クリック→focusTime の可視レンジ移動を検証するため・本番表示には不使用）。
  if (typeof window !== "undefined") window.__priceChart = _chart;
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

// 直近の描画意図（hoverId/filter）。可視レンジ変更時の再描画で選択/抽出状態を保持する。
let _lastMarkerOpts = { hoverId: null, filter: null };

// 売買マーカーを描画し、点7 chartBadge に可視取引件数を表示する。
export function renderMarkers(rows, opts) {
  if (!_candle) return;
  const { hoverId = null, filter = null } = opts || {};
  _lastMarkerOpts = { hoverId, filter }; // pan/zoom 再描画で再利用
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

// 可視レンジ内の接点のみを返す（売買 _visibleTrades と同流儀・全件 cap 超過時の恒常非表示回避）。
function _visibleContacts() {
  if (!_chart) return _contacts;
  return contactsInRange(_contacts, _chart.timeScale().getVisibleRange());
}

// 接点マーカーを接点専用系列へ描画する（可視レンジ絞り→cap→トグル・売買 setMarkers とは分離）。
export function renderContactMarkers() {
  if (!_contactSeries) return;
  _contactSeries.setMarkers(
    contactsToMarkers(_visibleContacts(), { visible: _contactsVisible }));
}

// 接点マーカーの表示/非表示を切り替える（トグル UI から結線）。既定は表示。
export function setContactsVisible(visible) {
  _contactsVisible = !!visible;
  renderContactMarkers();
  return _contactsVisible;
}

// 現在の接点トグル state（表示中=true）を返す。
export function contactsVisible() { return _contactsVisible; }

// ペア[entry,exit]区間外のローソク足を減光する（試作 dimCandlesForTrade）。
export function dimCandlesForTrade(t) {
  if (!_candle) return;
  if (!t || t.entry_price == null) { restoreCandles(); return; }
  const lo = _bisectLeft(_barTimes, t.entry_time), hi = _bisectLeft(_barTimes, t.exit_time + 1);
  const merged = _barsDim.slice();
  for (let i = lo; i < hi; i++) merged[i] = _barsNormal[i];
  _candle.setData(merged);
  _candlesDimmed = true;
  if (typeof window !== "undefined") window.__candlesDimmed = true; // E2E フック
}
export function restoreCandles() {
  if (_candlesDimmed && _candle) { _candle.setData(_barsNormal); _candlesDimmed = false; }
  if (typeof window !== "undefined") window.__candlesDimmed = false; // E2E フック
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
