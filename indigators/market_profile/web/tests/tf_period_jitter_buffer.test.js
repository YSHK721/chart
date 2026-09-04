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

// ISSUE-055: windowSecForTf 注入で tf 連動窓。1D は大きな窓＝可視域を少数チャンクで満たす（fan-out 抑制）。
test('windowSecForTf: tf ごとにチャンク幅を切替える（fan-out 抑制）', async () => {
  const c = fakeClient();
  // 1D=1000 秒窓、それ以外=100 秒窓 の擬似規則。
  const buf = newBuf(c, { windowSecForTf: (tf) => (tf === '1D' ? 1000 : 100) });
  // 5m は 100 窓（従来どおり）。
  const t5 = buf.ensure('5m', 250, 260);
  assert.deepEqual(t5, [100, 200, 300]);
  // 1D は 1000 窓 → 可視 [250,260] は 1 チャンク（chunkStart=0）＋prefetch(1000 前後)。
  const t1d = buf.ensure('1D', 250, 260);
  assert.deepEqual(t1d, [-1000, 0, 1000]); // 幅 1000・floor(250/1000)*1000=0 中心。
});

// ISSUE-055: windowSecForTf 未注入時は固定 windowSec のまま（後方互換＝既存挙動不変）。
test('windowSecForTf 未注入: 固定 windowSec を維持（後方互換）', async () => {
  const c = fakeClient();
  const buf = newBuf(c); // windowSec:100 固定・windowSecForTf なし。
  assert.deepEqual(buf.ensure('1D', 250, 260), [100, 200, 300]); // tf に依らず 100 窓。
});

// ISSUE-083（日別プロファイルのライブ育成）: refreshAt(time) は time を含む ready チャンクを
//   stale-while-revalidate で再取得する（旧列を保持したまま背景取得→応答で差替え→onReady）。
test('refreshAt: ready チャンクを再取得し列を差し替える（成功で true・onReady 発火）', async () => {
  let readyCount = 0;
  const calls = [];
  let liveLevels = [[100, 1]];
  const client = {
    async fetchWindow({ from, to }) {
      calls.push([from, to]);
      return { unit: 0.5, columns: [{ time: from, levels: liveLevels }] };
    },
  };
  const buf = new TfPeriodJitterBuffer({
    client, datasetRef: 'jp225_tick', windowSec: 100, prefetch: 0, cacheMax: 12,
    onReady: () => { readyCount += 1; },
  });
  buf.ensure('5m', 250, 260);
  await Promise.resolve(); await Promise.resolve();
  assert.equal(calls.length, 1);
  const before = readyCount;
  liveLevels = [[100, 7], [110, 2]]; // ライブ進行で列が育った想定。
  const swapped = await buf.refreshAt(255);
  assert.equal(swapped, true, '再取得成功で true');
  assert.equal(calls.length, 2, '同一チャンクを再取得する');
  assert.deepEqual(buf.getColumns(0, 1000)[0].levels, [[100, 7], [110, 2]], '列が差し替わる');
  assert.equal(readyCount, before + 1, '差し替え完了で onReady 発火');
});

test('refreshAt: 未取得/loading チャンク・失敗応答では差し替えない（非破壊）', async () => {
  let fail = false;
  let block = null;
  const client = {
    async fetchWindow({ from }) {
      if (block) await block;
      if (fail) return null;
      return { unit: 0.5, columns: [{ time: from, levels: [[100, 1]] }] };
    },
  };
  const buf = new TfPeriodJitterBuffer({ client, datasetRef: 'd', windowSec: 100, prefetch: 0 });
  // 未取得チャンク → false（ensure 経路に委ねる・fetch しない）。
  assert.equal(await buf.refreshAt(255), false, '未取得チャンクは no-op');
  buf.ensure('5m', 250, 260);
  await Promise.resolve(); await Promise.resolve();
  // 失敗応答 → 旧列を保持して false。
  fail = true;
  assert.equal(await buf.refreshAt(255), false, '失敗は false');
  assert.deepEqual(buf.getColumns(0, 1000).map((c) => c.time), [200], '旧列を保持（非破壊）');
  fail = false;
  // 再取得中の連打 → 二重 fetch しない。
  let release;
  block = new Promise((r) => { release = r; });
  const p1 = buf.refreshAt(255);
  const p2 = buf.refreshAt(255); // 進行中 → 即 false。
  assert.equal(await p2, false, '再取得中の連打は no-op');
  release(); block = null;
  assert.equal(await p1, true);
});

