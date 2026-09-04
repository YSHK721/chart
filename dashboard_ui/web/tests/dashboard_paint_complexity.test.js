// 計算量テスト（絶対命令 2026-08-28）— **作ってから捨てる**色計算が無いことを固定する。
//
// なぜ状態検証と別に要るか: 版面の DOM が正しいかを見る検定は、View が「描かない要素の色まで
//   計算していた」場合でも緑のままになる。出力が正しいからである（ISSUE-450 / ISSUE-257 と同型）。
//   したがって**回数**を数える。時間は測らない（マシン負荷で揺れ、閾値が緩んで浪費を通す）。
//
// 最小形: Test Spy（style.backgroundColor への代入 = heat_scale への 1 回の問い合わせ）で
//   発行を数え、`発行した計算 − 出力に使った計算 = 0` を assert する。
//   **回数そのものは期待値に焼き込まない**（焼き込むと浪費が仕様へ昇格する）。固定するのは
//   「無駄の不在」と「発行が出力量だけで決まる」ことである。

import { test, describe } from 'node:test';
import assert from 'node:assert/strict';

import { fakeDoc, flatten, sheetResponse, ladderRow, oscCell } from './_fake_dom.js';
import { createReachSheetView } from '../js/adapter/front/reach_sheet_view.js';
import { createOscillatorSheetView } from '../js/adapter/front/oscillator_sheet_view.js';

/**
 * 色の発行を数える document。
 *
 * `style.backgroundColor` への代入 1 回 = heat_scale から色を 1 つ取った回数。
 * 捨てられた要素（出力に現れない要素）への代入も等しく数える——それが検出したい浪費である。
 */
function countingDoc() {
  const doc = fakeDoc();
  const create = doc.createElement;
  let issued = 0;
  const painted = new Set();
  doc.createElement = (tag) => {
    const el = create(tag);
    const inner = el.style;
    el.style = new Proxy({}, {
      get: (_t, prop) => inner[prop],
      set: (_t, prop, value) => {
        if (prop === 'backgroundColor') {
          issued += 1;
          painted.add(el);
        }
        inner[prop] = value;
        return true;
      },
    });
    return el;
  };
  return {
    doc,
    reset() { issued = 0; painted.clear(); },
    issued: () => issued,
    /** 出力（host 配下）に実際に残っている「色を決めた要素」の数。 */
    used(host) {
      const inTree = new Set(flatten(host));
      return [...painted].filter((el) => inTree.has(el)).length;
    },
  };
}

/** 第 1 表を n 行で描き、発行と使用を返す。 */
function renderLadder(rows) {
  const spy = countingDoc();
  const host = spy.doc.createElement('div');
  const view = createReachSheetView({ doc: spy.doc });
  view.mount(host);
  spy.reset();
  view.render(sheetResponse({ rows, current_index: 1 }));
  return { issued: spy.issued(), used: spy.used(host) };
}

/** 第 2 表を描き、発行と使用を返す。 */
function renderOsc(cells) {
  const spy = countingDoc();
  const host = spy.doc.createElement('div');
  const view = createOscillatorSheetView({ doc: spy.doc, now: () => 1_700_000_000 });
  view.mount(host);
  spy.reset();
  view.render(sheetResponse({ cells }));
  return { issued: spy.issued(), used: spy.used(host) };
}

const ladderRows = (n) => Array.from({ length: n }, (_, i) => ladderRow({ price: 65_000 + i, distance: i - n / 2 }));

describe('版面の色計算 — 発行と使用の差', () => {
  test('the_ladder_issues_no_colour_it_does_not_paint_into_the_output', () => {
    // Arrange / Act
    const small = renderLadder(ladderRows(2));
    const large = renderLadder(ladderRows(6));
    // Assert: 発行 − 使用 = 0（捨てられる色計算が無い）。
    assert.equal(small.issued - small.used, 0, `捨てられた色計算があります: ${small.issued} 発行 / ${small.used} 使用`);
    assert.equal(large.issued - large.used, 0, `捨てられた色計算があります: ${large.issued} 発行 / ${large.used} 使用`);
    // Assert: 発行は出力量だけで決まる（オーダーの表明・2 点で固定）。
    assert.ok(large.used > small.used, '行を増やしても出力が増えていない（前提の崩壊）');
    assert.equal(large.issued / large.used, small.issued / small.used);
  });

  test('the_oscillator_sheet_issues_no_colour_it_does_not_paint_into_the_output', () => {
    const cells = ['1m', '5m'].flatMap((timeframe) => [
      oscCell({ indicator_id: 'ma_marod', timeframe, p: 0.2 }),
      oscCell({ indicator_id: 'profit_rsi', timeframe, p: 0.8, tail_unscaled: true }),
    ]);
    const result = renderOsc(cells);
    assert.equal(result.issued - result.used, 0, `捨てられた色計算があります: ${result.issued} 発行 / ${result.used} 使用`);
  });

  test('input_the_sheet_cannot_display_costs_no_colour_at_all', () => {
    // 表示しない時間足の応答が増えても、発行は増えない（＝発行は出力量だけの関数）。
    const shown = ['1m', '5m'].flatMap((timeframe) => [
      oscCell({ indicator_id: 'ma_marod', timeframe, p: 0.2 }),
    ]);
    const extra = ['7m', '13m', '97m'].map((timeframe) => oscCell({ indicator_id: 'ma_marod', timeframe, p: 0.9 }));

    const lean = renderOsc(shown);
    const fat = renderOsc([...shown, ...extra]);

    assert.equal(fat.used, lean.used, '表示されない入力が出力を変えています（前提の崩壊）');
    assert.equal(fat.issued, lean.issued, '表示しない入力に色計算を発行しています');
    assert.equal(fat.issued - fat.used, 0);
  });
});
