// indicator_controller.js（F3 系列名照合の実行主体・§3.3.6）の純ロジック検証。
//
// 設計入力: 内部設計書 §3.3.6（_validateSeriesNames: 応答 series[].name を
//   SeriesDef.series_name（dynamic は series_name_pattern 展開）集合と突合し、
//   不一致系列はスキップ＋console.warn。正常系 pOL 99% を誤検出しない）。
// 照合基準は domain SeriesDef（catalog 由来）。DOM 非依存の純ロジックのみ検証。
// 構造: Arrange-Act-Assert（AAA）。

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { IndicatorController } from '../js/adapter/front/indicator_controller.js';
import { get } from '../js/usecase/catalog.js';

// DOM/port を使わない純ロジック検証のため、ports は最小スタブで生成。
function controller() {
  const noop = () => {};
  return new IndicatorController({
    catalog: { listIndicators: () => [], get },
    compute: { compute: async () => ({ ok: true, generation: 0, series: [] }) },
    persistence: { loadApplied: () => [], saveApplied: noop, loadFavorites: () => [], saveFavorites: noop, loadUiState: () => ({}), saveUiState: noop, nextSeq: () => 1 },
    renderer: { renderLine: noop, renderHorizontal: noop, setData: noop, setVisible: noop, remove: noop },
    facade: {},
    document: null,
  });
}

test('_expectedSeriesNames expands static series_name set (tgp_btlm)', () => {
  const ctrl = controller();
  const def = get('tgp_btlm');
  const expected = ctrl._expectedSeriesNames(def);
  assert.ok(expected.has('btlm_mean'));
  assert.ok(expected.has('btlm_q5'));
  assert.ok(expected.has('btlm_q95'));
});

test('_expectedSeriesNames expands dynamic series_name_pattern set (profit_band 28 names)', () => {
  const ctrl = controller();
  const def = get('profit_band');
  const expected = ctrl._expectedSeriesNames(def);
  assert.equal(expected.size, 28);
  assert.ok(expected.has('pOL 99%'));
  assert.ok(expected.has('nOH 51%'));
});

// 汎用の params 対応 F3 展開（*FromParam）の検証。合成 def を用いる（moving_averages は
//   現在 4 固定系列のため dynamic pattern を持たないが、_expandPattern の汎用機能は維持する）。
const SYNTH_DYNAMIC_DEF = {
  id: 'synthetic',
  series: [{
    dynamic: true,
    seriesNamePattern: {
      template: '{bucket} {pct}',
      bucketsFromParam: 'types', bucketsUpper: true,
      pctsFromParam: 'periods', pctsInt: true,
      buckets: ['X'], pcts: ['1'],
    },
  }],
};

test('_expectedSeriesNames derives names from params, allowing arbitrary periods (252)', () => {
  const ctrl = controller();
  const params = { types: ['sma', 'ema'], periods: [252] };
  const expected = ctrl._expectedSeriesNames(SYNTH_DYNAMIC_DEF, params);
  assert.ok(expected.has('SMA 252'));
  assert.ok(expected.has('EMA 252'));
  const kept = ctrl._validateSeriesNames(
    [{ name: 'SMA 252', kind: 'line', data: [] }, { name: 'EMA 252', kind: 'line', data: [] }],
    SYNTH_DYNAMIC_DEF, params,
  );
  assert.deepEqual(kept.map((p) => p.name), ['SMA 252', 'EMA 252']);
});

test('_expectedSeriesNames falls back to static buckets/pcts when params omitted', () => {
  const ctrl = controller();
  const fb = ctrl._expectedSeriesNames(SYNTH_DYNAMIC_DEF);
  assert.ok(fb.has('X 1'));
  assert.ok(!fb.has('SMA 252'));
});

test('_validateSeriesNames keeps matching series and drops mismatches (F3 §3.3.6)', () => {
  const ctrl = controller();
  const def = get('tgp_btlm');
  const payloads = [
    { name: 'btlm_mean', kind: 'line', data: [] },
    { name: 'btlm_GARBAGE', kind: 'line', data: [] }, // 契約違反 → スキップ対象
    { name: 'btlm_q95', kind: 'line', data: [] },
  ];
  const kept = ctrl._validateSeriesNames(payloads, def);
  assert.deepEqual(kept.map((p) => p.name), ['btlm_mean', 'btlm_q95']);
});

