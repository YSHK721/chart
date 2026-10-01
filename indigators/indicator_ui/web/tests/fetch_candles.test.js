// GET /candles の問い合わせ（fetchCandles）。範囲読み fetchCandleRange は借り手が 0 になり 2026-10-01 に撤去した。
//
// 観測の境界: 注入する fetch（Test Spy）。要求した URL と回数を数える。
import { test } from 'node:test';
import assert from 'node:assert/strict';

import * as wiring from '../js/adapter/front/chart_app_wiring.js';

const { fetchCandles } = wiring;

function spyFetch(response) {
  const urls = [];
  const fetchImpl = async (url) => { urls.push(url); return response(url); };
  return { urls, fetchImpl };
}

const ok = (candles) => () => ({ ok: true, status: 200, json: async () => ({ ok: true, candles }) });

test('fetchCandles は datasetRef・timeframe・limit を問い合わせへ載せ、範囲の欄を載せない', async () => {
  const candles = [{ time: 100 }, { time: 160 }];
  const s = spyFetch(ok(candles));

  const got = await fetchCandles(s.fetchImpl, 'jp225_mt5_spread', '1m', 1500);

  assert.deepEqual(got, candles);
  assert.equal(s.urls[0], '/candles?datasetRef=jp225_mt5_spread&timeframe=1m&limit=1500');
});

test('fetchCandles は失敗（HTTP・ok:false・通信）を null にする', async () => {
  const http = () => ({ ok: false, status: 400, json: async () => ({ ok: false }) });
  const refused = () => ({ ok: true, status: 200, json: async () => ({ ok: false, error: 'bad' }) });
  const broken = () => { throw new Error('network'); };

  assert.equal(await fetchCandles(spyFetch(http).fetchImpl, 'x', '1m'), null);
  assert.equal(await fetchCandles(spyFetch(refused).fetchImpl, 'x', '1m'), null);
  assert.equal(await fetchCandles(spyFetch(broken).fetchImpl, 'x', '1m'), null);
});

test('範囲読みの口は公開しない（借り手の無い名前を残さない）', () => {
  assert.equal('fetchCandleRange' in wiring, false);
});

test('計算量: 1 回の読み込みで要求は 1 回・足の本数を変えても増えない', async () => {
  const issued = [];
  for (const n of [2, 5000]) {
    const candles = Array.from({ length: n }, (_, i) => ({ time: 60 * i }));
    const s = spyFetch(ok(candles));

    const got = await fetchCandles(s.fetchImpl, 'x', '1m');

    // 発行した要求 − 出力に使った応答 = 0
    assert.equal(s.urls.length - (got === null ? 0 : 1), 0);
    assert.equal(got.length, n);
    issued.push(s.urls.length);
  }
  assert.equal(issued[0], issued[1]);
});
