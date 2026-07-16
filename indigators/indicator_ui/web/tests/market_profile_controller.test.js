// market_profile_controller.test.js — MP 委譲コントローラ（A7 オーケストレーション）の単体テスト。
//
// 対象: js/adapter/front/market_profile_controller.js（ISSUE-094 🔴-4 抽出）。
//   indicator_controller.js（A6）へ混在していた MP アクター駆動の一式（apply/enable/toggle/remove/
//   gear/reapply/restore/live-recompute）を host 参照で操作する協働オブジェクトへ外出しした対象。
//   host（IndicatorController）フィールド/メソッドを最小限モックし、抽出後の委譲挙動を固定する。

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { MarketProfileController } from '../js/adapter/front/market_profile_controller.js';

// host（IndicatorController）の最小モック。MP コントローラが参照するフィールド/メソッドだけを備える。
function makeHost(overrides = {}) {
  const calls = [];
  const actor = {
    setParams: (p) => calls.push(['setParams', p]),
    applyGrowthState: (s) => calls.push(['applyGrowthState', s]),
    setEnabled: async (v) => calls.push(['setEnabled', v]),
    onLiveTick: async () => calls.push(['onLiveTick']),
    refresh: async () => calls.push(['refresh']),
    isEnabled: () => true,
    ...(overrides.actor || {}),
  };
  const host = {
    _marketProfile: 'actor' in overrides ? overrides.actor : actor,
    _mpModeResolver: overrides._mpModeResolver ?? null,
    _mpGrowthResolver: overrides._mpGrowthResolver ?? null,
    _state: overrides._state ?? { applied: [] },
    _catalog: overrides._catalog ?? { get: () => null },
    _untilTime: overrides._untilTime,
    _persistAll: () => calls.push(['persist']),
    _renderLegend: () => calls.push(['legend']),
    _mpParams: (p) => ({ ...p }),
    _isMarketProfile: (def) => def?.compute?.computeId === 'market_profile',
    _paramsObject: (p) => (Array.isArray(p) ? Object.fromEntries(p) : (p ?? {})),
    _defaultParams: () => ({}),
  };
  return { host, actor, calls };
}

test('applyMpParams: mpModeResolver 注入時は mode を解決して setParams へ渡す', () => {
  const { host, calls } = makeHost({ _mpModeResolver: () => 'ticklive' });
  const mpc = new MarketProfileController(host);
  mpc.applyMpParams({ mode: 'normal', va: 0.7 });
  const setParams = calls.find((c) => c[0] === 'setParams');
  assert.equal(setParams[1].mode, 'ticklive');
});

test('applyMpParams: marketProfile 未注入なら no-op（setParams を呼ばない）', () => {
  const { host, calls } = makeHost({ actor: null });
  const mpc = new MarketProfileController(host);
  mpc.applyMpParams({ va: 0.7 });
  assert.equal(calls.some((c) => c[0] === 'setParams'), false);
});

test('applyMpGrowth: growthResolver=true で applyGrowthState({growing:true}) を適用し true を返す', () => {
  const { host, calls } = makeHost({ _mpGrowthResolver: () => true });
  const mpc = new MarketProfileController(host);
  const growing = mpc.applyMpGrowth();
  assert.equal(growing, true);
  const g = calls.find((c) => c[0] === 'applyGrowthState');
  assert.deepEqual(g[1], { growing: true });
});

test('applyMpGrowth: growthResolver 未注入なら no-op で false を返す', () => {
  const { host, calls } = makeHost();
  const mpc = new MarketProfileController(host);
  assert.equal(mpc.applyMpGrowth(), false);
  assert.equal(calls.some((c) => c[0] === 'applyGrowthState'), false);
});

test('onLiveRecompute: 可視かつ onLiveTick 所持なら onLiveTick を呼ぶ', async () => {
  const { host, calls } = makeHost();
  const mpc = new MarketProfileController(host);
  await mpc.onLiveRecompute({ visible: true });
  assert.equal(calls.some((c) => c[0] === 'onLiveTick'), true);
});

test('onLiveRecompute: 非表示なら onLiveTick を呼ばない', async () => {
  const { host, calls } = makeHost();
  const mpc = new MarketProfileController(host);
  await mpc.onLiveRecompute({ visible: false });
  assert.equal(calls.some((c) => c[0] === 'onLiveTick'), false);
});

test('restoreInstance: 可視インスタンスは applyMpParams 後に setEnabled(true) を await する', async () => {
  const { host, calls } = makeHost();
  const mpc = new MarketProfileController(host);
  await mpc.restoreInstance({ visible: true, params: { va: 0.7 } });
  assert.equal(calls.some((c) => c[0] === 'setParams'), true);
  const en = calls.find((c) => c[0] === 'setEnabled');
  assert.deepEqual(en, ['setEnabled', true]);
});

test('reapplyMode: mpModeResolver 未注入なら no-op', async () => {
  const { host, calls } = makeHost();
  const mpc = new MarketProfileController(host);
  await mpc.reapplyMode();
  assert.equal(calls.length, 0);
});
