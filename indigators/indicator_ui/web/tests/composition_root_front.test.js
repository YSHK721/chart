// composition_root_front.js（フロント側 Composition Root）の配線切替検証。
//
// 設計入力: 内部設計書 §2.1 / §3.3.5（ComputeHttpClient）/ §6.3（/candles）、
//   パラメータ設定ダイアログ §9（B方式 params 実反映）。
// 観点:
//   - modeForProtocol: http/https → 'b'、file:/その他 → 'a'。
//   - bootstrap: served（http）時は ComputeHttpClient（/compute）を注入し /candles を取得して
//     メイン系列を差し替える。file:// 時は EmbeddedComputeGateway + SAMPLE_DATA。
// 構造: Arrange-Act-Assert。upstream JS（lwc）と fetch は Fake を注入（DOM/実ネットワーク非依存）。

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { bootstrap, modeForProtocol } from '../js/adapter/front/composition_root_front.js';
import { ComputeHttpClient } from '../js/adapter/front/compute_http_client.js';
import { EmbeddedComputeGateway } from '../js/adapter/front/embedded_compute_gateway.js';
import { LiveUpdater } from '../js/adapter/front/live_updater.js';

// Fake lwc（v5）: createChart → chart（addSeries/panes/addPane/timeScale/subscribeCrosshairMove）。
//   ColorType / CandlestickSeries / createTextWatermark も公開（composition・ChartRenderer が参照）。
function fakeLwc() {
  const setDataCalls = [];
  const mainSeries = { setData: (d) => setDataCalls.push(d) };
  const chart = {
    addSeries: () => mainSeries,
    timeScale: () => ({ fitContent: () => {} }),
    panes: () => [{ setStretchFactor: () => {}, paneIndex: () => 0 }],
    addPane: () => ({ addSeries: () => ({ setData: () => {} }), setStretchFactor: () => {}, paneIndex: () => 1 }),
    removePane: () => {},
    removeSeries: () => {},
    subscribeCrosshairMove: () => {},
  };
  return {
    lwc: {
      createChart: () => chart,
      ColorType: { Solid: 'solid' },
      CandlestickSeries: {}, LineSeries: {}, HistogramSeries: {},
      createTextWatermark: () => ({ applyOptions: () => {}, detach: () => {} }),
    },
    setDataCalls,
  };
}

const noStorage = { getItem: () => null, setItem: () => {}, removeItem: () => {} };

test('modeForProtocol maps http/https to b and others to a', () => {
  assert.equal(modeForProtocol('http:'), 'b');
  assert.equal(modeForProtocol('https:'), 'b');
  assert.equal(modeForProtocol('file:'), 'a');
  assert.equal(modeForProtocol('about:'), 'a');
});

test('bootstrap injects ComputeHttpClient and mode=b when served over http', async () => {
  // Arrange
  const { lwc } = fakeLwc();
  const fakeFetch = async () => ({ ok: true, async json() { return { ok: true, candles: [] }; } });
  // Act
  const { controller, mode, ready } = await bootstrap({
    lwc, container: {}, doc: null, storage: noStorage, protocol: 'http:', fetch: fakeFetch,
  });
  await ready;
  // Assert
  assert.equal(mode, 'b');
  assert.ok(controller._compute instanceof ComputeHttpClient);
});

test('bootstrap falls back to EmbeddedComputeGateway and mode=a on file://', async () => {
  // Arrange
  const { lwc } = fakeLwc();
  // Act（A方式は SAMPLE_DATA を動的 import するため await）
  const { controller, mode } = await bootstrap({
    lwc, container: {}, doc: null, storage: noStorage, protocol: 'file:',
  });
  // Assert
  assert.equal(mode, 'a');
  assert.ok(controller._compute instanceof EmbeddedComputeGateway);
});

test('bootstrap (served) fetches /candles and replaces main series data', async () => {
  // Arrange
  const { lwc, setDataCalls } = fakeLwc();
  const candles = [{ time: 1277769600, open: 1.2667, high: 1.6667, low: 1.1693, close: 1.5927 }];
  let candlesUrl = null;
  const fakeFetch = async (url) => {
    candlesUrl = url;
    return { ok: true, async json() { return { ok: true, candles }; } };
  };
  // Act
  const { ready } = await bootstrap({
    lwc, container: {}, doc: null, storage: noStorage, protocol: 'https:', fetch: fakeFetch,
  });
  await ready;
  // Assert: 既定時間足（1D）・直近 RECENT_BARS（1500）本を /candles へ伝搬する（§配信設計）。
  assert.match(candlesUrl, /^\/candles\?datasetRef=sample&timeframe=1D&limit=1500$/);
  // B方式は SAMPLE_DATA を読み込まず、/candles 取得後に setData する（唯一の setData が取得 candles）。
  assert.deepEqual(setDataCalls.at(-1), candles);
});

test('bootstrap (served) draws nothing when /candles fetch fails (no SAMPLE_DATA in B mode)', async () => {
  // Arrange
  const { lwc, setDataCalls } = fakeLwc();
  const fakeFetch = async () => { throw new TypeError('Failed to fetch'); };
  const before = setDataCalls.length;
  // Act
  const { ready } = await bootstrap({
    lwc, container: {}, doc: null, storage: noStorage, protocol: 'http:', fetch: fakeFetch,
  });
  await ready;
  // Assert: B方式は SAMPLE_DATA を読み込まないため、/candles 失敗時は setData 0 回（空チャート）。
  assert.equal(setDataCalls.length, before + 0);
});

