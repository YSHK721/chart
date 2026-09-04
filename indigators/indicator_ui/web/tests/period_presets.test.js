// period_presets.js の仕様検証（node:test / node:assert）。
//
// 対象: usecase/period_presets.js（純関数・DOM/chart/fetch 非依存）。
// 設計入力: 基本設計_期間プリセット.md v0.1.0
//   §4.3 換算表 v1 / §4.4 表の凍結 / §6.1 提示 / §6.3 期間表記入力 / §6.5 実効計算時間足。
// 構造: Arrange-Act-Assert（AAA）。各テスト独立・再現可能（F.I.R.S.T）。

import { test } from 'node:test';
import assert from 'node:assert/strict';

import {
  MAX_PRESETS,
  MAX_PRESET_BARS,
  MIN_PRESET_BARS,
  PRESET_TABLE_VERSION,
  effectiveTimeframe,
  parsePeriodInput,
  presetsFor,
  tableRow,
  unitLabel,
} from '../js/usecase/period_presets.js';
import { RECENT_BARS } from '../js/adapter/front/composition_root_front.js';

const REF = 'jp225_tick';

// ---- 換算表 v2 の固定（§4.4-3: 版上げ時に必ず落ちること）---------------------

// 設計書 §4.3（v2・2026-07-29 計測）の表そのもの。ここを書き換えるときは必ず版を上げる。
//   v2 は v1 の 11 単位へ中間刻み 9 単位（2h/6h/12h/2d/3d/2w/3w/2mo/9mo）を加法したもの。
const TABLE_V2_EXPECTED = {
  '1m': { '1h': 60, '2h': 120, '4h': 237, '6h': 352, '12h': 615, '1d': 1281, '2d': 2455, '3d': 3107, '1w': 6424, '2w': 12788, '3w': 19118, '1mo': 27515, '2mo': 54692, '3mo': 82563, '6mo': 164817, '9mo': 246610, '1y': 328975, '2y': 645583, '3y': 976817, '5y': 1634919 },
  '5m': { '1h': 12, '2h': 24, '4h': 48, '6h': 72, '12h': 131, '1d': 266, '2d': 528, '3d': 646, '1w': 1329, '2w': 2649, '3w': 3961, '1mo': 5730, '2mo': 11338, '3mo': 17098, '6mo': 34009, '9mo': 51081, '1y': 68151, '2y': 136155, '3y': 204186, '5y': 340504 },
  '15m': { '1h': 4, '2h': 8, '4h': 16, '6h': 24, '12h': 44, '1d': 89, '2d': 178, '3d': 216, '1w': 445, '2w': 889, '3w': 1322, '1mo': 1919, '2mo': 3798, '3mo': 5729, '6mo': 11400, '9mo': 17096, '1y': 22814, '2y': 45584, '3y': 68369, '5y': 113985 },
  '30m': { '1h': 2, '2h': 4, '4h': 8, '6h': 12, '12h': 22, '1d': 45, '2d': 90, '3d': 108, '1w': 225, '2w': 450, '3w': 668, '1mo': 969, '2mo': 1921, '3mo': 2897, '6mo': 5765, '9mo': 8646, '1y': 11538, '2y': 23052, '3y': 34576, '5y': 57645 },
  '1h': { '1h': 1, '2h': 2, '4h': 4, '6h': 6, '12h': 11, '1d': 23, '2d': 46, '3d': 56, '1w': 115, '2w': 230, '3w': 341, '1mo': 495, '2mo': 981, '3mo': 1481, '6mo': 2947, '9mo': 4419, '1y': 5895, '2y': 11781, '3y': 17669, '5y': 29456 },
  '4h': { '4h': 1, '6h': 1, '12h': 3, '1d': 6, '2d': 12, '3d': 15, '1w': 31, '2w': 62, '3w': 93, '1mo': 134, '2mo': 266, '3mo': 401, '6mo': 798, '9mo': 1197, '1y': 1597, '2y': 3195, '3y': 4789, '5y': 7985 },
  '1D': { '1d': 1, '2d': 2, '3d': 2, '1w': 5, '2w': 10, '3w': 15, '1mo': 21, '2mo': 43, '3mo': 65, '6mo': 129, '9mo': 193, '1y': 258, '2y': 516, '3y': 774, '5y': 1291 },
  '1W': { '1w': 1, '2w': 2, '3w': 3, '1mo': 4, '2mo': 8, '3mo': 13, '6mo': 26, '9mo': 39, '1y': 52, '2y': 104, '3y': 156, '5y': 260 },
  '1M': { '1mo': 1, '2mo': 2, '3mo': 3, '6mo': 6, '9mo': 9, '1y': 12, '2y': 24, '3y': 36, '5y': 60 },
};

