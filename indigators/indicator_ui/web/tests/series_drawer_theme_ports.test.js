// series_drawer_theme_ports.test.js — テーマ適用のために SeriesDrawer へ加える 2 つの席（A-5）
//   （基本設計_指標カラーテーマ.md §7.2 S2(b)(c)・R-3・R-6・§7.4 段階 2 通過条件 7）。
//
// (c) styleMeta へ**不変**フィールド baseColor を足す。
//     根拠（E-20）: styleMeta.color は applySeriesStyle が破壊的に上書きするため、payload 色
//     （backend 既定色）は描画後に失われる。解決順ステップ 4 がこれを読むと、テーマ A→B の
//     切替結果が適用履歴に依存して非決定になる（R-6）。
// (b) 水準線（horizontal_line）は priceLine 経路で生成され applySeriesStyle に到達しない
//     （E-10）。テーマの level トークンを届けるには公開入口が 1 個要る。

import test from 'node:test';
import assert from 'node:assert/strict';

import { SeriesDrawer } from '../js/adapter/front/series_drawer.js';

// --- 最小 Fake（lwc の系列生成・価格線のみ）------------------------------
function makeHost() {
  const created = [];
  const priceLines = [];
  const mkSeries = () => {
    const s = {
      options: null, data: null,
      applyOptions(o) { this.options = { ...(this.options ?? {}), ...o }; },
      setData(d) { this.data = d; },
      createPriceLine(o) { const pl = { ...o }; priceLines.push(pl); return pl; },
      removePriceLine(pl) { const i = priceLines.indexOf(pl); if (i >= 0) priceLines.splice(i, 1); },
    };
    created.push(s);
    return s;
  };
  const pane = { addSeries: () => mkSeries(), setStretchFactor() {}, setPreserveEmptyPane() {} };
  const host = {
    _instances: new Map(),
    _mainStretchSet: true,
    _lwc: { LineSeries: 'L', HistogramSeries: 'H', CandlestickSeries: 'C' },
    _chart: { addSeries: () => mkSeries(), addPane: () => pane, panes: () => [pane], removeSeries() {} },
    _mainSeries: mkSeries(),
    _restorePaneScaleRange() {},
  };
  return { host, created, priceLines };
}

const payload = (name, color) => ({ name, color, width: 1, style: 'solid', data: [{ time: 1, value: 1 }] });

// =========================================================================
// (c) styleMeta.baseColor — 通過条件 7
// =========================================================================

test('R-6: 生成時に payload 色が baseColor へ記録される', () => {
  const { host } = makeHost();
  const d = new SeriesDrawer(host);
  d._renderSeries('i#1', [payload('s', 'rgba(123, 104, 238, 1)')], 'line', {});
  const meta = host._instances.get('i#1').styleMeta.get('i#1::s');
  assert.equal(meta.baseColor, 'rgba(123, 104, 238, 1)');
  assert.equal(meta.color, 'rgba(123, 104, 238, 1)');
});

test('通過条件 7: baseColor は applySeriesStyle 呼び出し後も変化しない（不変）', () => {
  const { host } = makeHost();
  const d = new SeriesDrawer(host);
  d._renderSeries('i#1', [payload('s', '#111111')], 'line', {});
  const meta = host._instances.get('i#1').styleMeta.get('i#1::s');

  d.applySeriesStyle('i#1', 's', { color: '#ffffff' });
  assert.equal(meta.color, '#ffffff', 'color は上書きされる（従来どおり）');
  assert.equal(meta.baseColor, '#111111', 'baseColor は不変でなければならない');

  d.applySeriesStyle('i#1', 's', { color: '#222222', width: 3, style: 'dashed', visible: false });
  assert.equal(meta.baseColor, '#111111');
});

test('R-6: baseColor が無い payload（色未指定）は null で記録される', () => {
  const { host } = makeHost();
  const d = new SeriesDrawer(host);
  d._renderSeries('i#1', [{ name: 's', data: [] }], 'line', {});
  assert.equal(host._instances.get('i#1').styleMeta.get('i#1::s').baseColor, null);
});

test('R-6: 系列種別スワップ（line ⇄ histogram）後も baseColor は保たれる', () => {
  const { host } = makeHost();
  const d = new SeriesDrawer(host);
  d._renderSeries('i#1', [{ ...payload('s', '#111111'), bar_editable: true }], 'line', {});
  const meta = host._instances.get('i#1').styleMeta.get('i#1::s');
  d.applySeriesStyle('i#1', 's', { display: 'bar' });
  assert.equal(meta.kind, 'histogram', 'スワップが起きていること');
  assert.equal(meta.baseColor, '#111111');
});

test('R-2 / F-C2: ヒート系列は色 patch を無視するが baseColor は記録される', () => {
  const { host } = makeHost();
  const d = new SeriesDrawer(host);
  d._renderSeries('i#1', [{ name: 's', color: '#111111', data: [{ time: 1, value: 1, color: '#ff0000' }] }], 'histogram', {});
  const meta = host._instances.get('i#1').styleMeta.get('i#1::s');
  assert.equal(meta.heat, true);
  d.applySeriesStyle('i#1', 's', { color: '#ffffff' });
  assert.equal(meta.color, '#111111', 'ヒートは色 patch を構造的に無視する（確定仕様）');
  assert.equal(meta.baseColor, '#111111');
});

