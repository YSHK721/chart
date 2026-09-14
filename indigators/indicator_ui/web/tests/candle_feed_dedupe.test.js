// ISSUE-167: 上流（/candles）に同一 time の重複バーが混じると lightweight-charts が
//   「厳密増加 time」不変条件違反で candlestick 描画を毎 rAF フレーム "Value is null" で落とし、
//   時間足切替後の再描画が完了せず長時間フリーズする（実測: 1m 切替で 31 秒）。しかも例外は
//   update/setData の同期呼出ではなく後続の rAF ペイントで飛ぶため呼出側 try/catch では捕捉不能。
//   CandleFeed は series へ渡す直前に重複 time を keep-last で畳み厳密増加を保証する（多重防御）。
// 構造: Arrange-Act-Assert（AAA）。DOM 非依存（Fake chart/series 注入）。

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { ChartRenderer } from '../js/adapter/front/chart_renderer.js';
import { dedupeCandlesByTime } from '../js/adapter/front/candle_feed.js';

// Fake series。setData / update を記録（chart_renderer.test.js と同型の最小構成）。
function fakeSeries() {
  return {
    _data: null, _updates: [], _options: {}, _priceLines: [],
    setData(points) { this._data = points; },
    update(point) { this._updates.push(point); },
    applyOptions(opts) { Object.assign(this._options, opts); },
    createPriceLine(opt) { const pl = { opt }; this._priceLines.push(pl); return pl; },
    removePriceLine(pl) { this._priceLines = this._priceLines.filter((x) => x !== pl); },
  };
}

function fakeChart() {
  return {
    timeScale() { return { fitContent() {} }; },
    subscribeCrosshairMove() {},
    applyOptions() {},
    panes() { return []; },
  };
}

function bar(time, close) {
  return { time, open: close - 1, high: close + 1, low: close - 2, close };
}

function newRenderer() {
  const chart = fakeChart();
  const main = fakeSeries();
  const renderer = new ChartRenderer({ chart, mainSeries: main, lwc: {} });
  return { renderer, main };
}

function isStrictlyIncreasing(arr) {
  for (let i = 1; i < arr.length; i += 1) {
    if (!(arr[i].time > arr[i - 1].time)) return false;
  }
  return true;
}

// ===========================================================================
// 純関数 dedupeCandlesByTime
// ===========================================================================

test('dedupe: 連続同一 time は keep-last（後勝ち）で 1 本へ畳む（実データ回帰: 日境界 23:59 二重）', () => {
  // 実測の再現: jp225_tick 1m の time=1784851140 が 2 本（vol 相当の異なる OHLC）。
  const input = [
    bar(1784851080, 100),
    bar(1784851140, 200), // 重複 1 本目
    bar(1784851140, 205), // 重複 2 本目（後勝ちで残る）
    bar(1784851200, 300),
  ];
  const out = dedupeCandlesByTime(input);
  assert.equal(out.length, 3);
  assert.ok(isStrictlyIncreasing(out), '厳密増加を保証する');
  assert.equal(out[1].time, 1784851140);
  assert.equal(out[1].close, 205, '同 time は後勝ち（keep-last）');
});

test('dedupe: 重複の無い正常配列は同一長・同順で素通し（挙動不変）', () => {
  const input = [bar(1, 10), bar(2, 20), bar(3, 30)];
  const out = dedupeCandlesByTime(input);
  assert.deepEqual(out, input);
});

test('dedupe: 後退 time（想定外）は捨てて厳密増加を維持する', () => {
  const out = dedupeCandlesByTime([bar(1, 10), bar(3, 30), bar(2, 20), bar(4, 40)]);
  assert.deepEqual(out.map((b) => b.time), [1, 3, 4]);
  assert.ok(isStrictlyIncreasing(out));
});

// ===========================================================================
// setCandles / resyncMissedCandles 統合（series へ渡る前に畳まれる）
// ===========================================================================

test('setCandles: 重複 time を含む配列でも series.setData は厳密増加（クラッシュ防壁）', () => {
  const { renderer, main } = newRenderer();
  renderer.setCandles([bar(1784851080, 100), bar(1784851140, 200), bar(1784851140, 205), bar(1784851200, 300)]);
  assert.equal(main._data.length, 3);
  assert.ok(isStrictlyIncreasing(main._data), 'setData へ渡る配列は厳密増加');
  assert.equal(main._data[1].close, 205);
});

test('resyncMissedCandles: 全置換経路も重複を畳んでから setData する', () => {
  const { renderer, main } = newRenderer();
  renderer.setCandles([bar(1, 10), bar(2, 20), bar(3, 30)]);
  // 休止明けで 4,5,6 まで進み、かつ 5 が二重（上流重複）で届くケース。
  const resynced = renderer.resyncMissedCandles([
    bar(1, 10), bar(2, 20), bar(3, 30), bar(4, 40), bar(5, 50), bar(5, 55), bar(6, 60),
  ]);
  assert.equal(resynced, true);
  assert.ok(isStrictlyIncreasing(main._data), 'resync の setData も厳密増加');
  assert.equal(main._data.filter((b) => b.time === 5).length, 1, 'time=5 は 1 本へ');
});
