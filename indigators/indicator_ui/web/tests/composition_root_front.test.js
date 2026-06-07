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

// Fake lwc: createChart → chart（addCandlestickSeries/timeScale）。setData 呼出を記録。
function fakeLwc() {
  const setDataCalls = [];
  const mainSeries = { setData: (d) => setDataCalls.push(d) };
  const chart = {
    addCandlestickSeries: () => mainSeries,
    timeScale: () => ({ fitContent: () => {} }),
  };
  return {
    lwc: { createChart: () => chart },
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
  const { controller, mode, ready } = bootstrap({
    lwc, container: {}, doc: null, storage: noStorage, protocol: 'http:', fetch: fakeFetch,
  });
  await ready;
  // Assert
  assert.equal(mode, 'b');
  assert.ok(controller._compute instanceof ComputeHttpClient);
});

test('bootstrap falls back to EmbeddedComputeGateway and mode=a on file://', () => {
  // Arrange
  const { lwc } = fakeLwc();
  // Act
  const { controller, mode } = bootstrap({
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
  const { ready } = bootstrap({
    lwc, container: {}, doc: null, storage: noStorage, protocol: 'https:', fetch: fakeFetch,
  });
  await ready;
  // Assert
  assert.match(candlesUrl, /^\/candles\?datasetRef=sample$/);
  // 初期 SAMPLE_DATA → /candles 取得後に再 setData（最後の呼び出しが取得 candles）。
  assert.deepEqual(setDataCalls.at(-1), candles);
});

test('bootstrap (served) keeps SAMPLE_DATA when /candles fetch fails', async () => {
  // Arrange
  const { lwc, setDataCalls } = fakeLwc();
  const fakeFetch = async () => { throw new TypeError('Failed to fetch'); };
  const before = setDataCalls.length;
  // Act
  const { ready } = bootstrap({
    lwc, container: {}, doc: null, storage: noStorage, protocol: 'http:', fetch: fakeFetch,
  });
  await ready;
  // Assert: 取得失敗時は再 setData しない（初期 SAMPLE_DATA の 1 回のみ）。
  assert.equal(setDataCalls.length, before + 1);
});
