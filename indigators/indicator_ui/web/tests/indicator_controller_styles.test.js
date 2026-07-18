// ISSUE-109: IndicatorController のスタイル永続適用（_applyStoredStyles）の回帰検証。
//
// 設計入力: 内部設計_パラメータ設定ダイアログ.md §6.3（適用順: recompute→スタイル適用）。
//   redraw（remove+_draw）は系列をペイロード既定色で再生成するため、_draw の最後に
//   AppliedInstance.styles（ユーザー上書き差分）を renderer.applySeriesStyle で再適用する。
// 構造: Arrange-Act-Assert（AAA）。ports は最小スタブ・DOM 非依存。

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { IndicatorController } from '../js/adapter/front/indicator_controller.js';
import { get } from '../js/usecase/catalog.js';
import { setSeriesStyles } from '../js/usecase/facade.js';

function styleRecordingController() {
  const noop = () => {};
  const styleCalls = [];
  const ctrl = new IndicatorController({
    catalog: { listIndicators: () => [], get },
    compute: { compute: async (req) => ({ ok: true, generation: req.generation ?? 0, series: [] }) },
    persistence: { loadApplied: () => [], saveApplied: noop, loadFavorites: () => [], saveFavorites: noop, loadUiState: () => ({}), saveUiState: noop, nextSeq: () => 1 },
    renderer: {
      renderLine: noop,
      renderHistogram: noop,
      renderHorizontal: noop,
      setData: noop,
      setVisible: noop,
      remove: noop,
      getSeriesStyles: () => [],
      applySeriesStyle: (instanceId, name, patch) => { styleCalls.push([instanceId, name, patch]); return true; },
    },
    document: null,
    mode: 'b',
    datasetRef: 'jp225_m1',
    timeframe: '1D',
  });
  return { ctrl, styleCalls };
}

test('ISSUE-109 _draw: AppliedInstance.styles の保存済み上書きを applySeriesStyle で再適用する', async () => {
  const { ctrl, styleCalls } = styleRecordingController();
  const inst = await ctrl.applyIndicator('moving_averages', 'default');
  assert.ok(inst);
  assert.equal(styleCalls.length, 0, 'styles 未保存の初回描画は適用なし');
  // スタイル上書きを state へ保存（歯車 OK 相当）→ recompute（redraw）でスタイルが再適用される。
  ctrl._state = setSeriesStyles(ctrl._state, inst.instanceId, { MA: { color: '#ff0000', width: 4 } });
  const accepted = await ctrl.recomputeInstance(inst.instanceId, null, {});
  assert.equal(accepted, true);
  assert.deepEqual(styleCalls.at(-1), [inst.instanceId, 'MA', { color: '#ff0000', width: 4 }]);
});

test('ISSUE-109 _applyStoredStyles: styles 未保存・renderer 非対応（旧 Fake）は no-op（防御）', async () => {
  const { ctrl } = styleRecordingController();
  const inst = await ctrl.applyIndicator('moving_averages', 'default');
  // styles 無し → 例外なく no-op
  assert.doesNotThrow(() => ctrl._applyStoredStyles(inst.instanceId));
  // renderer が applySeriesStyle を持たない場合も no-op（後方互換 Fake・SSR）
  ctrl._renderer = { renderLine: () => {}, remove: () => {} };
  ctrl._state = setSeriesStyles(ctrl._state, inst.instanceId, { MA: { color: '#ff0000' } });
  assert.doesNotThrow(() => ctrl._applyStoredStyles(inst.instanceId));
});

test('ISSUE-109 永続化往復: styles が saveApplied へ渡る（restore で再適用可能な形）', async () => {
  const saved = [];
  const noop = () => {};
  const ctrl = new IndicatorController({
    catalog: { listIndicators: () => [], get },
    compute: { compute: async (req) => ({ ok: true, generation: req.generation ?? 0, series: [] }) },
    persistence: {
      loadApplied: () => [],
      saveApplied: (json) => saved.push(json),
      loadFavorites: () => [], saveFavorites: noop, loadUiState: () => ({}), saveUiState: noop, nextSeq: () => 1,
    },
    renderer: { renderLine: noop, renderHorizontal: noop, setData: noop, setVisible: noop, remove: noop, applySeriesStyle: () => true },
    document: null,
    mode: 'b',
    datasetRef: 'jp225_m1',
    timeframe: '1D',
  });
  const inst = await ctrl.applyIndicator('moving_averages', 'default');
  ctrl._state = setSeriesStyles(ctrl._state, inst.instanceId, { MA: { color: '#ff0000' } });
  await ctrl.recomputeInstance(inst.instanceId, null, {});
  const last = saved.at(-1);
  assert.ok(last, 'recompute 後に persist される');
  const parsed = typeof last === 'string' ? JSON.parse(last) : last;
  const arr = Array.isArray(parsed) ? parsed : parsed.applied ?? [];
  const savedInst = arr.find((i) => i.instanceId === inst.instanceId);
  assert.deepEqual(savedInst.styles, { MA: { color: '#ff0000' } });
});

// ---- ISSUE-110: スタイルのみ高速経路（🟡-2）と stale キー剪定（🔴-1） ----------

