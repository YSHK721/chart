// composition_root_front.js の Market Profile 結線（追加のみ・挙動保存）検証。
//
// 設計入力: 依頼「アクター注入を 1 箇所追加・既存の /candles 経路に非干渉・トグルまで fetch しない」。
//   観点:
//   - bootstrap の返り値に marketProfile（MarketProfileActor）が含まれる（トグルが setEnabled する）。
//   - bootstrap 自身は /market_profile を fetch しない（既存 candles 経路に副作用 fetch を足さない）。
//   構造: Arrange-Act-Assert。upstream JS（lwc）と fetch は Fake を注入。

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { bootstrap } from '../js/adapter/front/composition_root_front.js';
import { MarketProfileActor } from '../js/adapter/front/market_profile_actor.js';

function fakeLwc() {
  const mainSeries = { setData: () => {}, attachPrimitive: () => {} };
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

test('bootstrap exposes a MarketProfileActor on the return value (toggle deferred to index.html)', async () => {
  // Arrange
  const lwc = fakeLwc();
  const fakeFetch = async () => ({ ok: true, async json() { return { ok: true, candles: [] }; } });
  // Act
  const { marketProfile } = await bootstrap({
    lwc, container: {}, doc: null, storage: noStorage, protocol: 'http:', fetch: fakeFetch,
  });
  // Assert
  assert.ok(marketProfile instanceof MarketProfileActor);
});

test('bootstrap does not fetch /market_profile itself (no extra side-effect fetch)', async () => {
  // Arrange: fetch された URL を記録し、/market_profile が呼ばれないことを固定する。
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
  // Assert
  assert.ok(!urls.some((u) => String(u).includes('market_profile')));
});
