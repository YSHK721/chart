// tf_period_jitter_buffer.js の検証（ローリング窓・先読み・LRU・tf 変更破棄・重複排除）。
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { TfPeriodJitterBuffer } from '../js/adapter/front/tf_period_jitter_buffer.js';

// チャンク（from,to）に対し、from に列 time=from を1本返す fake client。取得呼び出しを記録。
function fakeClient() {
  const calls = [];
  return {
    calls,
    async fetchWindow({ timeframe, from, to }) {
      calls.push([from, to]);
      return { tf: timeframe, unit: 0.5, from, to, columns: [{ time: from, levels: [[100, 1]] }] };
    },
  };
}

function newBuf(client, over = {}) {
  return new TfPeriodJitterBuffer({ client, datasetRef: 'jp225_tick', windowSec: 100, prefetch: 1, cacheMax: 12, ...over });
}

test('ensure: 可視チャンク＋前後 prefetch を取得発火する（先読み）', async () => {
  const c = fakeClient();
  const buf = newBuf(c);
  const targets = buf.ensure('5m', 250, 260); // 可視チャンク=200。prefetch1 → 100,200,300。
  assert.deepEqual(targets, [100, 200, 300]);
  await Promise.resolve(); await Promise.resolve();
  assert.deepEqual(c.calls.map((x) => x[0]).sort((a, b) => a - b), [100, 200, 300]);
});

test('getColumns: ready 列を time 昇順・[from,to] で返す', async () => {
  const c = fakeClient();
  const buf = newBuf(c);
  buf.ensure('5m', 250, 260);
  await Promise.resolve(); await Promise.resolve();
  const cols = buf.getColumns(0, 1000);
  assert.deepEqual(cols.map((x) => x.time), [100, 200, 300]);
  assert.equal(buf.unit(), 0.5);
});

test('キャッシュヒット: 同一チャンクは再取得しない', async () => {
  const c = fakeClient();
  const buf = newBuf(c);
  buf.ensure('5m', 250, 260);
  await Promise.resolve(); await Promise.resolve();
  const n1 = c.calls.length;
  buf.ensure('5m', 250, 260); // 同じ → 再取得なし
  await Promise.resolve();
  assert.equal(c.calls.length, n1);
});

test('LRU: cacheMax 超で古いチャンクを破棄', async () => {
  const c = fakeClient();
  const buf = newBuf(c, { cacheMax: 3, prefetch: 0 });
  for (const t of [0, 100, 200, 300, 400]) { buf.ensure('5m', t, t); await Promise.resolve(); await Promise.resolve(); }
  const cols = buf.getColumns(-1000, 10000);
  assert.ok(cols.length <= 3, `LRU で <=3 チャンク（実際 ${cols.length}）`);
  assert.deepEqual(cols.map((x) => x.time), [200, 300, 400]); // 直近3
});

test('tf 変更でキャッシュ破棄・再取得', async () => {
  const c = fakeClient();
  const buf = newBuf(c);
  buf.ensure('5m', 250, 260); await Promise.resolve(); await Promise.resolve();
  const before = c.calls.length;
  buf.ensure('15m', 250, 260); // tf 変更 → 全消去→再取得
  await Promise.resolve(); await Promise.resolve();
  assert.ok(c.calls.length > before, 'tf 変更で再取得が走る');
  assert.deepEqual(buf.getColumns(0, 1000).map((x) => x.time), [100, 200, 300]);
});
