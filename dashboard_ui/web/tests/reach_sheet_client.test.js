// reach_sheet_client — dashboard core の `/reach_sheet` を叩く唯一の口。
//
// 設計入力: arch-spec §9（JSON 契約・単一ソースは dashboard_ui/usecase/sheet_models.py）／
//   arch-spec §7（`sw_rewrite.js` は改変不要＝View が **apiPrefix 注入**で
//   `/dashboard/reach_sheet` を直接叩く）。
//
// prefix を文字列で書き写さない理由: モードの prefix は unified_ui の `mode_table.js` が
//   唯一源だが、dashboard_ui からそれを import すると依存が逆流する（arch-spec §1 の依存方向）。
//   代わりに**自分が配信されている場所**から導く——本モジュールは
//   `/dashboard/js/adapter/front/reach_sheet_client.js` として配信されるので、`/js/` の手前が
//   そのまま prefix である。写しではなく実際の配信位置なので、ズレようがない。
//
// 計算量テスト（絶対命令・§4.1）: fetch を Test Spy にして、1 要求 = 1 発行であること
//   （ボディを組むだけで余分な往復が出ないこと）を表明する。回数は焼き込まない。
//
// 構造は AAA。テスト名は「対象_条件_期待結果」。

import { test, describe } from 'node:test';
import assert from 'node:assert/strict';

import { createReachSheetClient, deriveApiPrefix } from '../js/adapter/front/reach_sheet_client.js';

/** fetch の Test Spy（発行を数え、投げた要求をそのまま残す）。 */
function spyFetch(response = { ok: true, current_price: 1, rows: [], current_index: 0, cells: [], degradations: [] }) {
  const calls = [];
  const fetchFn = (url, init) => {
    calls.push({ url, init });
    return Promise.resolve({
      ok: true,
      status: 200,
      json: () => Promise.resolve(response),
    });
  };
  return { fetchFn, calls };
}

const BODY = Object.freeze({
  dataset_ref: 'jp225_tick',
  chart_timeframe: '1m',
  instances: [{ instance_id: '1m/ma_marod#1', indicator_id: 'ma_marod', variant: 'default', params: {}, timeframe: '1m' }],
  mode: 'full',
});

