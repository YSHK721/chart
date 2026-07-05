// growth_coordinator.js（GrowthCoordinator・表示モード×成長状態の直交化協調役）の仕様検証。
//
// 確定仕様（Model A 直交化・ライブトグル状態と MP 成長状態の連動）:
//   - resolve(userMode): 選択表示モードを返す（'ticklive' を返さない）。userMode を記憶し、FOLLOW/ANALYSIS に
//       依存せず「gear 記憶モード（未選択は defaultMode='normal'）」を返す。null は「記憶更新なし・解決のみ」。
//   - isGrowing(): 成長信号。FOLLOW=true（growing）／ANALYSIS=false（static）。表示モードとは独立（直交）。
//   - onLiveStateChange(isFollow): LiveFollowController の遷移フック。状態を更新し reapply を呼ぶ。
//       同状態は再適用しない（冪等・flicker/二重 fetch 回避）。reapply 未注入は no-op（MP 不在相当）。
// 構造: Arrange-Act-Assert（AAA）。DOM/actor 非依存（純ロジック・reapply は関数注入）。

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { GrowthCoordinator } from '../js/usecase/growth_coordinator.js';

// reapply 呼び出し回数を数える spy。
function spyReapply() {
  const s = { calls: 0, fn() { s.calls += 1; } };
  return s;
}

test('resolve: FOLLOW（既定）は選択モードを記憶し、その選択モードを返す（ticklive を返さない）', () => {
  const coord = new GrowthCoordinator({ defaultMode: 'normal' });

  const effective = coord.resolve('sessions');

  assert.equal(effective, 'sessions', 'FOLLOW でも実効は選択表示モード（ticklive 置換なし＝直交化）');
  assert.equal(coord.userMode(), 'sessions', 'gear 選択（sessions）は記憶される');
  assert.equal(coord.isFollow(), true, '初期はチャート既定 FOLLOW');
  assert.equal(coord.isGrowing(), true, 'FOLLOW は growing=true（成長 ON）');
});

test('resolve: 既定（未選択・FOLLOW）は defaultMode を返す', () => {
  const coord = new GrowthCoordinator({ defaultMode: 'normal' });

  assert.equal(coord.resolve(null), 'normal', '未選択は catalog 既定モード（normal）');
  assert.equal(coord.isGrowing(), true, 'FOLLOW+normal は growing=true');
});

test('resolve: ANALYSIS でも選択モードを返す（表示モードは成長状態と独立）', () => {
  const coord = new GrowthCoordinator({ defaultMode: 'normal' });
  coord.onLiveStateChange(false); // ANALYSIS へ

  const effective = coord.resolve('replay');

  assert.equal(effective, 'replay', 'ANALYSIS でも選択モードを返す');
  assert.equal(coord.userMode(), 'replay');
  assert.equal(coord.isGrowing(), false, 'ANALYSIS は growing=false（static）');
});

test('resolve: ANALYSIS で userMode 未選択なら defaultMode を返す', () => {
  const coord = new GrowthCoordinator({ defaultMode: 'normal' });
  coord.onLiveStateChange(false); // ANALYSIS へ

  assert.equal(coord.resolve(null), 'normal', '未選択時は catalog 既定モードへフォールバック');
});

test('isGrowing: FOLLOW↔ANALYSIS で growing がトグルし、表示モードは維持される', () => {
  const coord = new GrowthCoordinator({ defaultMode: 'normal' });
  coord.resolve('sessions'); // 選択 sessions（FOLLOW・growing）

  coord.onLiveStateChange(false); // ANALYSIS（static）
  assert.equal(coord.isGrowing(), false, 'ANALYSIS は static');
  assert.equal(coord.resolve(null), 'sessions', '表示モードは維持（sessions のまま）');

  coord.onLiveStateChange(true); // FOLLOW 復帰（growing）
  assert.equal(coord.isGrowing(), true, 'FOLLOW 復帰は growing');
  assert.equal(coord.resolve(null), 'sessions', '表示モードは依然維持（FOLLOW でも sessions・ticklive 化しない）');
});

test('resolve(null) は記憶モードを上書きしない（実効解決のみ）', () => {
  const coord = new GrowthCoordinator({ defaultMode: 'normal' });
  coord.resolve('sessions');

  coord.resolve(null); // 実効解決のみ（reapply 経路）。

  assert.equal(coord.userMode(), 'sessions', 'null 渡しは記憶を消さない');
});

test('onLiveStateChange: 状態遷移で reapply を呼ぶ（FOLLOW→ANALYSIS→FOLLOW で 2 回）', () => {
  const spy = spyReapply();
  const coord = new GrowthCoordinator({ reapply: () => spy.fn() });

  coord.onLiveStateChange(false); // FOLLOW→ANALYSIS
  coord.onLiveStateChange(true);  // ANALYSIS→FOLLOW

  assert.equal(spy.calls, 2, '各遷移で 1 回ずつ reapply');
});

test('onLiveStateChange: 同状態は再適用しない（冪等・二重 fetch/flicker 回避）', () => {
  const spy = spyReapply();
  const coord = new GrowthCoordinator({ reapply: () => spy.fn() });

  coord.onLiveStateChange(true);  // 既に FOLLOW（初期）＝同状態
  coord.onLiveStateChange(false); // 遷移
  coord.onLiveStateChange(false); // 同状態（連続 auto-off 等）

  assert.equal(spy.calls, 1, '遷移した 1 回だけ reapply（同状態は no-op）');
});

test('MP 不在相当: reapply 未注入でも onLiveStateChange は例外を出さない（no-op）', () => {
  const coord = new GrowthCoordinator({});

  assert.doesNotThrow(() => coord.onLiveStateChange(false));
  assert.equal(coord.isFollow(), false, '状態は更新される（reapply が無いだけ）');
});

test('async reapply の拒否は握り潰す（unhandledRejection を出さない）', () => {
  const coord = new GrowthCoordinator({ reapply: async () => { throw new Error('boom'); } });

  assert.doesNotThrow(() => coord.onLiveStateChange(false), '同期呼び出しは例外を投げない');
});
