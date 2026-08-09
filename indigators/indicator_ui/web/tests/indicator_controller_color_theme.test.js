// indicator_controller_color_theme.test.js — 色の決定を resolver 経由へ差し替える注入点（A-6）
//   （基本設計_指標カラーテーマ.md §7.2 S2(a)・§5.2 UC-C02 手順 3・§7.4 段階 2 通過条件 1・2・8）。
//
// 適用点を新設せず、描画完了の後段に既に集約済みの _applyStoredStyles（E-9・
//   series_render_router.js:103）へ相乗りする。ここが色の**唯一の書き手**（R-1）。
//
// 眼目は「テーマ未設定なら段階 1 と 1 色も変わらないこと」。テーマ機能は加法であり、既定状態の
//   見た目を 1 ピクセルも動かさない（D-11 恒等テーマ）。

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { IndicatorController } from '../js/adapter/front/indicator_controller.js';
import { get } from '../js/usecase/catalog.js';
import { setSeriesStyles } from '../js/usecase/facade.js';
import { ColorRole } from '../js/domain/color_roles.js';

// moving_averages の実描画系列（backend 既定色は payload 由来＝baseColor）。
const MA_METAS = [
  { name: 'MA', kind: 'line', color: '#1e88e5', baseColor: '#1e88e5', width: 1, style: 'solid', visible: true, heat: false },
  { name: 'Smoothing', kind: 'line', color: '#fb8c00', baseColor: '#fb8c00', width: 1, style: 'solid', visible: true, heat: false },
  { name: 'Upper', kind: 'line', color: '#26c6da', baseColor: '#26c6da', width: 1, style: 'solid', visible: true, heat: false },
  { name: 'Lower', kind: 'line', color: '#26c6da', baseColor: '#26c6da', width: 1, style: 'solid', visible: true, heat: false },
];

function makeController({ metas = MA_METAS, colorThemeProvider } = {}) {
  const noop = () => {};
  const styleCalls = [];
  const levelCalls = [];
  const ctrl = new IndicatorController({
    catalog: { listIndicators: () => [], get },
    compute: { compute: async (req) => ({ ok: true, generation: req.generation ?? 0, series: [] }) },
    persistence: {
      loadApplied: () => [], saveApplied: noop, loadFavorites: () => [], saveFavorites: noop,
      loadUiState: () => ({}), saveUiState: noop, nextSeq: () => 1,
    },
    renderer: {
      renderLine: noop, renderHistogram: noop, renderHorizontal: noop,
      setData: noop, setVisible: noop, remove: noop,
      getSeriesStyles: () => metas.map((m) => ({ ...m })),
      applySeriesStyle: (instanceId, name, patch) => { styleCalls.push([instanceId, name, { ...patch }]); return true; },
      applyLevelLineColor: (instanceId, color) => { levelCalls.push([instanceId, color]); return true; },
    },
    document: null,
    mode: 'b',
    datasetRef: 'jp225_m1',
    timeframe: '1D',
    colorThemeProvider,
  });
  return { ctrl, styleCalls, levelCalls };
}

const colorsByName = (calls) => Object.fromEntries(calls.map(([, name, patch]) => [name, patch.color]));

const theme = (roleColors, tfModifier = null) => ({ themeId: 'thm#1', name: 't', roleColors, tfModifier });

// =========================================================================
// 通過条件 1: テーマ未設定時の描画色が段階 1 と完全一致
// =========================================================================

test('通過条件 1: テーマ未設定・個別上書き無しなら全系列が backend 既定色（baseColor）で解決される', async () => {
  const { ctrl, styleCalls } = makeController();
  const inst = await ctrl.applyIndicator('moving_averages', 'default');
  styleCalls.length = 0;
  ctrl._applyStoredStyles(inst.instanceId);
  assert.deepEqual(colorsByName(styleCalls), {
    MA: '#1e88e5', Smoothing: '#fb8c00', Upper: '#26c6da', Lower: '#26c6da',
  });
});

test('通過条件 1: 解決色は色のみを書き、他フィールドを勝手に足さない（R-1）', async () => {
  const { ctrl, styleCalls } = makeController();
  const inst = await ctrl.applyIndicator('moving_averages', 'default');
  styleCalls.length = 0;
  ctrl._applyStoredStyles(inst.instanceId);
  for (const [, , patch] of styleCalls) {
    assert.deepEqual(Object.keys(patch), ['color'], JSON.stringify(patch));
  }
});

