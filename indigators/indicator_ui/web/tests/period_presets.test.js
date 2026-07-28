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

// ---- 換算表 v1 の固定（§4.4-3: 版上げ時に必ず落ちること）---------------------

// 設計書 §4.3 の表そのもの。ここを書き換えるときは必ず版を上げる（v2 の追加）。
const TABLE_V1_EXPECTED = {
  '1m': { '1h': 60, '4h': 237, '1d': 1281, '1w': 6425, '1mo': 27505, '3mo': 82560, '6mo': 164788, '1y': 328956, '2y': 645585, '3y': 976826, '5y': 1634898 },
  '5m': { '1h': 12, '4h': 48, '1d': 266, '1w': 1329, '1mo': 5731, '3mo': 17099, '6mo': 34011, '1y': 68151, '2y': 136153, '3y': 204185, '5y': 340504 },
  '15m': { '1h': 4, '4h': 16, '1d': 89, '1w': 445, '1mo': 1921, '3mo': 5730, '6mo': 11400, '1y': 22814, '2y': 45585, '3y': 68373, '5y': 113987 },
  '30m': { '1h': 2, '4h': 8, '1d': 45, '1w': 225, '1mo': 971, '3mo': 2897, '6mo': 5765, '1y': 11538, '2y': 23052, '3y': 34578, '5y': 57646 },
  '1h': { '1h': 1, '4h': 4, '1d': 23, '1w': 115, '1mo': 496, '3mo': 1480, '6mo': 2946, '1y': 5895, '2y': 11781, '3y': 17670, '5y': 29456 },
  '4h': { '4h': 1, '1d': 6, '1w': 31, '1mo': 134, '3mo': 401, '6mo': 798, '1y': 1597, '2y': 3195, '3y': 4789, '5y': 7985 },
  '1D': { '1d': 1, '1w': 5, '1mo': 21, '3mo': 65, '6mo': 129, '1y': 258, '2y': 516, '3y': 774, '5y': 1291 },
  '1W': { '1w': 1, '1mo': 4, '3mo': 13, '6mo': 26, '1y': 52, '2y': 104, '3y': 156, '5y': 260 },
  '1M': { '1mo': 1, '3mo': 3, '6mo': 6, '1y': 12, '2y': 24, '3y': 36, '5y': 60 },
};

test('換算表 v1: 全セルが設計書 §4.3 と一致する（版上げ検出）', () => {
  assert.equal(PRESET_TABLE_VERSION, 'v1');
  for (const [tf, expected] of Object.entries(TABLE_V1_EXPECTED)) {
    assert.deepEqual(tableRow(REF, tf), expected, `${tf} の行が設計書と不一致`);
  }
});

test('換算表 v1: 日足の 1年 は 258（慣行値 252 ではない・D-1）', () => {
  assert.equal(tableRow(REF, '1D')['1y'], 258);
});

test('換算表 v1: 1時間足の 1日 は 23（名目 24 ではない・休場帯 1h45m）', () => {
  assert.equal(tableRow(REF, '1h')['1d'], 23);
});

test('未登録の datasetRef / timeframe は null（F-P2 / F-P3）', () => {
  assert.equal(tableRow('unknown_ref', '1D'), null);
  assert.equal(tableRow(REF, '2h'), null);
});

// ---- 提示上限は RECENT_BARS と一致する（§6.1-2・A-6・二重定義の検出）----------

test('MAX_PRESET_BARS は composition root の RECENT_BARS と一致する', () => {
  assert.equal(MAX_PRESET_BARS, RECENT_BARS);
});

// ---- UC-P01 提示集合（§6.1 の結果表）---------------------------------------

const PRESETS_EXPECTED = {
  '1m': [['1時間', 60], ['4時間', 237], ['1日', 1281]],
  '5m': [['1時間', 12], ['4時間', 48], ['1日', 266], ['1週間', 1329]],
  '15m': [['1時間', 4], ['4時間', 16], ['1日', 89], ['1週間', 445]],
  '30m': [['1時間', 2], ['4時間', 8], ['1日', 45], ['1週間', 225], ['1ヶ月', 971]],
  '1h': [['4時間', 4], ['1日', 23], ['1週間', 115], ['1ヶ月', 496], ['3ヶ月', 1480]],
  '4h': [['1日', 6], ['1週間', 31], ['1ヶ月', 134], ['3ヶ月', 401], ['6ヶ月', 798]],
  '1D': [['1週間', 5], ['1ヶ月', 21], ['3ヶ月', 65], ['6ヶ月', 129], ['1年', 258]],
  '1W': [['1ヶ月', 4], ['3ヶ月', 13], ['6ヶ月', 26], ['1年', 52], ['2年', 104]],
  '1M': [['3ヶ月', 3], ['6ヶ月', 6], ['1年', 12], ['2年', 24], ['3年', 36]],
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

test('presetsFor: 提示は最大 MAX_PRESETS 件（A-5）', () => {
  // 1D は候補が 8 件（1週間〜5年）あるが 5 件で打ち切られる。
  const got = presetsFor({ datasetRef: REF, timeframe: '1D' });
  assert.equal(MAX_PRESETS, 5);
  assert.equal(got.length, 5);
  assert.ok(!got.some((p) => p.unit === '5y'));
});

test('presetsFor: パラメータの min / max 制約を満たさない候補を除く（§6.1-3）', () => {
  // Arrange: 30m 足の '1時間'=2 本は min:3 を満たさない。
  const got = presetsFor({ datasetRef: REF, timeframe: '30m', min: 3 });
  // Assert: 2 本の候補が落ち、次の候補まで繰り上がる。
  assert.deepEqual(got.map((p) => p.bars), [8, 45, 225, 971, 2897].slice(0, 5).filter((b) => b <= MAX_PRESET_BARS));
});

test('presetsFor: max 制約でも候補を絞る', () => {
  const got = presetsFor({ datasetRef: REF, timeframe: '1D', max: 100 });
  assert.deepEqual(got.map((p) => p.bars), [5, 21, 65]);
});

test('presetsFor: 未登録 datasetRef / tf は空配列（UI を出さない）', () => {
  assert.deepEqual(presetsFor({ datasetRef: 'unknown', timeframe: '1D' }), []);
  assert.deepEqual(presetsFor({ datasetRef: REF, timeframe: '2h' }), []);
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
  // 1h の 3M は表の '3mo'=1480（3 × 496 = 1488 ではない）。
  assert.equal(parsePeriodInput('3M', ctx1h).bars, 1480);
  // 1h の 1w は表の '1w'=115。
  assert.equal(parsePeriodInput('1w', ctx1h).bars, 115);
});

test('parsePeriodInput: 直接エントリが無ければ基本単位の N 倍（§6.3-2）', () => {
  // 1h の 2M は表に無いので 2 × 496 = 992。
  assert.equal(parsePeriodInput('2M', ctx1h).bars, 992);
  // 1D の 2w は表に無いので 2 × 5 = 10。
  assert.equal(parsePeriodInput('2w', ctx1D).bars, 10);
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
  assert.equal(parsePeriodInput('3ヶ月', ctx1h).bars, 1480);
  assert.equal(parsePeriodInput('3mo', ctx1h).bars, 1480);
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
  assert.equal(parsePeriodInput('1M', ctx1h).bars, 496);
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
