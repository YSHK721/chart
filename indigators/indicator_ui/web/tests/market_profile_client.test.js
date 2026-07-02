// market_profile_client.js の純ロジック検証（URL 組み立て・応答整形・fetch 失敗時 null）。
//
// 設計入力: 依頼「取得ロジックの単体テスト（URL組み立て・応答→primitiveデータ整形）」。
//   Backend 契約: GET /market_profile?datasetRef=&timeframe=&limit=&bins=&va=
//   応答 {ok:true, profile:{bins:[{price,tpo,norm}],poc,va_low,va_high,price_min,price_max,tpo_units,n_bins}}
//   失敗時 {ok:false, error:{...}}。DOM/chart/実 fetch 非依存（Fake fetch を注入）。
// 構造: Arrange-Act-Assert。

import { test } from 'node:test';
import assert from 'node:assert/strict';

import {
  buildMarketProfileUrl,
  parseProfileResponse,
  MarketProfileClient,
} from '../js/adapter/front/market_profile_client.js';

const OK_PAYLOAD = {
  ok: true,
  profile: {
    bins: [{ price: 100, tpo: 2, norm: 0.5 }, { price: 101, tpo: 4, norm: 1 }],
    poc: 101, va_low: 100, va_high: 101, price_min: 100, price_max: 101,
    tpo_units: 6, n_bins: 2,
  },
};

test('buildMarketProfileUrl encodes datasetRef and always includes it', () => {
  // Arrange / Act
  const url = buildMarketProfileUrl({ datasetRef: 'jp225_tick' });
  // Assert
  assert.equal(url, '/market_profile?datasetRef=jp225_tick');
});

test('buildMarketProfileUrl appends timeframe/limit/bins/va only when provided', () => {
  // Arrange / Act
  const url = buildMarketProfileUrl({
    datasetRef: 'sample', timeframe: '1D', limit: 1500, bins: 24, va: 0.7,
  });
  // Assert: 各パラメータが URL に含まれる
  assert.ok(url.startsWith('/market_profile?datasetRef=sample'));
  assert.ok(url.includes('&timeframe=1D'));
  assert.ok(url.includes('&limit=1500'));
  assert.ok(url.includes('&bins=24'));
  assert.ok(url.includes('&va=0.7'));
});

test('buildMarketProfileUrl omits optional params when null/undefined', () => {
  // Arrange / Act
  const url = buildMarketProfileUrl({ datasetRef: 'sample', timeframe: null, limit: undefined });
  // Assert: 省略パラメータは付かない
  assert.equal(url, '/market_profile?datasetRef=sample');
});

test('buildMarketProfileUrl appends src when provided (src=dwell)', () => {
  // Arrange / Act
  const url = buildMarketProfileUrl({ datasetRef: 'jp225_tick', src: 'dwell' });
  // Assert
  assert.ok(url.includes('&src=dwell'));
});

test('buildMarketProfileUrl omits src when not provided (candle 後方互換=URLに付けない)', () => {
  // Arrange / Act
  const url = buildMarketProfileUrl({ datasetRef: 'sample' });
  // Assert: src 省略時は URL に付与しない（サーバ既定 candle）
  assert.ok(!url.includes('src='));
  assert.equal(url, '/market_profile?datasetRef=sample');
});

test('buildMarketProfileUrl maps range to &barw when range is a value (range=50)', () => {
  // Arrange / Act
  const url = buildMarketProfileUrl({ datasetRef: 'jp225_tick', range: '50' });
  // Assert: フロントの range（バー幅pt）は backend param 名 barw へ写像される
  assert.ok(url.includes('&barw=50'));
});

test('buildMarketProfileUrl omits barw when range is auto (=従来 bins・URLに付けない)', () => {
  // Arrange / Act
  const url = buildMarketProfileUrl({ datasetRef: 'sample', range: 'auto', bins: 30 });
  // Assert: auto は「bins に委ねる」＝barw を付与しない
  assert.ok(!url.includes('barw='));
  assert.ok(url.includes('&bins=30'));
});

test('buildMarketProfileUrl omits barw when range is null/undefined', () => {
  // Arrange / Act
  const url = buildMarketProfileUrl({ datasetRef: 'sample', range: null });
  // Assert
  assert.ok(!url.includes('barw='));
});

test('buildMarketProfileUrl appends src=m1 when provided (tick数)', () => {
  // Arrange / Act
  const url = buildMarketProfileUrl({ datasetRef: 'jp225_tick', src: 'm1' });
  // Assert: m1 も既存 src 経路で URL に載る
  assert.ok(url.includes('&src=m1'));
});