// =========================================================================
// 通過条件 2: 既存の styles[名].color を持つ状態でも色が変わらない（ステップ 3 の回帰）
// =========================================================================

test('通過条件 2: ロックなし個別上書きはテーマ未設定時に生き残る（U-5）', async () => {
  const { ctrl, styleCalls } = makeController();
  const inst = await ctrl.applyIndicator('moving_averages', 'default');
  ctrl._state = setSeriesStyles(ctrl._state, inst.instanceId, { MA: { color: '#ff0000', width: 4 } });
  styleCalls.length = 0;
  ctrl._applyStoredStyles(inst.instanceId);
  const byName = Object.fromEntries(styleCalls.map(([, n, p]) => [n, p]));
  assert.equal(byName.MA.color, '#ff0000', '個別色が payload 色へ戻ってはならない');
  assert.equal(byName.MA.width, 4, '色以外の上書きは従来どおり適用される');
  assert.equal(byName.Smoothing.color, '#fb8c00');
});

test('R-1: 色以外（width/style/visible/display）は styles から従来どおり流れる', async () => {
  const { ctrl, styleCalls } = makeController();
  const inst = await ctrl.applyIndicator('moving_averages', 'default');
  ctrl._state = setSeriesStyles(ctrl._state, inst.instanceId, {
    Upper: { width: 3, style: 'dashed', visible: false, display: 'dots' },
  });
  styleCalls.length = 0;
  ctrl._applyStoredStyles(inst.instanceId);
  const patch = styleCalls.find(([, n]) => n === 'Upper')[2];
  assert.equal(patch.width, 3);
  assert.equal(patch.style, 'dashed');
  assert.equal(patch.visible, false);
  assert.equal(patch.display, 'dots');
  assert.equal(patch.color, '#26c6da', '色は resolver が決める');
});

// =========================================================================
// テーマ適用（§5.2 UC-C02 手順 3）
// =========================================================================

test('テーマが宣言したトークンの系列だけが意味色になる（他は不変）', async () => {
  let active = null;
  const { ctrl, styleCalls } = makeController({ colorThemeProvider: () => active });
  const inst = await ctrl.applyIndicator('moving_averages', 'default');
  active = theme({ [ColorRole.RANGE]: '#ffffff' });
  styleCalls.length = 0;
  ctrl._applyStoredStyles(inst.instanceId);
  assert.deepEqual(colorsByName(styleCalls), {
    MA: '#1e88e5', Smoothing: '#fb8c00', Upper: '#ffffff', Lower: '#ffffff',
  });
});

test('FR-C11: 同一トークンの系列は指標を跨いでも同じ色になる', async () => {
  let active = theme({ [ColorRole.BULLISH]: '#00ff00' });
  const pbMetas = [
    { name: 'pOH 95%', kind: 'line', color: '#00897b', baseColor: '#00897b', visible: true, heat: false },
    { name: 'nOL 95%', kind: 'line', color: '#c62828', baseColor: '#c62828', visible: true, heat: false },
    { name: 'pOL 95%', kind: 'line', color: '#1565c0', baseColor: '#1565c0', visible: true, heat: false },
  ];
  const { ctrl, styleCalls } = makeController({ metas: pbMetas, colorThemeProvider: () => active });
  const inst = await ctrl.applyIndicator('profit_band', 'robust');
  styleCalls.length = 0;
  ctrl._applyStoredStyles(inst.instanceId);
  const byName = colorsByName(styleCalls);
  assert.equal(byName['pOH 95%'], '#00ff00', 'bullish が届く');
  assert.equal(byName['nOL 95%'], '#c62828', 'bearish 未宣言なら payload 色のまま');
  assert.equal(byName['pOL 95%'], '#1565c0', 'range 未宣言なら payload 色のまま');
});

test('FR-C05: colorLocked の系列はテーマ適用の対象外（ステップ 1）', async () => {
  let active = theme({ [ColorRole.RANGE]: '#ffffff' });
  const { ctrl, styleCalls } = makeController({ colorThemeProvider: () => active });
  const inst = await ctrl.applyIndicator('moving_averages', 'default');
  ctrl._state = setSeriesStyles(ctrl._state, inst.instanceId, { Upper: { color: '#abcdef', colorLocked: true } });
  styleCalls.length = 0;
  ctrl._applyStoredStyles(inst.instanceId);
  const byName = colorsByName(styleCalls);
  assert.equal(byName.Upper, '#abcdef', 'ロックした系列は不変');
  assert.equal(byName.Lower, '#ffffff', 'ロックしていない同トークンはテーマ色');
});