test('換算表 v2: 全セルが設計書 §4.3 と一致する（版上げ検出）', () => {
  assert.equal(PRESET_TABLE_VERSION, 'v2');
  for (const [tf, expected] of Object.entries(TABLE_V2_EXPECTED)) {
    assert.deepEqual(tableRow(REF, tf), expected, `${tf} の行が設計書と不一致`);
  }
});

test('換算表 v2: 日足の 1年 は 258（慣行値 252 ではない・D-1）', () => {
  assert.equal(tableRow(REF, '1D')['1y'], 258);
});

test('換算表 v2: 1時間足の 1日 は 23（名目 24 ではない・休場帯 1h45m）', () => {
  assert.equal(tableRow(REF, '1h')['1d'], 23);
});

test('未登録の datasetRef / timeframe は null（F-P2 / F-P3）', () => {
  assert.equal(tableRow('unknown_ref', '1D'), null);
  assert.equal(tableRow(REF, '3m'), null); // 存在しない時間足（'2h' は v2 の単位キーだが tf ではない）
});

// ---- 提示上限は RECENT_BARS と一致する（§6.1-2・A-6・二重定義の検出）----------

test('MAX_PRESET_BARS は composition root の RECENT_BARS と一致する', () => {
  assert.equal(MAX_PRESET_BARS, RECENT_BARS);
});

// ---- UC-P01 提示集合（§6.1 の結果表）---------------------------------------

const PRESETS_EXPECTED = {
  '1m': [['1時間', 60], ['2時間', 120], ['4時間', 237], ['6時間', 352], ['12時間', 615], ['1日', 1281]],
  '5m': [['1時間', 12], ['2時間', 24], ['4時間', 48], ['6時間', 72], ['12時間', 131], ['1日', 266], ['2日', 528], ['3日', 646], ['1週間', 1329]],
  '15m': [['1時間', 4], ['2時間', 8], ['4時間', 16], ['6時間', 24], ['12時間', 44], ['1日', 89], ['2日', 178], ['3日', 216], ['1週間', 445], ['2週間', 889], ['3週間', 1322]],
  '30m': [['1時間', 2], ['2時間', 4], ['4時間', 8], ['6時間', 12], ['12時間', 22], ['1日', 45], ['2日', 90], ['3日', 108], ['1週間', 225], ['2週間', 450], ['3週間', 668], ['1ヶ月', 969]],
  '1h': [['2時間', 2], ['4時間', 4], ['6時間', 6], ['12時間', 11], ['1日', 23], ['2日', 46], ['3日', 56], ['1週間', 115], ['2週間', 230], ['3週間', 341], ['1ヶ月', 495], ['2ヶ月', 981], ['3ヶ月', 1481]],
  '4h': [['12時間', 3], ['1日', 6], ['2日', 12], ['3日', 15], ['1週間', 31], ['2週間', 62], ['3週間', 93], ['1ヶ月', 134], ['2ヶ月', 266], ['3ヶ月', 401], ['6ヶ月', 798], ['9ヶ月', 1197]],
  '1D': [['2日', 2], ['1週間', 5], ['2週間', 10], ['3週間', 15], ['1ヶ月', 21], ['2ヶ月', 43], ['3ヶ月', 65], ['6ヶ月', 129], ['9ヶ月', 193], ['1年', 258], ['2年', 516], ['3年', 774], ['5年', 1291]],
  '1W': [['2週間', 2], ['3週間', 3], ['1ヶ月', 4], ['2ヶ月', 8], ['3ヶ月', 13], ['6ヶ月', 26], ['9ヶ月', 39], ['1年', 52], ['2年', 104], ['3年', 156], ['5年', 260]],
  '1M': [['2ヶ月', 2], ['3ヶ月', 3], ['6ヶ月', 6], ['9ヶ月', 9], ['1年', 12], ['2年', 24], ['3年', 36], ['5年', 60]],
};

