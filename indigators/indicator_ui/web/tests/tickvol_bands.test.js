// tickvol_bands.js（domain）— 取引密度ハイライトの純ロジック検証（DOM/lwc/fetch 非依存・AAA）。
//
// 仕様（依頼者確定 2026-08-01）: 1 時間足以下のみ有効。バックエンドが返す帯
// （セッション日始端からの [startOff, endOff) 秒）に対し、**スロットの 50% 以上が帯に入るバー**を塗る。
// セッション日境界は session_day.js が唯一の規則源（DST 日は 23h/25h＝start+86400 禁止）。

import { test } from 'node:test';
import assert from 'node:assert/strict';

import {
  TICKVOL_BANDS_MAX_BAR_SEC,
  tickvolBandsSupportsTf,
  bandOverlapSec,
  paintedBarFlags,
  mergeRuns,
  bandRangesForCandles,
} from '../js/domain/tickvol_bands.js';
import { TF_BAR_SEC } from '../js/domain/tf_meta.js';
import { sessionDayStart, nextSessionDayStart } from '../js/domain/session_day.js';

// --- 時間足ゲート（1h 以下） --------------------------------------------------- //
test('supports only timeframes of 1h or shorter, derived from the tf ledger', () => {
  assert.equal(TICKVOL_BANDS_MAX_BAR_SEC, 3600);
  for (const tf of ['1m', '5m', '15m', '30m', '1h']) {
    assert.equal(tickvolBandsSupportsTf(tf), true, tf);
  }
  for (const tf of ['4h', '1D', '1W', '1M']) {
    assert.equal(tickvolBandsSupportsTf(tf), false, tf);
  }
});

test('unknown timeframes are unsupported (never painted by accident)', () => {
  assert.equal(tickvolBandsSupportsTf('3h'), false);
  assert.equal(tickvolBandsSupportsTf(undefined), false);
  assert.equal(tickvolBandsSupportsTf(null), false);
});

test('the supported set is exactly the tf ledger entries at or below 1h', () => {
  // 値の列挙でなく台帳から導出する＝tf の追加・改名で集合がずれない。
  const expected = Object.keys(TF_BAR_SEC).filter((tf) => TF_BAR_SEC[tf] <= 3600);
  assert.deepEqual(Object.keys(TF_BAR_SEC).filter(tickvolBandsSupportsTf), expected);
});

// --- 重なり秒 ------------------------------------------------------------------ //
const BANDS = [{ startOff: 10800, endOff: 19800 }, { startOff: 59400, endOff: 67500 }];

test('bandOverlapSec measures the seconds a bar slot shares with the bands', () => {
  assert.equal(bandOverlapSec(10800, 900, BANDS), 900);        // 完全に内側
  assert.equal(bandOverlapSec(10500, 900, BANDS), 600);        // 左端をまたぐ
  assert.equal(bandOverlapSec(19500, 900, BANDS), 300);        // 右端をまたぐ
  assert.equal(bandOverlapSec(0, 900, BANDS), 0);              // 帯の外
  assert.equal(bandOverlapSec(0, 86400, BANDS), 9000 + 8100);  // 複数帯は加算
});

test('bandOverlapSec is 0 for degenerate inputs instead of throwing', () => {
  assert.equal(bandOverlapSec(0, 900, null), 0);
  assert.equal(bandOverlapSec(0, 0, BANDS), 0);
});

// --- 塗るバーの判定 ------------------------------------------------------------ //
const DAY = sessionDayStart(Date.UTC(2026, 6, 22, 12) / 1000); // 2026-07-22 のセッション日始端

function barsAt(offsets, barSec) {
  return offsets.map((o) => ({ time: DAY + o, barSec }));
}

test('a bar is painted when at least half of its slot lies inside a band', () => {
  const barSec = 900;
  const candles = barsAt([9900, 10350, 10800, 19350, 19800], barSec);
  //  9900: 重なり 0     → 塗らない
  // 10350: 重なり 450   → ちょうど 50%＝塗る（境界を明示的に固定する）
  // 10800: 重なり 900   → 塗る
  // 19350: 重なり 450   → ちょうど 50%＝塗る
  // 19800: 重なり 0     → 塗らない
  assert.deepEqual(paintedBarFlags(candles, BANDS, barSec), [false, true, true, true, false]);
});