describe('reach_sheet_client — /reach_sheet の唯一の口', () => {
  test('derive_api_prefix_takes_the_serving_location_not_a_copied_literal', () => {
    // Arrange: 実配信の URL（statically served by dashboard core）。
    // Act
    const prefix = deriveApiPrefix('http://127.0.0.1:8000/dashboard/js/adapter/front/reach_sheet_client.js');
    // Assert
    assert.equal(prefix, '/dashboard');
  });

  test('derive_api_prefix_of_a_root_served_module_is_empty', () => {
    // dashboard core を単体で開いた場合（prefix 無しで配信される）。
    assert.equal(deriveApiPrefix('http://127.0.0.1:8481/js/adapter/front/reach_sheet_client.js'), '');
  });

  test('derive_api_prefix_refuses_a_url_outside_the_js_tree', () => {
    // 導出できないなら黙って '' に倒さない（宛先が静かに変わると 404 の原因が追えない）。
    assert.throws(() => deriveApiPrefix('http://127.0.0.1:8000/somewhere/else.js'), /prefix/);
  });

  test('the_request_is_posted_to_the_reach_sheet_endpoint_under_the_injected_prefix', async () => {
    // Arrange
    const spy = spyFetch();
    const client = createReachSheetClient({ fetch: spy.fetchFn, apiPrefix: '/dashboard' });
    // Act
    await client.fetchSheet(BODY);
    // Assert
    assert.equal(spy.calls.length, 1);
    assert.equal(spy.calls[0].url, '/dashboard/reach_sheet');
    assert.equal(spy.calls[0].init.method, 'POST');
    assert.equal(spy.calls[0].init.headers['Content-Type'], 'application/json');
  });

  test('the_request_body_is_the_arch_spec_json_contract_unchanged', async () => {
    // フロントは数値の再計算をしない（arch-spec §9）。送るのは束と表示足だけ。
    const spy = spyFetch();
    const client = createReachSheetClient({ fetch: spy.fetchFn, apiPrefix: '/dashboard' });
    // Act
    await client.fetchSheet(BODY);
    // Assert: フィールド名を発明しない（サーバ側 sheet_models.py と同名で送る）。
    const sent = JSON.parse(spy.calls[0].init.body);
    assert.deepEqual(Object.keys(sent).sort(), ['chart_timeframe', 'dataset_ref', 'instances', 'mode']);
    assert.deepEqual(sent.instances[0], BODY.instances[0]);
  });

  test('a_successful_response_is_returned_as_is_without_client_side_recomputation', async () => {
    // Arrange
    const payload = {
      ok: true, current_price: 65756.0, current_index: 1,
      rows: [{ price: 65803.4, timeframe: '5m', label: 'cvfe 外側上 2σ', distance: 47.4, gap_to_previous: 16.2, horizon_marks: ['short'], reach: { reached: false, since_time: null, truncated: false }, horizon_p: { short: 0.058, medium: 0.077, long: 0.128 } }],
      cells: [], degradations: [],
    };
    const spy = spyFetch(payload);
    const client = createReachSheetClient({ fetch: spy.fetchFn, apiPrefix: '/dashboard' });
    // Act
    const result = await client.fetchSheet(BODY);
    // Assert: p も並びも到達判定もサーバ側が単一ソース。クライアントは触らない。
    assert.deepEqual(result, payload);
  });

  test('a_server_reported_failure_is_passed_through_instead_of_being_swallowed', async () => {
    // arch-spec §9 の失敗形 {"ok": false, "error": {...}}。
    const spy = spyFetch({ ok: false, error: { type: 'BindingMissing', message: '紐付けがありません' } });
    const client = createReachSheetClient({ fetch: spy.fetchFn, apiPrefix: '/dashboard' });
    const result = await client.fetchSheet(BODY);
    assert.equal(result.ok, false);
    assert.equal(result.error.type, 'BindingMissing');
  });

  test('a_transport_failure_becomes_a_reported_error_not_a_thrown_exception', async () => {
    // 掲示できる形へ倒す（無言 no-op も、握り潰しもしない）。
    const client = createReachSheetClient({
      fetch: () => Promise.reject(new Error('ECONNREFUSED')),
      apiPrefix: '/dashboard',
    });
    const result = await client.fetchSheet(BODY);
    assert.equal(result.ok, false);
    assert.match(result.error.message, /ECONNREFUSED/);
  });

  test('a_non_2xx_status_becomes_a_reported_error_naming_the_status', async () => {
    const client = createReachSheetClient({
      fetch: () => Promise.resolve({ ok: false, status: 503, json: () => Promise.reject(new Error('no body')) }),
      apiPrefix: '/dashboard',
    });
    const result = await client.fetchSheet(BODY);
    assert.equal(result.ok, false);
    assert.match(result.error.message, /503/);
  });

  // ---- 計算量テスト（絶対命令・§4.1）------------------------------------
  test('one_request_issues_exactly_one_round_trip', async () => {
    // 無駄の不在: 1 要求で往復が 2 本出ない（前置きの照会・再取得を足さない）。
    const spy = spyFetch();
    const client = createReachSheetClient({ fetch: spy.fetchFn, apiPrefix: '/dashboard' });
    // Act
    await client.fetchSheet(BODY);
    // Assert
    assert.equal(spy.calls.length, 1);
  });

  test('round_trips_do_not_grow_when_the_instance_bundle_grows', async () => {
    // オーダーの表明: 束の大きさ（＝表示行数・セル数の源）を変えた 2 点で発行数が一致する。
    //   回数そのものは焼き込まない。
    const runWith = async (count) => {
      const spy = spyFetch();
      const client = createReachSheetClient({ fetch: spy.fetchFn, apiPrefix: '/dashboard' });
      await client.fetchSheet({
        ...BODY,
        instances: Array.from({ length: count }, (_unused, i) => ({
          instance_id: `1m/ma_marod#${i}`, indicator_id: 'ma_marod',
          variant: 'default', params: { length: i }, timeframe: '1m',
        })),
      });
      return spy.calls.length;
    };
    // Act
    const few = await runWith(11);
    const many = await runWith(23);
    // Assert
    assert.ok(few > 0, '発行が起きていません');
    assert.equal(few, many);
  });

  test('a_missing_fetch_injection_is_refused_at_construction', () => {
    // 既定で globalThis.fetch を勝手に掴むと、テストが実ネットワークへ出る経路が残る。
    assert.throws(() => createReachSheetClient({ apiPrefix: '/dashboard', fetch: null }), /fetch/);
  });
});