test('通過条件 8: テーマ A → B（B は当該トークン未宣言）で未適用時の色へ戻る', async () => {
  let active = null;
  const { ctrl, styleCalls } = makeController({ colorThemeProvider: () => active });
  const inst = await ctrl.applyIndicator('moving_averages', 'default');

  styleCalls.length = 0;
  ctrl._applyStoredStyles(inst.instanceId);
  const none = colorsByName(styleCalls);

  active = theme({ [ColorRole.RANGE]: '#ffffff' });
  styleCalls.length = 0;
  ctrl._applyStoredStyles(inst.instanceId);
  assert.equal(colorsByName(styleCalls).Upper, '#ffffff');

  active = theme({ [ColorRole.PRIMARY]: '#222222' });
  styleCalls.length = 0;
  ctrl._applyStoredStyles(inst.instanceId);
  const b = colorsByName(styleCalls);
  assert.equal(b.Upper, none.Upper, '適用履歴に依存している（R-6 違反）');
  assert.equal(b.Upper, '#26c6da');
});

test('§4.7: tfModifier は計算.時間足で解決される（chart 追従 / 固定足）', async () => {
  let active = theme({ [ColorRole.RANGE]: '#808080' }, { '1D': -0.5, '5m': 0.5 });
  const { ctrl, styleCalls } = makeController({ colorThemeProvider: () => active });
  const inst = await ctrl.applyIndicator('moving_averages', 'default');

  // 計算.時間足が 'chart'（既定）→ チャート足 1D の係数 -0.5。
  styleCalls.length = 0;
  ctrl._applyStoredStyles(inst.instanceId);
  assert.equal(colorsByName(styleCalls).Upper, '#404040');

  // 固定足 5m → チャート足に依らず 5m の係数 +0.5。
  ctrl._state = {
    ...ctrl._state,
    applied: ctrl._state.applied.map((i) => (i.instanceId === inst.instanceId
      ? { ...i, instanceId: i.instanceId, params: { ...i.params, timeframe: '5m' }, styles: i.styles }
      : i)),
  };
  styleCalls.length = 0;
  ctrl._applyStoredStyles(inst.instanceId);
  assert.equal(colorsByName(styleCalls).Upper, '#c0c0c0');
});

// =========================================================================
// 水準線ポート（R-3・§7.2 S2(b)）
// =========================================================================

test('R-3: テーマが level を宣言していなければ水準線ポートへ null を渡す（現行経路）', async () => {
  const { ctrl, levelCalls } = makeController({ colorThemeProvider: () => null });
  const inst = await ctrl.applyIndicator('moving_averages', 'default');
  levelCalls.length = 0;
  ctrl._applyStoredStyles(inst.instanceId);
  assert.deepEqual(levelCalls, [[inst.instanceId, null]]);
});

test('R-3: テーマが level を宣言したら解決色を水準線ポートへ渡す', async () => {
  const active = theme({ [ColorRole.LEVEL]: '#123456' });
  const { ctrl, levelCalls } = makeController({ colorThemeProvider: () => active });
  const inst = await ctrl.applyIndicator('moving_averages', 'default');
  levelCalls.length = 0;
  ctrl._applyStoredStyles(inst.instanceId);
  assert.deepEqual(levelCalls, [[inst.instanceId, '#123456']]);
});

test('§4.7: 水準線の色にも tfModifier が効く（指標系列の一部であるため）', async () => {
  const active = theme({ [ColorRole.LEVEL]: '#808080' }, { '1D': -0.5 });
  const { ctrl, levelCalls } = makeController({ colorThemeProvider: () => active });
  const inst = await ctrl.applyIndicator('moving_averages', 'default');
  levelCalls.length = 0;
  ctrl._applyStoredStyles(inst.instanceId);
  assert.deepEqual(levelCalls, [[inst.instanceId, '#404040']]);
});

// =========================================================================
// 防御（後方互換 Fake / SSR）
// =========================================================================

