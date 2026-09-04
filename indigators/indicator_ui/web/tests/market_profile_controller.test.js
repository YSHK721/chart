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

// ---- OCP-5 S1: アクターの受け取り口（ISSUE-479 Wave2 J-1）--------------------
//
// なぜ要るか: 本協働子は MP アクターを host のフィールド名（`_marketProfile`）で引いており、
//   「誰がアクターを持っているか」を協働子が知っている状態だった。合成根が注入できる口を
//   **加法で**開けば、host のフィールド名に依存しない登録（registerActorController）へ移せる。
//   既定（opts 省略）は従来どおり host 読み＝挙動 byte 不変。

test('ctor opts.actor: 注入したアクターを host のフィールドより優先して使う', () => {
  // Arrange: host には別のアクターが居るが、注入した方が使われること。
  const injectedCalls = [];
  const injected = {
    setParams: (p) => injectedCalls.push(['setParams', p]),
    applyGrowthState: () => {},
  };
  const { host, calls } = makeHost();
  // Act
  const mpc = new MarketProfileController(host, { actor: injected });
  mpc.applyMpParams({ va: 0.7 });
  // Assert
  assert.equal(injectedCalls.some((c) => c[0] === 'setParams'), true, '注入したアクターへ渡していない');
  assert.equal(calls.some((c) => c[0] === 'setParams'), false, 'host のアクターへ渡してしまっている');
});

test('ctor opts 省略: 従来どおり host のアクターを読む（既定は byte 不変）', () => {
  // Arrange
  const { host, calls } = makeHost();
  // Act
  const mpc = new MarketProfileController(host);
  mpc.applyMpParams({ va: 0.7 });
  // Assert
  assert.equal(calls.some((c) => c[0] === 'setParams'), true);
});

test('ctor opts.actor: 注入後も host 側の後付け差し替えに引きずられない', () => {
  // Arrange: 合成根が後から host._marketProfile を差し替えても、注入した口が優先される
  //   （replay 合成根は構築後に controller._marketProfile へ代入する経路を持つ）。
  const injectedCalls = [];
  const injected = { setParams: () => injectedCalls.push('injected'), applyGrowthState: () => {} };
  const { host } = makeHost();
  const mpc = new MarketProfileController(host, { actor: injected });
  // Act
  host._marketProfile = { setParams: () => injectedCalls.push('host'), applyGrowthState: () => {} };
  mpc.applyMpParams({ va: 0.7 });
  // Assert
  assert.deepEqual(injectedCalls, ['injected']);
});
