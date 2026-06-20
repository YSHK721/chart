// trade_markers_renderer.js（TradeMarkersRenderer・lwc createSeriesMarkers 隔離点）の仕様検証。
//
// 設計入力: CHART_TRADE_MARKERS_DETAILED_DESIGN.md §3.1（setMarkers/clear/load・ハンドル方式・
//   失敗時 warn+0・lwc サブセット抽出）、CHART_TRADE_MARKERS_BASIC_DESIGN.md §12.5（C-3 v5 ハンドル）。
// 構造: Arrange-Act-Assert（AAA）。upstream lwc.createSeriesMarkers と fetch は Fake を注入。

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { TradeMarkersRenderer } from '../js/adapter/front/trade_markers_renderer.js';

// Fake lwc.createSeriesMarkers — handle を 1 つ返し、setMarkers 呼び出しを記録する。
function fakeLwc() {
  const calls = { create: [], setMarkers: [] };
  const handle = {
    setMarkers(markers) { calls.setMarkers.push(markers); },
  };
  return {
    calls,
    handle,
    createSeriesMarkers(series, markers) {
      calls.create.push({ series, markers });
      return handle;
    },
  };
}

function muteConsole() {
  const orig = { warn: console.warn, info: console.info };
  const seen = { warn: [], info: [] };
  console.warn = (...a) => seen.warn.push(a);
  console.info = (...a) => seen.info.push(a);
  return {
    seen,
    restore() { console.warn = orig.warn; console.info = orig.info; },
  };
}

test('setMarkers creates the lwc marker handle once on first call', () => {
  // Arrange
  const lwc = fakeLwc();
  const mainSeries = { id: 'main' };
  const r = new TradeMarkersRenderer({ lwc, mainSeries });
  const markers = [{ time: 1, position: 'belowBar', shape: 'arrowUp', color: '#26a69a', text: 'BUY' }];
  // Act
  r.setMarkers(markers);
  // Assert: createSeriesMarkers(mainSeries, markers) が 1 回呼ばれる
  assert.equal(lwc.calls.create.length, 1);
  assert.equal(lwc.calls.create[0].series, mainSeries);
  assert.deepEqual(lwc.calls.create[0].markers, markers);
});

test('setMarkers reuses the existing handle on subsequent calls (handle method, not re-create)', () => {
  // Arrange
  const lwc = fakeLwc();
  const r = new TradeMarkersRenderer({ lwc, mainSeries: {} });
  // Act
  r.setMarkers([{ time: 1 }]);
  r.setMarkers([{ time: 2 }]);
  // Assert: 2 回目は handle.setMarkers（再 create しない）
  assert.equal(lwc.calls.create.length, 1);
  assert.equal(lwc.calls.setMarkers.length, 1);
  assert.deepEqual(lwc.calls.setMarkers[0], [{ time: 2 }]);
});

test('clear empties the markers via the handle when a handle exists', () => {
  // Arrange
  const lwc = fakeLwc();
  const r = new TradeMarkersRenderer({ lwc, mainSeries: {} });
  r.setMarkers([{ time: 1 }]);
  // Act
  r.clear();
  // Assert: handle.setMarkers([]) で空配列
  assert.deepEqual(lwc.calls.setMarkers.at(-1), []);
});

test('clear is a no-op when no handle has been created yet', () => {
  // Arrange
  const lwc = fakeLwc();
  const r = new TradeMarkersRenderer({ lwc, mainSeries: {} });
  // Act
  r.clear();
  // Assert: handle 未生成なら create も setMarkers も呼ばれない
  assert.equal(lwc.calls.create.length, 0);
  assert.equal(lwc.calls.setMarkers.length, 0);
});

