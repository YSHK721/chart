// tf_period_profile_client.js の検証（URL 組立・fetch・パース・失敗時 null）。
import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  buildTfPeriodUrl, parseTfPeriod, TfPeriodProfileClient,
} from '../js/adapter/front/tf_period_profile_client.js';

test('buildTfPeriodUrl: datasetRef 必須・timeframe/from/to を付加', () => {
  assert.equal(
    buildTfPeriodUrl({ datasetRef: 'jp225_tick', timeframe: '5m', from: 1000, to: 2000 }),
    '/tf_period_profile?datasetRef=jp225_tick&timeframe=5m&from=1000&to=2000',
  );
});

test('parseTfPeriod: ok の columns を整形／ok:false・非配列は null', () => {
  const p = parseTfPeriod({ ok: true, tf: '5m', unit: 0.0255, from: 1, to: 9, columns: [{ time: 5 }] });
  assert.deepEqual(p, { tf: '5m', unit: 0.0255, from: 1, to: 9, columns: [{ time: 5 }] });
  assert.equal(parseTfPeriod({ ok: false }), null);
  assert.equal(parseTfPeriod({ ok: true }), null); // columns 欠落
  assert.equal(parseTfPeriod(null), null);
});

test('fetchWindow: 成功で {unit, columns}／HTTP 非ok・例外は null', async () => {
  const ok = new TfPeriodProfileClient({
    fetch: async () => ({ ok: true, async json() { return { ok: true, tf: '1m', unit: 1, from: 0, to: 60, columns: [{ time: 0, levels: [[10, 2]] }] }; } }),
  });
  const r = await ok.fetchWindow({ datasetRef: 'jp225_tick', timeframe: '1m', from: 0, to: 60 });
  assert.equal(r.columns[0].levels[0][1], 2);

  const bad = new TfPeriodProfileClient({ fetch: async () => ({ ok: false }) });
  assert.equal(await bad.fetchWindow({ datasetRef: 'jp225_tick' }), null);

  const thrown = new TfPeriodProfileClient({ fetch: async () => { throw new Error('net'); } });
  assert.equal(await thrown.fetchWindow({ datasetRef: 'jp225_tick' }), null);

  const noFetch = new TfPeriodProfileClient({});
  assert.equal(await noFetch.fetchWindow({ datasetRef: 'x' }), null);
});
