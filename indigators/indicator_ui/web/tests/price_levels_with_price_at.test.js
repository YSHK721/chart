// price_levels_with_price_at.test.js — 水準種別 → 更新経路の表を固定する（ISSUE-479 Wave2 J-3）。
//
// 固定するのは「掴める水準を 1 種増やすときにドラッグ殻を改変しなくてよい」構造である。
// ドラッグ殻の `if (handle.kind === 'entry') ... else if ('stop') ... else if ('take')` は
// 「どの水準が掴めるか」という**ドメインの知識**が adapter へ漏れた形であり、種別が増えるたびに
// 殻が伸びる。更新経路の宣言は水準の所有者（domain の price_levels）に置く。
//
//   R1: ドラッグ殻に水準種別の文字列リテラルが 0 件（表引きへ委譲）。
//   R2: 3 種別で既存の非破壊更新メソッドと同一結果・未知種別と読み取り専用種別は null。
//   R3: 掴める種別 ∩ 読み取り専用種別 = 空（線プリミティブ側の宣言と食い違わない）。
//
// 計算量: 1 ドラッグあたり新しい PriceLevels の生成 − 出力に使った数(=1) = 0。
//   建玉本数を変えても生成は増えない（発行が入力量に比例しない＝オーダーの表明）。

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

import {
  createPriceLevels,
  withPriceAt,
  DRAGGABLE_KINDS,
} from '../js/domain/price_levels.js';
import { PriceLevelDragController } from '../js/adapter/front/price_level_drag_controller.js';

const LONG_BASE = {
  direction: 'long',
  entryPrices: [58700, 59700, 60700],
  stopPrice: 58340,
  takePrice: 61500,
};

const DRAG_CONTROLLER_SRC = fileURLToPath(
  new URL('../js/adapter/front/price_level_drag_controller.js', import.meta.url),
);
const LINES_PRIMITIVE_SRC = fileURLToPath(
  new URL('../js/adapter/front/price_level_lines_primitive.js', import.meta.url),
);

/** 行コメント・ブロックコメントを落とす（宣言の走査に文章を混ぜない）。 */
function stripComments(source) {
  return source.replace(/\/\*[\s\S]*?\*\//g, '').replace(/(^|[^:])\/\/[^\n]*/g, '$1');
}

// --------------------------------------------------------------------------- //
// R1: ドラッグ殻に水準種別のリテラルが無い
// --------------------------------------------------------------------------- //
test('ドラッグ殻が水準種別の文字列リテラルを持たない（更新経路は domain の表が持つ）', () => {
  const source = stripComments(readFileSync(DRAG_CONTROLLER_SRC, 'utf8'));
  const offenders = [...DRAGGABLE_KINDS].filter(
    (kind) => source.includes(`'${kind}'`) || source.includes(`"${kind}"`),
  );
  assert.deepEqual(offenders, [],
    `ドラッグ殻に水準種別が直書きされている（withPriceAt へ委譲すること）: ${offenders}`);
});

// --------------------------------------------------------------------------- //
// R2: 表引きの結果が既存メソッドと同一
// --------------------------------------------------------------------------- //
test('掴める 3 種別は既存の非破壊更新メソッドと同一結果を返す', () => {
  const levels = createPriceLevels(LONG_BASE);

  assert.deepEqual(
    { ...withPriceAt(levels, 'entry', 1, 59900) },
    { ...levels.withEntry(1, 59900) });
  assert.deepEqual(
    { ...withPriceAt(levels, 'stop', null, 58200) },
    { ...levels.withStop(58200) });
  assert.deepEqual(
    { ...withPriceAt(levels, 'take', null, 62000) },
    { ...levels.withTake(62000) });
});

test('未知種別と読み取り専用種別（損切り水準の自動線）は null＝更新しない', () => {
  const levels = createPriceLevels(LONG_BASE);
  assert.equal(withPriceAt(levels, 'losscut', null, 58000), null);
  assert.equal(withPriceAt(levels, 'unknown_kind', null, 58000), null);
  assert.equal(withPriceAt(levels, undefined, null, 58000), null);
});

test('建玉番号が範囲外なら既存の withEntry と同じ例外契約（範囲外は投げる）', () => {
  const levels = createPriceLevels(LONG_BASE);
  assert.throws(() => withPriceAt(levels, 'entry', 9, 59000), /建玉番号が範囲外/);
});

// --------------------------------------------------------------------------- //
// R3: 掴める種別と読み取り専用種別が食い違わない
// --------------------------------------------------------------------------- //
test('掴める種別と読み取り専用種別は交わらない', () => {
  const source = stripComments(readFileSync(LINES_PRIMITIVE_SRC, 'utf8'));
  const declared = source.match(/READ_ONLY_KINDS\s*=\s*new Set\(\[([^\]]*)\]\)/);
  assert.ok(declared, '線プリミティブの READ_ONLY_KINDS 宣言が見つからない（前提崩壊）');
  const readOnly = [...declared[1].matchAll(/'([^']+)'|"([^"]+)"/g)].map((m) => m[1] ?? m[2]);
  assert.ok(readOnly.length > 0, 'READ_ONLY_KINDS が空（検定の前提崩壊）');
  const overlap = readOnly.filter((kind) => DRAGGABLE_KINDS.has(kind));
  assert.deepEqual(overlap, [], `掴める種別と読み取り専用種別が重複: ${overlap}`);
});

