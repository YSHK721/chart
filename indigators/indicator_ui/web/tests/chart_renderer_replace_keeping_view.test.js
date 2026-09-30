// chart_renderer.js replaceDataKeepingView / visibleLogicalRange / subscribeVisibleLogicalRange
//   （ISSUE-552/554 段階 2-2・表示を保つ差し替えの口）の仕様検証。
//
// 設計入力（実測 2026-09-30・lightweight-charts の実物・試験ページ）:
//   持っている足の区間を差し替えると、lwc は末尾の足を基準に表示を保つ。そのため
//     前へ足す・前を捨てる … 表示は動かない
//     後ろへ足す・後ろを捨てる … 表示が動く（実測: 380px に在った足が -2658px / 2277px へ移った）
//   差し替えの後に「同じ時刻の足が同じ論理位置へ来るようにずらした論理範囲」を指定し直すと、
//   6 通り（前へ足す・後ろへ足す・前を捨てる・後ろを捨てる・その組み合わせ 2 つ）すべてで
//   同じ足が同じ画面位置（小数点以下まで一致）に留まり、足 1 本の幅も変わらなかった。
//   指定し直しは**すべての系列を差し替えた後**に 1 回行う（途中で行うと、時間軸が新旧の時刻の
//   和集合になっている間の位置で指定することになる）。
//
// 観測の境界: 注入する Fake の chart / series（ChartRenderer の公開メソッドだけを呼ぶ）。
// 構造: Arrange-Act-Assert。DOM 非依存。

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { ChartRenderer } from '../js/adapter/front/chart_renderer.js';

/** 呼び出しの順序を 1 本の記録へ残す Fake 一式。 */
function fakes() {
  const log = [];
  let range = { from: 400, to: 700 };
  const handlers = new Set();
  const series = (name) => ({
    name, _data: null, _options: {},
    setData(points) { this._data = points; log.push(['setData', name, points.length]); },
    update() {},
    applyOptions(opts) { Object.assign(this._options, opts); },
    createPriceLine(opt) { return { opt }; },
    removePriceLine() {},
    priceScale() { return { applyOptions() {} }; },
  });
  const added = [];
  const timeScale = {
    fitContent() { log.push(['fitContent']); },
    scrollToRealTime() { log.push(['scrollToRealTime']); },
    getVisibleLogicalRange() { return range; },
    setVisibleLogicalRange(next) { range = next; log.push(['setVisibleLogicalRange', next.from, next.to]); },
    subscribeVisibleLogicalRangeChange(fn) { handlers.add(fn); },
    unsubscribeVisibleLogicalRangeChange(fn) { handlers.delete(fn); },
    applyOptions() {},
    width() { return 1200; },
    options() { return { barSpacing: 4 }; },
  };
  const chart = {
    timeScale() { return timeScale; },
    subscribeCrosshairMove() {},
    applyOptions() {},
    panes() { return []; },
    addSeries() { const s = series(`line${added.length}`); added.push(s); log.push(['addSeries']); return s; },
  };
  const main = series('main');
  const renderer = new ChartRenderer({ chart, mainSeries: main, lwc: { LineSeries: {} } });
  return {
    renderer, main, log, added, handlers,
    setRange(next) { range = next; },
    count(kind) { return log.filter((e) => e[0] === kind).length; },
  };
}

/** Bar 列の中の位置 i の足（時刻は 60 秒刻み）。 */
const bar = (i) => ({ time: 1_600_000_000 + i * 60, open: 1, high: 2, low: 0.5, close: 1.5 });
const bars = (start, end) => Array.from({ length: end - start }, (_, k) => bar(start + k));
const points = (start, end) => bars(start, end).map((b) => ({ time: b.time, value: start }));
const instance = (start, end) => [
  { instanceId: 'price:sma', payloads: [{ name: 'sma', color: '#fff', width: 1, style: 'solid', data: points(start, end) }] },
];

/** 位置 [start, end) を描いた状態（初期表示と同じ経路: setCandles + renderLine）。 */
function drawn(start, end) {
  const f = fakes();
  f.renderer.setCandles(bars(start, end));
  for (const inst of instance(start, end)) f.renderer.renderLine(inst.instanceId, inst.payloads, { pane: false });
  f.log.length = 0;
  return f;
}

// 差し替えの 6 通り: [名前, 新しい区間, 同じ足の論理位置のずれ（旧の位置 0 が新で来る位置）]
const SWAPS = [
  ['前へ足す', [8500, 11500], 1500],
  ['後ろへ足す', [10000, 13000], 0],
  ['後ろを捨てる', [10000, 11000], 0],
  ['前を捨てる', [10300, 11500], -300],
  ['前へ足して後ろを捨てる', [8500, 11000], 1500],
  ['後ろへ足して前を捨てる', [10300, 13000], -300],
];

