// indicator_controller.js — Latest（末尾K）増分計算の mode 伝播・差分反映の検証。
//
// 設計入力（Latest フレームワーク Stage A）:
//   - recomputeAllApplied({mode}) / recomputeInstance(.., {mode}) に mode を伝播する。
//   - mode==='latest' のとき gateway へ mode を渡し、返った末尾K点を
//     renderer.updateSeriesTail へ渡す（remove+_draw の全描画はしない）。
//   - mode==='full'（既定）は従来どおり remove+_draw（全 setData/再生成）。
//   - horizontal_line 系は latest でも従来の全差替（updateSeriesTail 対象外）。
//
// 構造: Arrange-Act-Assert（AAA）。DOM/ネット非依存（全注入・recording fake）。

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { IndicatorController } from '../js/adapter/front/indicator_controller.js';
import { get } from '../js/usecase/catalog.js';

// renderer の呼び出しを記録する recording fake。
function recordingRenderer() {
  const calls = { removes: [], renderLine: [], updateSeriesTail: [], renderHorizontal: [] };
  return {
    calls,
    renderLine: (id, payloads, opts) => calls.renderLine.push({ id, payloads, opts }),
    renderHistogram: () => {},
    renderHorizontal: (id, lines) => calls.renderHorizontal.push({ id, lines }),
    updateSeriesTail: (key, points) => calls.updateSeriesTail.push({ key, points }),
    setData: () => {},
    setVisible: () => {},
    remove: (id) => calls.removes.push(id),
    setCandles: () => {},
  };
}

// moving_averages（MA 系列）を返す compute fake。mode をエコー記録する。
function controllerWith(renderer, seriesFor) {
  const noop = () => {};
  const computeCalls = [];
  const ctrl = new IndicatorController({
    catalog: { listIndicators: () => [], get },
    compute: {
      compute: async (req) => {
        computeCalls.push(req);
        return { ok: true, generation: req.generation ?? 0, series: seriesFor(req) };
      },
    },
    persistence: {
      loadApplied: () => [], saveApplied: noop, loadFavorites: () => [], saveFavorites: noop,
      loadUiState: () => ({}), saveUiState: noop, nextSeq: () => 1,
    },
    renderer,
    document: null,
    mode: 'b',
    datasetRef: 'jp225_m1',
    timeframe: '1D',
    recentBars: 1500,
    loadCandles: async () => [{ time: 1, open: 1, high: 1, low: 1, close: 1 }],
  });
  return { ctrl, computeCalls };
}

// moving_averages の MA line 系列（末尾K点想定）を返す。
function maSeries() {
  return [{ name: 'MA', kind: 'line', data: [{ time: 100, value: 42 }] }];
}

test('recomputeAllApplied({mode:"latest"}) forwards mode to the gateway compute', async () => {
  // Arrange
  const renderer = recordingRenderer();
  const { ctrl, computeCalls } = controllerWith(renderer, maSeries);
  await ctrl.applyIndicator('moving_averages', 'default');
  const before = computeCalls.length;
  // Act
  await ctrl.recomputeAllApplied({ mode: 'latest' });
  // Assert: 再計算 compute が mode='latest' を伴う。
  const after = computeCalls.slice(before);
  assert.ok(after.length >= 1);
  assert.equal(after.at(-1).mode, 'latest');
});

test('latest recompute routes tail points to updateSeriesTail and does NOT remove+redraw', async () => {
  // Arrange
  const renderer = recordingRenderer();
  const { ctrl } = controllerWith(renderer, maSeries);
  const inst = await ctrl.applyIndicator('moving_averages', 'default');
  // apply（mode 既定 full）で renderLine が呼ばれているので基準を取る。
  const removesBefore = renderer.calls.removes.length;
  const tailBefore = renderer.calls.updateSeriesTail.length;
  // Act: latest 再計算。
  await ctrl.recomputeInstance(inst.instanceId, null, ctrl._defaultParams(get('moving_averages')), { mode: 'latest' });
  // Assert: 末尾K点が updateSeriesTail({instanceId}::{name}, points) へ。remove は増えない。
  assert.equal(renderer.calls.removes.length, removesBefore, 'latest は remove を呼ばない');
  assert.ok(renderer.calls.updateSeriesTail.length > tailBefore, 'latest は updateSeriesTail を呼ぶ');
  const last = renderer.calls.updateSeriesTail.at(-1);
  assert.equal(last.key, `${inst.instanceId}::MA`);
  assert.deepEqual(last.points, [{ time: 100, value: 42 }]);
});