test('load extracts the lwc subset from json.markers and passes it to setMarkers', async () => {
  // Arrange
  const lwc = fakeLwc();
  const r = new TradeMarkersRenderer({ lwc, mainSeries: {} });
  const json = {
    ok: true, count: 2,
    markers: [
      { lwc: { time: 1, position: 'belowBar', shape: 'arrowUp', color: '#26a69a', text: 'BUY' }, meta: { kind: 'entry', side: 'buy' } },
      { lwc: { time: 2, position: 'aboveBar', shape: 'circle', color: '#26a69a', text: 'TP' }, meta: { kind: 'exit', side: 'buy' } },
    ],
  };
  const fakeFetch = async () => ({ ok: true, async json() { return json; } });
  const m = muteConsole();
  // Act
  let count;
  try { count = await r.load('/data/trade_markers.json', fakeFetch); } finally { m.restore(); }
  // Assert: lwc サブセットのみ（meta 除外）を createSeriesMarkers へ渡す（M-2）
  assert.equal(count, 2);
  assert.deepEqual(lwc.calls.create[0].markers, [json.markers[0].lwc, json.markers[1].lwc]);
});

test('load returns 0 and warns without throwing when fetch response is not ok', async () => {
  // Arrange
  const lwc = fakeLwc();
  const r = new TradeMarkersRenderer({ lwc, mainSeries: {} });
  const fakeFetch = async () => ({ ok: false, status: 404, async json() { return {}; } });
  const m = muteConsole();
  // Act
  let count;
  try { count = await r.load('/data/trade_markers.json', fakeFetch); } finally { m.restore(); }
  // Assert: warn + 0 件・例外を伝播しない（M-3・candles 非干渉）
  assert.equal(count, 0);
  assert.equal(lwc.calls.create.length, 0);
  assert.equal(m.seen.warn.length, 1);
});

test('load returns 0 and warns without throwing when fetch rejects', async () => {
  // Arrange
  const lwc = fakeLwc();
  const r = new TradeMarkersRenderer({ lwc, mainSeries: {} });
  const fakeFetch = async () => { throw new TypeError('Failed to fetch'); };
  const m = muteConsole();
  // Act
  let count;
  try { count = await r.load('/x', fakeFetch); } finally { m.restore(); }
  // Assert
  assert.equal(count, 0);
  assert.equal(lwc.calls.create.length, 0);
  assert.equal(m.seen.warn.length, 1);
});

// ── Fix v3（§9）: 可視範囲内マーカーのみ描画 ──────────────────────────────
// 設計入力: CHART_TRADE_MARKERS_DETAILED_DESIGN.md §9（左端クランプ列の除去）。
//   constructor に chart（任意）を追加し chart.timeScale().subscribeVisibleTimeRangeChange を購読、
//   load() は全件を内部保持し from<=time<=to のマーカーのみ setMarkers。range=null は空。
//   購読 API 非提供（後方互換）の chart / chart 無しは throw せず全件フォールバック。

// Fake chart — subscribeVisibleTimeRangeChange に渡されたコールバックを捕捉し、
//   テストから任意の range で発火できるようにする（範囲変更の再適用を駆動するため）。
function fakeChart() {
  const cbs = [];
  return {
    cbs,
    emit(range) { cbs.forEach((cb) => cb(range)); },
    timeScale() {
      return {
        subscribeVisibleTimeRangeChange(cb) { cbs.push(cb); },
      };
    },
  };
}

// json.markers 形（lwc サブセット + meta）を time 配列から組み立てる補助。
function markersJson(times) {
  return {
    ok: true,
    count: times.length,
    markers: times.map((t) => ({ lwc: { time: t, position: 'belowBar', shape: 'arrowUp', color: '#26a69a', text: `T${t}` }, meta: {} })),
  };
}

test('Fix v3: constructor accepts an omitted chart without throwing (backward compatible)', () => {
  // Arrange + Act + Assert: chart 省略の既存呼出が壊れない（後方互換）。
  const lwc = fakeLwc();
  assert.doesNotThrow(() => new TradeMarkersRenderer({ lwc, mainSeries: {} }));
});

test('Fix v3: with no visible range yet (initial), no markers are applied (range null → empty)', async () => {
  // Arrange: chart は購読 API を持つが、まだ範囲変更を発火していない（初期 range=null 相当）。
  const lwc = fakeLwc();
  const chart = fakeChart();
  const r = new TradeMarkersRenderer({ lwc, mainSeries: {}, chart });
  const fakeFetch = async () => ({ ok: true, async json() { return markersJson([10, 20, 30]); } });
  const m = muteConsole();
  // Act: load は全件を内部保持するが、可視範囲未確定なので適用は空。
  try { await r.load('/data/trade_markers.json', fakeFetch); } finally { m.restore(); }
  // Assert: 初期（range 未発火）は空配列で適用（左端クランプ列を出さない）。
  assert.deepEqual(lwc.calls.create[0].markers, []);
});

