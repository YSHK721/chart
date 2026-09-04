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
//
// ISSUE-479 Wave2b J-1 OCP-5 S3: アクター・mode 解決役・growth 解決役は **host の面ではない**。
//   協働子の依存として ctor の opts で受け取る（host から掘り出さない＝DIP）。本ヘルパは
//   `makeHost(...)` が返す `deps` をそのまま ctor へ渡す形に揃える（各テストで組み立てを複製しない）。
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
  const deps = {
    actor: 'actor' in overrides ? overrides.actor : actor,
    modeResolver: overrides._mpModeResolver ?? null,
    growthResolver: overrides._mpGrowthResolver ?? null,
  };
  return { host, actor, calls, deps };
}

test('applyMpParams: mode 解決役 注入時は mode を解決して setParams へ渡す', () => {
  const { host, calls, deps } = makeHost({ _mpModeResolver: () => 'ticklive' });
  const mpc = new MarketProfileController(host, deps);
  mpc.applyMpParams({ mode: 'normal', va: 0.7 });
  const setParams = calls.find((c) => c[0] === 'setParams');
  assert.equal(setParams[1].mode, 'ticklive');
});

test('applyMpParams: アクター未注入なら no-op（setParams を呼ばない）', () => {
  const { host, calls, deps } = makeHost({ actor: null });
  const mpc = new MarketProfileController(host, deps);
  mpc.applyMpParams({ va: 0.7 });
  assert.equal(calls.some((c) => c[0] === 'setParams'), false);
});

test('applyMpGrowth: growthResolver=true で applyGrowthState({growing:true}) を適用し true を返す', () => {
  const { host, calls, deps } = makeHost({ _mpGrowthResolver: () => true });
  const mpc = new MarketProfileController(host, deps);
  const growing = mpc.applyMpGrowth();
  assert.equal(growing, true);
  const g = calls.find((c) => c[0] === 'applyGrowthState');
  assert.deepEqual(g[1], { growing: true });
});

test('applyMpGrowth: growthResolver 未注入なら no-op で false を返す', () => {
  const { host, calls, deps } = makeHost();
  const mpc = new MarketProfileController(host, deps);
  assert.equal(mpc.applyMpGrowth(), false);
  assert.equal(calls.some((c) => c[0] === 'applyGrowthState'), false);
});

test('onLiveRecompute: 可視かつ onLiveTick 所持なら onLiveTick を呼ぶ', async () => {
  const { host, calls, deps } = makeHost();
  const mpc = new MarketProfileController(host, deps);
  await mpc.onLiveRecompute({ visible: true });
  assert.equal(calls.some((c) => c[0] === 'onLiveTick'), true);
});

test('onLiveRecompute: 非表示なら onLiveTick を呼ばない', async () => {
  const { host, calls, deps } = makeHost();
  const mpc = new MarketProfileController(host, deps);
  await mpc.onLiveRecompute({ visible: false });
  assert.equal(calls.some((c) => c[0] === 'onLiveTick'), false);
});

test('restoreInstance: 可視インスタンスは applyMpParams 後に setEnabled(true) を await する', async () => {
  const { host, calls, deps } = makeHost();
  const mpc = new MarketProfileController(host, deps);
  await mpc.restoreInstance({ visible: true, params: { va: 0.7 } });
  assert.equal(calls.some((c) => c[0] === 'setParams'), true);
  const en = calls.find((c) => c[0] === 'setEnabled');
  assert.deepEqual(en, ['setEnabled', true]);
});

test('reapplyMode: mode 解決役 未注入なら no-op', async () => {
  const { host, calls, deps } = makeHost();
  const mpc = new MarketProfileController(host, deps);
  await mpc.reapplyMode();
  assert.equal(calls.length, 0);
});

// ---- OCP-5 S3: 依存は注入で受ける（host から掘り出さない）・ISSUE-479 Wave2b ----------
//
// 旧 S1 の 3 検定（「注入は host のフィールドより優先」「opts 省略なら host を読む」
//   「注入後も host の後付け差し替えに引きずられない」）が固定していた性質は、S3 で
//   **より強い 1 つの性質**へ畳まれた: 本協働子は host のフィールド名を一切見ない。
//   優先順位も後付け差し替えの影響も、読む口が 1 つしか無ければ問題として存在しない。

test('S3: アクターは opts でのみ供給される（host のフィールド名を読まない）', () => {
  // Arrange: host に MP アクターらしき面を置いても使われないこと。
  //   （旧「注入が host より優先」「後付け差し替えに引きずられない」の引き継ぎ先）
  const injectedCalls = [];
  const injected = {
    setParams: (p) => injectedCalls.push(['setParams', p]),
    applyGrowthState: () => {},
  };
  const { host, calls } = makeHost();
  host._marketProfile = { setParams: () => calls.push(['setParams', 'host']), applyGrowthState: () => {} };
  // Act
  const mpc = new MarketProfileController(host, { actor: injected });
  mpc.applyMpParams({ va: 0.7 });
  // Assert
  assert.equal(injectedCalls.some((c) => c[0] === 'setParams'), true, '注入したアクターへ渡していない');
  assert.equal(calls.some((c) => c[0] === 'setParams'), false,
    'host のフィールドを読んでいる（S3 で断ち切ったはずの経路）');
});

test('S3: 解決役も opts でのみ供給される（host の _mpModeResolver / _mpGrowthResolver を読まない）', () => {
  // Arrange
  const { host, calls, deps } = makeHost();
  host._mpModeResolver = () => 'ticklive';
  host._mpGrowthResolver = () => true;
  // Act: opts では解決役を渡さない。
  const mpc = new MarketProfileController(host, { actor: deps.actor });
  mpc.applyMpParams({ mode: 'normal', va: 0.7 });
  // Assert: mode は解決されず、growing 信号も適用されない。
  const setParams = calls.find((c) => c[0] === 'setParams');
  assert.equal(setParams[1].mode, 'normal', 'host の解決役を読んでいる');
  assert.equal(calls.some((c) => c[0] === 'applyGrowthState'), false, 'host の成長解決役を読んでいる');
});

test('S3: opts 省略は全経路 no-op（アクター不在＝何もしない）', () => {
  // 旧「opts 省略なら host を読む」の引き継ぎ先。供給経路が 1 本になった以上、
  //   省略は「別の口から取る」ではなく「依存が無い」を意味する。
  const { host, calls } = makeHost();
  host._marketProfile = { setParams: () => calls.push(['setParams', 'host']), applyGrowthState: () => {} };
  const mpc = new MarketProfileController(host);
  mpc.applyMpParams({ va: 0.7 });
  assert.equal(mpc.applyMpGrowth(), false);
  assert.deepEqual(calls, [], 'アクター未注入なのに副作用が出ている');
});