test('F-C10 相当: renderer が applyLevelLineColor を持たなくても例外にならない', async () => {
  const noop = () => {};
  const styleCalls = [];
  const ctrl = new IndicatorController({
    catalog: { listIndicators: () => [], get },
    compute: { compute: async (req) => ({ ok: true, generation: req.generation ?? 0, series: [] }) },
    persistence: {
      loadApplied: () => [], saveApplied: noop, loadFavorites: () => [], saveFavorites: noop,
      loadUiState: () => ({}), saveUiState: noop, nextSeq: () => 1,
    },
    renderer: {
      renderLine: noop, remove: noop, setData: noop, setVisible: noop,
      getSeriesStyles: () => MA_METAS.map((m) => ({ ...m })),
      applySeriesStyle: (i, n, p) => { styleCalls.push([i, n, p]); return true; },
    },
    document: null, mode: 'b', datasetRef: 'jp225_m1', timeframe: '1D',
    colorThemeProvider: () => theme({ [ColorRole.LEVEL]: '#123456' }),
  });
  const inst = await ctrl.applyIndicator('moving_averages', 'default');
  assert.doesNotThrow(() => ctrl._applyStoredStyles(inst.instanceId));
});

test('colorThemeProvider 未注入なら常にテーマなし（既定は恒等）', async () => {
  const { ctrl, styleCalls } = makeController();
  const inst = await ctrl.applyIndicator('moving_averages', 'default');
  styleCalls.length = 0;
  ctrl._applyStoredStyles(inst.instanceId);
  assert.deepEqual(colorsByName(styleCalls), {
    MA: '#1e88e5', Smoothing: '#fb8c00', Upper: '#26c6da', Lower: '#26c6da',
  });
});

// =========================================================================
// §3.4 ビュー自動介入の禁止（結線ガード）
// =========================================================================

test('§3.4: 色の適用は再計算もビュー操作も起こさない（/compute・setData・レンジ操作を呼ばない）', async () => {
  const noop = () => {};
  const forbidden = [];
  const trap = (name) => (...a) => { forbidden.push([name, a]); };
  let computeCalls = 0;
  const ctrl = new IndicatorController({
    catalog: { listIndicators: () => [], get },
    compute: { compute: async (req) => { computeCalls += 1; return { ok: true, generation: req.generation ?? 0, series: [] }; } },
    persistence: {
      loadApplied: () => [], saveApplied: noop, loadFavorites: () => [], saveFavorites: noop,
      loadUiState: () => ({}), saveUiState: noop, nextSeq: () => 1,
    },
    renderer: {
      renderLine: noop, renderHistogram: noop, renderHorizontal: noop, remove: noop, setVisible: noop,
      getSeriesStyles: () => MA_METAS.map((m) => ({ ...m })),
      applySeriesStyle: () => true,
      applyLevelLineColor: () => true,
      // 呼ばれてはならない面。
      setData: trap('setData'),
      setCandles: trap('setCandles'),
      fitContent: trap('fitContent'),
      setVisibleLogicalRange: trap('setVisibleLogicalRange'),
      scrollToPosition: trap('scrollToPosition'),
      setAutoScale: trap('setAutoScale'),
    },
    document: null, mode: 'b', datasetRef: 'jp225_m1', timeframe: '1D',
    colorThemeProvider: () => theme({ [ColorRole.RANGE]: '#ffffff', [ColorRole.LEVEL]: '#123456' }),
  });
  const inst = await ctrl.applyIndicator('moving_averages', 'default');
  const computeBefore = computeCalls;
  forbidden.length = 0;

  ctrl._applyStoredStyles(inst.instanceId);

  assert.equal(computeCalls, computeBefore, 'テーマ適用は /compute を呼ばない');
  assert.deepEqual(forbidden.map(([n]) => n), [], `ビュー操作を呼んでいる: ${forbidden.map(([n]) => n).join(', ')}`);
});

test('未描画の系列（renderer が知らない名前）に保存された patch は逐語で渡る', async () => {
  const { ctrl, styleCalls } = makeController();
  const inst = await ctrl.applyIndicator('moving_averages', 'default');
  // reconcile は実系列集合が空でないときだけ剪定するため、既知名 1 件を残して未知名を混ぜる。
  ctrl._state = setSeriesStyles(ctrl._state, inst.instanceId, { MA: { width: 2 } });
  styleCalls.length = 0;
  ctrl._applyStoredStyles(inst.instanceId);
  const patch = styleCalls.find(([, n]) => n === 'MA')[2];
  assert.equal(patch.width, 2);
  assert.equal(patch.color, '#1e88e5', '色を持たない patch にも解決色が載る');
});