test('a band shorter than the bar does not paint the bar (no 4x exaggeration on 1h)', () => {
  // 1 時間足に対する 15 分帯はスロットの 25% しか占めない＝塗らない。
  const short = [{ startOff: 30600, endOff: 31500 }];
  const candles = barsAt([28800], 3600);
  assert.deepEqual(paintedBarFlags(candles, short, 3600), [false]);
  // 同じ帯でも 15 分足なら完全一致で塗る。
  assert.deepEqual(paintedBarFlags(barsAt([30600], 900), short, 900), [true]);
});

test('bars are classified against their own session day (offset resets each day)', () => {
  const barSec = 900;
  const day2 = nextSessionDayStart(DAY);
  const candles = [{ time: DAY + 10800 }, { time: day2 + 10800 }, { time: day2 + 900 }];
  assert.deepEqual(paintedBarFlags(candles, BANDS, barSec), [true, true, false]);
});

test('a DST session of 25h keeps classifying by session offset (start+86400 would slip)', () => {
  // 2026-11-01 の米 DST 終了を含むセッション（25 時間）。
  const dstDay = sessionDayStart(Date.UTC(2026, 10, 1, 12) / 1000);
  assert.equal(nextSessionDayStart(dstDay) - dstDay, 25 * 3600);
  const candles = [{ time: dstDay + 10800 }, { time: dstDay + 24 * 3600 }];
  assert.deepEqual(paintedBarFlags(candles, BANDS, 900), [true, false]);
});

test('no bands, no bars, or a bad bar time paint nothing instead of throwing', () => {
  assert.deepEqual(paintedBarFlags([], BANDS, 900), []);
  assert.deepEqual(paintedBarFlags(barsAt([10800], 900), [], 900), [false]);
  assert.deepEqual(paintedBarFlags([{ time: NaN }, { time: DAY + 10800 }], BANDS, 900), [false, true]);
  assert.deepEqual(paintedBarFlags(barsAt([10800], 900), BANDS, 0), [false]);
});

// --- 連続バーの結合 ------------------------------------------------------------ //
test('consecutive painted bars merge into one run', () => {
  assert.deepEqual(mergeRuns([false, true, true, false, true]), [
    { from: 1, to: 2 }, { from: 4, to: 4 },
  ]);
  assert.deepEqual(mergeRuns([]), []);
  assert.deepEqual(mergeRuns([false, false]), []);
});

test('bandRangesForCandles returns the first/last bar time of each run', () => {
  const candles = barsAt([9900, 10800, 11700, 30600, 59400], 900);
  assert.deepEqual(bandRangesForCandles(candles, BANDS, 900), [
    { from: DAY + 10800, to: DAY + 11700 },
    { from: DAY + 59400, to: DAY + 59400 },
  ]);
});

test('missing bars inside a band are simply not painted (no empty region is filled)', () => {
  // 帯の中央のバーが欠損（休場・祝日）していても、存在するバーだけが帯の端になる。
  // 時刻レンジを直接塗る実装だと、この欠損区間まで背景が塗られてしまう。
  const candles = barsAt([10800, 11700, /* 12600 欠損 */ 13500], 900);
  assert.deepEqual(bandRangesForCandles(candles, BANDS, 900), [
    { from: DAY + 10800, to: DAY + 13500 },
  ]);
  // 帯の外のバーが間に挟まれば run は割れる（添字の隣接が切れる）。
  const withOutside = [
    { time: DAY + 10800 }, { time: DAY + 9900 }, { time: DAY + 11700 },
  ];
  assert.deepEqual(bandRangesForCandles(withOutside, BANDS, 900), [
    { from: DAY + 10800, to: DAY + 10800 },
    { from: DAY + 11700, to: DAY + 11700 },
  ]);
});