test('Fix v3: applies only markers inside the visible range (from<=time<=to), excluding outside', async () => {
  // Arrange
  const lwc = fakeLwc();
  const chart = fakeChart();
  const r = new TradeMarkersRenderer({ lwc, mainSeries: {}, chart });
  const fakeFetch = async () => ({ ok: true, async json() { return markersJson([5, 10, 20, 30, 99]); } });
  const m = muteConsole();
  // Act: load 後に可視範囲 [10, 30] を発火する。
  try {
    await r.load('/data/trade_markers.json', fakeFetch);
    chart.emit({ from: 10, to: 30 });
  } finally { m.restore(); }
  // Assert: 範囲内（10,20,30）のみ適用。範囲外（5,99）は除外。
  const applied = lwc.calls.setMarkers.at(-1) || lwc.calls.create.at(-1).markers;
  assert.deepEqual(applied.map((x) => x.time), [10, 20, 30]);
});

test('Fix v3: re-applies the in-range subset when the visible range changes (subscription callback)', async () => {
  // Arrange
  const lwc = fakeLwc();
  const chart = fakeChart();
  const r = new TradeMarkersRenderer({ lwc, mainSeries: {}, chart });
  const fakeFetch = async () => ({ ok: true, async json() { return markersJson([5, 10, 20, 30, 99]); } });
  const m = muteConsole();
  // Act: 範囲 [10,30] → 次に [0,12] へ変更（パン/ズーム/時間足切替を模す）。
  try {
    await r.load('/data/trade_markers.json', fakeFetch);
    chart.emit({ from: 10, to: 30 });
    chart.emit({ from: 0, to: 12 });
  } finally { m.restore(); }
  // Assert: 変更後の範囲 [0,12] 内（5,10）のみが再適用される。
  const applied = lwc.calls.setMarkers.at(-1);
  assert.deepEqual(applied.map((x) => x.time), [5, 10]);
});

test('Fix v3: falls back to all markers when chart lacks the subscribe API (backward compatible)', async () => {
  // Arrange: 購読 API を持たない chart（既存 fake と同型: timeScale のみ）。
  const lwc = fakeLwc();
  const chartNoSubscribe = { timeScale: () => ({ fitContent: () => {} }) };
  const r = new TradeMarkersRenderer({ lwc, mainSeries: {}, chart: chartNoSubscribe });
  const fakeFetch = async () => ({ ok: true, async json() { return markersJson([5, 10, 99]); } });
  const m = muteConsole();
  // Act
  let count;
  try { count = await r.load('/data/trade_markers.json', fakeFetch); } finally { m.restore(); }
  // Assert: throw せず全件描画（フォールバック・現行挙動）。
  assert.equal(count, 3);
  assert.deepEqual(lwc.calls.create[0].markers.map((x) => x.time), [5, 10, 99]);
});

test('Fix v3: falls back to all markers when no chart is provided at all (backward compatible)', async () => {
  // Arrange: chart 完全省略（既存 new TradeMarkersRenderer({lwc, mainSeries}) 経路）。
  const lwc = fakeLwc();
  const r = new TradeMarkersRenderer({ lwc, mainSeries: {} });
  const fakeFetch = async () => ({ ok: true, async json() { return markersJson([5, 10, 99]); } });
  const m = muteConsole();
  // Act
  let count;
  try { count = await r.load('/data/trade_markers.json', fakeFetch); } finally { m.restore(); }
  // Assert: throw せず全件描画（現行挙動の完全保存）。
  assert.equal(count, 3);
  assert.deepEqual(lwc.calls.create[0].markers.map((x) => x.time), [5, 10, 99]);
});

// ── v4（§10）: ペア線 primitive 付与・hover 減光・単一 _render 統合 ──────────
// 設計入力: CHART_TRADE_MARKERS_DETAILED_DESIGN.md §10。フェーズ2 必須条件 C1/C2/C3。
//   - C1: subscribeCrosshairMove を購読登録する（副作用非衝突はブラウザ確認＝DoD 分離）。
//   - C2: hover 減光と §9 範囲フィルタを単一 _render() に統合。setMarkers 二重発火しない。
//   - 後方互換: attachPrimitive/subscribeCrosshairMove 非提供・chart 省略でも throw しない。