test('refreshAt: 取得中に tf が変わったら破棄する（stale 防御）', async () => {
  let release;
  const block = new Promise((r) => { release = r; });
  let calls = 0;
  const client = {
    async fetchWindow({ from }) {
      calls += 1;
      if (calls >= 2) await block; // refreshAt の取得だけ遅延させる。
      return { unit: 0.5, columns: [{ time: from, levels: [[100, calls]] }] };
    },
  };
  const buf = new TfPeriodJitterBuffer({ client, datasetRef: 'd', windowSec: 100, prefetch: 0 });
  buf.ensure('5m', 250, 260);
  await Promise.resolve(); await Promise.resolve();
  const p = buf.refreshAt(255);
  buf.ensure('15m', 250, 260); // tf 変更 → キャッシュ破棄。
  release();
  assert.equal(await p, false, 'tf 変更後の応答は破棄');
});

// ---- 本番の合成形（ISSUE-255 追補・ISSUE-275 の再発防止） ----
//
// 上のテスト群は windowSec / prefetch を**テスト側が明示注入**して構築している。しかし本番の
// 合成根（composition_root_front.js）はどちらも渡さず、`windowSecForTf`（tf→チャンク幅の関数）と
// 既定 prefetch で組む。注入形だけを検証していると「本番が作る形」を一度も通さないまま緑になり、
// ISSUE-275（本番が渡さない前提をテストが自分で満たし、機能喪失に気付かなかった）と同じ穴が空く。
// 以下は**合成根と同じ引数の形**で構築し、tf 連動のチャンク幅と既定 prefetch を実測で固定する。
// 施行は tools/tests/test_composition_root_arg_parity.py。

test('本番の合成形（windowSecForTf 注入・windowSec/prefetch 非注入）で tf 連動のチャンク幅になる', async () => {
  const client = fakeClient();
  // 合成根と同一の導出規則（6h 下限・45 日上限・96 周期/チャンク）。
  const windowSecForTf = (tf) => {
    const bar = { '5m': 300, '1h': 3600, '1D': 86400 }[tf] || 86400;
    return Math.max(6 * 3600, Math.min(45 * 86400, bar * 96));
  };
  const buf = new TfPeriodJitterBuffer({
    client, datasetRef: 'jp225_tick', windowSecForTf, cacheMax: 32, onReady: () => {},
  });

  // 5m: 300*96=28,800s（8h）。可視 [30000,31000) は 28,800 床のチャンク 28,800 に属する。
  const t5 = buf.ensure('5m', 30000, 31000);
  assert.deepEqual(t5, [0, 28800, 57600], 'tf 連動幅（8h）で可視±prefetch=1 の 3 チャンクを取る');

  // 1D: 86400*96=8,294,400s を 45 日（3,888,000s）へクランプする。
  const t1d = buf.ensure('1D', 4000000, 4100000);
  assert.deepEqual(t1d, [0, 3888000, 7776000], '1D は 45 日上限へクランプされた幅になる');
});

test('本番の合成形では prefetch が既定 1（前後 1 チャンクを先読みする）', () => {
  const client = fakeClient();
  const buf = new TfPeriodJitterBuffer({
    client, datasetRef: 'jp225_tick', windowSecForTf: () => 100, cacheMax: 32, onReady: () => {},
  });

  const targets = buf.ensure('5m', 250, 260);
  assert.deepEqual(targets, [100, 200, 300], '既定 prefetch=1（明示注入なしでも前後を取る）');
});