test('parseProfileResponse passes through src/atom when present, else no extra keys', () => {
  // Arrange
  const withMeta = { ok: true, profile: { bins: [], poc: 1 }, src: 'dwell', atom: 'tick滞在秒' };
  // Act
  const p = parseProfileResponse(withMeta);
  // Assert: src/atom を素通し・既存キー維持
  assert.equal(p.src, 'dwell');
  assert.equal(p.atom, 'tick滞在秒');
  assert.equal(p.poc, 1);
  // src/atom 無しの応答には余分キーを足さない（後方互換）
  const plain = parseProfileResponse(OK_PAYLOAD);
  assert.ok(!('src' in plain));
  assert.ok(!('atom' in plain));
});

test('parseProfileResponse passes through bar_width when present, else omitted (後方互換)', () => {
  // Arrange: 応答トップレベルに bar_width（実効バー幅pt）がある場合。
  const withBw = {
    ok: true, profile: { bins: [{ price: 100, tpo: 1, norm: 1 }], poc: 100 },
    src: 'candle', atom: '足レンジ', bar_width: 25,
  };
  // Act
  const p = parseProfileResponse(withBw);
  // Assert: bar_width が src/atom と同様に素通しされる。
  assert.equal(p.bar_width, 25);
  assert.equal(p.src, 'candle');
  assert.equal(p.poc, 100);
  // bar_width を含まない応答には bar_width キーを足さない（後方互換）。
  const plain = parseProfileResponse(OK_PAYLOAD);
  assert.ok(!('bar_width' in plain));
});

test('parseProfileResponse includes bar_width even when src/atom absent', () => {
  // Arrange: src/atom が無く bar_width だけある応答でも素通しする。
  const onlyBw = { ok: true, profile: { bins: [], poc: 1 }, bar_width: 12.5 };
  // Act
  const p = parseProfileResponse(onlyBw);
  // Assert
  assert.equal(p.bar_width, 12.5);
});

test('parseProfileResponse returns the profile object on ok:true', () => {
  // Arrange / Act
  const profile = parseProfileResponse(OK_PAYLOAD);
  // Assert
  assert.equal(profile.poc, 101);
  assert.equal(profile.bins.length, 2);
});

test('parseProfileResponse returns null on ok:false', () => {
  // Arrange / Act / Assert
  assert.equal(parseProfileResponse({ ok: false, error: { code: 'x' } }), null);
});

test('parseProfileResponse returns null on malformed payload (no bins array)', () => {
  // Arrange / Act / Assert
  assert.equal(parseProfileResponse({ ok: true, profile: { poc: 1 } }), null);
  assert.equal(parseProfileResponse(null), null);
});

test('MarketProfileClient.fetchProfile builds the URL from context and returns the profile', async () => {
  // Arrange
  const urls = [];
  const fakeFetch = async (u) => { urls.push(u); return { ok: true, async json() { return OK_PAYLOAD; } }; };
  const client = new MarketProfileClient({ fetch: fakeFetch });
  // Act
  const profile = await client.fetchProfile({ datasetRef: 'sample', timeframe: '1D', limit: 1500 });
  // Assert
  assert.equal(urls.length, 1);
  assert.ok(urls[0].includes('datasetRef=sample') && urls[0].includes('timeframe=1D') && urls[0].includes('limit=1500'));
  assert.equal(profile.poc, 101);
});

test('MarketProfileClient.fetchProfile returns null on non-ok HTTP status', async () => {
  // Arrange
  const client = new MarketProfileClient({ fetch: async () => ({ ok: false, status: 500, async json() { return {}; } }) });
  // Act / Assert
  assert.equal(await client.fetchProfile({ datasetRef: 'sample' }), null);
});

test('MarketProfileClient.fetchProfile returns null when fetch throws (non-disruptive)', async () => {
  // Arrange
  const client = new MarketProfileClient({ fetch: async () => { throw new Error('network'); } });
  // Act / Assert
  assert.equal(await client.fetchProfile({ datasetRef: 'sample' }), null);
});

test('MarketProfileClient.fetchProfile returns null when no fetch impl injected', async () => {
  // Arrange
  const client = new MarketProfileClient({ fetch: undefined });
  // Act / Assert
  assert.equal(await client.fetchProfile({ datasetRef: 'sample' }), null);
});
