// 価格チャート（ローソク足）描画（詳細設計 §11.1 chart.js・F-1 最小サブセット）。
// vendor lightweight-charts v4.1.3 の createChart / addCandlestickSeries を使用する。

let _chart = null;
let _series = null;
let _markerHoverCb = null; // chart→linkage 通知のコールバック注入（直接 import を作らない）
let _rows = []; // 直近の trades 行（マーカー再描画用）
const DIM_ALPHA = 0.18; // 非 hover ペアの減光アルファ（entry/exit マーカー共通）
const MARKER_CAP = 700;
const EXIT_COLOR = "#6b7785"; // 決済マーカーの基準色（減光時は DIM_ALPHA を適用）

function _withAlpha(hex, a) {
  const n = parseInt(hex.slice(1), 16);
  const r = (n >> 16) & 255, g = (n >> 8) & 255, b = n & 255;
  return `rgba(${r},${g},${b},${a})`;
}

function _ensureChart(container) {
  if (_chart) return;
  _chart = LightweightCharts.createChart(container, {
    layout: { background: { color: "#161b22" }, textColor: "#e6edf3" },
    grid: { vertLines: { color: "#21262d" }, horzLines: { color: "#21262d" } },
    timeScale: { timeVisible: true, secondsVisible: false },
    autoSize: true,
  });
  _series = _chart.addCandlestickSeries({
    upColor: "#2ea043", downColor: "#f85149",
    borderVisible: false, wickUpColor: "#2ea043", wickDownColor: "#f85149",
  });
}

// segment.bars（[{time,open,high,low,close}]）をローソク足へ流す。
export function renderChart(containerId, segment) {
  const container = document.getElementById(containerId);
  if (!container) return;
  _ensureChart(container);
  const bars = (segment.bars || []).map((b) => ({
    time: b.time, open: b.open, high: b.high, low: b.low, close: b.close,
  }));
  _series.setData(bars);
  _rows = segment.trades || [];
  _chart.timeScale().fitContent();
  _ensureCrosshair();
  renderMarkers(_rows, { hoverId: null, filter: null });
}

// crosshair でマーカーグリフ命中時に注入コールバックへ trade id を通知（chart→linkage 直接 import なし）。
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

// マーカー hover 通知の購読登録（main.js が linkage.setHover を注入。直接 import を作らない＝参照方向制御）。
export function onMarkerHover(cb) {
  _markerHoverCb = cb;
}

// テスト用: 登録済みマーカー hover コールバックを id で駆動する（crosshair グリフ命中の代理）。
// 実ブラウザのマーカー画素 hover は不安定なため、E2E は本経路で chart→linkage 結線を検証する。
export function emitMarkerHover(id) {
  if (_markerHoverCb) _markerHoverCb(id);
}

// 売買マーカーを描画。hover 中は当該ペアを通常色・大きく、非ペアを減光する。
export function renderMarkers(rows, opts) {
  if (!_series) return;
  const { hoverId = null, filter = null } = opts || {};
  let vt = rows || [];
  if (filter) vt = vt.filter((t) => filter.has(t.id));
  if (vt.length > MARKER_CAP) { _series.setMarkers([]); return; }
  const hovering = hoverId != null;
  const mk = [];
  for (const t of vt) {
    const win = t.profit > 0;
    const hot = t.id === hoverId;
    const dim = hovering && !hot;
    const ecol = win ? "#26a69a" : "#ef5350";
    mk.push({
      time: t.entry_time,
      position: t.side === "buy" ? "belowBar" : "aboveBar",
      color: dim ? _withAlpha(ecol, DIM_ALPHA) : ecol,
      shape: t.side === "buy" ? "arrowUp" : "arrowDown",
      size: hot ? 1.4 : 1, id: "e" + t.id, text: hot ? "#" + t.id : "",
    });
    mk.push({
      time: t.exit_time,
      position: t.side === "buy" ? "aboveBar" : "belowBar",
      color: dim ? _withAlpha(EXIT_COLOR, DIM_ALPHA) : EXIT_COLOR,
      shape: "circle", size: hot ? 1.4 : 0.6, id: "x" + t.id,
    });
  }
  mk.sort((a, b) => a.time - b.time);
  _series.setMarkers(mk);
}

// 直近の trades 行を返す（main.js の hover 購読が再描画に使用）。
export function currentRows() {
  return _rows;
}