// v4 用の fake mainSeries — attachPrimitive を持ち、付与された primitive を記録する。
function fakeSeriesWithPrimitive() {
  const attached = [];
  return {
    attached,
    attachPrimitive(p) { attached.push(p); },
  };
}

// v4 用の fake chart — subscribeVisibleTimeRangeChange と subscribeCrosshairMove の両購読を捕捉。
function fakeChartV4() {
  const rangeCbs = [];
  const crossCbs = [];
  return {
    rangeCbs,
    crossCbs,
    emitRange(range) { rangeCbs.forEach((cb) => cb(range)); },
    emitCross(param) { crossCbs.forEach((cb) => cb(param)); },
    subscribeCrosshairMove(cb) { crossCbs.push(cb); },
    timeScale() {
      return { subscribeVisibleTimeRangeChange(cb) { rangeCbs.push(cb); } };
    },
  };
}

// pairs を含む v4 JSON（lwc に id 付き）を組み立てる補助。
function markersJsonV4(times) {
  return {
    ok: true,
    count: times.length,
    markers: times.map((t, idx) => ({
      lwc: { time: t, position: 'belowBar', shape: 'arrowUp', color: '#26a69a', text: `T${t}`, id: `t${idx}:entry` },
      meta: { kind: 'entry', side: 'buy', pair: idx },
    })),
    pairs: times.map((t, idx) => ({
      i: idx, side: 'buy', win: true,
      entry: { time: t, price: 100 + idx }, exit: { time: t + 1, price: 130 + idx },
    })),
  };
}

test('v4: attaches a pair-lines primitive to mainSeries when attachPrimitive is available', async () => {
  // Arrange
  const lwc = fakeLwc();
  const mainSeries = fakeSeriesWithPrimitive();
  const chart = fakeChartV4();
  const r = new TradeMarkersRenderer({ lwc, mainSeries, chart });
  const fakeFetch = async () => ({ ok: true, async json() { return markersJsonV4([10, 20]); } });
  const m = muteConsole();
  // Act
  try { await r.load('/data/trade_markers.json', fakeFetch); } finally { m.restore(); }
  // Assert: ペア線 primitive が付与される（v5 で dimming primitive も併設されるため、
  //   「ペア線 primitive が 1 つ存在する」ことを検証する／総数固定はしない）。
  const pairLines = mainSeries.attached.filter((p) => typeof p.zOrder !== 'function');
  assert.equal(pairLines.length, 1);
});

test('v4: backward compatible — no throw when mainSeries lacks attachPrimitive', async () => {
  // Arrange: attachPrimitive 非提供 series（旧 API）
  const lwc = fakeLwc();
  const r = new TradeMarkersRenderer({ lwc, mainSeries: {}, chart: fakeChartV4() });
  const fakeFetch = async () => ({ ok: true, async json() { return markersJsonV4([10]); } });
  const m = muteConsole();
  // Act / Assert: throw しない（primitive skip）
  let count;
  await assert.doesNotReject(async () => {
    try { count = await r.load('/x', fakeFetch); } finally { m.restore(); }
  });
});

test('C1: subscribes to crosshair move when the chart provides subscribeCrosshairMove', () => {
  // Arrange
  const lwc = fakeLwc();
  const chart = fakeChartV4();
  // Act
  // eslint-disable-next-line no-new
  new TradeMarkersRenderer({ lwc, mainSeries: fakeSeriesWithPrimitive(), chart });
  // Assert: crosshair 購読が 1 件登録される（既存 ChartRenderer 購読と共存・C1）
  assert.equal(chart.crossCbs.length, 1);
});

test('C1: backward compatible — no throw when chart lacks subscribeCrosshairMove', () => {
  // Arrange: subscribeCrosshairMove 非提供 chart（timeScale のみ）
  const lwc = fakeLwc();
  const chart = { timeScale: () => ({ subscribeVisibleTimeRangeChange() {} }) };
  // Act / Assert
  assert.doesNotThrow(() => new TradeMarkersRenderer({ lwc, mainSeries: {}, chart }));
});