// --------------------------------------------------------------------------- //
// ドラッグ殻の実挙動（委譲後も同じ）
// --------------------------------------------------------------------------- //
function dragHarness(entryCount) {
  const entryPrices = Array.from({ length: entryCount }, (_, i) => 58700 + i * 1000);
  let levels = createPriceLevels({ ...LONG_BASE, entryPrices });
  const changed = [];
  const controller = new PriceLevelDragController({
    getLevels: () => levels,
    onLevelsChange: (next) => { changed.push(next); levels = next; },
  });
  return { controller, changed, get levels() { return levels; } };
}

test('ドラッグ殻は掴んだ種別に応じて水準を更新する（委譲後も従来と同じ）', () => {
  const harness = dragHarness(3);
  harness.controller._applyPrice({ kind: 'entry', index: 1 }, 59900);
  assert.deepEqual([...harness.levels.entryPrices], [58700, 59900, 60700]);
  harness.controller._applyPrice({ kind: 'stop', index: null }, 58200);
  assert.equal(harness.levels.stopPrice, 58200);
  harness.controller._applyPrice({ kind: 'take', index: null }, 62000);
  assert.equal(harness.levels.takePrice, 62000);
});

test('読み取り専用種別・非有限価格では更新しない（従来の防御を保つ）', () => {
  const harness = dragHarness(3);
  harness.controller._applyPrice({ kind: 'losscut', index: null }, 58000);
  harness.controller._applyPrice({ kind: 'entry', index: 0 }, Number.NaN);
  harness.controller._applyPrice({ kind: 'entry', index: 0 }, null);
  assert.equal(harness.changed.length, 0);
});

// --------------------------------------------------------------------------- //
// 計算量: 1 ドラッグあたりの水準生成 − 使用(=1) = 0
// --------------------------------------------------------------------------- //
for (const entryCount of [1, 8]) {
  test(`1 ドラッグで生成する水準は 1 個（建玉 ${entryCount} 本でも増えない）`, () => {
    const harness = dragHarness(entryCount);
    harness.controller._applyPrice({ kind: 'entry', index: 0 }, 58800);
    // 「使った水準」= 変更通知で外へ出た 1 個。作って捨てる生成があれば差が出る。
    assert.equal(harness.changed.length - 1, 0);
    assert.equal(harness.changed[0], harness.levels);
  });
}

test('計算量ゲートの検出力: 二重生成に変異させると差が 0 でなくなる', () => {
  const harness = dragHarness(3);
  // 捨てられる生成を 1 回混ぜる（浪費の再現）。
  harness.controller._applyPrice({ kind: 'entry', index: 0 }, 58800);
  harness.controller._applyPrice({ kind: 'entry', index: 0 }, 58900);
  assert.notEqual(harness.changed.length - 1, 0);
});
