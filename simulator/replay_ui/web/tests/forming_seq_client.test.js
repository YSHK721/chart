// forming_seq_client.test.js — 足内一括計算の HTTP クライアント（ISSUE-232 / ISSUE-233）。
//
// 固定する契約:
//   1. 注入された fetch は **関数として** 呼ぶ（レシーバを付けない）。ブラウザの素の `fetch` は
//      Window 以外をレシーバに取ると "Illegal invocation" で必ず失敗する。replay.js の既定
//      `fetchImpl = fetch`（束縛なし）が渡るため、`this._fetch(...)` 形式だと足内一括計算が
//      1 度も成立しない（実 UI 実測: 指標更新回数 0＝ISSUE-233 の症状そのもの）。
//      呼び出し側は失敗を握り潰して従来経路へ落とすため、テストが無いと無言で退行する。
//   2. steps（formingSeq と同順の series 配列）を返す。ok=false / HTTP エラーは例外。

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { FormingSeqClient } from '../js/adapter/front/forming_seq_client.js';

// ブラウザの素の fetch と同じ制約（Window 以外のレシーバを拒否）を模した fetch。
//   ES モジュールは strict mode のため、関数として呼べば this は undefined になる。
function makeStrictFetch(payload = { ok: true, steps: [[{ name: 'MA' }]] }) {
  const calls = [];
  function strictFetch(url, init) {
    if (this !== undefined && this !== globalThis) {
      throw new TypeError("Failed to execute 'fetch' on 'Window': Illegal invocation");
    }
    calls.push({ url, init });
    return Promise.resolve({ ok: true, status: 200, json: async () => payload });
  }
  return { strictFetch, calls };
}

const REQ = {
  indicatorId: 'moving_averages', variant: 'default', params: { length: 9 },
  datasetRef: 'jp225_tick', timeframe: '1h', limit: 100, untilTime: 1000,
  formingSeq: [{ time: 1000, open: 1, high: 2, low: 0, close: 1.5 }],
};

test('束縛していない fetch（replay.js の既定値と同形）でも呼び出せる', async () => {
  const { strictFetch, calls } = makeStrictFetch();
  const client = new FormingSeqClient({ fetch: strictFetch });
  const steps = await client.computeSeq(REQ);
  assert.deepEqual(steps, [[{ name: 'MA' }]]);
  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, '/compute');
});

test('body は mode=latest_seq と formingSeq を含む', async () => {
  const { strictFetch, calls } = makeStrictFetch();
  await new FormingSeqClient({ fetch: strictFetch }).computeSeq(REQ);
  const body = JSON.parse(calls[0].init.body);
  assert.equal(body.mode, 'latest_seq');
  assert.equal(body.indicatorId, 'moving_averages');
  assert.deepEqual(body.formingSeq, REQ.formingSeq);
});

test('ok=false は例外（呼び出し側が従来経路へ落とせるように）', async () => {
  const { strictFetch } = makeStrictFetch({ ok: false, error: { message: 'boom' } });
  const client = new FormingSeqClient({ fetch: strictFetch });
  await assert.rejects(() => client.computeSeq(REQ), /足内一括計算に失敗: boom/);
});

test('steps が無い応答は空配列', async () => {
  const { strictFetch } = makeStrictFetch({ ok: true });
  assert.deepEqual(await new FormingSeqClient({ fetch: strictFetch }).computeSeq(REQ), []);
});

// ---- 計算.時間足（ISSUE-291） ----
//
// 実 UI で検出（:8000 リプレイ・5m チャート × 計算.時間足 1D の EMA5）: 足内の末尾点だけが
//   **5m の値**（実測 64970.3855＝5m 窓の latest と 4 桁一致）で描かれ、リビール値
//   （投影済み・66098.5467）と 1128 の段差になっていた。サーバは ISSUE-290 で H 形成足の
//   計算経路を持っていたが、本クライアントが `computeTimeframe` を送っていないため
//   その分岐に入らず、チャート足で計算していた（＝機能が無言で死んでいた）。

test('body は計算.時間足（computeTimeframe）を載せる', async () => {
  const { strictFetch, calls } = makeStrictFetch();

  await new FormingSeqClient({ fetch: strictFetch }).computeSeq({ ...REQ, computeTimeframe: '1D' });

  assert.equal(JSON.parse(calls[0].init.body).computeTimeframe, '1D',
    'これが無いとサーバはチャート足で計算する（足内だけ別物の値になる）');
});

test('チャート足（未指定）なら computeTimeframe を載せない＝従来ボディと同一', async () => {
  const { strictFetch, calls } = makeStrictFetch();

  await new FormingSeqClient({ fetch: strictFetch }).computeSeq(REQ);

  assert.ok(!('computeTimeframe' in JSON.parse(calls[0].init.body)));
});