for (const [name, [start, end], shift] of SWAPS) {
  test(`表示を保つ差し替え（${name}）: 同じ時刻の足が同じ論理位置へ来る範囲を指定し直す`, () => {
    const f = drawn(10000, 11500);
    const kept = f.renderer.replaceDataKeepingView(bars(start, end), instance(start, end));
    assert.equal(kept, true);
    assert.deepEqual(f.log.at(-1), ['setVisibleLogicalRange', 400 + shift, 700 + shift]);
    assert.equal(f.main._data.length, end - start);
    assert.equal(f.added[0]._data.length, end - start);
  });
}

test('表示を保つ差し替えは fitContent も scrollToRealTime も呼ばない（setCandles と違う点）', () => {
  const f = drawn(10000, 11500);
  f.renderer.replaceDataKeepingView(bars(8500, 11500), instance(8500, 11500));
  assert.equal(f.count('fitContent'), 0);
  assert.equal(f.count('scrollToRealTime'), 0);
});

test('範囲の指定し直しは、足と値の系列をすべて差し替えた後に 1 回だけ', () => {
  const f = drawn(10000, 11500);
  f.renderer.replaceDataKeepingView(bars(8500, 11500), instance(8500, 11500));
  assert.deepEqual(f.log.map((e) => e[0]), ['setData', 'setData', 'setVisibleLogicalRange']);
});

test('値の系列は作り直さず同じ系列へ入れる（目のボタンの状態が残る）', () => {
  const f = drawn(10000, 11500);
  f.renderer.setVisible('price:sma', false);
  const series = f.added[0];
  f.renderer.replaceDataKeepingView(bars(8500, 11500), instance(8500, 11500));
  assert.equal(f.count('addSeries'), 0);
  assert.equal(f.added.length, 1);
  assert.equal(f.added[0], series);
  assert.equal(series._options.visible, false);
});

test('新旧の区間に同じ足が無ければ範囲を指定せず false を返す（保つ基準が無い）', () => {
  const f = drawn(10000, 11500);
  const kept = f.renderer.replaceDataKeepingView(bars(20000, 21500), instance(20000, 21500));
  assert.equal(kept, false);
  assert.equal(f.count('setVisibleLogicalRange'), 0);
  assert.equal(f.main._data.length, 1500);
});

test('差し替え後の足が基準になる（getCandles・lastClose・最新足が見えているかの判定）', () => {
  const f = drawn(10000, 11500);
  const next = bars(10300, 13000);
  next[next.length - 1] = { ...next.at(-1), close: 77 };
  f.renderer.replaceDataKeepingView(next, instance(10300, 13000));
  assert.equal(f.renderer.getCandles().length, 2700);
  assert.equal(f.renderer.lastClose(), 77);
  f.setRange({ from: 0, to: 100 });
  assert.equal(f.renderer.isLatestBarVisible(), false);
});

test('売買ペアの外を暗くしている間に差し替えても、暗くした状態を保つ', () => {
  const f = drawn(10000, 11500);
  f.renderer.dimCandlesOutsidePair({ from: bar(10010).time, to: bar(10020).time });
  f.log.length = 0;
  f.renderer.replaceDataKeepingView(bars(9000, 11500), instance(9000, 11500));
  const inside = f.main._data.find((b) => b.time === bar(10015).time);
  const outside = f.main._data.find((b) => b.time === bar(9100).time);
  assert.equal(inside.color, undefined);
  assert.notEqual(outside.color, undefined);
  // 計算量: 暗くした足と素の足を 2 回書かない（足の系列への書き込み − 使った書き込み = 0）。
  assert.equal(f.log.filter((e) => e[0] === 'setData' && e[1] === 'main').length - 1, 0);
});

test('見えている論理範囲を読める・変化を購読でき、解除すると届かない', () => {
  const f = drawn(10000, 11500);
  assert.deepEqual(f.renderer.visibleLogicalRange(), { from: 400, to: 700 });
  const seen = [];
  const unsubscribe = f.renderer.subscribeVisibleLogicalRange((r) => seen.push(r));
  const before = new Set(f.handlers);
  f.setRange({ from: 1, to: 2 });
  for (const h of f.handlers) h({ from: 1, to: 2 });
  assert.deepEqual(seen, [{ from: 1, to: 2 }]);
  unsubscribe();
  assert.equal(f.handlers.size, before.size - 1);
});

test('計算量: 1 回の差し替えの書き込みは系列ごとに 1 回で、足の本数 2 点で同じ（書き込み − 系列の数 = 0）', () => {
  const counts = [];
  for (const n of [10, 5000]) {
    const f = drawn(10000, 10000 + n);
    f.renderer.replaceDataKeepingView(bars(10000 - n, 10000 + n), instance(10000 - n, 10000 + n));
    const seriesCount = 1 + f.added.length;
    assert.equal(f.count('setData') - seriesCount, 0);
    // 渡した点 − 系列へ入った点 = 0（足も値も）。
    assert.equal(f.main._data.length - 2 * n, 0);
    assert.equal(f.added[0]._data.length - 2 * n, 0);
    counts.push([f.count('setData'), f.count('setVisibleLogicalRange'), f.count('addSeries')]);
  }
  assert.deepEqual(counts[0], counts[1]);
});
