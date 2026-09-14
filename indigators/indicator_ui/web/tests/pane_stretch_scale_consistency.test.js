// 新設 pane の stretch は「価格 pane の現在値からの相対」で決まる（stretch 目盛りの単一系保証）。
//
// なぜ在るか（実測 2026-09-04・実 UI 再現）: stretch factor には 2 つの書き手がいる。
//   - SeriesDrawer._ensurePane — 設計比（メイン 3 : 指標 1）
//   - PaneGeometryController._applyGoalRatios（ISSUE-442）— 版面変化時の再配分。目標 **px 値**
//     （数百）をそのまま stretch へ書くため、以後の目盛りは px 系になる。
//   px 系へ移った後に絶対値 1 を書くと比が 1:数百 になり、新設 pane が約 2px へ潰れる
//   （実測: sim 開閉（版面変化）→ 指標適用 → 新 pane 高さ 2px・stretch [437,147,144,1]）。
//   本テストは「どちらの目盛りでも設計比 3:1 が成立する」ことを固定する。
//
// 構造: Arrange-Act-Assert。Fake chart は getStretchFactor を備える（実 lwc v5.2.0 と同形）。

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { ChartRenderer } from '../js/adapter/front/chart_renderer.js';

const LineSeries = { kind: 'Line' };
const HistogramSeries = { kind: 'Histogram' };

// Fake chart（v5・stretch の読み書きと発行回数を記録する Test Spy）。
function fakeChart() {
  const panesArr = [];
  const makePane = () => {
    const pane = {
      _series: [], _stretch: 1, _stretchWrites: 0,   // lwc 既定 stretch は 1
      paneIndex() { return panesArr.indexOf(pane); },
      getStretchFactor() { return this._stretch; },
      setStretchFactor(f) { this._stretch = f; this._stretchWrites += 1; },
      setPreserveEmptyPane() {},
      addSeries(def) {
        const s = {
          _def: def, _pane: pane,
          setData() {}, data() { return []; }, applyOptions() {},
          createPriceLine() { return {}; }, removePriceLine() {},
        };
        pane._series.push(s);
        return s;
      },
    };
    return pane;
  };
  panesArr.push(makePane()); // pane 0（ローソク）
  return {
    panes() { return panesArr; },
    addPane() { const p = makePane(); panesArr.push(p); return p; },
    removePane(i) { panesArr.splice(i, 1); },
    addSeries(def) { return panesArr[0].addSeries(def); },
    removeSeries() {},
    applyOptions() {},
    timeScale() { return { fitContent() {} }; },
    subscribeCrosshairMove() {},
  };
}

function newRenderer() {
  const chart = fakeChart();
  const main = { setData() {}, data() { return []; }, applyOptions() {} };
  const renderer = new ChartRenderer({ chart, mainSeries: main, lwc: { LineSeries, HistogramSeries } });
  return { renderer, chart };
}

test('design-ratio scale: fresh chart keeps the historical 3:1 assignment (byte 互換)', () => {
  const { renderer, chart } = newRenderer();
  renderer.renderHistogram('rsi#1', [{ name: 'rsi', kind: 'histogram', data: [] }], { pane: true, name: 'RSI' });
  assert.equal(chart.panes()[0]._stretch, 3);
  assert.equal(chart.panes()[1]._stretch, 1);
});

test('px scale: a pane created after redistribution keeps the 3:1 design ratio (潰れない)', () => {
  const { renderer, chart } = newRenderer();
  // 1 本目の pane 指標で従来どおり [3, 1] になる。
  renderer.renderHistogram('rsi#1', [{ name: 'rsi', kind: 'histogram', data: [] }], { pane: true, name: 'RSI' });
  // 版面変化の再配分（_applyGoalRatios）を模す: 目盛りが px 系（数百）へ移る。
  chart.panes()[0]._stretch = 437;
  chart.panes()[1]._stretch = 147;
  // Act: px 系の目盛りの下で新しい pane 指標を適用する。
  renderer.renderLine('macd#1', [{ name: 'm', kind: 'line', data: [] }], { pane: true, name: 'MACD' });
  const [price, , added] = chart.panes();
  // 設計比 3:1 が px 系でも成立する（絶対値 1 なら比 437:1＝実測 2px へ潰れていた）。
  assert.equal(added._stretch, price._stretch / 3);
  // 比としての表明（実装値 437/3 を焼き込まず、価格 pane に対する相対だけを固定する）。
  assert.ok(added._stretch >= price._stretch / 3 - 1e-9, '新 pane は価格 pane の 1/3 を下回らない');
});

test('getStretchFactor 非提供（Fake/旧版）: 従来の絶対値 1 へ縮退する', () => {
  const { renderer, chart } = newRenderer();
  delete chart.panes()[0].getStretchFactor;
  renderer.renderHistogram('rsi#1', [{ name: 'rsi', kind: 'histogram', data: [] }], { pane: true, name: 'RSI' });
  assert.equal(chart.panes()[1]._stretch, 1);
});

// 計算量（発行回数）: 発行した stretch 書き込み − レイアウトに使う書き込み = 0。
//   pane 1 面の適用で必要な書き込みは「メイン昇格 1 回（初回のみ）＋新 pane 1 回」だけであり、
//   既存 pane への再書き込み・同一 slot の再適用による重複発行を作らない。
//   入力（適用する pane 指標の数）を増やしても、発行は増えた pane の分しか増えない。
test('complexity: stretch writes = (main once) + (one per new pane), and never re-issued', () => {
  const { renderer, chart } = newRenderer();
  renderer.renderHistogram('a#1', [{ name: 'a', kind: 'histogram', data: [] }], { pane: true, name: 'A' });
  renderer.renderLine('b#1', [{ name: 'b', kind: 'line', data: [] }], { pane: true, name: 'B' });
  // 同一インスタンスの再描画（再計算経路）は既存 pane を再利用し stretch を再発行しない。
  renderer.renderLine('b#1', [{ name: 'b', kind: 'line', data: [] }], { pane: true, name: 'B' });
  const writes = chart.panes().map((p) => p._stretchWrites);
  // メイン: 昇格 1 回のみ。指標 pane: 生成時 1 回のみ（再適用で増えない）。
  assert.deepEqual(writes, [1, 1, 1]);
  const total = writes.reduce((x, y) => x + y, 0);
  const used = 1 + (chart.panes().length - 1);   // メイン昇格 + 新 pane 数
  assert.equal(total - used, 0, '発行した書き込み − 使った書き込み = 0（無駄の不在）');
});
