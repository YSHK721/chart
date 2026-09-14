// embedded_compute_gateway.js（ComputeGateway 実装・A方式）の仕様検証。
//
// 設計入力: 内部設計書 §7.1.1（ComputeGateway 契約）、A方式（fetch せず埋め込み事前計算を返す）。
//   キー = `${indicatorId}:${variant}`、SAMPLE_DATA.precomputed から取得。
//   キー不在は ComputeError 相当（ok:false / throw）。
// 構造: Arrange-Act-Assert（AAA）。

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { EmbeddedComputeGateway, ComputeError } from '../js/adapter/front/embedded_compute_gateway.js';

// Fake precomputed source（SAMPLE_DATA.precomputed 相当）を注入してテスト。
const FAKE = {
  precomputed: {
    'tgp_btlm:default': [{ name: 'btlm_mean', kind: 'line', data: [{ time: 1, value: 1 }] }],
    'profit_band:global': [{ name: 'pOL 99%', kind: 'line', data: [] }],
    'profit_band:robust': [{ name: 'pOL 99%', kind: 'line', data: [] }],
  },
};

test('compute returns the precomputed series for a known indicatorId:variant key', async () => {
  // Arrange
  const gw = new EmbeddedComputeGateway(FAKE);
  // Act
  const result = await gw.compute({ indicatorId: 'tgp_btlm', variant: 'default', params: {}, generation: 0 });
  // Assert
  assert.equal(result.ok, true);
  assert.equal(result.generation, 0);
  assert.equal(result.series[0].name, 'btlm_mean');
});

test('compute echoes back the request generation (race-control contract §7.1.1)', async () => {
  const gw = new EmbeddedComputeGateway(FAKE);
  const result = await gw.compute({ indicatorId: 'profit_band', variant: 'global', params: {}, generation: 5 });
  assert.equal(result.generation, 5);
});

test('compute switches profit_band variant global <-> robust by key', async () => {
  const gw = new EmbeddedComputeGateway(FAKE);
  const g = await gw.compute({ indicatorId: 'profit_band', variant: 'global', params: {}, generation: 0 });
  const r = await gw.compute({ indicatorId: 'profit_band', variant: 'robust', params: {}, generation: 0 });
  assert.equal(g.ok, true);
  assert.equal(r.ok, true);
});

test('compute throws ComputeError for an unknown key (A-mode limitation)', async () => {
  const gw = new EmbeddedComputeGateway(FAKE);
  await assert.rejects(
    () => gw.compute({ indicatorId: 'nope', variant: 'x', params: {}, generation: 0 }),
    (err) => err instanceof ComputeError,
  );
});

test('compute uses default variant key when variant is null', async () => {
  const gw = new EmbeddedComputeGateway(FAKE);
  const result = await gw.compute({ indicatorId: 'tgp_btlm', variant: null, params: {}, generation: 0 });
  assert.equal(result.ok, true);
  assert.equal(result.series[0].name, 'btlm_mean');
});
