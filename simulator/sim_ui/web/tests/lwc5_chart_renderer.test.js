// lwc5_chart_renderer（lightweight-charts v5.2.0 アダプタ・F-3）の単体テスト（fake lwc）。
//
// 本スイートが固定するのは **v5 のどの API 名を叩くか**と、**表示規則を移植元から受け取って
// いるか**の 2 点だけである。表示規則そのものの正しさは移植元 report_ui の 12 本が被覆する
// （sim 側に純ロジックの再テストを置かない・複製 0 の方針）。
//
// v5 置換範囲（vendor 実測 2026-08-11・v5.2.0）:
//   addCandlestickSeries → addSeries(CandlestickSeries, …)
//   addAreaSeries        → addSeries(AreaSeries, …)
//   addBaselineSeries    → addSeries(BaselineSeries, …)
//   series.setMarkers    → createSeriesMarkers(series, markers) が返すハンドル
// fake lwc は v4 のメソッドを**持たない**ので、v4 を呼べば TypeError で落ちる。
import { test } from "node:test";
import assert from "node:assert/strict";

import { fakeDoc, fakeLwc } from "./_fakes.js";
import { createLwc5ChartRenderer } from "../js/adapter/front/lwc5_chart_renderer.js";

/** マイクロタスク境界まで待つ（遅延通知の観測点）。 */
const flush = () => new Promise((resolve) => queueMicrotask(resolve));

// 移植元 chart.js の純関数群（本来は /sim/report-js/chart.js から合成根が注入する）を
// 検定用に最小実装で代替する。ここで確かめたいのは「注入したものを使うか」であって、
// 純関数の中身ではない。
function fakeLogic(calls = {}) {
  return {
    DIM_ALPHA: 0.15,
    MARKER_CAP: 700,
    DEFAULT_DEPOSIT: 10000,
    balanceForwardFill(barTimes, curve, init) {
      calls.balanceForwardFill = { barTimes, curve, init };
      return {
        balData: barTimes.map((t) => ({ time: t, value: 10000 })),
        ddData: barTimes.map((t) => ({ time: t, value: 0 })),
      };
    },
    byTimeResolve(series) {
      return new Map((series || []).map((p) => [p.time, p.value]));
    },
    buildTradeMarkers(trades, hoverId) {
      calls.buildTradeMarkers = { trades, hoverId };
      return (trades || []).map((t) => ({ time: t.entry_time, id: "e" + t.id }));
    },
    buildDimBars(bars) {
      calls.buildDimBars = bars;
      return (bars || []).map((b) => ({ ...b, color: "dim" }));
    },
    mergeDimBarsForTrade(barTimes, normal, dim, trade) {
      calls.mergeDimBarsForTrade = { barTimes, normal, dim, trade };
      return [{ merged: true }];
    },
    visibleTradesInRange(rows, range) {
      calls.visibleTradesInRange = { rows, range };
      return rows || [];
    },
    chartBadgeText(n) {
      calls.chartBadgeText = n;
      return `${n} trades in view`;
    },
  };
}

const SEGMENT = {
  bars: [
    { time: 100, open: 10, high: 12, low: 9, close: 11 },
    { time: 200, open: 11, high: 13, low: 10, close: 12 },
  ],
  trades: [
    { id: 1, side: "buy", profit: 10, entry_time: 100, exit_time: 200, entry_price: 10 },
  ],
  agg: { balance_curve: [{ time: 200, value: 10010 }] },
  meta: { initial_deposit: 10000 },
};

function build(extraCalls) {
  const calls = extraCalls || {};
  const doc = fakeDoc();
  const lwc = fakeLwc();
  const hosts = {
    chart: doc.createElement("div"),
    bal: doc.createElement("div"),
    dd: doc.createElement("div"),
    badge: doc.createElement("div"),
  };
  const logic = fakeLogic(calls);
  const renderer = createLwc5ChartRenderer({ lwc, hosts, logic });
  return { lwc, hosts, renderer, logic, calls };
}

// --- v5 API の使用（置換範囲）----------------------------------------------------

test("render creates three charts (price / balance / drawdown) — 点1,2", () => {
  const { lwc, renderer } = build();
  renderer.render(SEGMENT);
  assert.equal(lwc.charts.length, 3);
});

