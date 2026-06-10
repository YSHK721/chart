// catalog_client.js（IndicatorCatalogPort 実装）の仕様検証。
//
// 設計入力: 内部設計書 §7.1.5（list_indicators / get）。usecase/catalog.js をラップ。
// 構造: Arrange-Act-Assert（AAA）。

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { IndicatorCatalogClient } from '../js/adapter/front/catalog_client.js';

test('listIndicators returns the 4 registered indicators', () => {
  const client = new IndicatorCatalogClient();
  const ids = client.listIndicators().map((d) => d.id).sort();
  assert.deepEqual(ids, ['moving_averages', 'price_range_power', 'profit_band', 'tgp_btlm']);
});

test('get returns the indicator by id', () => {
  const client = new IndicatorCatalogClient();
  assert.equal(client.get('tgp_btlm').id, 'tgp_btlm');
});

test('get unknown id returns null', () => {
  const client = new IndicatorCatalogClient();
  assert.equal(client.get('missing'), null);
});