// =========================================================================
// (b) applyLevelLineColor — 水準線用の公開入口 1 個
// =========================================================================

function withHlines({ pane }) {
  const { host, priceLines } = makeHost();
  const d = new SeriesDrawer(host);
  d._renderSeries('i#1', [payload('body', '#111111')], 'line', pane ? { pane: true, name: 'p' } : {});
  const slot = host._instances.get('i#1');
  slot.hlinePayloads = [
    { price: 10, color: 'rgba(84,84,84,1)', width: 1, style: 'dashed', text: 'a' },
    { price: 20, color: 'rgba(84,84,84,1)', width: 1, style: 'dashed', text: 'b' },
    { price: 30, color: 'rgba(84,84,84,1)', width: 1, style: 'dashed', text: 'c' },
  ];
  d._createPriceLines(slot, slot.hlinePayloads);
  return { d, host, slot, priceLines };
}

test('R-3: level 未宣言（color=null）なら現行経路のまま（overlay は backend 色）', () => {
  const { d, slot } = withHlines({ pane: false });
  const before = slot.priceLines.map((pl) => pl.color);
  assert.deepEqual(before, ['rgba(84,84,84,1)', 'rgba(84,84,84,1)', 'rgba(84,84,84,1)']);
  d.applyLevelLineColor('i#1', null);
  assert.deepEqual(slot.priceLines.map((pl) => pl.color), before);
});

test('R-3: level 未宣言なら pane 指標は schemeColor（緑→赤の距離補間）のまま', () => {
  const { d, slot } = withHlines({ pane: true });
  const before = slot.priceLines.map((pl) => pl.color);
  assert.ok(before.every((c) => /^rgb\(/.test(c)), `schemeColor 由来でない: ${before}`);
  assert.notEqual(before[0], before[1], '距離補間で色が分かれている');
  d.applyLevelLineColor('i#1', null);
  assert.deepEqual(slot.priceLines.map((pl) => pl.color), before);
});

test('R-3: level を宣言すると schemeColor / backend 色に優先して解決色が使われる', () => {
  for (const pane of [false, true]) {
    const { d, slot } = withHlines({ pane });
    assert.equal(d.applyLevelLineColor('i#1', '#abcdef'), true);
    assert.deepEqual(slot.priceLines.map((pl) => pl.color), ['#abcdef', '#abcdef', '#abcdef'], `pane=${pane}`);
    // 色以外（price/width/style/title）は不変（R-1: 適用先は色のみ）。
    assert.deepEqual(slot.priceLines.map((pl) => pl.price), [10, 20, 30]);
    assert.deepEqual(slot.priceLines.map((pl) => pl.title), ['a', 'b', 'c']);
    assert.deepEqual(slot.priceLines.map((pl) => pl.lineWidth), [1, 1, 1]);
    assert.deepEqual(slot.priceLines.map((pl) => pl.lineStyle), [2, 2, 2]);
  }
});

test('applyLevelLineColor は再生成経路（setVisible の OFF→ON）を跨いで保たれる', () => {
  const { d, slot } = withHlines({ pane: true });
  d.applyLevelLineColor('i#1', '#abcdef');
  d.setVisible('i#1', false);
  assert.equal(slot.priceLines.length, 0);
  d.setVisible('i#1', true);
  assert.deepEqual(slot.priceLines.map((pl) => pl.color), ['#abcdef', '#abcdef', '#abcdef']);
});

test('applyLevelLineColor は null で現行経路へ戻せる（テーマ解除）', () => {
  const { d, slot } = withHlines({ pane: true });
  d.applyLevelLineColor('i#1', '#abcdef');
  d.applyLevelLineColor('i#1', null);
  assert.ok(slot.priceLines.every((pl) => /^rgb\(/.test(pl.color)), '現行の schemeColor へ戻る');
});

test('applyLevelLineColor は未知 instance / 水準線を持たない instance で安全（全域的）', () => {
  const { host } = makeHost();
  const d = new SeriesDrawer(host);
  assert.equal(d.applyLevelLineColor('nope', '#abcdef'), false);
  d._renderSeries('i#1', [payload('s', '#111111')], 'line', {});
  assert.equal(d.applyLevelLineColor('i#1', '#abcdef'), true);
  assert.equal(host._instances.get('i#1').priceLines.length, 0);
});

test('applyLevelLineColor は不正な色を無視する（現行経路のまま）', () => {
  const { d, slot } = withHlines({ pane: false });
  for (const bad of ['nonsense', '', 0, {}, []]) {
    d.applyLevelLineColor('i#1', bad);
    assert.deepEqual(slot.priceLines.map((pl) => pl.color),
      ['rgba(84,84,84,1)', 'rgba(84,84,84,1)', 'rgba(84,84,84,1)'], String(bad));
  }
});
