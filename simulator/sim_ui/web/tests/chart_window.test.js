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
  const reads = planReads({ held, totalRows: 2_000_000, readRows, cap, visible: { from: 100, to: 400 } });
  assert.deepEqual(reads, [{
    side: 'before', start: held.start - readRows, end: held.start,
    next: { start: held.start - readRows, end: held.end },
  }]);
});

test('見えている右端が持っている右端へ近づいたら、後の 1 回ぶんを読む（run の末尾を越えない）', () => {
  const held = { start: 50_000, end: 50_000 + readRows };
  const visible = { from: readRows - 400, to: readRows - 100 };
  assert.deepEqual(
    planReads({ held, totalRows: 2_000_000, readRows, cap, visible }),
    [{ side: 'after', start: held.end, end: held.end + readRows, next: { start: held.start, end: held.end + readRows } }],
  );
  assert.deepEqual(
    planReads({ held, totalRows: held.end + 7, readRows, cap, visible }),
    [{ side: 'after', start: held.end, end: held.end + 7, next: { start: held.start, end: held.end + 7 } }],
  );
});

test('端から 1 画面ぶんより離れていれば読まない。run の端に着いていれば近くても読まない', () => {
  const held = { start: 50_000, end: 50_000 + readRows };
  assert.deepEqual(planReads({ held, totalRows: 2_000_000, readRows, cap, visible: { from: 600, to: 900 } }), []);
  const whole = { start: 0, end: readRows };
  assert.deepEqual(planReads({ held: whole, totalRows: readRows, readRows, cap, visible: { from: -50, to: readRows + 50 } }), []);
});

test('両端に近ければ前と後を 1 回ずつ', () => {
  const held = { start: 50_000, end: 50_000 + readRows };
  const reads = planReads({ held, totalRows: 2_000_000, readRows, cap, visible: { from: -10, to: readRows + 10 } });
  assert.deepEqual(reads.map((r) => r.side), ['before', 'after']);
});

test('見えている範囲が読めないときは読まない（推測で読まない）', () => {
  const held = { start: 50_000, end: 50_000 + readRows };
  assert.deepEqual(planReads({ held, totalRows: 2_000_000, readRows, cap, visible: null }), []);
});

test('上限を超える分は、読む側と反対の端の「守る範囲」の外から捨てる（読んだ区間は残る）', () => {
  const held = { start: 100_000, end: 100_000 + cap };
  // 左端の近くを見ていて前を読む → 右端を捨てる。
  const [before] = planReads({ held, totalRows: 2_000_000, readRows, cap, visible: { from: 100, to: 400 } });
  assert.deepEqual(before, {
    side: 'before', start: held.start - readRows, end: held.start,
    next: { start: held.start - readRows, end: held.end - readRows },
  });
  // 右端の近くを見ていて後を読む → 左端を捨てる。
  const [after] = planReads({ held, totalRows: 2_000_000, readRows, cap, visible: { from: cap - 400, to: cap - 100 } });
  assert.deepEqual(after, {
    side: 'after', start: held.end, end: held.end + readRows,
    next: { start: held.start + readRows, end: held.end + readRows },
  });
});

test('上限に届かなければ捨てない', () => {
  const held = { start: 100_000, end: 100_000 + readRows };
  const [read] = planReads({ held, totalRows: 2_000_000, readRows, cap, visible: { from: 100, to: 400 } });
  assert.deepEqual(read.next, { start: held.start - readRows, end: held.end });
});

test('つないだ後に残らない区間は読まない: 上限まで持ち、捨てられる足が無ければ発行しない', () => {
  const held = { start: 100_000, end: 100_000 + cap };
  // 見えている幅が上限の 1/2 超で、持っている区間の中央を見ている（両端とも守る範囲の中）。
  const width = Math.floor(cap * 0.6);
  const from = (cap - width) / 2;
  assert.deepEqual(planReads({ held, totalRows: 2_000_000, readRows, cap, visible: { from, to: from + width } }), []);
  // 持てる量より広く見ている（持っている区間がすべて見えている）ときも発行しない。
  assert.deepEqual(planReads({ held, totalRows: 2_000_000, readRows, cap, visible: { from: -cap, to: 2 * cap } }), []);
});