test('_validateSeriesNames does NOT false-positive on the valid pOL 99% (D-2)', () => {
  const ctrl = controller();
  const def = get('profit_band');
  const payloads = [{ name: 'pOL 99%', kind: 'line', data: [] }, { name: 'pOL_99', kind: 'line', data: [] }];
  const kept = ctrl._validateSeriesNames(payloads, def);
  // series_name 'pOL 99%' は通過、source_column 風の 'pOL_99' はスキップ（照合は series_name 基準）
  assert.deepEqual(kept.map((p) => p.name), ['pOL 99%']);
});

test('_validateSeriesNames keeps horizontal_line whose name matches series_name (price_range_power)', () => {
  const ctrl = controller();
  const def = get('price_range_power');
  const payloads = [{ name: 'price_range_power', kind: 'horizontal_line', lines: [] }];
  const kept = ctrl._validateSeriesNames(payloads, def);
  assert.deepEqual(kept.map((p) => p.name), ['price_range_power']);
});

// ===========================================================================
// setTimeframe（§チャート表示時間選択・1 分足原子から resample）
// ===========================================================================

// 計算呼び出しを記録し generation をエコーする compute（recompute 採用条件 accepts を満たす）。
function recordingController() {
  const noop = () => {};
  const computeCalls = [];
  const setCandlesCalls = [];
  const loadCandlesCalls = [];
  const ctrl = new IndicatorController({
    catalog: { listIndicators: () => [], get },
    compute: { compute: async (req) => { computeCalls.push(req); return { ok: true, generation: req.generation ?? 0, series: [] }; } },
    persistence: { loadApplied: () => [], saveApplied: noop, loadFavorites: () => [], saveFavorites: noop, loadUiState: () => ({}), saveUiState: noop, nextSeq: () => 1 },
    renderer: { renderLine: noop, renderHorizontal: noop, setData: noop, setVisible: noop, remove: noop, setCandles: (c) => setCandlesCalls.push(c) },
    document: null,
    mode: 'b',
    datasetRef: 'jp225_m1',
    timeframe: '1D',
    recentBars: 1500,
    loadCandles: async (ref, tf) => { loadCandlesCalls.push([ref, tf]); return [{ time: 1, open: 1, high: 1, low: 1, close: 1 }]; },
  });
  return { ctrl, computeCalls, setCandlesCalls, loadCandlesCalls };
}

test('setTimeframe is a no-op when the timeframe is unchanged', async () => {
  const { ctrl, loadCandlesCalls } = recordingController();
  await ctrl.setTimeframe('1D'); // 既定と同一
  assert.equal(ctrl._timeframe, '1D');
  assert.equal(loadCandlesCalls.length, 0);
});

test('setTimeframe re-fetches candles and replaces the main series via renderer.setCandles', async () => {
  const { ctrl, loadCandlesCalls, setCandlesCalls } = recordingController();
  await ctrl.setTimeframe('1W');
  assert.equal(ctrl._timeframe, '1W');
  // datasetRef と新時間足で candles を再取得し、メイン系列へ反映する。
  assert.deepEqual(loadCandlesCalls.at(-1), ['jp225_m1', '1W']);
  assert.equal(setCandlesCalls.length, 1);
});

test('setTimeframe recomputes applied indicators carrying the new timeframe and limit', async () => {
  const { ctrl, computeCalls } = recordingController();
  // 指標を 1 つ適用（apply 時は timeframe='1D'）。
  await ctrl.applyIndicator('tgp_btlm', 'default');
  const beforeCount = computeCalls.length;
  // 時間足切替 → 適用済み指標が新時間足で再計算される。
  await ctrl.setTimeframe('1W');
  const after = computeCalls.slice(beforeCount);
  assert.ok(after.length >= 1, '再計算の compute が発火する');
  // 再計算 compute は新 timeframe と直近 N 本（limit）を伴う（gateway 注入）。
  const last = after.at(-1);
  assert.equal(last.timeframe, '1W');
  assert.equal(last.limit, 1500);
});
