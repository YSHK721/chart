// ISSUE-109: ChartRenderer 系列スタイル API（getSeriesStyles / applySeriesStyle）と
//   setVisible の系列別可視性 AND 合成の回帰検証。
//
// 設計入力: 内部設計_パラメータ設定ダイアログ.md §6.1（適用先 renderLine の
//   color/lineWidth/lineStyle・applyOptions で再計算不要）・§6.2（系列単位可視性）。
// 構造: Arrange-Act-Assert（AAA）。Fake chart/series は chart_renderer.test.js と同型の最小版。

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { ChartRenderer } from '../js/adapter/front/chart_renderer.js';

const LineSeries = { kind: 'Line' };
const HistogramSeries = { kind: 'Histogram' };

function fakeSeries(def) {
  return {
    _def: def, _data: null, _options: {}, _createOpts: null, _updates: [],
    setData(points) { this._data = points; },
    data() { return this._data ?? []; },
    update(p) { this._updates.push(p); },
    applyOptions(opts) { Object.assign(this._options, opts); },
    createPriceLine(opt) { return { opt }; },
    removePriceLine() {},
  };
}

function fakeChart() {
  const created = [];
  const pane0 = {
    _series: [],
    paneIndex() { return 0; },
    setStretchFactor() {},
    setPreserveEmptyPane() {},
    addSeries(def, opts) {
      const s = fakeSeries(def); s._createOpts = opts; created.push(s); return s;
    },
  };
  return {
    created,
    panes() { return [pane0]; },
    addPane() { return pane0; },
    removePane() {},
    addSeries(def, opts) { return pane0.addSeries(def, opts); },
    removeSeries() {},
    applyOptions() {},
    timeScale() { return { fitContent() {} }; },
    subscribeCrosshairMove() {},
  };
}

function newRenderer() {
  const chart = fakeChart();
  const main = { setData() {}, applyOptions() {}, priceScale: () => ({ applyOptions() {} }) };
  const renderer = new ChartRenderer({
    chart, mainSeries: main, lwc: { LineSeries, HistogramSeries },
  });
  return { renderer, chart };
}

const MA_PAYLOADS = [
  { name: 'MA', kind: 'line', style: 'solid', width: 1, color: '#2962ff', data: [{ time: 1, value: 10 }] },
  { name: 'Smoothing', kind: 'line', style: 'dotted', width: 2, color: '#00aa00', data: [{ time: 1, value: 9 }] },
];

test('ISSUE-109 getSeriesStyles: 生成時ペイロードのスタイルを実描画値として返す', () => {
  const { renderer } = newRenderer();
  renderer.renderLine('ma#1', MA_PAYLOADS);
  const styles = renderer.getSeriesStyles('ma#1');
  assert.equal(styles.length, 2);
  assert.deepEqual(styles[0], { name: 'MA', kind: 'line', color: '#2962ff', width: 1, style: 'solid', visible: true, heat: false });
  assert.deepEqual(styles[1], { name: 'Smoothing', kind: 'line', color: '#00aa00', width: 2, style: 'dotted', visible: true, heat: false });
});

test('ISSUE-109 getSeriesStyles: 未知 instance は空配列（防御）', () => {
  const { renderer } = newRenderer();
  assert.deepEqual(renderer.getSeriesStyles('nothing#1'), []);
});

test('ISSUE-109 applySeriesStyle: 色/線幅/線種を series.applyOptions で即時反映し styleMeta も同期', () => {
  const { renderer, chart } = newRenderer();
  renderer.renderLine('ma#1', MA_PAYLOADS);
  const ok = renderer.applySeriesStyle('ma#1', 'MA', { color: '#ff0000', width: 4, style: 'dashed' });
  assert.equal(ok, true);
  const series = chart.created[0];
  assert.equal(series._options.color, '#ff0000');
  assert.equal(series._options.lineWidth, 4);
  assert.equal(series._options.lineStyle, 2, 'dashed → lineStyle 整数 2');
  assert.equal(series._options.visible, true);
  const styles = renderer.getSeriesStyles('ma#1');
  assert.equal(styles[0].color, '#ff0000');
  assert.equal(styles[0].width, 4);
  assert.equal(styles[0].style, 'dashed');
});

test('ISSUE-109 applySeriesStyle: 差分のみの patch（色だけ）で他フィールドは維持', () => {
  const { renderer, chart } = newRenderer();
  renderer.renderLine('ma#1', MA_PAYLOADS);
  renderer.applySeriesStyle('ma#1', 'Smoothing', { color: '#123456' });
  const series = chart.created[1];
  assert.equal(series._options.color, '#123456');
  assert.equal(series._options.lineWidth, 2, '生成時 width を維持');
  assert.equal(series._options.lineStyle, 1, '生成時 dotted を維持');
});

