// candles_client — ローソク取得（GET /candles）の唯一の口。
//
// 固定する契約:
//   - URL は live 参照実装（chart_app_wiring.js:74-96）と同じクエリ 3 点（datasetRef/timeframe/limit）
//   - 失敗（接続不能・非 200・非 JSON・契約外の形）は**掲示できる形** { ok:false, error } へ倒す
//   - 成功は { ok:true, candles } をそのまま返す（フロントは数値を再計算しない）
//
// 構造は AAA。テスト名は「対象_条件_期待結果」。

import { test, describe } from 'node:test';
import assert from 'node:assert/strict';

import { createCandlesClient } from '../js/adapter/front/candles_client.js';

const CANDLES = [{ time: 60, open: 1, high: 2, low: 0.5, close: 1.5 }];

function okFetch(payload = { ok: true, candles: CANDLES }) {
  const calls = [];
  const fetchFn = (url) => {
    calls.push(url);
    return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(payload) });
  };
  return { fetchFn, calls };
}

describe('candles_client — 取得の口と失敗の掲示', () => {
  test('constructor_requires_fetch_and_prefix_injection', () => {
    // 既定で globalThis を掴むと検定が実ネットワークへ出る経路が残る（reach_sheet_client と同じ）。
    assert.throws(() => createCandlesClient({ apiPrefix: '/live' }), /fetch/);
    assert.throws(() => createCandlesClient({ fetch: () => {} }), /apiPrefix/);
  });

  test('fetch_candles_builds_the_live_candles_url_with_all_three_params', async () => {
    // Arrange
    const spy = okFetch();
    const client = createCandlesClient({ fetch: spy.fetchFn, apiPrefix: '/live' });
    // Act
    await client.fetchCandles({ datasetRef: 'jp225_tick', timeframe: '5m', limit: 180 });
    // Assert: live の参照実装と同じクエリ（datasetRef / timeframe / limit）。
    assert.equal(spy.calls.length, 1);
    assert.equal(spy.calls[0], '/live/candles?datasetRef=jp225_tick&timeframe=5m&limit=180');
  });

  test('a_successful_response_returns_the_candles_unchanged', async () => {
    const client = createCandlesClient({ fetch: okFetch().fetchFn, apiPrefix: '/live' });
    // Act
    const result = await client.fetchCandles({ datasetRef: 'jp225_tick', timeframe: '1m', limit: 10 });
    // Assert: フロントは数値を再計算しない（そのまま返す）。
    assert.equal(result.ok, true);
    assert.deepEqual(result.candles, CANDLES);
  });

  test('a_network_failure_becomes_a_postable_error_instead_of_a_throw', async () => {
    const client = createCandlesClient({
      fetch: () => Promise.reject(new Error('connection refused')),
      apiPrefix: '/live',
    });
    // Act
    const result = await client.fetchCandles({ datasetRef: 'jp225_tick', timeframe: '1m', limit: 10 });
    // Assert: 例外で落とさず、理由を掲示できる形で返す。
    assert.equal(result.ok, false);
    assert.match(result.error.message, /connection refused/);
  });

  test('a_non_200_response_reports_the_http_status', async () => {
    const client = createCandlesClient({
      fetch: () => Promise.resolve({ ok: false, status: 404 }),
      apiPrefix: '/live',
    });
    const result = await client.fetchCandles({ datasetRef: 'jp225_tick', timeframe: '1m', limit: 10 });
    assert.equal(result.ok, false);
    assert.match(result.error.message, /404/);
  });

  test('a_payload_without_the_contract_shape_is_reported_not_passed_through', async () => {
    // サーバ側の失敗（ok:false や candles 欠落）を成功として流すと、空チャートと区別が付かない。
    const client = createCandlesClient({
      fetch: okFetch({ ok: false, error: { message: '未知の timeframe' } }).fetchFn,
      apiPrefix: '/live',
    });
    const result = await client.fetchCandles({ datasetRef: 'jp225_tick', timeframe: '9x', limit: 10 });
    assert.equal(result.ok, false);
    assert.match(result.error.message, /未知の timeframe/);
  });
});