test("render attaches each chart to the host element it was given", () => {
  const { lwc, hosts, renderer } = build();
  renderer.render(SEGMENT);
  assert.deepEqual(lwc.charts.map((c) => c.container), [hosts.chart, hosts.bal, hosts.dd]);
});

test("render uses addSeries(CandlestickSeries) — v5 置換（v4 addCandlestickSeries は不在）", () => {
  const { lwc, renderer } = build();
  renderer.render(SEGMENT);
  assert.equal(lwc.charts[0].series[0].kind, "CandlestickSeries");
});

test("render uses addSeries(AreaSeries) for the balance pane — 点1", () => {
  const { lwc, renderer } = build();
  renderer.render(SEGMENT);
  assert.equal(lwc.charts[1].series[0].kind, "AreaSeries");
});

test("render uses addSeries(BaselineSeries) for the drawdown pane — 点2", () => {
  const { lwc, renderer } = build();
  renderer.render(SEGMENT);
  assert.equal(lwc.charts[2].series[0].kind, "BaselineSeries");
});

test("the baseline series is anchored at price 0 (アンダーウォーター表示)", () => {
  const { lwc, renderer } = build();
  renderer.render(SEGMENT);
  assert.deepEqual(lwc.charts[2].series[0].options.baseValue, { type: "price", price: 0 });
});

test("markers go through createSeriesMarkers, not series.setMarkers (v5 置換)", () => {
  const { lwc, renderer } = build();
  renderer.render(SEGMENT);
  assert.equal(lwc.markerHandles.length, 1);
  assert.equal(lwc.markerHandles[0].series, lwc.charts[0].series[0]);
});

test("re-rendering markers reuses the same marker handle (ハンドルを積み上げない)", () => {
  const { lwc, renderer } = build();
  renderer.render(SEGMENT);
  renderer.renderMarkers(SEGMENT.trades, { hoverId: 1 });
  renderer.renderMarkers(SEGMENT.trades, { hoverId: null });
  assert.equal(lwc.markerHandles.length, 1);
});

test("crosshair mode comes from the vendor enum (LightweightCharts.CrosshairMode)", () => {
  const { lwc, renderer } = build();
  renderer.render(SEGMENT);
  assert.equal(lwc.charts[0].options.crosshair.mode, lwc.CrosshairMode.Normal);
});

// --- 表示規則は移植元から注入されたものを使う（複製 0）---------------------------

test("candles are fed from segment.bars", () => {
  const { lwc, renderer } = build();
  renderer.render(SEGMENT);
  assert.deepEqual(lwc.charts[0].series[0].data.map((b) => b.time), [100, 200]);
});

test("balance / drawdown series come from the injected balanceForwardFill", () => {
  const calls = {};
  const { renderer } = build(calls);
  renderer.render(SEGMENT);
  assert.deepEqual(calls.balanceForwardFill.barTimes, [100, 200]);
  assert.deepEqual(calls.balanceForwardFill.curve, SEGMENT.agg.balance_curve);
  assert.equal(calls.balanceForwardFill.init, 10000);
});

test("markers come from the injected buildTradeMarkers with the hovered id", () => {
  const calls = {};
  const { renderer } = build(calls);
  renderer.render(SEGMENT);
  renderer.renderMarkers(SEGMENT.trades, { hoverId: 1 });
  assert.equal(calls.buildTradeMarkers.hoverId, 1);
});

test("the badge text comes from the injected chartBadgeText — 点7", () => {
  const { hosts, renderer } = build();
  renderer.render(SEGMENT);
  assert.equal(hosts.badge.textContent, "1 trades in view");
});

test("dimming comes from the injected mergeDimBarsForTrade — 点S4", () => {
  const calls = {};
  const { lwc, renderer } = build(calls);
  renderer.render(SEGMENT);
  renderer.dimCandlesForTrade(SEGMENT.trades[0]);
  assert.equal(calls.mergeDimBarsForTrade.trade, SEGMENT.trades[0]);
  assert.deepEqual(lwc.charts[0].series[0].data, [{ merged: true }]);
});

