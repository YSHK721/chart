// 価格チャート（ローソク足）描画（詳細設計 §11.1 chart.js・F-1 最小サブセット）。
// vendor lightweight-charts v4.1.3 の createChart / addCandlestickSeries を使用する。

let _chart = null;
let _series = null;

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
  _chart.timeScale().fitContent();
}
