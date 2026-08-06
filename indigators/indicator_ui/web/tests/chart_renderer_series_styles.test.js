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
const CandlestickSeries = { kind: 'Candlestick' };

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
    chart, mainSeries: main, lwc: { LineSeries, HistogramSeries, CandlestickSeries },
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
  assert.deepEqual(styles[0], { name: 'MA', kind: 'line', color: '#2962ff', width: 1, style: 'solid', visible: true, heat: false, display: null });
  assert.deepEqual(styles[1], { name: 'Smoothing', kind: 'line', color: '#00aa00', width: 2, style: 'dotted', visible: true, heat: false, display: null });
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

// ISSUE-276: 値と色の表示先は読み取り欄からペイン別凡例へ移った。色の追従はそこで固定する。
test('ISSUE-109/276 ペイン別凡例: applySeriesStyle の色変更が凡例の値の色へ追従する', () => {
  const { renderer } = newRenderer();
  renderer.renderLine('ma#1', MA_PAYLOADS);
  renderer.applySeriesStyle('ma#1', 'MA', { color: '#ff0000' });
  const model = renderer.paneLegendModel(null);
  const row = model.groups.flatMap((g) => g.rows).find((r) => r.instanceId === 'ma#1');
  const ma = row.values.find((v) => v.name === 'MA');
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

test('btlm_trail: readout_only 系列は軸ラベル/プライスライン/クロスヘアマーカーを一切出さない', () => {
  const { renderer, chart } = newRenderer();
  renderer.renderLine('trail#5', [{
    name: 'btlm_trail_sigma', kind: 'line', style: 'solid', width: 1, color: '#a0a0a0',
    line_visible: false, point_markers: false, readout_only: true,
    data: [{ time: 1, value: 2500 }],
  }]);
  const o = chart.created[0]._createOpts;
  // 価格軸の名前ラベルは series.title 由来（lastValueVisible とは独立）。読取専用は空 title で抑止。
  assert.equal(o.title, '', 'readout_only は title を空にして軸の名前ラベルを出さない');
  assert.equal(o.lastValueVisible, false);
  assert.equal(o.priceLineVisible, false);
  assert.equal(o.crosshairMarkerVisible, false);
});

// 価格軸（画面右端）のラベル仕様（ユーザー指示 2026-07-23）: 系列名チップ（title）ではなく
//   現在値（数値・lastValueVisible=true）を表示する。
test('通常系列は価格軸に現在値（数値）を表示する（title 無し・lastValueVisible=true）', () => {
  const { renderer, chart } = newRenderer();
  renderer.renderLine('ma#11', MA_PAYLOADS);
  assert.equal('title' in chart.created[0]._createOpts, false, '名前チップ（title）は設定しない');
  assert.equal(chart.created[0]._createOpts.lastValueVisible, true, '現在値ラベルを表示する');
  assert.equal('crosshairMarkerVisible' in chart.created[0]._createOpts, false);
});

// 案A（btlm_trail）: スタイルタブの「系列表示（ドット/ライン）」= applySeriesStyle の display patch を
//   pointMarkersVisible/lineVisible へ写像する。styleMeta にも反映し getSeriesStyles が現在値を返す。
test('btlm_trail: applySeriesStyle({display:"line"}) → pointMarkersVisible=false/lineVisible=true', () => {
  const { renderer, chart } = newRenderer();
  renderer.renderLine('trail#d1', [{
    name: 'btlm_trail_mean', kind: 'line', style: 'solid', width: 2, color: '#7b68ee',
    point_markers: true, line_visible: false, point_markers_radius: 3.5,
    data: [{ time: 1, value: 10 }],
  }]);
  // 初期は payload 由来で dots。
  assert.equal(renderer.getSeriesStyles('trail#d1')[0].display, 'dots');
  const ok = renderer.applySeriesStyle('trail#d1', 'btlm_trail_mean', { display: 'line' });
  assert.equal(ok, true);
  assert.equal(chart.created[0]._options.pointMarkersVisible, false);
  assert.equal(chart.created[0]._options.lineVisible, true);
  assert.equal(renderer.getSeriesStyles('trail#d1')[0].display, 'line');
});

test('btlm_trail: applySeriesStyle({display:"dots"}) → pointMarkersVisible=true/lineVisible=false', () => {
  const { renderer, chart } = newRenderer();
  renderer.renderLine('trail#d2', [{
    name: 'btlm_trail_mean', kind: 'line', style: 'solid', width: 2, color: '#7b68ee',
    point_markers: false, line_visible: true, data: [{ time: 1, value: 10 }],
  }]);
  assert.equal(renderer.getSeriesStyles('trail#d2')[0].display, 'line');
  renderer.applySeriesStyle('trail#d2', 'btlm_trail_mean', { display: 'dots' });
  assert.equal(chart.created[0]._options.pointMarkersVisible, true);
  assert.equal(chart.created[0]._options.lineVisible, false);
  assert.equal(renderer.getSeriesStyles('trail#d2')[0].display, 'dots');
});

test('display 未指定の patch は pointMarkersVisible/lineVisible を触らない（非破壊）', () => {
  const { renderer, chart } = newRenderer();
  renderer.renderLine('ma#d3', MA_PAYLOADS);
  renderer.applySeriesStyle('ma#d3', 'MA', { color: '#ff0000' });
  assert.equal('pointMarkersVisible' in chart.created[0]._options, false);
  assert.equal('lineVisible' in chart.created[0]._options, false);
  // ヒント無し系列は display=null。
  assert.equal(renderer.getSeriesStyles('ma#d3')[0].display, null);
});

// ==========================================================================
// 案A（MAROD 棒グラフ）: bar_editable ゲート + line ⇄ histogram 系列スワップ
// ==========================================================================

const MAROD_LINE_PAYLOAD = {
  name: 'btlm_trail_marod', kind: 'line', color: '#7b68ee', width: 2, style: 'solid',
  bar_editable: true, data: [{ time: 1, value: 0.5 }, { time: 2, value: -0.3 }],
};

test('案A(MAROD) _renderSeries: bar_editable 系列は seriesData を保持し styleMeta.barEditable=true', () => {
  const { renderer } = newRenderer();
  renderer.renderLine('marod#1', [MAROD_LINE_PAYLOAD], { pane: true, name: 'MAROD' });
  const slot = renderer._instances.get('marod#1');
  assert.deepEqual(slot.seriesData.get('marod#1::btlm_trail_marod'), MAROD_LINE_PAYLOAD.data, '保持データ');
  const meta = slot.styleMeta.get('marod#1::btlm_trail_marod');
  assert.equal(meta.barEditable, true);
});

test('案A(MAROD) _renderSeries: 非ゲート系列は seriesData 非保持・barEditable キーを持たない（挙動不変）', () => {
  const { renderer } = newRenderer();
  renderer.renderLine('ma#g', MA_PAYLOADS);
  const slot = renderer._instances.get('ma#g');
  assert.equal(slot.seriesData.size, 0, '非ゲート系列は seriesData に載せない');
  assert.equal('barEditable' in slot.styleMeta.get('ma#g::MA'), false);
});

test('案A(MAROD) applySeriesStyle({display:"bar"}): LineSeries→HistogramSeries へ再生成（base:0・データ保持・同一キー）', () => {
  const { renderer, chart } = newRenderer();
  renderer.renderLine('marod#2', [MAROD_LINE_PAYLOAD], { pane: true, name: 'MAROD' });
  const before = chart.created.length;
  const ok = renderer.applySeriesStyle('marod#2', 'btlm_trail_marod', { display: 'bar' });
  assert.equal(ok, true);
  // 新系列は HistogramSeries で base:0。
  const newSeries = chart.created[chart.created.length - 1];
  assert.equal(chart.created.length, before + 1, '系列を 1 本再生成');
  assert.equal(newSeries._def, HistogramSeries, 'HistogramSeries へ差し替え');
  assert.equal(newSeries._createOpts.base, 0, '0% 中心（base:0）');
  assert.equal('lineWidth' in newSeries._createOpts, false, 'histogram は lineWidth を出さない');
  assert.equal('lineStyle' in newSeries._createOpts, false, 'histogram は lineStyle を出さない');
  assert.equal(newSeries._createOpts.color, '#7b68ee', '色は活かす');
  assert.deepEqual(newSeries._data, MAROD_LINE_PAYLOAD.data, '保持データを再設定');
  // 同一キーで slot.lines を差し替え。
  const slot = renderer._instances.get('marod#2');
  assert.equal(slot.lines.get('marod#2::btlm_trail_marod'), newSeries, '同一キー維持');
  // meta.kind=histogram / display=bar。getSeriesStyles も往復。
  assert.equal(slot.styleMeta.get('marod#2::btlm_trail_marod').kind, 'histogram');
  const styles = renderer.getSeriesStyles('marod#2');
  assert.equal(styles[0].display, 'bar', 'getSeriesStyles は display=bar を読み戻す');
  assert.equal(styles[0].kind, 'histogram');
});

test('案A(MAROD) applySeriesStyle: histogram→{display:line,style:dotted} で LineSeries へ戻す（線種復元・データ保持）', () => {
  const { renderer, chart } = newRenderer();
  renderer.renderLine('marod#3', [MAROD_LINE_PAYLOAD], { pane: true, name: 'MAROD' });
  renderer.applySeriesStyle('marod#3', 'btlm_trail_marod', { display: 'bar' }); // line→histogram
  const ok = renderer.applySeriesStyle('marod#3', 'btlm_trail_marod', { display: 'line', style: 'dotted' });
  assert.equal(ok, true);
  const back = chart.created[chart.created.length - 1];
  assert.equal(back._def, LineSeries, 'LineSeries へ戻す');
  assert.equal(back._createOpts.lineStyle, 1, 'dotted → lineStyle 1');
  assert.deepEqual(back._data, MAROD_LINE_PAYLOAD.data, '保持データを再設定');
  const slot = renderer._instances.get('marod#3');
  assert.equal(slot.styleMeta.get('marod#3::btlm_trail_marod').kind, 'line');
  assert.equal(renderer.getSeriesStyles('marod#3')[0].display, 'line');
});

test('案A(MAROD) 二重ゲート: barEditable でない系列は {display:"bar"} でもスワップしない（native histogram を線化しない）', () => {
  const { renderer, chart } = newRenderer();
  // barEditable ヒント無しの histogram（他指標＝profit_band 等）。
  renderer.renderHistogram('vol#1', [{ name: 'vol', kind: 'histogram', color: '#888888', data: [{ time: 1, value: 3 }] }]);
  const before = chart.created.length;
  renderer.applySeriesStyle('vol#1', 'vol', { display: 'bar' });
  assert.equal(chart.created.length, before, '系列再生成なし（スワップ不発）');
  assert.equal(renderer.getSeriesStyles('vol#1')[0].kind, 'histogram', 'kind 不変');
  // barEditable でない line も display:bar でスワップしない。
  renderer.renderLine('ma#g2', MA_PAYLOADS);
  const before2 = chart.created.length;
  renderer.applySeriesStyle('ma#g2', 'MA', { display: 'bar' });
  assert.equal(chart.created.length, before2, 'line も再生成なし');
  assert.equal(renderer.getSeriesStyles('ma#g2')[0].kind, 'line');
});

test('案A(MAROD) スワップ: 0% 基準線（priceLine）を新 host へ再生成し scaleHost を張り替える', () => {
  const { renderer, chart } = newRenderer();
  renderer.renderLine('marod#4', [MAROD_LINE_PAYLOAD], { pane: true, name: 'MAROD' });
  renderer.renderHorizontal('marod#4', [{ price: 0, color: '#888888', width: 1, style: 'dashed', text: '0%' }]);
  const slot = renderer._instances.get('marod#4');
  const oldSeries = slot.lines.get('marod#4::btlm_trail_marod');
  assert.equal(slot.scaleHost, oldSeries, 'スワップ前は line が scaleHost');
  assert.equal(slot.priceLines.length, 1, '0% 線が 1 本');
  renderer.applySeriesStyle('marod#4', 'btlm_trail_marod', { display: 'bar' });
  const newSeries = chart.created[chart.created.length - 1];
  assert.equal(slot.scaleHost, newSeries, 'scaleHost を新系列へ張り替え');
  assert.equal(slot.priceLineHost, newSeries, '0% 線 host も新系列');
  assert.equal(slot.priceLines.length, 1, '0% 線は再生成されて 1 本を維持');
});


// --- level_dash（ローソク足幅の水平ダッシュ・ISSUE-226）------------------------
// CandlestickSeries は `color` オプションを持たず up/down/border/wick の 6 経路で着色する。
//   生成時と applySeriesStyle で同じ写像を通さないと「色を変えても反映されない」不具合になる。

const DASH_PAYLOADS = [
  { name: 'cvfe_u1', kind: 'level_dash', color: 'rgba(233, 30, 99, 0.5)', data: [{ time: 1, value: 10 }, { time: 2, value: 11 }] },
];

test('ISSUE-226 level_dash: 生成時に単色が 6 つの色経路へ複製され、値は同値 4 値へ展開される', () => {
  const { renderer, chart } = newRenderer();
  renderer.renderLevelDash('cvfe#1', DASH_PAYLOADS);
  const s = chart.created[0];
  assert.equal(s._def, CandlestickSeries, 'CandlestickSeries で生成されること');
  for (const k of ['upColor', 'downColor', 'borderUpColor', 'borderDownColor', 'wickUpColor', 'wickDownColor']) {
    assert.equal(s._createOpts[k], 'rgba(233, 30, 99, 0.5)', k);
  }
  assert.equal(s._createOpts.wickVisible, false, 'ヒゲは出さない');
  assert.deepEqual(s.data()[0], { time: 1, open: 10, high: 10, low: 10, close: 10 });
});

test('ISSUE-226 level_dash: applySeriesStyle の色が 6 経路すべてへ反映される（回帰）', () => {
  const { renderer, chart } = newRenderer();
  renderer.renderLevelDash('cvfe#1', DASH_PAYLOADS);
  const ok = renderer.applySeriesStyle('cvfe#1', 'cvfe_u1', { color: '#00ff00' });
  assert.equal(ok, true);
  const s = chart.created[0];
  for (const k of ['upColor', 'downColor', 'borderUpColor', 'borderDownColor', 'wickUpColor', 'wickDownColor']) {
    assert.equal(s._options[k], '#00ff00', `${k} が更新されていない＝色変更が無視される`);
  }
  assert.equal(renderer.getSeriesStyles('cvfe#1')[0].color, '#00ff00', 'styleMeta も同期');
});

test('ISSUE-226 level_dash: 可視性の切替は従来どおり visible で効く', () => {
  const { renderer, chart } = newRenderer();
  renderer.renderLevelDash('cvfe#1', DASH_PAYLOADS);
  renderer.applySeriesStyle('cvfe#1', 'cvfe_u1', { visible: false });
  assert.equal(chart.created[0]._options.visible, false);
});

test('ISSUE-226 line 系列の色経路は従来どおり color のみ（非波及）', () => {
  const { renderer, chart } = newRenderer();
  renderer.renderLine('ma#1', MA_PAYLOADS);
  renderer.applySeriesStyle('ma#1', 'MA', { color: '#123456' });
  const s = chart.created[0];
  assert.equal(s._options.color, '#123456');
  assert.equal(s._options.upColor, undefined, 'line 系列に candlestick 用オプションが漏れていない');
});
