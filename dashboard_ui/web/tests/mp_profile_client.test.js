// mp_profile_client — ライブ MP（GET <prefix>/market_profile）の借用クライアント。
//
// 設計入力（依頼者承認 2026-09-06「MP 列＝ライブ MP の借用」）:
//   ラダーの MP 列は、ローソク（candles_client / ISSUE-470）と同じ流儀で live core から**借りる**。
//   dashboard core は MP の供給面を複製しない。
//
// 本スイートが固定する最重要の不変条件（**URL を手書きしない**）:
//   URL の唯一源はライブの `buildMarketProfileUrl`（market_profile_client.js:31・純関数）である。
//   ここで URL を組み直すと、ライブ側が 1 パラメータ足した瞬間にラダーだけが古い URL を投げ、
//   サーバ側メモの共有も仕様の同期も**無言で**壊れる（出力は「それらしい MP」のままなので
//   状態検証では落ちない）。したがって組み立ては注入された関数へ委譲し、本モジュールは
//   prefix を足すだけであることを、委譲の観測とソース走査の両方で表明する。
//
// 失敗は**掲示できる形**へ倒す（candles_client.js と同じ規約。例外で落とさず無言 no-op にもしない）。
//
// 構造は AAA。テスト名は「対象_条件_期待結果」。

import { test, describe } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

import { createMpProfileClient } from '../js/adapter/front/mp_profile_client.js';

const PROFILE = Object.freeze({
  bins: [{ price: 1, norm: 0.5 }],
  n_bins: 1,
  price_min: 0,
  price_max: 2,
  poc: 1,
});

/** 応答 1 本を返す Test Spy。呼ばれた URL を全部覚える。 */
function spyFetch(responder) {
  const urls = [];
  const fetchFn = (url) => {
    urls.push(url);
    return Promise.resolve(responder(url));
  };
  return { fetchFn, urls };
}

const okResponse = (payload) => ({ ok: true, status: 200, json: () => Promise.resolve(payload) });

/** 既定の組み立て（委譲先）。呼ばれた文脈を覚える。 */
function spyBuildUrl(returns = '/market_profile?datasetRef=jp225_tick') {
  const contexts = [];
  const buildUrl = (context) => {
    contexts.push(context);
    return returns;
  };
  return { buildUrl, contexts };
}

