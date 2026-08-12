// sim 表示層の検定で共有するテストダブル（fake DOM / fake lightweight-charts v5）。
//
// jsdom 等は導入しない（ライブラリ追加禁止・移植元 report_ui のテスト流儀に合わせる）。
// ここで固定したいのは「どの DOM を作るか」「v5 の**どの API 名**を叩くか」であり、
// 実ブラウザの描画結果ではない（実 UI の実測は e2e の責務）。

/** 最小の DOM 要素ダブル（子を保持し、id で引ける）。 */
export function fakeEl(tag = "div") {
  return {
    tagName: String(tag).toUpperCase(),
    id: "",
    className: "",
    textContent: "",
    innerHTML: "",
    href: "",
    rel: "",
    style: {},
    dataset: {},
    children: [],
    parent: null,
    _listeners: {},
    classList: {
      _set: new Set(),
      add(c) { this._set.add(c); },
      remove(c) { this._set.delete(c); },
      contains(c) { return this._set.has(c); },
      toggle(c, on) { if (on === undefined) { this._set.has(c) ? this._set.delete(c) : this._set.add(c); } else if (on) { this._set.add(c); } else { this._set.delete(c); } },
    },
    appendChild(child) { child.parent = this; this.children.push(child); return child; },
    removeChild(child) {
      const i = this.children.indexOf(child);
      if (i >= 0) { this.children.splice(i, 1); child.parent = null; }
      return child;
    },
    addEventListener(ev, fn) { (this._listeners[ev] ||= []).push(fn); },
    querySelector() { return null; },
    querySelectorAll() { return []; },
  };
}

/** 部分木を走査して id 一致の要素を返す（fake DOM 版 getElementById）。 */
export function findById(root, id) {
  if (!root) return null;
  if (root.id === id) return root;
  for (const c of root.children || []) {
    const hit = findById(c, id);
    if (hit) return hit;
  }
  return null;
}

/** 部分木の全要素を列挙する。 */
export function flatten(root) {
  const out = [];
  const walk = (el) => { out.push(el); (el.children || []).forEach(walk); };
  if (root) walk(root);
  return out;
}

/** document ダブル（createElement / head / body）。 */
export function fakeDoc() {
  const head = fakeEl("head");
  const body = fakeEl("body");
  return {
    head,
    body,
    createElement: (tag) => fakeEl(tag),
    createDocumentFragment: () => fakeEl("fragment"),
    getElementById: () => null,
    querySelector: () => null,
    querySelectorAll: () => [],
  };
}

// --- lightweight-charts v5.2.0 のダブル -----------------------------------------
// v5 の実 API 名（vendor 実測: AreaSeries / BaselineSeries / CandlestickSeries /
// addSeries / createSeriesMarkers / CrosshairMode）だけを提供する。v4 の
// addCandlestickSeries / addAreaSeries / addBaselineSeries / series.setMarkers は
// **わざと生やさない**——アダプタが v4 を呼んでいれば TypeError で落ちる。

/** 系列ダブル（setData / applyOptions の呼び出しを記録する）。 */
export function fakeSeries(kind) {
  return {
    kind,
    data: null,
    options: null,
    setData(d) { this.data = d; },
    applyOptions(o) { this.options = { ...(this.options || {}), ...o }; },
  };
}

/** timeScale ダブル。 */
function fakeTimeScale() {
  return {
    _visibleRange: null,
    _logicalRange: null,
    _rangeSubs: [],
    _logicalSubs: [],
    setVisibleRange(r) { this._visibleRange = r; this._rangeSubs.forEach((f) => f(r)); },
    getVisibleRange() { return this._visibleRange; },
    setVisibleLogicalRange(r) { this._logicalRange = r; },
    getVisibleLogicalRange() { return this._logicalRange; },
    subscribeVisibleTimeRangeChange(fn) { this._rangeSubs.push(fn); },
    subscribeVisibleLogicalRangeChange(fn) { this._logicalSubs.push(fn); },
    emitLogicalRange(r) { this._logicalSubs.forEach((f) => f(r)); },
  };
}

/** chart ダブル（addSeries / subscribeCrosshairMove / timeScale / remove）。 */
export function fakeChart(container, options) {
  const ts = fakeTimeScale();
  return {
    container,
    options,
    series: [],
    removed: false,
    crosshairSubs: [],
    crosshairPositions: [],
    crosshairCleared: 0,
    resized: 0,
    addSeries(type, opts) {
      const s = fakeSeries(type && type.__name);
      s.options = opts || null;
      this.series.push(s);
      return s;
    },
    timeScale() { return ts; },
    subscribeCrosshairMove(fn) { this.crosshairSubs.push(fn); },
    emitCrosshair(param) { this.crosshairSubs.forEach((f) => f(param)); },
    setCrosshairPosition(value, time, series) { this.crosshairPositions.push({ value, time, series }); },
    clearCrosshairPosition() { this.crosshairCleared += 1; },
    resize() { this.resized += 1; },
    applyOptions() {},
    remove() { this.removed = true; },
  };
}

/** マーカーハンドルのダブル（v5 の createSeriesMarkers 戻り）。 */
export function fakeMarkerHandle(series, markers) {
  return {
    series,
    markers: markers || [],
    setMarkers(m) { this.markers = m; },
    getMarkers() { return this.markers; },
  };
}

/** lightweight-charts v5.2.0 名前空間のダブル。 */
export function fakeLwc() {
  const lwc = {
    charts: [],
    markerHandles: [],
    AreaSeries: { __name: "AreaSeries" },
    BaselineSeries: { __name: "BaselineSeries" },
    CandlestickSeries: { __name: "CandlestickSeries" },
    LineSeries: { __name: "LineSeries" },
    HistogramSeries: { __name: "HistogramSeries" },
    CrosshairMode: { Normal: 0, Magnet: 1 },
    createChart(container, options) {
      const c = fakeChart(container, options);
      lwc.charts.push(c);
      return c;
    },
    createSeriesMarkers(series, markers) {
      const h = fakeMarkerHandle(series, markers);
      lwc.markerHandles.push(h);
      return h;
    },
    version() { return "5.2.0"; },
  };
  return lwc;
}