test('presetsFor: 9 時間足すべてで設計書 §6.1 の結果表と一致する', () => {
  for (const [tf, expected] of Object.entries(PRESETS_EXPECTED)) {
    const got = presetsFor({ datasetRef: REF, timeframe: tf }).map((p) => [p.label, p.bars]);
    assert.deepEqual(got, expected, `${tf} の提示集合が設計書と不一致`);
  }
});

test('presetsFor: 本数 1 の単位は提示しない（下限 MIN_PRESET_BARS）', () => {
  // Arrange: 1h 足の '1h' は 1 本 / 1D 足の '1d' は 1 本。
  const oneHour = presetsFor({ datasetRef: REF, timeframe: '1h' });
  const oneDay = presetsFor({ datasetRef: REF, timeframe: '1D' });
  // Assert
  assert.equal(MIN_PRESET_BARS, 2);
  assert.ok(!oneHour.some((p) => p.unit === '1h'));
  assert.ok(!oneDay.some((p) => p.unit === '1d'));
});

test('presetsFor: RECENT_BARS 超の候補は提示しない（F-P4）', () => {
  const got = presetsFor({ datasetRef: REF, timeframe: '1m' });
  assert.ok(got.every((p) => p.bars <= MAX_PRESET_BARS));
  // 1m の 1週間=6425 は上限超のため落ちる。
  assert.ok(!got.some((p) => p.unit === '1w'));
});

test('presetsFor: 提示は最大 MAX_PRESETS 件（暴走防止の安全弁）', () => {
  // v2 では上限 14 件・実際の最大候補数は 13 件（1h / 1D）＝打ち切りは起きない。
  assert.equal(MAX_PRESETS, 14);
  for (const tf of ['1m', '5m', '15m', '30m', '1h', '4h', '1D', '1W', '1M']) {
    assert.ok(presetsFor({ datasetRef: REF, timeframe: tf }).length <= MAX_PRESETS);
  }
  // 1D は 5 年（1291 本）まで提示される（v1 では 5 件打ち切りで落ちていた）。
  const oneDay = presetsFor({ datasetRef: REF, timeframe: '1D' });
  assert.equal(oneDay.length, 13);
  assert.ok(oneDay.some((p) => p.unit === '5y'));
});

test('presetsFor: 同一本数へ落ちる候補は期間の短い方だけを提示する（重複行の抑止）', () => {
  // 1D 足では '2d' も '3d' も 2 本（実測）。短い '2d' のみを出す。
  const got = presetsFor({ datasetRef: REF, timeframe: '1D' });
  const two = got.filter((p) => p.bars === 2);
  assert.equal(two.length, 1);
  assert.equal(two[0].unit, '2d');
  // 全時間足で本数の重複が無い。
  for (const tf of ['1m', '5m', '15m', '30m', '1h', '4h', '1D', '1W', '1M']) {
    const bars = presetsFor({ datasetRef: REF, timeframe: tf }).map((p) => p.bars);
    assert.equal(new Set(bars).size, bars.length, `${tf} に同一本数の重複行がある`);
  }
});