test('ISSUE-110 🟡-2 _applyDialogResult: params/variant 無変更＋styles のみ → recompute せず直適用＋persist', async () => {
  const noop = () => {};
  const computeCalls = [];
  const styleCalls = [];
  const saved = [];
  const ctrl = new IndicatorController({
    catalog: { listIndicators: () => [], get },
    compute: { compute: async (req) => { computeCalls.push(req); return { ok: true, generation: req.generation ?? 0, series: [] }; } },
    persistence: { loadApplied: () => [], saveApplied: (j) => saved.push(j), loadFavorites: () => [], saveFavorites: noop, loadUiState: () => ({}), saveUiState: noop, nextSeq: () => 1 },
    renderer: {
      renderLine: noop, renderHistogram: noop, renderHorizontal: noop, setData: noop, setVisible: noop, remove: noop,
      getSeriesStyles: () => [{ name: 'MA', kind: 'line', color: '#2962ff', width: 1, style: 'solid', visible: true }],
      applySeriesStyle: (id, name, patch) => { styleCalls.push([id, name, patch]); return true; },
    },
    document: null, mode: 'b', datasetRef: 'jp225_m1', timeframe: '1D',
  });
  const inst = await ctrl.applyIndicator('moving_averages', 'default');
  const currentParams = { ma_type: 'ema', length: 9 };
  const before = computeCalls.length;
  await ctrl._applyDialogResult(inst, currentParams, { ...currentParams }, inst.variant, { styles: { MA: { color: '#ff0000' } } });
  assert.equal(computeCalls.length, before, 'recompute（/compute）は発火しない');
  assert.deepEqual(styleCalls.at(-1), [inst.instanceId, 'MA', { color: '#ff0000' }], 'applySeriesStyle 直適用');
  const last = saved.at(-1);
  const arr = Array.isArray(last) ? last : [];
  assert.deepEqual(arr.find((i) => i.instanceId === inst.instanceId)?.styles, { MA: { color: '#ff0000' } }, 'persist される');
});

test('ISSUE-110 🟡-2 _applyDialogResult: params 変更ありは従来どおり recompute 経路', async () => {
  const noop = () => {};
  const computeCalls = [];
  const ctrl = new IndicatorController({
    catalog: { listIndicators: () => [], get },
    compute: { compute: async (req) => { computeCalls.push(req); return { ok: true, generation: req.generation ?? 0, series: [] }; } },
    persistence: { loadApplied: () => [], saveApplied: noop, loadFavorites: () => [], saveFavorites: noop, loadUiState: () => ({}), saveUiState: noop, nextSeq: () => 1 },
    renderer: { renderLine: noop, renderHistogram: noop, renderHorizontal: noop, setData: noop, setVisible: noop, remove: noop, getSeriesStyles: () => [], applySeriesStyle: () => true },
    document: null, mode: 'b', datasetRef: 'jp225_m1', timeframe: '1D',
  });
  const inst = await ctrl.applyIndicator('moving_averages', 'default');
  const before = computeCalls.length;
  await ctrl._applyDialogResult(inst, { length: 9 }, { length: 20 }, inst.variant, { styles: { MA: { color: '#ff0000' } } });
  assert.equal(computeCalls.length, before + 1, 'params 変更は recompute する');
});

test('ISSUE-110 🔴-1 _applyStoredStyles: 実系列に無い stale キーを剪定し適用も persist もしない', async () => {
  const noop = () => {};
  const styleCalls = [];
  const ctrl = new IndicatorController({
    catalog: { listIndicators: () => [], get },
    compute: { compute: async (req) => ({ ok: true, generation: req.generation ?? 0, series: [] }) },
    persistence: { loadApplied: () => [], saveApplied: noop, loadFavorites: () => [], saveFavorites: noop, loadUiState: () => ({}), saveUiState: noop, nextSeq: () => 1 },
    renderer: {
      renderLine: noop, renderHistogram: noop, renderHorizontal: noop, setData: noop, setVisible: noop, remove: noop,
      // 現在の実系列は btlm_q5 のみ（q_high 変更で btlm_q95 が消えた想定）
      getSeriesStyles: () => [{ name: 'btlm_q5', kind: 'line', color: '#111111', width: 1, style: 'solid', visible: true }],
      applySeriesStyle: (id, name, patch) => { styleCalls.push([name, patch]); return true; },
    },
    document: null, mode: 'b', datasetRef: 'jp225_m1', timeframe: '1D',
  });
  const inst = await ctrl.applyIndicator('tgp_btlm', 'default');
  ctrl._state = setSeriesStyles(ctrl._state, inst.instanceId, {
    'btlm_q5': { color: '#ff0000' }, 'btlm_q95': { color: '#00ff00' },
  });
  ctrl._applyStoredStyles(inst.instanceId);
  assert.deepEqual(styleCalls, [['btlm_q5', { color: '#ff0000' }]], 'stale キー btlm_q95 は適用されない');
  const after = ctrl._state.applied.find((i) => i.instanceId === inst.instanceId);
  assert.deepEqual(after.styles, { 'btlm_q5': { color: '#ff0000' } }, 'state からも剪定される（永続蓄積の遮断）');
});