test('full recompute (default) still removes+redraws (backward compatible)', async () => {
  // Arrange
  const renderer = recordingRenderer();
  const { ctrl } = controllerWith(renderer, maSeries);
  const inst = await ctrl.applyIndicator('moving_averages', 'default');
  const removesBefore = renderer.calls.removes.length;
  const tailBefore = renderer.calls.updateSeriesTail.length;
  // Act: mode 省略（既定 full）。
  await ctrl.recomputeInstance(inst.instanceId, null, ctrl._defaultParams(get('moving_averages')));
  // Assert: 従来どおり remove+renderLine（全描画）。updateSeriesTail は呼ばれない。
  assert.ok(renderer.calls.removes.length > removesBefore, 'full は remove+redraw する');
  assert.equal(renderer.calls.updateSeriesTail.length, tailBefore, 'full は updateSeriesTail を呼ばない');
});

test('latest recompute with horizontal_line series falls back to full redraw (remove+_draw)', async () => {
  // Arrange: horizontal_line のみを返す指標（price_range_power）。
  const renderer = recordingRenderer();
  const hl = () => [{ name: 'price_range_power', kind: 'horizontal_line', lines: [{ price: 10 }] }];
  const { ctrl } = controllerWith(renderer, hl);
  const inst = await ctrl.applyIndicator('price_range_power', 'default');
  const removesBefore = renderer.calls.removes.length;
  // Act: latest 再計算（horizontal_line は末尾K切り対象外＝従来の全差替）。
  await ctrl.recomputeInstance(inst.instanceId, null, ctrl._defaultParams(get('price_range_power')), { mode: 'latest' });
  // Assert: horizontal_line は updateSeriesTail を使わず remove+redraw する。
  assert.ok(renderer.calls.removes.length > removesBefore, 'horizontal_line は latest でも全差替');
  assert.equal(renderer.calls.updateSeriesTail.length, 0, 'horizontal_line には updateSeriesTail を使わない');
});

// 🔴 回帰: 混在 kind（line + horizontal_line）指標の latest 経路。
//   backend は latest で line/histogram を末尾K点へ trim する一方、混在指標はフロントで全差替に
//   落ちるため、trim 済み 1 点で renderLine され履歴が潰れていた。修正後は def に horizontal_line を
//   含む指標は「要求前」に full へ倒し、full データで全描画する（潰れない）。
test('latest recompute on a MIXED-kind indicator (line+horizontal_line) requests full and redraws with FULL data — 🔴 regression', async () => {
  // Arrange: backend を模した seriesFor。latest 要求時のみ line を末尾1点へ trim する（バグ誘発条件）。
  const renderer = recordingRenderer();
  const fullLine = (name) => ({ name, kind: 'line', data: [{ time: 1, value: 1 }, { time: 2, value: 2 }, { time: 3, value: 3 }] });
  const trimLine = (name) => ({ name, kind: 'line', data: [{ time: 3, value: 3 }] });
  const rsiSeriesFor = (req) => {
    const lines = req.mode === 'latest'
      ? [trimLine('rsi'), trimLine('rsi_ma')]   // backend の末尾K=1 trim
      : [fullLine('rsi'), fullLine('rsi_ma')];
    return [...lines, { name: 'profit_rsi', kind: 'horizontal_line', lines: [{ price: 70 }] }];
  };
  const { ctrl, computeCalls } = controllerWith(renderer, rsiSeriesFor);
  const inst = await ctrl.applyIndicator('profit_rsi', 'default');
  // Act: live tick 相当の latest 再計算。
  await ctrl.recomputeInstance(inst.instanceId, null, ctrl._defaultParams(get('profit_rsi')), { mode: 'latest' });
  // Assert 1: 混在指標は latest を要求しない（full を要求し trim を回避）。
  assert.notEqual(computeCalls.at(-1).mode, 'latest', '混在指標は latest を要求しない（full へ倒す）');
  // Assert 2（核心）: renderLine に渡る line データが full 長（trim された 1 点に潰れない）。
  const lastLine = renderer.calls.renderLine.at(-1);
  assert.ok(lastLine, 'renderLine が呼ばれる（全差替）');
  for (const p of lastLine.payloads) {
    assert.equal(p.data.length, 3, 'line は full データ（1 点に潰れない）');
  }
  // Assert 3: updateSeriesTail は使わない（全差替経路）。
  assert.equal(renderer.calls.updateSeriesTail.length, 0);
});

