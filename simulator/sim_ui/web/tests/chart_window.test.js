// chart_window.js — 売買履歴チャートが「表示する範囲だけを持つ」ための区間の計算（純ロジック・ISSUE-552/554 段階 2-2）。
//
// 位置はすべて Bar 列の中の位置（0 始まり・半開区間）。見えている範囲は ChartRenderer の論理範囲
//   （持っている区間の先頭を 0 とする位置・端の外は範囲外の値）で受ける。
// 期待値は宣言（1 回の本数・サーバが宣言する 1 回の上限）から導く。数を書き写さない。
import { test } from 'node:test';
import assert from 'node:assert/strict';

import {
  heldRowsCap,
  mergeColumns,
  mergeWindow,
  planReads,
  readRowsOf,
  tailWindow,
} from '../js/usecase/chart_window.js';

const RECENT = 1500;
const MAX_RETURNED = 20000;
const readRows = readRowsOf({ recentBars: RECENT, maxReturnedRows: MAX_RETURNED });
const cap = heldRowsCap({ readRows, maxReturnedRows: MAX_RETURNED });

test('1 回の本数はライブチャートの本数。サーバの 1 回の上限より多くは読まない', () => {
  assert.equal(readRows, RECENT);
  assert.equal(readRowsOf({ recentBars: RECENT, maxReturnedRows: 1000 }), 1000);
});

test('持つ総量の上限は 1 回の本数の整数倍で、サーバの 1 回の上限を超えない最大（1 回ぶんは必ず持てる）', () => {
  assert.equal(cap % readRows, 0);
  assert.ok(cap <= MAX_RETURNED && cap + readRows > MAX_RETURNED);
  assert.equal(heldRowsCap({ readRows: 1000, maxReturnedRows: 1000 }), 1000);
});

test('最初に持つのは run の末尾の 1 回ぶん（run が短ければ全部）', () => {
  assert.deepEqual(tailWindow({ totalRows: 2_000_000, readRows }), { start: 2_000_000 - readRows, end: 2_000_000 });
  assert.deepEqual(tailWindow({ totalRows: 10, readRows }), { start: 0, end: 10 });
});

test('見えている左端が持っている左端へ 1 画面ぶん以内に近づいたら、前の 1 回ぶんを読む', () => {
  const held = { start: 50_000, end: 50_000 + readRows };
  // 見えている幅 300 本・左に 100 本しか残っていない。
  const reads = planReads({ held, totalRows: 2_000_000, readRows, visible: { from: 100, to: 400 } });
  assert.deepEqual(reads, [{ side: 'before', start: held.start - readRows, end: held.start }]);
});

test('見えている右端が持っている右端へ近づいたら、後の 1 回ぶんを読む（run の末尾を越えない）', () => {
  const held = { start: 50_000, end: 50_000 + readRows };
  const visible = { from: readRows - 400, to: readRows - 100 };
  assert.deepEqual(
    planReads({ held, totalRows: 2_000_000, readRows, visible }),
    [{ side: 'after', start: held.end, end: held.end + readRows }],
  );
  assert.deepEqual(
    planReads({ held, totalRows: held.end + 7, readRows, visible }),
    [{ side: 'after', start: held.end, end: held.end + 7 }],
  );
});

test('端から 1 画面ぶんより離れていれば読まない。run の端に着いていれば近くても読まない', () => {
  const held = { start: 50_000, end: 50_000 + readRows };
  assert.deepEqual(planReads({ held, totalRows: 2_000_000, readRows, visible: { from: 600, to: 900 } }), []);
  const whole = { start: 0, end: readRows };
  assert.deepEqual(planReads({ held: whole, totalRows: readRows, readRows, visible: { from: -50, to: readRows + 50 } }), []);
});

test('両端に近ければ前と後を 1 回ずつ', () => {
  const held = { start: 50_000, end: 50_000 + readRows };
  const reads = planReads({ held, totalRows: 2_000_000, readRows, visible: { from: -10, to: readRows + 10 } });
  assert.deepEqual(reads.map((r) => r.side), ['before', 'after']);
});

