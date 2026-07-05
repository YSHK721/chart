// mp_live_mode_reapply.test.js — ライブ連動の「実効モード再適用」経路（IndicatorController.reapplyMarketProfileMode）を
//   実 MpLiveModeCoordinator と結線した状態で検証する（E2E 相当の往復・見せかけ緑を避ける）。
//
// 確定仕様（E2E バグ修正の回帰固定）:
//   - FOLLOW 復帰 → MP は「ticklive-entry」を実行する。present の ticklive-entry は onLiveTick（→_enterTicklive で
//     forming 取得）であり、refresh（/market_profile の base 累積）ではない。初期 add / 手動 gear の ticklive-entry
//     （live loop の onLiveTick 経路）と一致させる。単なる setParams+refresh は forming を発火しない＝バグ。
//   - ANALYSIS 復帰 → MP は選択モード（既定 normal）へ refresh で戻す（従来どおり）。
//   - MP 不在 / mpModeResolver 未注入（連動なし）は reapply が no-op（アクターへ触れない）。
// 構造: Arrange-Act-Assert（AAA）。DOM/ネット非依存（全注入・recording fake・実 coordinator/controller）。

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { get } from '../js/usecase/catalog.js';
import { IndicatorController } from '../js/adapter/front/indicator_controller.js';
import { MpLiveModeCoordinator } from '../js/adapter/front/mp_live_mode_coordinator.js';

const noop = () => {};
// reapply は onLiveStateChange から fire-and-forget（await されない）。ネストした await（reapply→onLiveTick）が
//   解決するまでマクロタスク境界で待つ。
const flush = () => new Promise((r) => setTimeout(r, 0));

// 状態を持つ recording fake actor。setParams で mode を保持し isTicklive/isEnabled に反映する
//   （実 actor 契約: setParams({mode:'ticklive'}) → isTicklive()=true）。呼び出し列を calls に記録。
function fakeMp() {
  return {
    calls: [],
    _enabled: false,
    _mode: null,
    isEnabled() { return this._enabled; },
    isTicklive() { return this._enabled && this._mode === 'ticklive'; },
    setParams(p) { this.calls.push(['setParams', p.mode]); if (p.mode != null) { this._mode = p.mode; } },
    async setEnabled(on) { this._enabled = !!on; this.calls.push(['setEnabled', !!on]); },
    async refresh() { this.calls.push(['refresh']); },
    async onLiveTick() { this.calls.push(['onLiveTick']); },
    detach() {},
  };
}

// 実 coordinator を resolver/reapply に結線した controller を組む（MP 適用済み・FOLLOW 初期）。
async function setupWired({ withMp = true, wireResolver = true } = {}) {
  const mp = withMp ? fakeMp() : null;
  let controller;
  const coord = new MpLiveModeCoordinator({
    liveMode: 'ticklive',
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
  });
  if (withMp) {
    await controller.applyIndicator('market_profile', 'default'); // FOLLOW 初期＝ticklive で追加。
  }
  return { controller, coord, mp };
}

test('FOLLOW 復帰: reapply は ticklive-entry(onLiveTick=forming)を実行し refresh(base累積)しない', async () => {
  const { coord, mp } = await setupWired();
  coord.onLiveStateChange(false); // 一旦 ANALYSIS へ（normal）。
  await flush();
  mp.calls.length = 0; // ここまでの呼び出しをクリア。

  coord.onLiveStateChange(true); // 右端復帰 → FOLLOW。
  await flush();

  const kinds = mp.calls.map((c) => c[0]);
  assert.ok(mp.calls.some((c) => c[0] === 'setParams' && c[1] === 'ticklive'), 'mode を ticklive に設定');
  assert.ok(kinds.includes('onLiveTick'), 'ticklive-entry として onLiveTick(forming 取得)を呼ぶ');
  assert.ok(!kinds.includes('refresh'), 'ticklive 復帰では refresh(base 累積)を呼ばない（forming を潰さない）');
});

test('ANALYSIS 復帰: reapply は選択モード(normal)を refresh で戻す（onLiveTick しない）', async () => {
  const { coord, mp } = await setupWired();
  mp.calls.length = 0; // add 後をクリア（初期は FOLLOW=ticklive）。

  coord.onLiveStateChange(false); // 過去へパン → ANALYSIS。
  await flush();

  const kinds = mp.calls.map((c) => c[0]);
  assert.ok(mp.calls.some((c) => c[0] === 'setParams' && c[1] === 'normal'), 'mode を記憶モード normal に設定');
  assert.ok(kinds.includes('refresh'), '選択モードは refresh で反映');
  assert.ok(!kinds.includes('onLiveTick'), '非 ticklive は onLiveTick(ticklive-entry)を呼ばない');
});

test('往復: ANALYSIS(normal,refresh)→FOLLOW(ticklive,onLiveTick) が交互に切り替わる', async () => {
  const { coord, mp } = await setupWired();

  coord.onLiveStateChange(false); await flush(); // ANALYSIS
  const afterAnalysis = mp.calls.map((c) => c[0]);
  mp.calls.length = 0;
  coord.onLiveStateChange(true); await flush();  // FOLLOW
  const afterFollow = mp.calls.map((c) => c[0]);

  assert.ok(afterAnalysis.includes('refresh') && !afterAnalysis.includes('onLiveTick'), 'ANALYSIS=refresh');
  assert.ok(afterFollow.includes('onLiveTick') && !afterFollow.includes('refresh'), 'FOLLOW=onLiveTick(forming)');
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
