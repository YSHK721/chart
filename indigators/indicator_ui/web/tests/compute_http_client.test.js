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

// mode（計算モード・Latest 増分計算）: 指定時のみボディに mode を載せる（未指定は従来＝載せない）。
test('compute forwards mode in the JSON body when provided', async () => {
  // Arrange
  let captured = null;
  const fakeFetch = async (url, init) => { captured = init; return fakeResponse(200, { ok: true, series: [] }); };
  const client = new ComputeHttpClient({ fetch: fakeFetch });
  // Act
  await client.compute({ indicatorId: 'moving_averages', variant: 'default', params: {}, datasetRef: 'jp225_m1', mode: 'latest' });
  // Assert: サーバが mode で full/latest を分岐する。
  const sent = JSON.parse(captured.body);
  assert.equal(sent.mode, 'latest');
});

test('compute omits mode from the body when not provided (backward compatible)', async () => {
  // Arrange
  let captured = null;
  const fakeFetch = async (url, init) => { captured = init; return fakeResponse(200, { ok: true, series: [] }); };
  const client = new ComputeHttpClient({ fetch: fakeFetch });
  // Act
  await client.compute({ indicatorId: 'tgp_btlm', variant: 'default', params: {}, datasetRef: 'sample' });
  // Assert: mode 未指定はボディに mode を含めない（サーバ既定 full・後方互換）。
  const sent = JSON.parse(captured.body);
  assert.ok(!('mode' in sent), 'mode should be absent when not provided');
});

// [PROTO 再生 seam] untilTime（そのフレームの時点・UNIX秒）: 指定時のみボディへ載せる。replay の
//   reveal（df[:t+1] 因果計算）が untilTime を送る。未指定（present ライブ）は載せない＝後方互換。
test('compute forwards untilTime in the JSON body when provided', async () => {
  // Arrange
  let captured = null;
  const fakeFetch = async (url, init) => { captured = init; return fakeResponse(200, { ok: true, series: [] }); };
  const client = new ComputeHttpClient({ fetch: fakeFetch });
  // Act
  await client.compute({ indicatorId: 'moving_averages', variant: 'default', params: {}, datasetRef: 'jp225_m1', untilTime: 1735810200 });
  // Assert: サーバが untilTime で df[:t+1] へ切り出して当時計算する（reveal 因果）。
  const sent = JSON.parse(captured.body);
  assert.equal(sent.untilTime, 1735810200);
});

test('compute omits untilTime from the body when not provided (present live compute)', async () => {
  // Arrange
  let captured = null;
  const fakeFetch = async (url, init) => { captured = init; return fakeResponse(200, { ok: true, series: [] }); };
  const client = new ComputeHttpClient({ fetch: fakeFetch });
  // Act
  await client.compute({ indicatorId: 'tgp_btlm', variant: 'default', params: {}, datasetRef: 'sample' });
  // Assert: untilTime 未指定はボディに含めない（present ライブ＝全件計算・後方互換）。
  const sent = JSON.parse(captured.body);
  assert.ok(!('untilTime' in sent), 'untilTime should be absent when not provided');
});

// [PROTO 再生 seam] 足内 MA 追従: forming（形成中バー暫定 OHLC）を指定時のみボディに載せる。
//   これが落ちると backend が最終足を差し替えられず MA が足内で動かない（回帰の番人）。
test('compute forwards forming (in-progress bar OHLC) in the JSON body when provided', async () => {
  // Arrange
  let captured = null;
  const fakeFetch = async (url, init) => { captured = init; return fakeResponse(200, { ok: true, series: [] }); };
  const client = new ComputeHttpClient({ fetch: fakeFetch });
  const forming = { time: 1735810200, open: 1.0, high: 3.0, low: 0.5, close: 2.5 };
  // Act
  await client.compute({ indicatorId: 'moving_averages', variant: 'default', params: {}, datasetRef: 'jp225_tick', mode: 'latest', forming });
  // Assert: サーバが forming で最終足を set/replace してから latest 計算する（MA が足内追従）。
  const sent = JSON.parse(captured.body);
  assert.deepEqual(sent.forming, forming);
});

test('compute omits forming from the body when not provided (confirmed-bar compute)', async () => {
  // Arrange
  let captured = null;
  const fakeFetch = async (url, init) => { captured = init; return fakeResponse(200, { ok: true, series: [] }); };
  const client = new ComputeHttpClient({ fetch: fakeFetch });
  // Act
  await client.compute({ indicatorId: 'moving_averages', variant: 'default', params: {}, datasetRef: 'jp225_m1', mode: 'latest' });
  // Assert: forming 未指定はボディに含めない（確定足のまま計算＝バー確定再計算の後方互換）。
  const sent = JSON.parse(captured.body);
  assert.ok(!('forming' in sent), 'forming should be absent when not provided');
});

// present byte 不変の番人: untilTime/forming 未指定時の body キー集合が seam 導入前と完全一致すること。
//   （seam が inert であることを固定＝present 回帰の実証）。
test('compute body keys are unchanged when untilTime/forming are not provided (inert seam)', async () => {
  // Arrange
  let captured = null;
  const fakeFetch = async (url, init) => { captured = init; return fakeResponse(200, { ok: true, series: [] }); };
  const client = new ComputeHttpClient({ fetch: fakeFetch });
  // Act
  await client.compute({ indicatorId: 'tgp_btlm', variant: 'default', params: { probabilities: [0.99] }, datasetRef: 'sample', generation: 3, timeframe: '1D', limit: 1500 });
  // Assert: seam 導入前と同一のキー集合（untilTime/forming/mode を含まない）。
  const sent = JSON.parse(captured.body);
  assert.deepEqual(
    Object.keys(sent).sort(),
    ['datasetRef', 'generation', 'indicatorId', 'limit', 'params', 'timeframe', 'variant'],
  );
});