describe('mp_profile_client — ライブ MP の借用', () => {
  test('createMpProfileClient_without_its_injections_refuses_instead_of_grabbing_globals', () => {
    // 既定で globalThis.fetch を掴むと、検定が実ネットワークへ出る経路が残る
    //   （candles_client.js:34-39 と同じ理由）。
    assert.throws(() => createMpProfileClient({}), TypeError);
    assert.throws(() => createMpProfileClient({ fetch: () => {} }), TypeError);
    assert.throws(
      () => createMpProfileClient({ fetch: () => {}, apiPrefix: '/live' }), TypeError,
    );
  });

  test('the_url_is_delegated_to_the_live_builder_and_only_the_prefix_is_added', () => {
    // Arrange
    const built = '/market_profile?datasetRef=jp225_tick&timeframe=1m&src=zp';
    const { buildUrl, contexts } = spyBuildUrl(built);
    const spy = spyFetch(() => okResponse({ ok: true, profile: PROFILE }));
    const client = createMpProfileClient({ fetch: spy.fetchFn, apiPrefix: '/live', buildUrl });
    const context = { datasetRef: 'jp225_tick', timeframe: '1m', src: 'zp' };
    // Act
    client.fetchProfile(context);
    // Assert: 文脈がそのまま渡り、戻り値が**そのまま** prefix の後ろに置かれる。
    assert.deepEqual(contexts, [context]);
    assert.deepEqual(spy.urls, [`/live${built}`]);
  });

  test('the_client_source_never_spells_the_market_profile_query_itself', async () => {
    // 手書き複製の機械的な禁止（唯一源はライブの buildMarketProfileUrl）。
    //   委譲の観測（上の検定）だけでは「委譲したうえで自分でも組む」経路を捕まえられない。
    const source = readFileSync(
      fileURLToPath(new URL('../js/adapter/front/mp_profile_client.js', import.meta.url)), 'utf8',
    );
    const code = source.replace(/\/\*[\s\S]*?\*\//g, '').replace(/(^|[^:])\/\/[^\n]*/g, '$1');
    // 走査語は URL のクエリ断片に限る（'?' 単体は三項演算子に当たるので使わない）。
    for (const spelled of [
      '/market_profile', 'datasetRef=', 'timeframe=', 'bins=', 'barw=', 'va=', 'src=',
      'sessions=', 'encodeURIComponent',
    ]) {
      assert.equal(
        code.includes(spelled), false,
        `URL の断片 '${spelled}' を自分で綴っています（buildMarketProfileUrl へ委譲すること）`,
      );
    }
  });

  test('a_well_formed_response_yields_the_profile', async () => {
    const { buildUrl } = spyBuildUrl();
    const spy = spyFetch(() => okResponse({ ok: true, profile: PROFILE }));
    const client = createMpProfileClient({ fetch: spy.fetchFn, apiPrefix: '/live', buildUrl });

    const result = await client.fetchProfile({ datasetRef: 'jp225_tick' });

    assert.equal(result.ok, true);
    assert.deepEqual(result.profile.bins, PROFILE.bins);
    assert.equal(result.profile.n_bins, 1);
  });

  test('a_transport_failure_is_posted_instead_of_thrown', async () => {
    const { buildUrl } = spyBuildUrl();
    const client = createMpProfileClient({
      fetch: () => Promise.reject(new Error('切断')), apiPrefix: '/live', buildUrl,
    });

    const result = await client.fetchProfile({ datasetRef: 'jp225_tick' });

    assert.equal(result.ok, false);
    assert.equal(result.error.type, 'TransportError');
    assert.match(result.error.message, /切断/);
  });

  test('an_http_failure_is_posted_with_its_status', async () => {
    const { buildUrl } = spyBuildUrl();
    const spy = spyFetch(() => ({ ok: false, status: 503, json: () => Promise.resolve({}) }));
    const client = createMpProfileClient({ fetch: spy.fetchFn, apiPrefix: '/live', buildUrl });

    const result = await client.fetchProfile({ datasetRef: 'jp225_tick' });

    assert.equal(result.ok, false);
    assert.match(result.error.message, /503/);
  });

  test('a_payload_outside_the_contract_is_posted_as_a_protocol_error', async () => {
    // ok:false・profile 欠損・bins 非配列。どれも「空の MP 列」と区別が付く形で掲示する。
    const { buildUrl } = spyBuildUrl();
    const payloads = [
      { ok: false, error: { message: 'zp は 1m を支えません' } },
      { ok: true },
      { ok: true, profile: { bins: 'nope' } },
    ];
    for (const payload of payloads) {
      const spy = spyFetch(() => okResponse(payload));
      const client = createMpProfileClient({ fetch: spy.fetchFn, apiPrefix: '/live', buildUrl });

      const result = await client.fetchProfile({ datasetRef: 'jp225_tick' });

      assert.equal(result.ok, false, JSON.stringify(payload));
      assert.equal(typeof result.error.message, 'string');
      assert.ok(result.error.message.length > 0);
    }
  });

  test('an_unreadable_body_is_posted_as_a_protocol_error', async () => {
    const { buildUrl } = spyBuildUrl();
    const spy = spyFetch(() => ({
      ok: true, status: 200, json: () => Promise.reject(new Error('壊れた JSON')),
    }));
    const client = createMpProfileClient({ fetch: spy.fetchFn, apiPrefix: '/live', buildUrl });

    const result = await client.fetchProfile({ datasetRef: 'jp225_tick' });

    assert.equal(result.ok, false);
    assert.equal(result.error.type, 'ProtocolError');
  });

  test('one_call_issues_exactly_one_round_trip', () => {
    // 計算量（絶対命令 §4.1）: 取得 1 回に対する往復は 1 本。再試行・二度読みを混ぜない。
    const { buildUrl } = spyBuildUrl();
    const spy = spyFetch(() => okResponse({ ok: true, profile: PROFILE }));
    const client = createMpProfileClient({ fetch: spy.fetchFn, apiPrefix: '/live', buildUrl });

    client.fetchProfile({ datasetRef: 'jp225_tick' });

    assert.equal(spy.urls.length, 1);
  });
});