// ===========================================================================
// LiveUpdater 配線（served のみ・1 分間隔ライブ更新）
//   合成根は LiveUpdater を組み立てて bootstrap 戻り値に加える（setInterval は合成根に置かない）。
//   index.html が served 時のみ liveUpdater.start() を呼ぶ（file:// はスキップ）。
// ===========================================================================

test('bootstrap (served) builds a LiveUpdater and exposes it on the return value', async () => {
  // Arrange
  const { lwc } = fakeLwc();
  const fakeFetch = async () => ({ ok: true, async json() { return { ok: true, candles: [] }; } });
  // Act
  const { liveUpdater, ready } = await bootstrap({
    lwc, container: {}, doc: null, storage: noStorage, protocol: 'http:', fetch: fakeFetch,
  });
  await ready;
  // Assert: served は LiveUpdater を組み立てて戻り値に載せる（start は index.html 側）。
  assert.ok(liveUpdater instanceof LiveUpdater);
});

test('bootstrap (file://) exposes liveUpdater=null so no live updates are wired', async () => {
  // Arrange / Act（A方式）。
  const { lwc } = fakeLwc();
  const { liveUpdater } = await bootstrap({
    lwc, container: {}, doc: null, storage: noStorage, protocol: 'file:',
  });
  // Assert: A方式（file://）はライブ更新を配線しない。
  assert.equal(liveUpdater, null);
});

// ===========================================================================
// クロスヘア価格読み取り欄（左上オーバーレイ）の配線
//   composition root が CrosshairReadoutView を生成し、ChartRenderer の onCrosshairReadout に
//   (dto) => view.render(dto) を注入する。crosshair 発火で読み取り要素へ描画される。
// ===========================================================================

// crosshair handler を捕捉できる fake lwc（fireCrosshair で発火可能）。
function fakeLwcFireable() {
  const created = [];
  const mainSeries = { setData: () => {}, update: () => {} };
  const CandlestickSeries = {};
  let handler = null;
  const chart = {
    // 最初の addSeries（CandlestickSeries）は main 系列を返す。以降（overlay）は別系列。
    addSeries: (def, opts) => {
      if (def === CandlestickSeries) { return mainSeries; }
      const s = { _opts: opts, setData: () => {}, applyOptions: () => {} }; created.push(s); return s;
    },
    timeScale: () => ({ fitContent: () => {} }),
    panes: () => [{ setStretchFactor: () => {}, paneIndex: () => 0 }],
    addPane: () => ({ addSeries: () => ({ setData: () => {} }), setStretchFactor: () => {}, setPreserveEmptyPane: () => {}, paneIndex: () => 1 }),
    removePane: () => {}, removeSeries: () => {},
    subscribeCrosshairMove: (h) => { handler = h; },
    fireCrosshair: (param) => { if (handler) handler(param); },
  };
  return {
    lwc: {
      createChart: () => chart,
      ColorType: { Solid: 'solid' },
      CandlestickSeries, LineSeries: {}, HistogramSeries: {},
      createTextWatermark: () => ({ applyOptions: () => {}, detach: () => {} }),
    },
    chart, mainSeries,
  };
}

// crosshair-readout 要素を持つ fake document（CrosshairReadoutView の描画先）。
function fakeReadoutDoc() {
  const mk = () => ({ className: '', textContent: '', style: {}, children: [],
    set innerHTML(v) { if (v === '') this.children = []; }, get innerHTML() { return ''; },
    append(...n) { this.children.push(...n); } });
  const readout = mk();
  return {
    _readout: readout,
    getElementById: (id) => (id === 'crosshair-readout' ? readout : null),
    createElement: () => mk(),
  };
}

test('bootstrap wires onCrosshairReadout so crosshair moves render into #crosshair-readout', async () => {
  // Arrange
  const { lwc, chart, mainSeries } = fakeLwcFireable();
  const doc = fakeReadoutDoc();
  // Act
  const { ready } = await bootstrap({
    lwc, container: {}, doc, storage: noStorage, protocol: 'file:',
  });
  await ready;
  // crosshair 移動を発火（main OHLC を seriesData に載せる）。
  chart.fireCrosshair({ time: 1277769600, seriesData: new Map([[mainSeries, { open: 1.2, high: 1.6, low: 1.1, close: 1.5 }]]) });
  // Assert: 読み取り要素へ何か描画される（OHLC 行）。
  assert.ok(doc._readout.children.length > 0, 'crosshair readout element should be populated');
});

test('bootstrap (file://) establishes _lastBar so hover-off shows OHLC without a prior hover', async () => {
  // 仕様: A方式（file://）でも成立（初期ロードの末尾足が _lastBar に立つ）。bootstrap が
  //   初期 candles を renderer.setCandles 経由で流すことを固定する（直接 mainSeries.setData だと
  //   _lastBar が立たず、hover 解除時に OHLC が空になる回帰を防ぐ）。
  // Arrange
  const { lwc, chart } = fakeLwcFireable();
  const doc = fakeReadoutDoc();
  // Act
  const { ready } = await bootstrap({
    lwc, container: {}, doc, storage: noStorage, protocol: 'file:',
  });
  await ready;
  // hover 解除（seriesData 空・事前 hover なし）。
  chart.fireCrosshair({ time: undefined, seriesData: new Map() });
  // Assert: SAMPLE_DATA 末尾足（close=185）が読み取り欄に描画される（_lastBar フォールバック）。
  const text = JSON.stringify(doc._readout.children);
  assert.match(text, /185/);
});
