// indicator_controller_tick_tails.test.js — ISSUE-250 Phase 1 のフロント結線（controller 側）。
//
// 設計入力: ライブは tick 適用ごとに /compute へ HTTP 往復して末尾点を取り直しており、
//   scheduler が in-flight 1 本へ coalesce するため「指標更新回数 == ローソク更新回数」が
//   構成上成立しなかった。サーバは /live_ticks の応答へ「各ティック時点の末尾値」を同梱する。
//   controller はその両端を担う:
//     - appliedComputeSpecs(): 何の末尾値が要るかをサーバへ申告する
//     - applyFormingTails(tails, barTime): 届いた末尾値を形成中バーの time へ**同期**で描く
//
// 構造: Arrange-Act-Assert（AAA）。DOM/ネット非依存（全注入・recording fake）。

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { IndicatorController } from '../js/adapter/front/indicator_controller.js';
import { get } from '../js/usecase/catalog.js';

function newController() {
  const noop = () => {};
  const calls = { updateSeriesTail: [] };
  const ctrl = new IndicatorController({
    catalog: { listIndicators: () => [], get },
    compute: { compute: async () => ({ ok: true, generation: 0, series: [] }) },
    persistence: {
      loadApplied: () => [], saveApplied: noop, loadFavorites: () => [], saveFavorites: noop,
      loadUiState: () => ({}), saveUiState: noop, nextSeq: () => 1,
    },
    renderer: { updateSeriesTail: (key, points) => calls.updateSeriesTail.push({ key, points }) },
    document: null,
    mode: 'b',
    datasetRef: 'jp225_tick',
    timeframe: '15m',
    recentBars: 1386,
  });
  return { ctrl, calls };
}

// applied 行（facade の AppliedInstance 相当・params は pairs 形式）を直接積む。
function addInstance(ctrl, { instanceId, indicatorId, params = [], drawn = true }) {
  ctrl._state = {
    ...ctrl._state,
    applied: [...ctrl._state.applied, { instanceId, indicatorId, variant: 'default', params, visible: true }],
  };
  if (drawn) {
    ctrl._meta.set(instanceId, { def: get(indicatorId) });
  }
}

// --------------------------------------------------------------------------- #
// appliedComputeSpecs: サーバへの申告
// --------------------------------------------------------------------------- #

test('appliedComputeSpecs declares drawn intrabar-registered instances with normalized params', () => {
  const { ctrl } = newController();
  addInstance(ctrl, { instanceId: 'profit_rsi#1', indicatorId: 'profit_rsi', params: [['length', 14]] });
  assert.deepEqual(ctrl.appliedComputeSpecs(), [{
    instanceId: 'profit_rsi#1',
    indicatorId: 'profit_rsi',
    variant: 'default',
    params: { length: 14 },       // pairs → object（/compute と同一の正規化）
  }]);
});

test('appliedComputeSpecs excludes instances that are not intrabar-registered', () => {
  const { ctrl } = newController();
  addInstance(ctrl, { instanceId: 'tgp_btlm#1', indicatorId: 'tgp_btlm' });   // 帯系＝足内で動かさない
  assert.deepEqual(ctrl.appliedComputeSpecs(), []);
});

test('appliedComputeSpecs excludes instances that are not drawn yet (no _meta)', () => {
  const { ctrl } = newController();
  addInstance(ctrl, { instanceId: 'profit_rsi#1', indicatorId: 'profit_rsi', drawn: false });
  assert.deepEqual(ctrl.appliedComputeSpecs(), []);
});

// ISSUE-274: 計算.時間足 override も申告する。サーバが計算足ごとに窓と形成中バーの畳み方を
//   分けるため、上位足指標にも「その tick 時点の上位足の値」が返る（旧: 除外していた）。
//   申告には params.timeframe をそのまま載せる（サーバのグループ分けの唯一の材料）。
test('appliedComputeSpecs declares instances with a per-indicator timeframe override', () => {
  const { ctrl } = newController();
  addInstance(ctrl, { instanceId: 'profit_rsi#1', indicatorId: 'profit_rsi', params: [['timeframe', '1h']] });
  const specs = ctrl.appliedComputeSpecs();
  assert.equal(specs.length, 1);
  assert.equal(specs[0].instanceId, 'profit_rsi#1');
  assert.equal(specs[0].params.timeframe, '1h');
  // 'chart'（＝チャート足に従う）も従来どおり申告する。
  ctrl._state = { ...ctrl._state, applied: [] };
  ctrl._meta.clear();
  addInstance(ctrl, { instanceId: 'profit_rsi#2', indicatorId: 'profit_rsi', params: [['timeframe', 'chart']] });
  assert.equal(ctrl.appliedComputeSpecs().length, 1);
});

// 窓長は /compute と同一（窓が違えば値も違う）。
test('computeLimit exposes the same display window that /compute uses', () => {
  const { ctrl } = newController();
  assert.equal(ctrl.computeLimit(), 1386);
});

// --------------------------------------------------------------------------- #
// applyFormingTails: 末尾値の同期描画
// --------------------------------------------------------------------------- #

test('applyFormingTails writes one tail point per series at the forming bar time', () => {
  const { ctrl, calls } = newController();
  addInstance(ctrl, { instanceId: 'profit_rsi#1', indicatorId: 'profit_rsi' });
  ctrl.applyFormingTails({ 'profit_rsi#1': { RSI: 58.25, 'RSI 平均': 55.0 } }, 1_784_300_400);
  assert.deepEqual(calls.updateSeriesTail, [
    { key: 'profit_rsi#1::RSI', points: [{ time: 1_784_300_400, value: 58.25 }] },
    { key: 'profit_rsi#1::RSI 平均', points: [{ time: 1_784_300_400, value: 55.0 }] },
  ]);
});

test('applyFormingTails ignores instances that are no longer drawn (removed mid-flight)', () => {
  const { ctrl, calls } = newController();
  ctrl.applyFormingTails({ 'gone#9': { RSI: 1 } }, 100);
  assert.deepEqual(calls.updateSeriesTail, []);
});

test('applyFormingTails skips non-finite values (leaves the previous point in place)', () => {
  const { ctrl, calls } = newController();
  addInstance(ctrl, { instanceId: 'profit_rsi#1', indicatorId: 'profit_rsi' });
  ctrl.applyFormingTails({ 'profit_rsi#1': { RSI: null, 'RSI 平均': 55.0 } }, 100);
  assert.deepEqual(calls.updateSeriesTail, [
    { key: 'profit_rsi#1::RSI 平均', points: [{ time: 100, value: 55.0 }] },
  ]);
});

test('applyFormingTails is a no-op without tails or a usable bar time', () => {
  const { ctrl, calls } = newController();
  addInstance(ctrl, { instanceId: 'profit_rsi#1', indicatorId: 'profit_rsi' });
  ctrl.applyFormingTails(null, 100);
  ctrl.applyFormingTails({ 'profit_rsi#1': { RSI: 1 } }, undefined);
  assert.deepEqual(calls.updateSeriesTail, []);
});

// 同期であること（await を挟まない＝呼び出し元 LiveTickPlayer の updateLastCandle と同一ブロック）。
test('applyFormingTails completes synchronously (no await in the tick path)', () => {
  const { ctrl, calls } = newController();
  addInstance(ctrl, { instanceId: 'profit_rsi#1', indicatorId: 'profit_rsi' });
  const result = ctrl.applyFormingTails({ 'profit_rsi#1': { RSI: 1 } }, 100);
  assert.equal(result, undefined, 'Promise を返さない');
  assert.equal(calls.updateSeriesTail.length, 1, '戻った時点で描画済み');
});