test('v4: hovering a marker dims non-highlighted markers (low-alpha color) via a single setMarkers', async () => {
  // Arrange
  const lwc = fakeLwc();
  const mainSeries = fakeSeriesWithPrimitive();
  const chart = fakeChartV4();
  const r = new TradeMarkersRenderer({ lwc, mainSeries, chart });
  const fakeFetch = async () => ({ ok: true, async json() { return markersJsonV4([10, 20, 30]); } });
  const m = muteConsole();
  try {
    await r.load('/data/trade_markers.json', fakeFetch);
    chart.emitRange({ from: 0, to: 100 }); // 全件可視
    const before = lwc.calls.setMarkers.length + lwc.calls.create.length;
    // Act: marker t1:entry にホバー
    chart.emitCross({ hoveredObjectId: 't1:entry' });
    // Assert(C2): hover 1 回で setMarkers/create は 1 回だけ増える（二重発火しない）
    const after = lwc.calls.setMarkers.length + lwc.calls.create.length;
    assert.equal(after - before, 1);
    // Assert: 適用された markers のうち pair!=1 は減光色（元の #26a69a と異なる）、pair==1 は通常色
    const applied = lwc.calls.setMarkers.at(-1) || lwc.calls.create.at(-1).markers;
    const dimmed = applied.filter((x) => x.id !== 't1:entry');
    const highlighted = applied.filter((x) => x.id === 't1:entry');
    assert.ok(dimmed.length > 0 && dimmed.every((x) => x.color !== '#26a69a'), '非ハイライトは減光色');
    assert.ok(highlighted.every((x) => x.color === '#26a69a'), 'ハイライトは通常色');
  } finally { m.restore(); }
});

test('v4: hovering forwards the highlight index to the pair-lines primitive', async () => {
  // Arrange: primitive の setHighlight が呼ばれた引数を記録する spy primitive を仕込む。
  const lwc = fakeLwc();
  const highlights = [];
  const mainSeries = {
    attached: [],
    attachPrimitive(p) {
      const orig = p.setHighlight.bind(p);
      p.setHighlight = (i) => { highlights.push(i); return orig(i); };
      this.attached.push(p);
    },
  };
  const chart = fakeChartV4();
  const r = new TradeMarkersRenderer({ lwc, mainSeries, chart });
  const fakeFetch = async () => ({ ok: true, async json() { return markersJsonV4([10, 20]); } });
  const m = muteConsole();
  try {
    await r.load('/data/trade_markers.json', fakeFetch);
    chart.emitRange({ from: 0, to: 100 });
    // Act
    chart.emitCross({ hoveredObjectId: 't1:entry' });
    // Assert: primitive へ highlight index 1 が転送される
    assert.ok(highlights.includes(1));
  } finally { m.restore(); }
});

test('v4: releasing hover (no hoveredObjectId) restores all markers to normal color', async () => {
  // Arrange
  const lwc = fakeLwc();
  const mainSeries = fakeSeriesWithPrimitive();
  const chart = fakeChartV4();
  const r = new TradeMarkersRenderer({ lwc, mainSeries, chart });
  const fakeFetch = async () => ({ ok: true, async json() { return markersJsonV4([10, 20, 30]); } });
  const m = muteConsole();
  try {
    await r.load('/data/trade_markers.json', fakeFetch);
    chart.emitRange({ from: 0, to: 100 });
    chart.emitCross({ hoveredObjectId: 't1:entry' }); // hover
    // Act: hover 解除（hoveredObjectId 無し）
    chart.emitCross({});
    // Assert: 全 marker が通常色へ復帰
    const applied = lwc.calls.setMarkers.at(-1) || lwc.calls.create.at(-1).markers;
    assert.ok(applied.every((x) => x.color === '#26a69a'), 'hover 解除で全件通常色');
  } finally { m.restore(); }
});

