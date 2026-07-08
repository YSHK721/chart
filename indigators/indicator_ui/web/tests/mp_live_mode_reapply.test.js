// mp_live_mode_reapply.test.js — ライブ連動の「実効再適用」経路（IndicatorController.reapplyMarketProfileMode）を
//   実 GrowthCoordinator と結線した状態で検証する（E2E 相当の往復・見せかけ緑を避ける）。
//
// 確定仕様（Model A 直交化・回帰固定）:
//   - 表示モードは gear 選択を維持する（FOLLOW/ANALYSIS で置換しない・既定 normal）。resolve は 'ticklive' を返さない。
//   - FOLLOW 復帰 → growing=true を適用（applyGrowthState）し、成長エンジンを onLiveTick（→_enterTicklive で forming
//     取得）で起動する。refresh（/market_profile の base 累積）ではない（forming を潰さない）。
//   - ANALYSIS 復帰 → growing=false（static）を適用し、選択モードを refresh で反映する（onLiveTick しない）。
//   - MP 不在 / mpModeResolver 未注入（連動なし）は reapply が no-op（アクターへ触れない＝byte 不変）。
// 構造: Arrange-Act-Assert（AAA）。DOM/ネット非依存（全注入・recording fake・実 coordinator/controller）。

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { get } from '../js/usecase/catalog.js';
import { IndicatorController } from '../js/adapter/front/indicator_controller.js';
import { GrowthCoordinator } from '../js/adapter/front/mp_live_mode_coordinator.js';

const noop = () => {};
// reapply は onLiveStateChange から fire-and-forget（await されない）。ネストした await（reapply→onLiveTick）が
//   解決するまでマクロタスク境界で待つ。
const flush = () => new Promise((r) => setTimeout(r, 0));

// 状態を持つ recording fake actor。setParams で mode を保持、applyGrowthState で growing を保持する
//   （実 actor 契約: mode は維持し growing 信号で成長 ON/OFF）。呼び出し列を calls に記録。
function fakeMp() {
  return {
    calls: [],
    _enabled: false,
    _mode: null,
    _growing: false,
    isEnabled() { return this._enabled; },
    isTicklive() { return this._enabled && this._mode === 'ticklive'; },
    setParams(p) { this.calls.push(['setParams', p.mode]); if (p.mode != null) { this._mode = p.mode; } },
    applyGrowthState({ growing } = {}) { this._growing = !!growing; this.calls.push(['applyGrowthState', !!growing]); },
    async setEnabled(on) { this._enabled = !!on; this.calls.push(['setEnabled', !!on]); },
    async refresh() { this.calls.push(['refresh']); },
    async onLiveTick() { this.calls.push(['onLiveTick']); },
    detach() {},
  };
}

// 実 coordinator を resolver/growthResolver/reapply に結線した controller を組む（MP 適用済み・FOLLOW 初期）。
async function setupWired({ withMp = true, wireResolver = true } = {}) {
  const mp = withMp ? fakeMp() : null;
  let controller;
  const coord = new GrowthCoordinator({
    defaultMode: 'normal',
    reapply: () => controller.reapplyMarketProfileMode(),
  });
  controller = new IndicatorController({
    catalog: { listIndicators: () => [], get },
    compute: { compute: async (r) => ({ ok: true, generation: r.generation ?? 0, series: [] }) },
    persistence: {
      loadApplied: () => [], saveApplied: noop, loadFavorites: () => [], saveFavorites: noop,
      loadUiState: () => ({}), saveUiState: noop, nextSeq: () => 1,
    },
    renderer: { renderLine: noop, renderHistogram: noop, renderHorizontal: noop, setData: noop, setVisible: noop, remove: noop },
    document: null,
    marketProfile: mp,
    mpModeResolver: wireResolver ? (m) => coord.resolve(m) : null,
    mpGrowthResolver: wireResolver ? () => coord.isGrowing() : null,
  });
  if (withMp) {
    await controller.applyIndicator('market_profile', 'default'); // FOLLOW 初期＝normal+growing で追加。
  }
  return { controller, coord, mp };
}

test('FOLLOW 復帰: reapply は growing=true を適用し onLiveTick(forming) を起動、refresh(base累積) しない', async () => {
  const { coord, mp } = await setupWired();
  coord.onLiveStateChange(false); // 一旦 ANALYSIS へ（static）。
  await flush();
  mp.calls.length = 0; // ここまでの呼び出しをクリア。

  coord.onLiveStateChange(true); // 右端復帰 → FOLLOW。
  await flush();

  const kinds = mp.calls.map((c) => c[0]);
  assert.ok(mp.calls.some((c) => c[0] === 'setParams' && c[1] === 'normal'), '表示モードは選択モード normal を維持（ticklive にしない）');
  assert.ok(mp.calls.some((c) => c[0] === 'applyGrowthState' && c[1] === true), 'growing=true を適用');
  assert.ok(kinds.includes('onLiveTick'), '成長起動として onLiveTick(forming 取得)を呼ぶ');
  assert.ok(!kinds.includes('refresh'), 'growing 復帰では refresh(base 累積)を呼ばない（forming を潰さない）');
});

test('ANALYSIS 復帰: reapply は growing=false(static) を適用し選択モード(normal)を refresh で戻す（onLiveTick しない）', async () => {
  const { coord, mp } = await setupWired();
  mp.calls.length = 0; // add 後をクリア（初期は FOLLOW=growing）。

  coord.onLiveStateChange(false); // 過去へパン → ANALYSIS。
  await flush();

  const kinds = mp.calls.map((c) => c[0]);
  assert.ok(mp.calls.some((c) => c[0] === 'setParams' && c[1] === 'normal'), '表示モードは記憶モード normal を維持');
  assert.ok(mp.calls.some((c) => c[0] === 'applyGrowthState' && c[1] === false), 'growing=false(static) を適用');
  assert.ok(kinds.includes('refresh'), 'static は refresh で選択モードを反映');
  assert.ok(!kinds.includes('onLiveTick'), 'static は onLiveTick(成長起動)を呼ばない');
});

test('往復: ANALYSIS(static,refresh)→FOLLOW(growing,onLiveTick) が交互に切り替わる（表示モードは normal 維持）', async () => {
  const { coord, mp } = await setupWired();

  coord.onLiveStateChange(false); await flush(); // ANALYSIS
  const afterAnalysis = mp.calls.map((c) => c[0]);
  mp.calls.length = 0;
  coord.onLiveStateChange(true); await flush();  // FOLLOW
  const afterFollow = mp.calls.map((c) => c[0]);

  assert.ok(afterAnalysis.includes('refresh') && !afterAnalysis.includes('onLiveTick'), 'ANALYSIS=refresh');
  assert.ok(afterFollow.includes('onLiveTick') && !afterFollow.includes('refresh'), 'FOLLOW=onLiveTick(forming)');
  assert.equal(mp._mode, 'normal', '往復しても表示モードは normal のまま（ticklive 化しない）');
});

test('MP 不在 no-op: marketProfile 未注入でも reapply は例外を出さない', async () => {
  const { controller } = await setupWired({ withMp: false });

  await assert.doesNotReject(() => controller.reapplyMarketProfileMode());
});

test('連動未配線 no-op: mpModeResolver 未注入なら reapply はアクターへ触れない', async () => {
  const { controller, mp } = await setupWired({ wireResolver: false });
  mp.calls.length = 0;

  await controller.reapplyMarketProfileMode();

  assert.equal(mp.calls.length, 0, 'resolver 未注入(連動なし)は reapply が no-op＝byte 不変');
});