test('ISSUE-109 applySeriesStyle: histogram は色のみ適用（lineWidth/lineStyle を送らない）', () => {
  const { renderer, chart } = newRenderer();
  renderer.renderHistogram('h#1', [{ name: 'vol', kind: 'histogram', color: '#888888', data: [] }]);
  renderer.applySeriesStyle('h#1', 'vol', { color: '#ff0000', width: 5, style: 'dashed' });
  const series = chart.created[0];
  assert.equal(series._options.color, '#ff0000');
  assert.equal('lineWidth' in series._options, false);
  assert.equal('lineStyle' in series._options, false);
});

test('ISSUE-109 applySeriesStyle: 未知系列は no-op（false）', () => {
  const { renderer } = newRenderer();
  renderer.renderLine('ma#1', MA_PAYLOADS);
  assert.equal(renderer.applySeriesStyle('ma#1', 'no-such', { color: '#ff0000' }), false);
  assert.equal(renderer.applySeriesStyle('none#1', 'MA', { color: '#ff0000' }), false);
});

test('ISSUE-109 系列別可視性: visible=false は series 非表示・instance eye と AND 合成', () => {
  const { renderer, chart } = newRenderer();
  renderer.renderLine('ma#1', MA_PAYLOADS);
  // 系列単位で MA を非表示
  renderer.applySeriesStyle('ma#1', 'MA', { visible: false });
  assert.equal(chart.created[0]._options.visible, false);
  assert.equal(chart.created[1]._options.visible ?? true, true, '他系列は表示のまま');
  // instance eye OFF → 全系列非表示
  renderer.setVisible('ma#1', false);
  assert.equal(chart.created[0]._options.visible, false);
  assert.equal(chart.created[1]._options.visible, false);
  // instance eye ON へ復帰 → 系列単位の非表示（MA）は維持される（AND 合成）
  renderer.setVisible('ma#1', true);
  assert.equal(chart.created[0]._options.visible, false, '個別非表示は eye ON でも維持');
  assert.equal(chart.created[1]._options.visible, true);
});

test('ISSUE-109 overlay 読み取り欄: applySeriesStyle の色変更が readout メタへ追従する', () => {
  const { renderer } = newRenderer();
  renderer.renderLine('ma#1', MA_PAYLOADS); // overlay（pane 0）の line は readout に載る
  renderer.applySeriesStyle('ma#1', 'MA', { color: '#ff0000' });
  const dto = renderer._buildReadoutDto(null);
  const ma = dto.overlays.find((o) => o.name === 'MA');
  assert.ok(ma);
  assert.equal(ma.color, '#ff0000');
});

// ---- ISSUE-112（ユーザー裁定）: バー別ヒート配色はユーザー色より絶対優先 -----------

const ADX_DATA = [
  { time: 1, value: 5, color: '#00aa00' },
  { time: 2, value: -3, color: '#aa0000' },
];

test('ISSUE-112 styleMeta: バー別色を持つ histogram は heat=true・持たない histogram/line は false', () => {
  const { renderer } = newRenderer();
  renderer.renderHistogram('adx#1', [{ name: 'adx_needle', kind: 'histogram', color: '#006400', data: ADX_DATA }]);
  renderer.renderHistogram('flat#1', [{ name: 'flat', kind: 'histogram', color: '#888888', data: [{ time: 1, value: 1 }] }]);
  renderer.renderLine('ma#9', [{ name: 'MA', kind: 'line', color: '#2962ff', width: 1, style: 'solid', data: [{ time: 1, value: 1, color: '#123456' }] }]);
  assert.equal(renderer.getSeriesStyles('adx#1')[0].heat, true);
  assert.equal(renderer.getSeriesStyles('flat#1')[0].heat, false);
  assert.equal(renderer.getSeriesStyles('ma#9')[0].heat, false, 'line は heat 判定対象外');
});

test('ISSUE-112 applySeriesStyle: heat histogram は色 patch を無視（データ・options・meta とも不変）', () => {
  const { renderer, chart } = newRenderer();
  renderer.renderHistogram('adx#1', [{ name: 'adx_needle', kind: 'histogram', color: '#006400', data: ADX_DATA }]);
  const ok = renderer.applySeriesStyle('adx#1', 'adx_needle', { color: '#ff0000' });
  assert.equal(ok, true);
  const series = chart.created[0];
  assert.deepEqual(series._data.map((p) => p.color), ['#00aa00', '#aa0000'], 'ヒート配色を維持（塗り替えない）');
  assert.equal(series._options.color, '#006400', 'series 色（軸ラベル）も変更しない');
  assert.equal(renderer.getSeriesStyles('adx#1')[0].color, '#006400', 'meta 色も不変');
});

test('ISSUE-112 applySeriesStyle: heat histogram でも可視性トグルは有効', () => {
  const { renderer, chart } = newRenderer();
  renderer.renderHistogram('adx#1', [{ name: 'adx_needle', kind: 'histogram', color: '#006400', data: ADX_DATA }]);
  renderer.applySeriesStyle('adx#1', 'adx_needle', { visible: false });
  assert.equal(chart.created[0]._options.visible, false);
});

