// price_levels.test.js — 価格水準の単一ソース（E-02 PriceLevels）の検定（ISSUE-368 スライス 2）。
//
// price_levels.js は **JS 固有**（Python 権威なし）。理由: 「保持するのは価格のみ・距離は常に派生」
//   という不変条件は本統合で新設したもの（距離指定モードの撤廃＝依頼者確定要件）であり、
//   参照実装にも Python 側にも対応物が無い。ただし**派生の式と配置の不変条件は参照実装に従う**:
//     :949  損切り距離 D  = long: max(P₀ − stop, 0) / short: max(stop − P₀, 0)
//     :953  利確距離 TP   = long: (take − P₀) / short: (P₀ − take)。0 以下は 0（=無効）
//     :965  nearest       = long: min(建値) / short: max(建値)
//     :974  各建玉の距離   = long: 建値 − stop / short: stop − 建値。0 以下があれば stop_invalid
//   P₀ は参照実装 :359 のラベル「第1建値 P₀」＝ direct 既定シード :931 の customP[0] であり、
//   建値の単一ソース化（TBD-1）後は entry_prices[0] がその役割を負う。
//   gap（建玉間隔）は TBD-1 で撤廃済みのため本モジュールは一切扱わない。

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { createPriceLevels } from '../js/domain/price_levels.js';

const LONG_BASE = { direction: 'long', entryPrices: [58700, 59700, 60700], stopPrice: 58340, takePrice: 61500 };
const SHORT_BASE = { direction: 'short', entryPrices: [58700, 57700, 56700], stopPrice: 59060, takePrice: 55900 };

test('保持するのは価格のみで、距離は毎回派生する（距離を状態に持たない）', () => {
  const levels = createPriceLevels(LONG_BASE);
  assert.deepEqual(Object.keys(levels).sort(),
    ['direction', 'entryPrices', 'stopPrice', 'takePrice'].sort());
  assert.deepEqual([...levels.entryPrices], [58700, 59700, 60700]);
});

test('ロングの派生距離が参照実装 :949/:953/:965/:974 と一致する', () => {
  const levels = createPriceLevels(LONG_BASE);
  assert.equal(levels.nearestEntry(), 58700);        // :965 long は min
  assert.equal(levels.stopDistance(), 360);          // :949 P₀ − stop
  assert.equal(levels.takeDistance(), 2800);         // :953 take − P₀
  assert.deepEqual(levels.entryDistances(), [360, 1360, 2360]); // :974
});

test('ショートの派生距離が参照実装 :949/:953/:965/:974 と一致する（符号反転）', () => {
  const levels = createPriceLevels(SHORT_BASE);
  assert.equal(levels.nearestEntry(), 58700);        // :965 short は max
  assert.equal(levels.stopDistance(), 360);          // :949 stop − P₀
  assert.equal(levels.takeDistance(), 2800);         // :953 P₀ − take
  assert.deepEqual(levels.entryDistances(), [360, 1360, 2360]); // :974
});

test('利確が無い／逆側にあるときの距離は 0（:953 の t>0?t:0 をそのまま保つ）', () => {
  assert.equal(createPriceLevels({ ...LONG_BASE, takePrice: null }).takeDistance(), 0);
  assert.equal(createPriceLevels({ ...LONG_BASE, takePrice: 58000 }).takeDistance(), 0);
  assert.equal(createPriceLevels({ ...SHORT_BASE, takePrice: 60000 }).takeDistance(), 0);
});

test('損切りが建値より逆側にあると配置不変条件に違反する（:971/:974 stopInvalid と同条件）', () => {
  assert.deepEqual(createPriceLevels(LONG_BASE).validate(), []);
  // ロング: stop が最近接建値以上 → 違反
  assert.deepEqual(createPriceLevels({ ...LONG_BASE, stopPrice: 58700 }).validate(), ['stop_invalid']);
  assert.deepEqual(createPriceLevels({ ...LONG_BASE, stopPrice: 59000 }).validate(), ['stop_invalid']);
  // ショート: stop が最遠建値以下 → 違反
  assert.deepEqual(createPriceLevels(SHORT_BASE).validate(), []);
  assert.deepEqual(createPriceLevels({ ...SHORT_BASE, stopPrice: 58700 }).validate(), ['stop_invalid']);
});

test('利確が利益側に無いと配置不変条件に違反する（take があるときのみ判定）', () => {
  assert.deepEqual(createPriceLevels({ ...LONG_BASE, takePrice: 58700 }).validate(), ['take_invalid']);
  assert.deepEqual(createPriceLevels({ ...LONG_BASE, takePrice: null }).validate(), []);
  assert.deepEqual(createPriceLevels({ ...SHORT_BASE, takePrice: 58700 }).validate(), ['take_invalid']);
});

test('違反は同時に複数返る（先に見つかった 1 件で打ち切らない）', () => {
  const bad = createPriceLevels({ ...LONG_BASE, stopPrice: 59000, takePrice: 58000 });
  assert.deepEqual(bad.validate(), ['stop_invalid', 'take_invalid']);
});

test('with* は非破壊で新しい水準を返す（元は不変）', () => {
  const levels = createPriceLevels(LONG_BASE);
  const moved = levels.withStop(58000);
  assert.equal(levels.stopPrice, 58340, '元は不変');
  assert.equal(moved.stopDistance(), 700);

  const reEntry = levels.withEntry(1, 59000);
  assert.deepEqual([...levels.entryPrices], [58700, 59700, 60700], '元は不変');
  assert.deepEqual([...reEntry.entryPrices], [58700, 59000, 60700]);

  const reTake = levels.withTake(62000);
  assert.equal(levels.takePrice, 61500, '元は不変');
  assert.equal(reTake.takeDistance(), 3300);

  const cleared = levels.withTake(null);
  assert.equal(cleared.takePrice, null);
  assert.equal(cleared.takeDistance(), 0);
});

test('範囲外の建玉番号・不正な方向・建値 0 本は明示失敗させる（無音で倒さない）', () => {
  const levels = createPriceLevels(LONG_BASE);
  assert.throws(() => levels.withEntry(3, 60000), /建玉番号/);
  assert.throws(() => levels.withEntry(-1, 60000), /建玉番号/);
  assert.throws(() => createPriceLevels({ ...LONG_BASE, direction: 'up' }), /direction/);
  assert.throws(() => createPriceLevels({ ...LONG_BASE, entryPrices: [] }), /entryPrices/);
});