test('見えている範囲が読めないときは読まない（推測で読まない）', () => {
  const held = { start: 50_000, end: 50_000 + readRows };
  assert.deepEqual(planReads({ held, totalRows: 2_000_000, readRows, visible: null }), []);
});

test('上限を超えた分は、見ている位置から遠い側を捨てる', () => {
  const held = { start: 100_000, end: 100_000 + cap };
  // 左端の近くを見ていて前を読んだ → 右（遠い側）を捨てる。
  const before = { side: 'before', start: held.start - readRows, end: held.start };
  assert.deepEqual(
    mergeWindow({ held, read: before, cap, viewCenter: held.start + 200 }),
    { start: before.start, end: before.start + cap },
  );
  // 右端の近くを見ていて後を読んだ → 左を捨てる。
  const after = { side: 'after', start: held.end, end: held.end + readRows };
  assert.deepEqual(
    mergeWindow({ held, read: after, cap, viewCenter: held.end - 200 }),
    { start: after.end - cap, end: after.end },
  );
});

test('上限に届かなければ捨てない', () => {
  const held = { start: 100_000, end: 100_000 + readRows };
  const read = { side: 'before', start: held.start - readRows, end: held.start };
  assert.deepEqual(mergeWindow({ held, read, cap, viewCenter: held.start }), { start: read.start, end: held.end });
});

test('列のつなぎ: 次に持つ区間ぶんだけを、持っている列と読んだ列から位置どおりに作る', () => {
  const column = (start, end) => Array.from({ length: end - start }, (_, k) => start + k);
  const held = { start: 10, end: 20 };
  const read = { side: 'before', start: 5, end: 10 };
  const next = { start: 5, end: 15 };
  const merged = mergeColumns({
    names: ['bar_index', 'close'],
    held, heldColumns: { bar_index: column(10, 20), close: column(10, 20) },
    read, readColumns: { bar_index: column(5, 10), close: column(5, 10), unused: [] },
    next,
  });
  assert.deepEqual(merged, { bar_index: column(5, 15), close: column(5, 15) });
  const after = mergeColumns({
    names: ['bar_index'],
    held, heldColumns: { bar_index: column(10, 20) },
    read: { side: 'after', start: 20, end: 25 }, readColumns: { bar_index: column(20, 25) },
    next: { start: 13, end: 25 },
  });
  assert.deepEqual(after, { bar_index: column(13, 25) });
});

test('計算量: 何回読み足しても持つ本数は上限以下で、1 回の本数と読む回数は run の長さ 2 点で同じ', () => {
  const observed = [];
  for (const totalRows of [3_000, 3_000_000]) {
    let held = tailWindow({ totalRows, readRows });
    const sizes = [];
    // 左端を見続ける（毎回 1 画面ぶん以内）操作を上限の 2 倍ぶん繰り返す。
    const steps = 2 * (cap / readRows);
    for (let i = 0; i < steps; i += 1) {
      const visible = { from: 0, to: 300 };
      for (const read of planReads({ held, totalRows, readRows, visible })) {
        sizes.push(read.end - read.start);
        held = mergeWindow({ held, read, cap, viewCenter: held.start + 150 });
      }
      assert.ok(held.end - held.start <= cap, `持つ本数 ${held.end - held.start} が上限 ${cap} を超えた`);
    }
    observed.push({ totalRows, sizes });
  }
  // run が短い側は run の先頭に着くまで（端で読む本数は残りぶん）。長い側は毎回 1 回の本数。
  assert.ok(observed[1].sizes.every((n) => n === readRows));
  assert.equal(observed[1].sizes.length, 2 * (cap / readRows));
  const shortTotal = observed[0].sizes.reduce((a, b) => a + b, 0) + readRows;
  assert.equal(shortTotal, observed[0].totalRows);
  // 同じ操作 1 回あたりの読みは、run の長さに依らず高々 1 回。
  assert.ok(observed[0].sizes.length <= observed[1].sizes.length);
  assert.ok(observed[0].sizes.every((n) => n <= readRows));
});