test('ISSUE-112 非 heat histogram: 色 patch は options.color で有効（バー別色が無く素で効く）', () => {
  const { renderer, chart } = newRenderer();
  renderer.renderHistogram('flat#1', [{ name: 'flat', kind: 'histogram', color: '#888888', data: [{ time: 1, value: 1 }] }]);
  renderer.applySeriesStyle('flat#1', 'flat', { color: '#ff0000' });
  assert.equal(chart.created[0]._options.color, '#ff0000');
  assert.deepEqual(chart.created[0]._data, [{ time: 1, value: 1 }], 'データは触らない');
});

test('ISSUE-112 setData/updateSeriesTail: 流入点は常に素通し（ユーザー色への写像機構は撤去済み）', () => {
  const { renderer, chart } = newRenderer();
  renderer.renderHistogram('adx#1', [{ name: 'adx_needle', kind: 'histogram', color: '#006400', data: ADX_DATA }]);
  renderer.applySeriesStyle('adx#1', 'adx_needle', { color: '#ff0000' }); // 無視される
  renderer.setData('adx#1::adx_needle', [{ time: 9, value: 1, color: '#123456' }]);
  assert.deepEqual(chart.created[0]._data, [{ time: 9, value: 1, color: '#123456' }], 'setData 素通し');
  renderer.updateSeriesTail('adx#1::adx_needle', [{ time: 10, value: 2, color: '#654321' }]);
  assert.deepEqual(chart.created[0]._updates, [{ time: 10, value: 2, color: '#654321' }], 'tail 素通し');
});

// btlm_trail 表示層: ドット/ライン切替ヒント（point_markers/line_visible）を
//   lightweight-charts の系列オプション（pointMarkersVisible/lineVisible）へ写像する。
test('btlm_trail: payload の point_markers/line_visible を lwc オプションへ写像（ドット）', () => {
  const { renderer, chart } = newRenderer();
  renderer.renderLine('trail#1', [{
    name: 'btlm_trail_mean', kind: 'line', style: 'solid', width: 2, color: '#7b68ee',
    point_markers: true, line_visible: false, data: [{ time: 1, value: 10 }],
  }]);
  assert.equal(chart.created[0]._createOpts.pointMarkersVisible, true);
  assert.equal(chart.created[0]._createOpts.lineVisible, false);
});

test('btlm_trail: line_visible=true/point_markers=false でライン描画へ写像', () => {
  const { renderer, chart } = newRenderer();
  renderer.renderLine('trail#2', [{
    name: 'btlm_trail_mean', kind: 'line', style: 'solid', width: 2, color: '#7b68ee',
    point_markers: false, line_visible: true, data: [{ time: 1, value: 10 }],
  }]);
  assert.equal(chart.created[0]._createOpts.pointMarkersVisible, false);
  assert.equal(chart.created[0]._createOpts.lineVisible, true);
});

test('既存 payload（ヒント無し）はオプションに pointMarkersVisible/lineVisible を含めない（後方互換）', () => {
  const { renderer, chart } = newRenderer();
  renderer.renderLine('ma#9', MA_PAYLOADS);
  assert.equal('pointMarkersVisible' in chart.created[0]._createOpts, false);
  assert.equal('lineVisible' in chart.created[0]._createOpts, false);
});

test('btlm_trail: readout_only 系列は価格軸オートスケールから除外（autoscaleInfoProvider→null）', () => {
  const { renderer, chart } = newRenderer();
  renderer.renderLine('trail#3', [{
    name: 'btlm_trail_beta', kind: 'line', style: 'solid', width: 1, color: '#a0a0a0',
    line_visible: false, point_markers: false, readout_only: true,
    data: [{ time: 1, value: 0.02 }],
  }]);
  const opts = chart.created[0]._createOpts;
  assert.equal(typeof opts.autoscaleInfoProvider, 'function');
  // 価格軸から除外する正しい契約は { priceRange: null }（null 返却は既定オートスケール＝除外にならない）。
  assert.deepEqual(opts.autoscaleInfoProvider(), { priceRange: null }, 'priceRange:null＝価格軸に寄与しない');
});

test('btlm_trail: point_markers_radius を pointMarkersRadius へ写像（ドット視認性）', () => {
  const { renderer, chart } = newRenderer();
  renderer.renderLine('trail#4', [{
    name: 'btlm_trail_mean', kind: 'line', style: 'solid', width: 2, color: '#7b68ee',
    point_markers: true, line_visible: false, point_markers_radius: 4,
    data: [{ time: 1, value: 10 }],
  }]);
  assert.equal(chart.created[0]._createOpts.pointMarkersRadius, 4);
});

test('readout_only 無しの系列は autoscaleInfoProvider を設定しない（後方互換）', () => {
  const { renderer, chart } = newRenderer();
  renderer.renderLine('ma#10', MA_PAYLOADS);
  assert.equal('autoscaleInfoProvider' in chart.created[0]._createOpts, false);
});
