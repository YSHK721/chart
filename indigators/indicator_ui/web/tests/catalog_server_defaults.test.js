// サーバ由来 param 既定値の解決（ISSUE-092 ③・要件③）。
//
// applyServerDefaults(schema): GET /catalog のスキーマを front レジストリへ overlay して既定値を
//   解決する。未知 id / 未知 param は無視（前方互換）。market_profile 等 schema 外は不変。
// IndicatorCatalogClient.load(fetch): /catalog を取得し overlay する。フェッチ失敗（例外 / 非 ok /
//   非 ok payload）時は静的値へフォールバック（UI 従来どおり・オフライン耐性）。
//
// 各テストは変更した既定値を restore し、他テストへ副作用を残さない（モジュール singleton 保護）。
// 構造: Arrange-Act-Assert（AAA）。

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { get, applyServerDefaults } from '../js/usecase/catalog.js';
import { IndicatorCatalogClient } from '../js/adapter/front/catalog_client.js';

function paramOf(id, name) {
  return get(id).params.find((p) => p.name === name);
}

test('applyServerDefaults overlays defaults onto the registry', () => {
  const p = paramOf('tgp_btlm', 'maxbars');
  const original = p.default;
  try {
    const applied = applyServerDefaults({ tgp_btlm: { maxbars: 42 } });
    assert.equal(applied, 1);
    assert.equal(paramOf('tgp_btlm', 'maxbars').default, 42);
  } finally {
    p.default = original;
  }
});

test('applyServerDefaults ignores unknown ids and unknown params', () => {
  const p = paramOf('tgp_btlm', 'maxbars');
  const original = p.default;
  try {
    // 未知 id・未知 param は黙って無視（前方互換）。既知 id の既知 param のみ反映。
    const applied = applyServerDefaults({
      no_such_indicator: { foo: 1 },
      tgp_btlm: { no_such_param: 9, maxbars: 7 },
    });
    assert.equal(applied, 1);
    assert.equal(paramOf('tgp_btlm', 'maxbars').default, 7);
  } finally {
    p.default = original;
  }
});

test('applyServerDefaults leaves indicators absent from schema unchanged (e.g. market_profile)', () => {
  const before = get('market_profile').params.map((p) => [p.name, p.default]);
  applyServerDefaults({ tgp_btlm: { maxbars: 100 } });
  const after = get('market_profile').params.map((p) => [p.name, p.default]);
  assert.deepStrictEqual(after, before);
});

test('applyServerDefaults tolerates non-object schema (no throw, returns 0)', () => {
  assert.equal(applyServerDefaults(null), 0);
  assert.equal(applyServerDefaults(undefined), 0);
  assert.equal(applyServerDefaults('nope'), 0);
});

test('client.load fetches /catalog and overlays server defaults', async () => {
  const p = paramOf('tgp_btlm', 'maxbars');
  const original = p.default;
  const fakeFetch = async (url) => {
    assert.equal(url, '/catalog');
    return { ok: true, json: async () => ({ ok: true, catalog: { tgp_btlm: { maxbars: 55 } } }) };
  };
  try {
    const ok = await new IndicatorCatalogClient().load(fakeFetch);
    assert.equal(ok, true);
    assert.equal(paramOf('tgp_btlm', 'maxbars').default, 55);
  } finally {
    p.default = original;
  }
});

test('client.load also overlays paramScopes (variant ごとの受理 param・ISSUE-278 #8)', async () => {
  // ISSUE-278 #8: 受理集合が front へ届かないと、効かないコントロールを出し続ける経路へ戻る。
  //   結線（load → applyServerParamScopes）そのものを固定する。
  const p = paramOf('profit_band', 'require_full');
  const fakeFetch = async () => ({
    ok: true,
    json: async () => ({
      ok: true,
      catalog: {},
      paramScopes: {
        profit_band: {
          global: ['require_full', 'timeframe'],
          robust: ['normalize', 'timeframe'],
        },
      },
    }),
  });
  try {
    const ok = await new IndicatorCatalogClient().load(fakeFetch);
    assert.equal(ok, true);
    assert.deepEqual(p.variants, ['global']);
    assert.deepEqual(paramOf('profit_band', 'normalize').variants, ['robust']);
  } finally {
    for (const q of get('profit_band').params) {
      q.variants = null;
    }
  }
});

test('client.load falls back to static defaults when fetch throws', async () => {
  const p = paramOf('tgp_btlm', 'maxbars');
  const original = p.default;
  const throwingFetch = async () => { throw new Error('network down'); };
  const ok = await new IndicatorCatalogClient().load(throwingFetch);
  assert.equal(ok, false);
  // 静的値のまま（UI 従来どおり）。
  assert.equal(paramOf('tgp_btlm', 'maxbars').default, original);
});

test('client.load falls back when response is not ok', async () => {
  const p = paramOf('tgp_btlm', 'maxbars');
  const original = p.default;
  const notOkFetch = async () => ({ ok: false, json: async () => ({}) });
  const ok = await new IndicatorCatalogClient().load(notOkFetch);
  assert.equal(ok, false);
  assert.equal(paramOf('tgp_btlm', 'maxbars').default, original);
});

test('client.load falls back when payload.ok is not true', async () => {
  const p = paramOf('tgp_btlm', 'maxbars');
  const original = p.default;
  const badPayloadFetch = async () => ({ ok: true, json: async () => ({ ok: false }) });
  const ok = await new IndicatorCatalogClient().load(badPayloadFetch);
  assert.equal(ok, false);
  assert.equal(paramOf('tgp_btlm', 'maxbars').default, original);
});