test('presetsFor: パラメータの min / max 制約を満たさない候補を除く（§6.1-3）', () => {
  // Arrange: 30m 足の '1時間'=2 本は min:3 を満たさない。
  const got = presetsFor({ datasetRef: REF, timeframe: '30m', min: 3 });
  // Assert: 2 本の候補（'1時間'）が落ち、残りはすべて提示される。
  assert.deepEqual(got.map((p) => p.bars), [4, 8, 12, 22, 45, 90, 108, 225, 450, 668, 969]);
});

test('presetsFor: max 制約でも候補を絞る', () => {
  const got = presetsFor({ datasetRef: REF, timeframe: '1D', max: 100 });
  assert.deepEqual(got.map((p) => p.bars), [2, 5, 10, 15, 21, 43, 65]);
});

test('presetsFor: 未登録 datasetRef / tf は空配列（UI を出さない）', () => {
  assert.deepEqual(presetsFor({ datasetRef: 'unknown', timeframe: '1D' }), []);
  assert.deepEqual(presetsFor({ datasetRef: REF, timeframe: '3m' }), []);
});

// ---- UC-P03 期間表記入力（§6.3）--------------------------------------------

const ctx1h = { datasetRef: REF, timeframe: '1h' };
const ctx1D = { datasetRef: REF, timeframe: '1D' };

test('parsePeriodInput: 単位なしは本数そのもの', () => {
  assert.deepEqual(parsePeriodInput('50', ctx1h), { ok: true, bars: 50, label: null });
});

test('parsePeriodInput: 1時間足の 5d は 115（設計 §1.2 の確定値）', () => {
  const r = parsePeriodInput('5d', ctx1h);
  assert.equal(r.ok, true);
  assert.equal(r.bars, 115);
  assert.equal(r.label, '5日');
});

test('parsePeriodInput: 表に直接エントリがあれば実測値を使う（§6.3-1）', () => {
  // 1h の 3M は表の '3mo'=1481（3 × 495 = 1485 ではない）。
  assert.equal(parsePeriodInput('3M', ctx1h).bars, 1481);
  // 1h の 1w は表の '1w'=115。
  assert.equal(parsePeriodInput('1w', ctx1h).bars, 115);
});

test('parsePeriodInput: 表 v2 で直接エントリが増えた単位は実測値を使う（§6.3-1）', () => {
  // v1 では 2M / 2w は表に無く「基本単位の N 倍」だったが、v2 は直接エントリを持つ。
  assert.equal(parsePeriodInput('2M', ctx1h).bars, 981);  // 表 '2mo'（2 × 495 = 990 ではない）
  assert.equal(parsePeriodInput('2w', ctx1D).bars, 10);   // 表 '2w'（2 × 5 と一致）
});

test('parsePeriodInput: 直接エントリが無ければ基本単位の N 倍（§6.3-2）', () => {
  // 1D の 4w は表に無いので 4 × 5 = 20。
  assert.equal(parsePeriodInput('4w', ctx1D).bars, 20);
  // 1D の 5M は表に無いので 5 × 21 = 105。
  assert.equal(parsePeriodInput('5M', ctx1D).bars, 105);
});

test('parsePeriodInput: 分単位は名目換算（§6.3-3）', () => {
  assert.equal(parsePeriodInput('30min', { datasetRef: REF, timeframe: '1m' }).bars, 30);
  assert.equal(parsePeriodInput('30min', { datasetRef: REF, timeframe: '5m' }).bars, 6);
  assert.equal(parsePeriodInput('60分', { datasetRef: REF, timeframe: '15m' }).bars, 4);
});