test("restoreCandles puts the normal bars back", () => {
  const { lwc, renderer } = build();
  renderer.render(SEGMENT);
  renderer.dimCandlesForTrade(SEGMENT.trades[0]);
  renderer.restoreCandles();
  assert.deepEqual(lwc.charts[0].series[0].data.map((b) => b.time), [100, 200]);
});

test("dimCandlesForTrade restores when the trade has no entry price", () => {
  const { lwc, renderer } = build();
  renderer.render(SEGMENT);
  renderer.dimCandlesForTrade(null);
  assert.deepEqual(lwc.charts[0].series[0].data.map((b) => b.time), [100, 200]);
});

// --- 窓の同期（点3 / 点4）--------------------------------------------------------

test("logical ranges are synced across the three panes — 点3", () => {
  const { lwc, renderer } = build();
  renderer.render(SEGMENT);
  lwc.charts[0].timeScale().emitLogicalRange({ from: 1, to: 5 });
  assert.deepEqual(lwc.charts[1].timeScale().getVisibleLogicalRange(), { from: 1, to: 5 });
  assert.deepEqual(lwc.charts[2].timeScale().getVisibleLogicalRange(), { from: 1, to: 5 });
});

test("logical range sync does not loop back onto the source pane — 点3", () => {
  const { lwc, renderer } = build();
  renderer.render(SEGMENT);
  lwc.charts[1].timeScale().emitLogicalRange({ from: 2, to: 6 });
  assert.equal(lwc.charts[1].timeScale().getVisibleLogicalRange(), null);
  assert.deepEqual(lwc.charts[0].timeScale().getVisibleLogicalRange(), { from: 2, to: 6 });
});

// 点4 の反映も**描画経路の外**で行う（R-A2）。購読ハンドラは param を読むだけで、
//   他窓への setCrosshairPosition は同期経路に置かない（vendor 再入の遮断）。
test("crosshair position is mirrored onto the other panes — 点4", async () => {
  const { lwc, renderer } = build();
  renderer.render(SEGMENT);
  lwc.charts[0].emitCrosshair({ time: 100, point: { x: 1, y: 2 } });
  await flush();
  assert.equal(lwc.charts[1].crosshairPositions.length, 1);
  assert.equal(lwc.charts[1].crosshairPositions[0].time, 100);
  assert.equal(lwc.charts[2].crosshairPositions.length, 1);
});

test("the crosshair mirror does not write inside the subscription (R-A2)", () => {
  const { lwc, renderer } = build();
  renderer.render(SEGMENT);
  lwc.charts[0].emitCrosshair({ time: 100, point: { x: 1, y: 2 } });
  assert.equal(lwc.charts[1].crosshairPositions.length, 0,
    "購読の同期経路で他窓へ書き込んでいる（vendor 再入の原因）");
});

test("leaving the chart clears the crosshair on the other panes — 点4", async () => {
  const { lwc, renderer } = build();
  renderer.render(SEGMENT);
  lwc.charts[0].emitCrosshair({ time: undefined, point: undefined });
  await flush();
  assert.equal(lwc.charts[1].crosshairCleared, 1);
  assert.equal(lwc.charts[2].crosshairCleared, 1);
});

// --- マーカー hover → 通知（点 S2 の入力側・R-A1〜R-A4）--------------------------
//
// 実測（2026-08-12・v5.2.0 実 UI）: crosshair 購読の**同期経路の中で** vendor へ書き込むと
// vendor 自身の hitTest→renderer→priceToCoordinate が再入し
// `RangeError: Maximum call stack size exceeded` になる（スタックは全フレームが vendor 内）。
// よって購読ハンドラは「読み取り＋通知」だけを行い、通知は同期経路の外へ出す（R-A2）。
// 参照実装 v4 chart.js:312-323 のハンドラも読み取り専用（lock イディオムは :186-215）。

test("hovering a marker glyph notifies the injected callback with the trade id", async () => {
  const { lwc, renderer } = build();
  const seen = [];
  renderer.onMarkerHover((id) => seen.push(id));
  renderer.render(SEGMENT);
  lwc.charts[0].emitCrosshair({ hoveredObjectId: "e7", time: 100, point: { x: 1, y: 1 } });
  await flush();
  assert.deepEqual(seen, [7]);
});