test('C2: hover dimming respects the visible range filter (only in-range markers are applied)', async () => {
  // Arrange: 範囲 [10,30] を発火 → 範囲外 t? は除外。hover 後も範囲フィルタが効く（単一 _render 統合）。
  const lwc = fakeLwc();
  const mainSeries = fakeSeriesWithPrimitive();
  const chart = fakeChartV4();
  const r = new TradeMarkersRenderer({ lwc, mainSeries, chart });
  const fakeFetch = async () => ({ ok: true, async json() { return markersJsonV4([5, 10, 20, 30, 99]); } });
  const m = muteConsole();
  try {
    await r.load('/data/trade_markers.json', fakeFetch);
    chart.emitRange({ from: 10, to: 30 });
    // Act: hover も範囲フィルタ後集合に対して適用される
    chart.emitCross({ hoveredObjectId: 't1:entry' });
    // Assert: 範囲内（10,20,30）のみ適用（範囲外 5,99 は hover 時も除外）
    const applied = lwc.calls.setMarkers.at(-1) || lwc.calls.create.at(-1).markers;
    assert.deepEqual(applied.map((x) => x.time), [10, 20, 30]);
  } finally { m.restore(); }
});

test('v4: range change re-render does not double-fire setMarkers (single _render path)', async () => {
  // Arrange
  const lwc = fakeLwc();
  const mainSeries = fakeSeriesWithPrimitive();
  const chart = fakeChartV4();
  const r = new TradeMarkersRenderer({ lwc, mainSeries, chart });
  const fakeFetch = async () => ({ ok: true, async json() { return markersJsonV4([10, 20]); } });
  const m = muteConsole();
  try {
    await r.load('/data/trade_markers.json', fakeFetch);
    const before = lwc.calls.setMarkers.length + lwc.calls.create.length;
    // Act: 範囲変更 1 回
    chart.emitRange({ from: 0, to: 100 });
    // Assert(C2): 1 範囲変更につき marker 適用は 1 回（二重発火しない不変条件）
    const after = lwc.calls.setMarkers.length + lwc.calls.create.length;
    assert.equal(after - before, 1);
  } finally { m.restore(); }
});

// ── v5（§11）: ホバー時に当該ペア以外のローソク足も減光（dimming オーバーレイ primitive）─────
// 設計入力: CHART_TRADE_MARKERS_DETAILED_DESIGN.md §11（案A・dimming オーバーレイ primitive）。
//   フェーズ2 確定方針:
//   - 専用 primitive PairDimPrimitive を併設（PairLinesPrimitive に相乗りしない・SRP）。
//   - load 時に dim primitive へも setPairs。highlight 時は単一 _render() で dim primitive へ
//     setHighlight(this._highlight) を 1 行転送（C2・二重発火なし）。
//   - 後方互換: attachPrimitive 非提供でも throw しない（dim primitive も skip）。
//   - canvas 実 z 合成（ローソクの上・マーカー/線の下）はブラウザ確認に委譲（node:test 範囲外）。

// dim primitive は zOrder()==='bottom' を返す点で PairLinesPrimitive と区別できる。
function findDimPrimitive(attached) {
  return attached.find((p) => typeof p.zOrder === 'function' && p.zOrder() === 'bottom');
}

test('v5: attaches a dimming overlay primitive (zOrder bottom) in addition to the pair-lines primitive', async () => {
  // Arrange
  const lwc = fakeLwc();
  const mainSeries = fakeSeriesWithPrimitive();
  const chart = fakeChartV4();
  const r = new TradeMarkersRenderer({ lwc, mainSeries, chart });
  const fakeFetch = async () => ({ ok: true, async json() { return markersJsonV4([10, 20]); } });
  const m = muteConsole();
  // Act
  try { await r.load('/data/trade_markers.json', fakeFetch); } finally { m.restore(); }
  // Assert: 専用の dimming primitive が併設される（ペア線 primitive とは別インスタンス）。
  assert.equal(mainSeries.attached.length, 2, 'pair-lines + dimming の 2 primitive を付与');
  assert.ok(findDimPrimitive(mainSeries.attached), 'zOrder=bottom の dimming primitive が付与される');
});

