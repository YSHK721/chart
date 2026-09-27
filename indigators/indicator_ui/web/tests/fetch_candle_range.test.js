// /candles の問い合わせの単一ソース（fetchCandles と fetchCandleRange が同じ処理を使う・2026-09-27）。
//
// 由来: 統合層（unified_root.js）が simチャートの足を読むために `/candles` の問い合わせを手書きしていた
//   （手書き複製）。組み立てと応答の読み方を chart_app_wiring.js の 1 つの処理へ寄せた。
//
// 観測の境界: 注入する fetch（Test Spy）。要求した URL と回数を数える。
import { test } from 'node:test';
import assert from 'node:assert/strict';

import { fetchCandles, fetchCandleRange } from '../js/adapter/front/chart_app_wiring.js';

function spyFetch(response) {
  const urls = [];
  const fetchImpl = async (url) => { urls.push(url); return response(url); };
  return { urls, fetchImpl };
}

const ok = (candles) => () => ({ ok: true, status: 200, json: async () => ({ ok: true, candles }) });

test('範囲読みは fetchCandles と同じ問い合わせに from / to を足したもの', async () => {
  const candles = [{ time: 100 }, { time: 160 }];
  const a = spyFetch(ok(candles));
  const b = spyFetch(ok(candles));

  const live = await fetchCandles(a.fetchImpl, 'jp225_mt5_spread', '1m');
  const range = await fetchCandleRange(b.fetchImpl, {
    datasetRef: 'jp225_mt5_spread', timeframe: '1m', from: 100, to: 160,
  });

  assert.deepEqual(live, candles);
  assert.deepEqual(range, candles);
  assert.equal(b.urls[0], `${a.urls[0]}&from=100&to=160`);
});

test('範囲読みは失敗を例外にする（空配列と区別する）・fetchCandles は従来どおり null', async () => {
  const http = () => ({ ok: false, status: 400, json: async () => ({ ok: false }) });
  const refused = () => ({ ok: true, status: 200, json: async () => ({ ok: false, error: 'bad range' }) });
  const req = { datasetRef: 'x', timeframe: '1m', from: 1, to: 2 };

  await assert.rejects(fetchCandleRange(spyFetch(http).fetchImpl, req), /400/);
  await assert.rejects(fetchCandleRange(spyFetch(refused).fetchImpl, req), /bad range/);
  assert.equal(await fetchCandles(spyFetch(http).fetchImpl, 'x', '1m'), null);
  assert.equal(await fetchCandles(spyFetch(refused).fetchImpl, 'x', '1m'), null);
});

test('計算量: 1 回の読み込みで要求は 1 回・範囲の長さを変えても増えない', async () => {
  for (const n of [2, 5000]) {
    const candles = Array.from({ length: n }, (_, i) => ({ time: 60 * i }));
    const s = spyFetch(ok(candles));

    const got = await fetchCandleRange(s.fetchImpl, {
      datasetRef: 'x', timeframe: '1m', from: 0, to: 60 * (n - 1),
    });

    // 発行した要求 − 出力に使った応答 = 0
    assert.equal(s.urls.length - 1, 0);
    assert.equal(got.length, n);
  }
});