test("the crosshair handler does not notify synchronously (R-A2: vendor 描画の中で書かない)", () => {
  const { lwc, renderer } = build();
  const seen = [];
  renderer.onMarkerHover((id) => seen.push(id));
  renderer.render(SEGMENT);
  lwc.charts[0].emitCrosshair({ hoveredObjectId: "e7", time: 100, point: { x: 1, y: 1 } });
  assert.deepEqual(seen, [], "購読の同期経路で通知している（vendor 再入の原因）");
});

test("hovering the exit glyph resolves to the same trade id ('x' + id)", async () => {
  const { lwc, renderer } = build();
  const seen = [];
  renderer.onMarkerHover((id) => seen.push(id));
  renderer.render(SEGMENT);
  lwc.charts[0].emitCrosshair({ hoveredObjectId: "x7", time: 200, point: { x: 1, y: 1 } });
  await flush();
  assert.deepEqual(seen, [7]);
});

test("leaving a marker notifies null (選択解除)", async () => {
  const { lwc, renderer } = build();
  const seen = [];
  renderer.onMarkerHover((id) => seen.push(id));
  renderer.render(SEGMENT);
  lwc.charts[0].emitCrosshair({ hoveredObjectId: "e7", time: 100, point: { x: 1, y: 1 } });
  await flush();
  lwc.charts[0].emitCrosshair({ hoveredObjectId: undefined, time: 100, point: { x: 1, y: 1 } });
  await flush();
  assert.deepEqual(seen, [7, null]);
});

test("the same hover id is not notified twice (同じ通知を出し続けない)", async () => {
  const { lwc, renderer } = build();
  const seen = [];
  renderer.onMarkerHover((id) => seen.push(id));
  renderer.render(SEGMENT);
  for (let i = 0; i < 5; i++) {
    lwc.charts[0].emitCrosshair({ hoveredObjectId: "e7", time: 100, point: { x: 1, y: 1 } });
  }
  await flush();
  assert.deepEqual(seen, [7]);
});

test("no hover is notified while the renderer is writing to the vendor (R-A3 lock)", async () => {
  const { lwc, renderer } = build();
  const seen = [];
  renderer.onMarkerHover((id) => seen.push(id));
  renderer.render(SEGMENT);
  // 描画呼出の最中に届いた crosshair は**プログラム由来の偽入力**として発行元で捨てる。
  const chart = lwc.charts[0];
  chart.series[0].setData = function (d) {
    this.data = d;
    chart.emitCrosshair({ hoveredObjectId: "e7", time: 100, point: { x: 1, y: 1 } });
  };
  renderer.dimCandlesForTrade(SEGMENT.trades[0]);
  await flush();
  assert.deepEqual(seen, [], "自分の描画中に発火した crosshair を通知している");
});

test("the price chart keeps exactly one marker-hover subscription (R-A1)", () => {
  const { lwc, renderer } = build();
  renderer.render(SEGMENT);
  const before = lwc.charts[0].crosshairSubs.length;
  renderer.renderMarkers(SEGMENT.trades, { hoverId: 1 });
  renderer.dimCandlesForTrade(SEGMENT.trades[0]);
  renderer.restoreCandles();
  renderer.focusTime(150);
  assert.equal(lwc.charts[0].crosshairSubs.length, before, "描画のたびに購読が増えている");
});

test("re-rendering does not stack subscriptions on the new chart (R-A1)", () => {
  const { lwc, renderer } = build();
  renderer.render(SEGMENT);
  const first = lwc.charts[0].crosshairSubs.length;
  renderer.render(SEGMENT);
  assert.equal(lwc.charts[3].crosshairSubs.length, first);
});

// --- 破棄（モード往復で積み上がらない）------------------------------------------

test("destroy removes every chart it created", () => {
  const { lwc, renderer } = build();
  renderer.render(SEGMENT);
  renderer.destroy();
  assert.deepEqual(lwc.charts.map((c) => c.removed), [true, true, true]);
});

