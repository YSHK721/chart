// composition_root_front.js の Trade Markers 結線（追加のみ・挙動保存）検証。
//
// 設計入力: CHART_TRADE_MARKERS_DETAILED_DESIGN.md §3.2（DI 結線）、依頼注意（bootstrap は
//   renderer を組み立てて返すのみ・副作用 fetch を増やさない・load トリガは index.html）。
// 観点:
//   - bootstrap の返り値に tradeMarkers（TradeMarkersRenderer）が含まれる（index.html が load する）。
//   - bootstrap 自身は trade_markers.json を fetch しない（既存 /candles 経路に分岐を足さない）。
// 構造: Arrange-Act-Assert。upstream JS（lwc）と fetch は Fake を注入。

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { bootstrap } from '../js/adapter/front/composition_root_front.js';
import { TradeMarkersRenderer } from '../js/adapter/front/trade_markers_renderer.js';

function fakeLwc() {
  const mainSeries = { setData: () => {} };
  const chart = {
    addSeries: () => mainSeries,
    timeScale: () => ({ fitContent: () => {} }),
    panes: () => [{ setStretchFactor: () => {}, paneIndex: () => 0 }],
    addPane: () => ({ addSeries: () => ({ setData: () => {} }), setStretchFactor: () => {}, paneIndex: () => 1 }),
    removePane: () => {}, removeSeries: () => {}, subscribeCrosshairMove: () => {},
  };
  return {
    createChart: () => chart,
    ColorType: { Solid: 'solid' },
    CandlestickSeries: {}, LineSeries: {}, HistogramSeries: {},
    createTextWatermark: () => ({ applyOptions: () => {}, detach: () => {} }),
  };
}

const noStorage = { getItem: () => null, setItem: () => {}, removeItem: () => {} };

test('bootstrap exposes a TradeMarkersRenderer on the return value (load deferred to index.html)', async () => {
  // Arrange
  const lwc = fakeLwc();
  const fakeFetch = async () => ({ ok: true, async json() { return { ok: true, candles: [] }; } });
  // Act
  const { tradeMarkers } = await bootstrap({
    lwc, container: {}, doc: null, storage: noStorage, protocol: 'http:', fetch: fakeFetch,
  });
  // Assert
  assert.ok(tradeMarkers instanceof TradeMarkersRenderer);
});

test('bootstrap does not fetch the trade_markers JSON itself (no extra side-effect fetch)', async () => {
  // Arrange: fetch された URL を記録し、/data/trade_markers.json が呼ばれないことを固定する。
  const lwc = fakeLwc();
  const urls = [];
  const fakeFetch = async (u) => {
    urls.push(u);
    return { ok: true, async json() { return { ok: true, candles: [] }; } };
  };
  // Act
  const { ready } = await bootstrap({
    lwc, container: {}, doc: null, storage: noStorage, protocol: 'http:', fetch: fakeFetch,
  });
  await ready;
  // Assert: bootstrap は trade_markers.json を fetch しない（candles 経路のみ）
  assert.ok(!urls.some((u) => String(u).includes('trade_markers')));
});
