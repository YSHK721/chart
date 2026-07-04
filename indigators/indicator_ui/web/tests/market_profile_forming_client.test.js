// market_profile_forming_client.js（Port 実装・DIP）の純ロジック検証。
//
// 設計入力: Phase2 設計 mp_ticklive_design.md「新規 front client」。
//   Backend 契約: GET /market_profile_forming?datasetRef=&timeframe=&since=&base=[&bins=&va=&barw=]
//     応答 {ok:true, formingStart, ticks:[[sec,mid]...], now[, baseFine, baseKmin, activeTable, priceMin,
//            priceMax, nBins, gridW]}。失敗時 {ok:false, error:{...}}。
//   純関数（buildFormingUrl / parseForming）を公開し単体検証を容易にする（SRP）。
//   DOM/chart/実 fetch 非依存（Fake fetch を注入）。構造: Arrange-Act-Assert。

import { test } from 'node:test';
import assert from 'node:assert/strict';

import {
  buildFormingUrl,
  parseForming,
  MarketProfileFormingClient,
} from '../js/adapter/front/market_profile_forming_client.js';

const OK_FULL = {
  ok: true,
  formingStart: 1704074400,
  ticks: [[1704074460, 1005], [1704074520, 1015]],
  baseFine: [10, 20, 0], baseKmin: 100,
  activeTable: [[1, 1]],
  priceMin: 1000, priceMax: 1100, nBins: 3, gridW: 10, now: 1704074600,
};

const OK_LIGHT = {
  ok: true, formingStart: 1704074400, ticks: [[1704074460, 1005]], now: 1704074600,
};

test('buildFormingUrl encodes datasetRef and always includes it', () => {
  const url = buildFormingUrl({ datasetRef: 'jp225_tick' });
  assert.equal(url, '/market_profile_forming?datasetRef=jp225_tick');
});

test('buildFormingUrl appends timeframe/since/base when provided', () => {
  const url = buildFormingUrl({ datasetRef: 'jp225_tick', timeframe: '1h', since: 1704074460, base: 0 });
  assert.ok(url.includes('&timeframe=1h'));
  assert.ok(url.includes('&since=1704074460'));
  assert.ok(url.includes('&base=0'));
});

test('buildFormingUrl appends base=1 for full base request', () => {
  const url = buildFormingUrl({ datasetRef: 'jp225_tick', timeframe: '1h', base: 1 });
  assert.ok(url.includes('&base=1'));
});

test('buildFormingUrl omits since when null/undefined', () => {
  assert.ok(!buildFormingUrl({ datasetRef: 'jp225_tick' }).includes('since='));
  assert.ok(!buildFormingUrl({ datasetRef: 'jp225_tick', since: null }).includes('since='));
});

test('buildFormingUrl maps resmode=range to &barw and omits bins (base 整列)', () => {
  const url = buildFormingUrl({ datasetRef: 'jp225_tick', resmode: 'range', range: '50', bins: 60 });
  assert.ok(url.includes('&barw=50'));
  assert.ok(!url.includes('bins='));
});

test('buildFormingUrl appends bins under resmode=bins and omits barw (base 整列)', () => {
  const url = buildFormingUrl({ datasetRef: 'jp225_tick', resmode: 'bins', bins: '30', range: '100' });
  assert.ok(url.includes('&bins=30'));
  assert.ok(!url.includes('barw='));
});

test('buildFormingUrl appends now when provided', () => {
  const url = buildFormingUrl({ datasetRef: 'jp225_tick', now: 1704074600 });
  assert.ok(url.includes('&now=1704074600'));
});

test('parseForming returns the full payload object on ok:true', () => {
  const out = parseForming(OK_FULL);
  assert.equal(out.formingStart, 1704074400);
  assert.deepEqual(out.ticks, [[1704074460, 1005], [1704074520, 1015]]);
  assert.deepEqual(out.baseFine, [10, 20, 0]);
  assert.equal(out.baseKmin, 100);
  assert.equal(out.gridW, 10);
});

test('parseForming returns the light payload (no baseFine) on ok:true', () => {
  const out = parseForming(OK_LIGHT);
  assert.equal(out.formingStart, 1704074400);
  assert.equal(out.baseFine, undefined);
});

test('parseForming returns null on ok:false', () => {
  assert.equal(parseForming({ ok: false, error: { type: 'validation' } }), null);
});

test('parseForming returns null on malformed payload (missing formingStart or ticks)', () => {
  assert.equal(parseForming({ ok: true, ticks: [] }), null);
  assert.equal(parseForming({ ok: true, formingStart: 1 }), null);
  assert.equal(parseForming(null), null);
});

test('fetchForming builds the URL from args and returns the parsed payload', async () => {
  const urls = [];
  const fakeFetch = async (u) => { urls.push(u); return { ok: true, async json() { return OK_FULL; } }; };
  const client = new MarketProfileFormingClient({ fetch: fakeFetch });
  const out = await client.fetchForming({ datasetRef: 'jp225_tick', timeframe: '1h', base: 1 });
  assert.equal(urls.length, 1);
  assert.ok(urls[0].includes('datasetRef=jp225_tick') && urls[0].includes('base=1'));
  assert.equal(out.formingStart, 1704074400);
});

test('fetchForming returns null on non-ok HTTP status', async () => {
  const client = new MarketProfileFormingClient({ fetch: async () => ({ ok: false, async json() { return {}; } }) });
  assert.equal(await client.fetchForming({ datasetRef: 'jp225_tick' }), null);
});

test('fetchForming returns null when fetch throws (non-disruptive)', async () => {
  const client = new MarketProfileFormingClient({ fetch: async () => { throw new Error('network'); } });
  assert.equal(await client.fetchForming({ datasetRef: 'jp225_tick' }), null);
});

test('fetchForming returns null when no fetch impl injected', async () => {
  const client = new MarketProfileFormingClient({ fetch: undefined });
  assert.equal(await client.fetchForming({ datasetRef: 'jp225_tick' }), null);
});
