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