test("rendering twice destroys the previous charts first (区間再描画で積み上げない)", () => {
  const { lwc, renderer } = build();
  renderer.render(SEGMENT);
  renderer.render(SEGMENT);
  assert.equal(lwc.charts.length, 6);
  assert.deepEqual(lwc.charts.slice(0, 3).map((c) => c.removed), [true, true, true]);
  assert.deepEqual(lwc.charts.slice(3).map((c) => c.removed), [false, false, false]);
});

test("focusTime moves the visible range around the given time", () => {
  const { lwc, renderer } = build();
  renderer.render(SEGMENT);
  renderer.focusTime(1000, 600);
  assert.deepEqual(lwc.charts[0].timeScale().getVisibleRange(), { from: 700, to: 1300 });
});

// --- E2E フック（実 UI の実測点・移植元 chart.js と同名/同流儀）--------------------
// 移植元は chart.js:307 で `window.__priceChart`、:382/:386 で `window.__candlesDimmed` を
// 公開しており、report_ui の verify_*.py はそれを実測点に使っている。sim 表示層も同じ
// 実測点を出す（出さないと「ブラウザで確かに減光した」を実測する手段が無くなる）。
// 名前は `__candlesDimmed` を移植元と**共有**する（二画面突合で同じ式を当てるため）。

/** window ダブルを差し込んで fn を実行し、必ず元へ戻す（F.I.R.S.T Independent）。 */
function withWindow(fn) {
  const had = Object.prototype.hasOwnProperty.call(globalThis, "window");
  const prev = globalThis.window;
  globalThis.window = {};
  try {
    return fn(globalThis.window);
  } finally {
    if (had) globalThis.window = prev;
    else delete globalThis.window;
  }
}

test("render publishes the price chart and candle series as E2E hooks", () => {
  withWindow((win) => {
    const { lwc, renderer } = build();
    renderer.render(SEGMENT);
    assert.equal(win.__simPriceChart, lwc.charts[0]);
    assert.equal(win.__simCandleSeries, lwc.charts[0].series[0]);
  });
});

test("dimCandlesForTrade raises the __candlesDimmed hook — 点S4 の実測点", () => {
  withWindow((win) => {
    const { renderer } = build();
    renderer.render(SEGMENT);
    assert.equal(win.__candlesDimmed, false);
    renderer.dimCandlesForTrade(SEGMENT.trades[0]);
    assert.equal(win.__candlesDimmed, true);
  });
});

test("restoreCandles lowers the __candlesDimmed hook", () => {
  withWindow((win) => {
    const { renderer } = build();
    renderer.render(SEGMENT);
    renderer.dimCandlesForTrade(SEGMENT.trades[0]);
    renderer.restoreCandles();
    assert.equal(win.__candlesDimmed, false);
  });
});

test("destroy clears the chart hooks (畳んだ後の残骸を実測させない)", () => {
  withWindow((win) => {
    const { renderer } = build();
    renderer.render(SEGMENT);
    renderer.destroy();
    assert.equal(win.__simPriceChart, null);
    assert.equal(win.__simCandleSeries, null);
  });
});

test("emitMarkerHover drives the registered callback (マーカー画素 hover の代理)", () => {
  const { renderer } = build();
  const seen = [];
  renderer.onMarkerHover((id) => seen.push(id));
  renderer.render(SEGMENT);
  renderer.emitMarkerHover(3);
  renderer.emitMarkerHover(null);
  assert.deepEqual(seen, [3, null]);
});

test("emitMarkerHover is safe without a registered callback", () => {
  const { renderer } = build();
  assert.doesNotThrow(() => renderer.emitMarkerHover(1));
});

test("the renderer works without a window (node/E2E 非依存)", () => {
  assert.equal(typeof globalThis.window, "undefined");
  const { renderer } = build();
  assert.doesNotThrow(() => {
    renderer.render(SEGMENT);
    renderer.dimCandlesForTrade(SEGMENT.trades[0]);
    renderer.restoreCandles();
    renderer.destroy();
  });
});

test("renderer methods are safe to call before render (呼び出し順に依存しない)", () => {
  const { renderer } = build();
  assert.doesNotThrow(() => {
    renderer.renderMarkers([], { hoverId: null });
    renderer.dimCandlesForTrade(SEGMENT.trades[0]);
    renderer.restoreCandles();
    renderer.focusTime(1);
    renderer.resize();
    renderer.destroy();
  });
});