// 🔴 回帰: [PROTO 再生] 足内追従。混在 kind 指標（profit_rsi）を recomputeFormingLatest で更新すると、
//   forceTail で末尾差分（updateSeriesTail）経路へ倒れる＝全差替（renderLine setData）に落ちないため
//   line 履歴が潰れない。かつ gateway へ mode='latest' と forming を伝播する（backend が形成中バーを
//   差し込む）。これが崩れると profit_* 8 指標が足内で固定表示に戻る。
test('recomputeFormingLatest on a MIXED-kind indicator uses tail-update (no collapse) and forwards latest+forming — 🔴 regression', async () => {
  // Arrange: backend 模擬。latest 要求時は line を末尾1点へ trim（混在の全差替なら潰れる条件）。
  const renderer = recordingRenderer();
  const trimLine = (name) => ({ name, kind: 'line', data: [{ time: 3, value: 3 }] });
  const fullLine = (name) => ({ name, kind: 'line', data: [{ time: 1, value: 1 }, { time: 2, value: 2 }, { time: 3, value: 3 }] });
  const rsiSeriesFor = (req) => {
    const lines = req.mode === 'latest' ? [trimLine('rsi'), trimLine('rsi_ma')] : [fullLine('rsi'), fullLine('rsi_ma')];
    return [...lines, { name: 'profit_rsi', kind: 'horizontal_line', lines: [{ price: 70 }] }];
  };
  const { ctrl, computeCalls } = controllerWith(renderer, rsiSeriesFor);
  await ctrl.applyIndicator('profit_rsi', 'default');   // 先に full 描画（末尾差分の前提＝既存系列）
  const removesBefore = renderer.calls.removes.length;
  const forming = { time: 3, open: 1, high: 9, low: 0.5, close: 2.5 };
  // Act: 足内追従入口。
  await ctrl.recomputeFormingLatest(forming);
  // Assert 1: 末尾差分経路（updateSeriesTail）を使い、remove+全差替には落ちない（履歴潰れ回避）。
  assert.equal(renderer.calls.removes.length, removesBefore, 'forceTail は remove（全差替）を呼ばない');
  assert.ok(renderer.calls.updateSeriesTail.length >= 2, 'line 系列の最終点を updateSeriesTail で更新');
  // Assert 2: gateway へ mode='latest' と forming を伝播（backend が形成中バーを差し込む）。
  const last = computeCalls.at(-1);
  assert.equal(last.mode, 'latest', '混在でも forceTail で latest を要求');
  assert.deepEqual(last.forming, forming, '形成中バーを backend へ伝播');
});

// [PROTO 再生] 対象外指標（tgp_btlm 帯系）は recomputeFormingLatest で触らない（頻度分離 帯=足）。
test('recomputeFormingLatest leaves non-target indicators (e.g. tgp_btlm band) untouched', async () => {
  // Arrange
  const renderer = recordingRenderer();
  const tgpSeries = () => [{ name: 'btlm_mean', kind: 'line', data: [{ time: 1, value: 1 }] }];
  const { ctrl, computeCalls } = controllerWith(renderer, tgpSeries);
  await ctrl.applyIndicator('tgp_btlm', 'default');
  const before = computeCalls.length;
  // Act
  await ctrl.recomputeFormingLatest({ time: 3, open: 1, high: 9, low: 0.5, close: 2.5 });
  // Assert: 対象集合外なので再計算 compute が増えない（足確定値のまま）。
  assert.equal(computeCalls.length, before, '対象外指標は足内再計算しない');
});
