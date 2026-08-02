// catalog_client.js（IndicatorCatalogPort 実装）の仕様検証。
//
// 設計入力: 内部設計書 §7.1.5（list_indicators / get）。usecase/catalog.js をラップ。
// 構造: Arrange-Act-Assert（AAA）。

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { IndicatorCatalogClient } from '../js/adapter/front/catalog_client.js';

test('listIndicators returns the 26 registered indicators (基本4 + btlm_trail + btlm_trail_marod + ma_marod + cvfe + profit_* 15 + market_profile + tickvol_bands + tickvol)', () => {
  const client = new IndicatorCatalogClient();
  const ids = client.listIndicators().map((d) => d.id);
  for (const base of ['moving_averages', 'price_range_power', 'profit_band', 'tgp_btlm']) {
    assert.ok(ids.includes(base), `missing ${base}`);
  }
  // market_profile（プロファイルタブ・メニュー一本化）を追加。既存19は不変（追加のみ）。
  assert.ok(ids.includes('market_profile'), 'market_profile がメニュー一覧に載る');
  assert.equal(ids.length, 26);
});

test('get returns the indicator by id', () => {
  const client = new IndicatorCatalogClient();
  assert.equal(client.get('tgp_btlm').id, 'tgp_btlm');
});

test('get unknown id returns null', () => {
  const client = new IndicatorCatalogClient();
  assert.equal(client.get('missing'), null);
});