test('v5: load forwards pairs to the dimming primitive (setPairs)', async () => {
  // Arrange: dimming primitive の setPairs 引数を spy する。
  const lwc = fakeLwc();
  const setPairsArgs = [];
  const mainSeries = {
    attached: [],
    attachPrimitive(p) {
      if (typeof p.zOrder === 'function' && p.zOrder() === 'bottom') {
        const orig = p.setPairs.bind(p);
        p.setPairs = (pairs) => { setPairsArgs.push(pairs); return orig(pairs); };
      }
      this.attached.push(p);
    },
  };
  const chart = fakeChartV4();
  const r = new TradeMarkersRenderer({ lwc, mainSeries, chart });
  const json = markersJsonV4([10, 20]);
  const fakeFetch = async () => ({ ok: true, async json() { return json; } });
  const m = muteConsole();
  // Act
  try { await r.load('/data/trade_markers.json', fakeFetch); } finally { m.restore(); }
  // Assert: dimming primitive へ json.pairs が渡る
  assert.ok(setPairsArgs.length >= 1, 'dimming primitive に setPairs される');
  assert.equal(setPairsArgs.at(-1).length, json.pairs.length);
});

test('v5: hovering a marker forwards the highlight index to the dimming primitive (single _render path)', async () => {
  // Arrange: dimming primitive の setHighlight 引数を spy する。
  const lwc = fakeLwc();
  const dimHighlights = [];
  const mainSeries = {
    attached: [],
    attachPrimitive(p) {
      if (typeof p.zOrder === 'function' && p.zOrder() === 'bottom') {
        const orig = p.setHighlight.bind(p);
        p.setHighlight = (i) => { dimHighlights.push(i); return orig(i); };
      }
      this.attached.push(p);
    },
  };
  const chart = fakeChartV4();
  const r = new TradeMarkersRenderer({ lwc, mainSeries, chart });
  const fakeFetch = async () => ({ ok: true, async json() { return markersJsonV4([10, 20]); } });
  const m = muteConsole();
  try {
    await r.load('/data/trade_markers.json', fakeFetch);
    chart.emitRange({ from: 0, to: 100 });
    // Act: marker t1:entry にホバー
    chart.emitCross({ hoveredObjectId: 't1:entry' });
    // Assert: dimming primitive へ highlight index 1 が転送される（§11・C2）
    assert.ok(dimHighlights.includes(1), 'dimming primitive へ highlight=1 を転送');
  } finally { m.restore(); }
});

test('v5: releasing hover forwards highlight=null to the dimming primitive (restores candles)', async () => {
  // Arrange
  const lwc = fakeLwc();
  const dimHighlights = [];
  const mainSeries = {
    attached: [],
    attachPrimitive(p) {
      if (typeof p.zOrder === 'function' && p.zOrder() === 'bottom') {
        const orig = p.setHighlight.bind(p);
        p.setHighlight = (i) => { dimHighlights.push(i); return orig(i); };
      }
      this.attached.push(p);
    },
  };
  const chart = fakeChartV4();
  const r = new TradeMarkersRenderer({ lwc, mainSeries, chart });
  const fakeFetch = async () => ({ ok: true, async json() { return markersJsonV4([10, 20]); } });
  const m = muteConsole();
  try {
    await r.load('/data/trade_markers.json', fakeFetch);
    chart.emitRange({ from: 0, to: 100 });
    chart.emitCross({ hoveredObjectId: 't1:entry' }); // hover
    // Act: hover 解除
    chart.emitCross({});
    // Assert: 解除で dimming primitive へ null が転送される（ローソク減光解除＝復帰）
    assert.ok(dimHighlights.includes(null), 'hover 解除で highlight=null を転送');
  } finally { m.restore(); }
});

test('v5: backward compatible — no throw when mainSeries lacks attachPrimitive (dimming skipped)', async () => {
  // Arrange: attachPrimitive 非提供 series（dimming primitive も skip されるべき）
  const lwc = fakeLwc();
  const chart = fakeChartV4();
  const r = new TradeMarkersRenderer({ lwc, mainSeries: {}, chart });
  const fakeFetch = async () => ({ ok: true, async json() { return markersJsonV4([10]); } });
  const m = muteConsole();
  // Act / Assert: hover も含め throw しない
  await assert.doesNotReject(async () => {
    try {
      await r.load('/x', fakeFetch);
      chart.emitRange({ from: 0, to: 100 });
      chart.emitCross({ hoveredObjectId: 't0:entry' });
      chart.emitCross({});
    } finally { m.restore(); }
  });
});