test('parsePeriodInput: 日本語表記・全角・大文字小文字を受理する', () => {
  assert.equal(parsePeriodInput('5日', ctx1h).bars, 115);
  assert.equal(parsePeriodInput('５ｄ', ctx1h).bars, 115);
  assert.equal(parsePeriodInput(' 5 D ', ctx1h).bars, 115);
  assert.equal(parsePeriodInput('3ヶ月', ctx1h).bars, 1481);
  assert.equal(parsePeriodInput('3mo', ctx1h).bars, 1481);
  assert.equal(parsePeriodInput('1年', ctx1D).bars, 258);
  assert.equal(parsePeriodInput('1y', ctx1D).bars, 258);
});

test('parsePeriodInput: 小数の係数を受理する', () => {
  // 1D の 0.5w = round(0.5 × 5) = 3（四捨五入・§6.3-4）。
  assert.equal(parsePeriodInput('0.5w', ctx1D).bars, 3);
});

test('parsePeriodInput: 裸の m は曖昧として拒否する（分と月の衝突）', () => {
  const r = parsePeriodInput('5m', ctx1h);
  assert.equal(r.ok, false);
  assert.equal(r.code, 'ambiguous_unit');
});

test('parsePeriodInput: M（月）と min（分）は区別される', () => {
  assert.equal(parsePeriodInput('1M', ctx1h).bars, 495);
  assert.equal(parsePeriodInput('60min', ctx1h).bars, 1);
});

test('parsePeriodInput: 解釈不能な入力は syntax エラー（F-P1）', () => {
  for (const bad of ['abc', 'd', '5x', '']) {
    const r = parsePeriodInput(bad, ctx1h);
    assert.equal(r.ok, false, `${bad} は失敗するべき`);
  }
});

test('parsePeriodInput: 0 以下の係数は拒否する', () => {
  const r = parsePeriodInput('0d', ctx1h);
  assert.equal(r.ok, false);
  assert.equal(r.code, 'too_small');
});

test('parsePeriodInput: min / max 制約違反は代入せずエラー（§6.3-5・F-P1）', () => {
  // 1D の 1w = 5 本。min:10 を満たさない。
  const below = parsePeriodInput('1w', { ...ctx1D, min: 10 });
  assert.equal(below.ok, false);
  assert.equal(below.code, 'below_min');
  // max:3 を超える。
  const above = parsePeriodInput('1w', { ...ctx1D, max: 3 });
  assert.equal(above.ok, false);
  assert.equal(above.code, 'above_max');
});

test('parsePeriodInput: RECENT_BARS 超は exceeds_capacity（F-P4）', () => {
  // 1m の 1w = 6425 本 > 1500。
  const r = parsePeriodInput('1w', { datasetRef: REF, timeframe: '1m' });
  assert.equal(r.ok, false);
  assert.equal(r.code, 'exceeds_capacity');
});

test('parsePeriodInput: 未登録 datasetRef は no_table（F-P3）', () => {
  const r = parsePeriodInput('5d', { datasetRef: 'unknown', timeframe: '1h' });
  assert.equal(r.ok, false);
  assert.equal(r.code, 'no_table');
});

// ---- 実効計算時間足（§6.5）--------------------------------------------------

test('effectiveTimeframe: timeframe override が chart / 未指定ならチャート足に追従する', () => {
  assert.equal(effectiveTimeframe({ timeframe: 'chart' }, '1D'), '1D');
  assert.equal(effectiveTimeframe({}, '5m'), '5m');
  assert.equal(effectiveTimeframe(null, '4h'), '4h');
});

test('effectiveTimeframe: 特定足の override はその足を返す（MTF）', () => {
  assert.equal(effectiveTimeframe({ timeframe: '1h' }, '1D'), '1h');
});

// ---- 表示名 ---------------------------------------------------------------

test('unitLabel: 単位キーを日本語表示名へ写す', () => {
  assert.equal(unitLabel('1w'), '1週間');
  assert.equal(unitLabel('3mo'), '3ヶ月');
  assert.equal(unitLabel('unknown'), 'unknown');
});