test('捨てられる足が 1 回の本数より少なければ、読む区間をそのぶんに縮める（持っている区間に接したまま）', () => {
  const held = { start: 100_000, end: 100_000 + cap };
  const width = Math.floor(cap * 0.6);
  const margin = (cap - width) / 2;
  // 右へ `spare` 本だけ寄せて見る → 左端に守る範囲の外の足が `spare` 本できる。
  const spare = 7;
  const from = margin + spare;
  const reads = planReads({ held, totalRows: 2_000_000, readRows, cap, visible: { from, to: from + width } });
  assert.deepEqual(reads, [{
    side: 'after', start: held.end, end: held.end + spare,
    next: { start: held.start + spare, end: held.end + spare },
  }]);
});

test('読む側を限れる（sides）: 名指ししない側は発行しない', () => {
  const held = { start: 50_000, end: 50_000 + readRows };
  const reads = planReads({
    held, totalRows: 2_000_000, readRows, cap, visible: { from: -10, to: readRows + 10 }, sides: ['after'],
  });
  assert.deepEqual(reads.map((r) => r.side), ['after']);
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
      for (const read of planReads({ held, totalRows, readRows, cap, visible })) {
        sizes.push(read.end - read.start);
        held = read.next;
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

// ---- 計算量: 「読んでから捨てる」の不在（独立レビュー 🟡-1） ----
//   発行を決める段（planReads）が、つないだ後に持つ区間（next）まで決める。ここは利用者の操作の
//   台本を純関数だけで回し、操作 1 回で読んだ行のうち操作の後も持っている行を数える。

/** 半開区間 a と b の重なりの行数。 */
const overlapRows = (a, b) => Math.max(0, Math.min(a.end, b.end) - Math.max(a.start, b.start));

/**
 * 見えている範囲（Bar 列の中の位置 `view`）で操作 1 回ぶんの読み足しを行う。
 * @returns {{held: object, read: number, kept: number, seenLost: number, sizes: number[]}}
 */
function operate({ held, totalRows, view }) {
  const visible = { from: view.from - held.start, to: view.to - held.start };
  const reads = planReads({ held, totalRows, readRows, cap, visible });
  let now = held;
  for (const read of reads) {
    // 読む区間は持っている区間に接している（飛び地を読まない）。
    assert.ok(read.end === now.start || read.start === now.end, '読む区間が持っている区間に接していない');
    assert.ok(read.end > read.start, '空の区間を発行した');
    now = read.next;
    assert.ok(now.end - now.start <= cap, `持つ本数 ${now.end - now.start} が上限 ${cap} を超えた`);
  }
  const seen = {
    start: Math.max(held.start, Math.ceil(view.from)),
    end: Math.min(held.end, Math.floor(view.to) + 1),
  };
  return {
    held: now,
    read: reads.reduce((n, r) => n + (r.end - r.start), 0),
    kept: reads.reduce((n, r) => n + overlapRows(r, now), 0),
    seenLost: Math.max(0, seen.end - seen.start) - overlapRows(seen, now),
    sizes: reads.map((r) => r.end - r.start),
  };
}

// 見えている幅の 4 点（整数の本数）: 上限の 1/4・上限の 1/3 の直下・上限の 3/5・上限の 3/2。
//   上限の 1/3 の直下は、守る範囲（幅 × 3）が上限にわずかに足りない帯。ここで見えている範囲が
//   小数だと、「覆っているか」と「捨てられる足」が別の境界を見ていた頃は、1 操作で前を読み、
//   その一部を後の読みで捨てた（独立レビュー 推奨 1・2）。
const WIDTHS = [
  Math.floor(cap / 4), Math.ceil(cap / 3) - 1, Math.floor((cap * 3) / 5), Math.floor((cap * 3) / 2),
];
// 端数の有無: 見えている範囲の両端へ足す端数（lwc の論理範囲は小数で来る・実測）。
const FRACTIONS = [{ from: 0, to: 0 }, { from: 0.14, to: 0.23 }];
const RUNS = [10 * cap, 3_000_000];
/** 幅（整数の本数）× 端数の有無。 */
const SHAPES = WIDTHS.flatMap((width) => FRACTIONS.map((fraction) => ({ width, fraction })));
const label = ({ width, fraction }) => `幅 ${width}・端数 ${fraction.from}/${fraction.to}`;

/** 整数の位置 `at` から幅 `width` の見えている範囲（端数を足す）。 */
const viewAt = (at, { width, fraction }) => ({ from: at + fraction.from, to: at + width + fraction.to });

test('検定の前提: 幅の 4 点は上限の 1/3 の両側と上限の外を通り、端数つきでも帯を出ない', () => {
  const [quarter, third, wide, wider] = WIDTHS;
  const grown = Math.max(...FRACTIONS.map((f) => f.to - f.from));
  assert.ok(quarter + grown < third && third + grown < cap / 3 && cap / 3 - third - grown < 1);
  assert.ok(wide > cap / 2 && wide < cap && wider > cap);
  assert.ok(FRACTIONS.some((f) => f.from === 0 && f.to === 0));
  assert.ok(FRACTIONS.some((f) => !Number.isInteger(f.from) && !Number.isInteger(f.to)));
});

/** 台本: 上限まで持った状態から、見えている幅の 1/4 ずつ左へ、続けて右へ動かす。 */
function panScript({ totalRows, shape }) {
  // run の中ほど（run の長さに依らず端から同じ距離）で上限まで持つ。
  let held = { start: totalRows - 3 * cap, end: totalRows - 2 * cap };
  let at = held.start + Math.floor((cap - shape.width) / 2);
  const results = [];
  const step = Math.floor(shape.width / 4);
  const moves = [...Array(12).fill(-step), ...Array(24).fill(step)];
  for (const move of [0, ...moves]) {
    at += move;
    const r = operate({ held, totalRows, view: viewAt(at, shape) });
    held = r.held;
    results.push(r);
  }
  return results;
}

test('計算量: 読んだ行 − つないだ後に持ち続けた行 = 0（見えている幅 4 点 × 端数の有無 × run の長さ 2 点）', () => {
  for (const shape of SHAPES) {
    const perRun = [];
    for (const totalRows of RUNS) {
      const results = panScript({ totalRows, shape });
      const read = results.reduce((n, r) => n + r.read, 0);
      const kept = results.reduce((n, r) => n + r.kept, 0);
      assert.ok(read > 0, `${label(shape)}: 読み足しが起きていない（検定が空虚）`);
      assert.equal(read - kept, 0, `${label(shape)}・run ${totalRows} 本: 読んだ ${read} 行のうち ${read - kept} 行を捨てた`);
      perRun.push(results.map((r) => r.sizes));
    }
    // 発行は run の長さに依らない（同じ台本なら同じ本数・同じ回数）。
    assert.deepEqual(perRun[0], perRun[1], label(shape));
  }
});

test('読み足しで、見えていた足を捨てない（見えている幅 4 点 × 端数の有無）', () => {
  for (const shape of SHAPES) {
    for (const r of panScript({ totalRows: RUNS[1], shape })) {
      assert.equal(r.seenLost, 0, `${label(shape)}: 見えていた足を ${r.seenLost} 本捨てた`);
    }
  }
});

/**
 * 同じ位置で操作を繰り返す。
 * @returns {{results: object[], held: object}}
 */
function repeatAt({ origin, totalRows, view }) {
  let held = origin;
  const results = [];
  // 守る範囲は上限に収まるので、1 操作 1 回ぶんずつ読んでも「上限 / 1 回の本数」回までに覆える。
  //   そこから先の操作は、読みが止まっていることを確かめるぶん。
  for (let i = 0; i < 2 * (cap / readRows) + 1; i += 1) {
    results.push(operate({ held, totalRows, view }));
    held = results.at(-1).held;
  }
  return { results, held };
}

test('再現（独立レビュー 推奨 1・2）: 見えている範囲が小数でも、1 操作で読んだ行を同じ操作で捨てず、同じ位置の次の操作は読まない', () => {
  // 本番値（1 回の本数 1500・上限 19500）で、上限まで持ち、幅が上限の 1/3 の少し下の小数。
  const origin = { start: 68_249, end: 68_249 + cap };
  const view = { from: 73_698.14, to: 80_023.23 };
  const { results } = repeatAt({ origin, totalRows: 2_150_000, view });
  const [first, ...rest] = results;
  assert.ok(first.read > 0, '読み足しが起きていない（検定が空虚）');
  assert.equal(first.read - first.kept, 0, `操作 1: 読んだ ${first.read} 行のうち ${first.read - first.kept} 行を捨てた`);
  const again = rest.reduce((n, r) => n + r.read, 0);
  assert.equal(again, 0, `同じ位置の操作 ${rest.length} 回で ${again} 行を読んだ（読みが止まらない）`);
});

test('同じ位置で操作を繰り返すと読みは止まり、読んだ行はすべて持ち続ける（幅 4 点 × 端数の有無 × run の長さ 2 点・持っている左端の近くと右端の近く）', () => {
  for (const shape of SHAPES) {
    const perRun = [];
    for (const totalRows of RUNS) {
      const origin = { start: totalRows - 3 * cap, end: totalRows - 2 * cap };
      const sizes = [];
      // 持っている左端の近く・右端の近く（run の長さに依らず持っている区間からの距離は同じ）。
      for (const at of [origin.start + 10, origin.end - 10 - shape.width]) {
        const where = `${label(shape)}・run ${totalRows} 本・位置 ${at - origin.start}`;
        const { results, held } = repeatAt({ origin, totalRows, view: viewAt(at, shape) });
        // 読みは止まる（覆えた後の操作は 1 行も読まない）。
        const settled = results.slice(cap / readRows + 1);
        assert.ok(settled.length > 0);
        const late = settled.reduce((n, r) => n + r.read, 0);
        assert.equal(late, 0, `${where}: 同じ位置で読みが止まらない（覆えた後に ${late} 行を読んだ）`);
        // 読んだ行 − 操作の後も持ち続けた行 = 0（操作ごと）。
        const read = results.reduce((n, r) => n + r.read, 0);
        const kept = results.reduce((n, r) => n + r.kept, 0);
        assert.equal(read - kept, 0, `${where}: 読んだ ${read} 行のうち ${read - kept} 行を同じ操作で捨てた`);
        // 読んだ行 − 最後に持っている行のうち新しく持った行 = 0（前を読んで後を捨て、次に後を
        //   読んで前を捨てる往復が無い・同じ行を読み直していない）。
        const gained = (held.end - held.start) - overlapRows(held, origin);
        assert.equal(read - gained, 0, `${where}: 読んだ ${read} 行のうち ${read - gained} 行を持っていない`);
        sizes.push(results.map((r) => r.sizes));
      }
      perRun.push(sizes);
    }
    assert.deepEqual(perRun[0], perRun[1], label(shape));
  }
});

// ---- 計算量: 不変条件は状態に依らない（上限まで持ったか否かで例外を作らない） ----
//   どの状態でも「読んだ行 − 操作の後も持ち続けた行 = 0」。上限未満で両側を読むとき、片側が
//   守る範囲を覆うのに必要な本数より多く読むと、その超過分を反対側の読みが捨てた（実測:
//   本番値・上限未満から約 100 万点のうち同じ操作で 15,909 件・操作をまたいで 218,718 件）。

/** 同じ位置で操作を繰り返した結果を数える。 */
function tally({ origin, totalRows, view }) {
  const { results, held } = repeatAt({ origin, totalRows, view });
  const read = results.reduce((n, r) => n + r.read, 0);
  return {
    read,
    // 同じ操作で捨てた行。
    droppedWithin: read - results.reduce((n, r) => n + r.kept, 0),
    // 操作をまたいで捨てた行（読んだ行 − 最後に持っている行のうち新しく持った行）。
    droppedAcross: read - ((held.end - held.start) - overlapRows(held, origin)),
    // 覆えた後の操作で読んだ行（止まらない）。
    late: results.slice(cap / readRows + 1).reduce((n, r) => n + r.read, 0),
    seenLost: results.reduce((n, r) => n + r.seenLost, 0),
    // 読みが止まった後、run の中の見えている足のうち持っていない足と、上限までの空き。
    //   どちらも残っていれば、空きがあるのに見えている足を読んでいない。
    unseen: (() => {
      const seen = { start: Math.max(0, Math.ceil(view.from)), end: Math.min(totalRows, Math.floor(view.to) + 1) };
      return Math.max(0, seen.end - seen.start) - overlapRows(seen, held);
    })(),
    room: cap - (held.end - held.start),
  };
}

test('再現: 上限未満で両側を読むとき、片側が読みすぎた分を反対側の読みが捨てない', () => {
  // 本番値。18,000 行を持ち（上限まで 1 回ぶんの空き）、持っている区間より広く見ている。
  const origin = { start: 263_556, end: 281_556 };
  assert.ok(origin.end - origin.start < cap);
  const t = tally({ origin, totalRows: 2_150_000, view: { from: 262_921, to: 289_814 } });
  assert.ok(t.read > 0, '読み足しが起きていない（検定が空虚）');
  assert.equal(t.droppedWithin, 0, `読んだ ${t.read} 行のうち ${t.droppedWithin} 行を同じ操作で捨てた`);
  assert.equal(t.droppedAcross, 0, `読んだ ${t.read} 行のうち ${t.droppedAcross} 行を持っていない`);
});

/** 決定的な乱数（線形合同法・種は固定）。 */
function seeded(seed) {
  let state = seed;
  return () => {
    state = (Math.imul(state, 1664525) + 1013904223) >>> 0;
    return state / 2 ** 32;
  };
}

test('計算量: どの状態からでも 読んだ行 − 持ち続けた行 = 0・読みは止まる（決定的な乱数・上限未満からと上限まで持った状態から × run の長さ 2 点）', () => {
  const POINTS = 3000;
  for (const totalRows of RUNS) {
    for (const full of [true, false]) {
      const random = seeded(20260930);
      let readAll = 0;
      let shapes = 0;
      for (let i = 0; i < POINTS; i += 1) {
        // 持っている本数: 上限、または 1 回の本数の整数倍で上限未満。
        const rows = full ? cap : readRows * (1 + Math.floor(random() * (cap / readRows - 1)));
        const start = Math.floor(random() * (totalRows - rows + 1));
        const origin = { start, end: start + rows };
        // 見えている幅: 上限の 0〜0.4 倍と 0〜1.6 倍を半々。位置: 持っている区間に重なる範囲。
        let width = random() * cap * (random() < 0.5 ? 0.4 : 1.6);
        let from = start - width + random() * (rows + width);
        if (random() < 0.2) { width = Math.floor(width); from = Math.floor(from); shapes += 1; }
        const where = `run ${totalRows} 本・持っている [${origin.start}, ${origin.end})・見えている ${from}〜${from + width}`;
        const t = tally({ origin, totalRows, view: { from, to: from + width } });
        assert.equal(t.droppedWithin, 0, `${where}: 読んだ ${t.read} 行のうち ${t.droppedWithin} 行を同じ操作で捨てた`);
        assert.equal(t.droppedAcross, 0, `${where}: 読んだ ${t.read} 行のうち ${t.droppedAcross} 行を操作をまたいで捨てた`);
        assert.equal(t.late, 0, `${where}: 同じ位置で読みが止まらない（覆えた後に ${t.late} 行を読んだ）`);
        assert.equal(t.seenLost, 0, `${where}: 見えていた足を ${t.seenLost} 本捨てた`);
        assert.equal(Math.min(t.unseen, t.room), 0, `${where}: 空きが ${t.room} 行あるのに、見えている足 ${t.unseen} 本を読んでいない`);
        readAll += t.read;
      }
      // 上限超えは operate が読みのたびに表明する。
      assert.ok(readAll > 0 && shapes > 0 && shapes < POINTS, '読み足し・端数の有無のどちらかを通っていない（検定が空虚）');
    }
  }
});
