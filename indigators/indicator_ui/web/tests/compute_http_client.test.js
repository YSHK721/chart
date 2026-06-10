// ComputeHttpClient（adapter/front/compute_http_client.js）の検証 — B方式 中核B。
//
// 設計入力: 内部設計書 §3.3.5（ComputeHttpClient: ComputeRequest→fetch POST→ComputeResult）、
//   §6.3.1 リクエスト・§6.3.2/6.3.3 正常応答・§6.3.4 エラー応答（error.type/message）、
//   §7.1.1 ComputeGateway 契約。
//
// 方針: fetch を注入可能にし Fake fetch で (a) リクエスト整形（URL/method/JSONボディ）、
//   (b) 200 応答の series 返却、(c) 非200 応答→ComputeError（error_type 保持）、
//   (d) ネットワーク例外→ComputeError 翻訳、を DOM・実ネットワーク非依存で検証する。

import test from 'node:test';
import assert from 'node:assert/strict';

import { ComputeHttpClient, ComputeError } from '../js/adapter/front/compute_http_client.js';

// Fake Response: fetch が返す最小スタブ（ok / status / json()）。
function fakeResponse(status, body) {
  return {
    ok: status >= 200 && status < 300,
    status,
    async json() { return body; },
  };
}

// 正常応答 200 → ComputeResult { ok, generation, series } を返す（EmbeddedComputeGateway と同形）。
test('compute returns the ComputeResult { ok, generation, series } from a 200 response', async () => {
  // Arrange
  const series = [{ name: 'btlm_mean', kind: 'line', data: [{ time: 1, value: 2 }] }];
  const fakeFetch = async () => fakeResponse(200, { ok: true, generation: 0, series });
  const client = new ComputeHttpClient({ fetch: fakeFetch });
  // Act
  const result = await client.compute({ indicatorId: 'tgp_btlm', variant: 'default', params: {}, datasetRef: 'sample' });
  // Assert: 描画は result.series、recompute 競合判定は result.generation を参照する。
  assert.deepEqual(result, { ok: true, generation: 0, series });
});

// リクエスト整形: URL='/compute'・method='POST'・JSON headers・body=JSON.stringify(request)。
test('compute POSTs to /compute with a JSON body carrying the request fields', async () => {
  // Arrange
  let captured = null;
  const fakeFetch = async (url, init) => { captured = { url, init }; return fakeResponse(200, { ok: true, series: [] }); };
  const client = new ComputeHttpClient({ fetch: fakeFetch });
  const req = { indicatorId: 'profit_band', variant: 'global', params: { probabilities: [0.99] }, datasetRef: 'sample' };
  // Act
  await client.compute(req);
  // Assert
  assert.equal(captured.url, '/compute');
  assert.equal(captured.init.method, 'POST');
  assert.equal(captured.init.headers['Content-Type'], 'application/json');
  const sent = JSON.parse(captured.init.body);
  assert.equal(sent.indicatorId, 'profit_band');
  assert.equal(sent.variant, 'global');
  assert.deepEqual(sent.params, { probabilities: [0.99] });
  assert.equal(sent.datasetRef, 'sample');
});

// 時間足切替: timeframe / limit（直近 N 本）をボディに転送する（§チャート表示時間選択・配信設計）。
test('compute forwards timeframe and limit in the JSON body', async () => {
  // Arrange
  let captured = null;
  const fakeFetch = async (url, init) => { captured = init; return fakeResponse(200, { ok: true, series: [] }); };
  const client = new ComputeHttpClient({ fetch: fakeFetch });
  // Act
  await client.compute({ indicatorId: 'tgp_btlm', variant: 'default', params: {}, datasetRef: 'jp225_m1', timeframe: '1W', limit: 1500 });
  // Assert: サーバが resample（timeframe）・直近 N 本（limit）に使う。
  const sent = JSON.parse(captured.body);
  assert.equal(sent.timeframe, '1W');
  assert.equal(sent.limit, 1500);
});

// 非200（400）→ ComputeError throw（error_type 保持）。
test('compute throws ComputeError with error_type for a 400 response', async () => {
  // Arrange
  const fakeFetch = async () => fakeResponse(400, { ok: false, error: { type: 'validation', message: 'q_low<q_high 違反' } });
  const client = new ComputeHttpClient({ fetch: fakeFetch });
  // Act / Assert
  await assert.rejects(
    () => client.compute({ indicatorId: 'tgp_btlm', variant: 'default', params: {}, datasetRef: 'sample' }),
    (err) => {
      assert.ok(err instanceof ComputeError);
      assert.equal(err.error_type, 'validation');
      assert.match(err.message, /q_low/);
      return true;
    },
  );
});

// 非200（500 backend_unavailable）→ ComputeError throw（error_type 保持）。
test('compute throws ComputeError with backend_unavailable for a 500 response', async () => {
  // Arrange
  const fakeFetch = async () => fakeResponse(500, { ok: false, error: { type: 'backend_unavailable', message: 'rpy2 不在' } });
  const client = new ComputeHttpClient({ fetch: fakeFetch });
  // Act / Assert
  await assert.rejects(
    () => client.compute({ indicatorId: 'tgp_btlm', variant: 'default', params: { fitter: 'tgp' }, datasetRef: 'sample' }),
    (err) => err instanceof ComputeError && err.error_type === 'backend_unavailable',
  );
});

// ネットワーク例外（fetch reject）→ ComputeError へ翻訳して throw。
test('compute translates a network failure into a ComputeError', async () => {
  // Arrange
  const fakeFetch = async () => { throw new TypeError('Failed to fetch'); };
  const client = new ComputeHttpClient({ fetch: fakeFetch });
  // Act / Assert
  await assert.rejects(
    () => client.compute({ indicatorId: 'tgp_btlm', variant: 'default', params: {}, datasetRef: 'sample' }),
    (err) => {
      assert.ok(err instanceof ComputeError);
      assert.equal(err.error_type, 'network');
      return true;
    },
  );
});
