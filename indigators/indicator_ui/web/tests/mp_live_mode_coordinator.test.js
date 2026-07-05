// mp_live_mode_coordinator.js（MpLiveModeCoordinator・ライブ連動 MP モード協調役）の仕様検証。
//
// 確定仕様（present 固有・ライブトグル状態と MP 表示モードの連動）:
//   - チャート FOLLOW（ライブ）→ MP 実効モードは liveMode（'ticklive'）。
//   - チャート ANALYSIS（分析）→ MP 実効モードは「ユーザーが gear で選んだ記憶モード」_mpUserMode
//     （未選択時は defaultMode＝catalog 既定 'normal'）。
//   - resolve(userMode): controller の MP param 構築から呼ばれ、userMode を記憶し実効モードを返す。
//       FOLLOW 中に gear でモードを選んでも実効は 'ticklive' のまま（記憶だけ更新）。
//       ANALYSIS 中は選んだモードを即返す（＝即適用）。
//   - onLiveStateChange(isFollow): LiveFollowController の遷移フック。状態を更新し reapply を呼ぶ。
//       同状態は再適用しない（冪等・flicker/二重 fetch 回避）。reapply 未注入は no-op（MP 不在相当）。
// 構造: Arrange-Act-Assert（AAA）。DOM/actor 非依存（純ロジック・reapply は関数注入）。

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { MpLiveModeCoordinator } from '../js/adapter/front/mp_live_mode_coordinator.js';

// reapply 呼び出し回数を数える spy。
function spyReapply() {
  const s = { calls: 0, fn() { s.calls += 1; } };
  return s;
}

test('resolve: FOLLOW（既定）は userMode を記憶しつつ実効 liveMode(ticklive) を返す', () => {
  const coord = new MpLiveModeCoordinator({ liveMode: 'ticklive', defaultMode: 'normal' });

  const effective = coord.resolve('sessions');

  assert.equal(effective, 'ticklive', 'FOLLOW 中は実効モードが ticklive');
  assert.equal(coord.userMode(), 'sessions', 'gear 選択（sessions）は記憶される');
  assert.equal(coord.isFollow(), true, '初期はチャート既定 FOLLOW');
});

test('resolve: ANALYSIS では記憶した userMode をそのまま返す（即適用）', () => {
  const coord = new MpLiveModeCoordinator({ liveMode: 'ticklive', defaultMode: 'normal' });
  coord.onLiveStateChange(false); // ANALYSIS へ

  const effective = coord.resolve('replay');

  assert.equal(effective, 'replay', 'ANALYSIS 中は選んだモードを即返す');
  assert.equal(coord.userMode(), 'replay');
});

test('resolve: ANALYSIS で userMode 未選択なら defaultMode を返す', () => {
  const coord = new MpLiveModeCoordinator({ liveMode: 'ticklive', defaultMode: 'normal' });
  coord.onLiveStateChange(false); // ANALYSIS へ

  assert.equal(coord.resolve(null), 'normal', '未選択時は catalog 既定モードへフォールバック');
});

test('FOLLOW→ticklive 復帰: ANALYSIS で記憶したモードは FOLLOW 復帰で ticklive に上書きされる', () => {
  const coord = new MpLiveModeCoordinator({ liveMode: 'ticklive', defaultMode: 'normal' });
  coord.onLiveStateChange(false); // ANALYSIS
  coord.resolve('sessions');       // 記憶 sessions
  coord.onLiveStateChange(true);   // FOLLOW 復帰

  assert.equal(coord.resolve(null), 'ticklive', 'FOLLOW は常に実効 ticklive');
  assert.equal(coord.userMode(), 'sessions', '記憶モードは保持（ANALYSIS 復帰で使う）');
});

test('ANALYSIS 復帰: FOLLOW 中に gear で選んだモードが ANALYSIS 復帰で適用される', () => {
  const coord = new MpLiveModeCoordinator({ liveMode: 'ticklive', defaultMode: 'normal' });
  // FOLLOW 中に gear で 'replay' を選ぶ（実効は ticklive のまま・記憶のみ）。
  assert.equal(coord.resolve('replay'), 'ticklive');
  // ANALYSIS へ。
  coord.onLiveStateChange(false);

  assert.equal(coord.resolve(null), 'replay', 'ANALYSIS 復帰で FOLLOW 中に選んだ記憶モードを適用');
});

test('resolve(null) は記憶モードを上書きしない（実効解決のみ）', () => {
  const coord = new MpLiveModeCoordinator({ liveMode: 'ticklive', defaultMode: 'normal' });
  coord.onLiveStateChange(false);
  coord.resolve('sessions');

  coord.resolve(null); // 実効解決のみ（reapply 経路）。

  assert.equal(coord.userMode(), 'sessions', 'null 渡しは記憶を消さない');
});

test('onLiveStateChange: 状態遷移で reapply を呼ぶ（FOLLOW→ANALYSIS→FOLLOW で 2 回）', () => {
  const spy = spyReapply();
  const coord = new MpLiveModeCoordinator({ reapply: () => spy.fn() });

  coord.onLiveStateChange(false); // FOLLOW→ANALYSIS
  coord.onLiveStateChange(true);  // ANALYSIS→FOLLOW

  assert.equal(spy.calls, 2, '各遷移で 1 回ずつ reapply');
});

test('onLiveStateChange: 同状態は再適用しない（冪等・二重 fetch/flicker 回避）', () => {
  const spy = spyReapply();
  const coord = new MpLiveModeCoordinator({ reapply: () => spy.fn() });

  coord.onLiveStateChange(true);  // 既に FOLLOW（初期）＝同状態
  coord.onLiveStateChange(false); // 遷移
  coord.onLiveStateChange(false); // 同状態（連続 auto-off 等）

  assert.equal(spy.calls, 1, '遷移した 1 回だけ reapply（同状態は no-op）');
});

test('MP 不在相当: reapply 未注入でも onLiveStateChange は例外を出さない（no-op）', () => {
  const coord = new MpLiveModeCoordinator({});

  assert.doesNotThrow(() => coord.onLiveStateChange(false));
  assert.equal(coord.isFollow(), false, '状態は更新される（reapply が無いだけ）');
});

test('async reapply の拒否は握り潰す（unhandledRejection を出さない）', () => {
  const coord = new MpLiveModeCoordinator({ reapply: async () => { throw new Error('boom'); } });

  assert.doesNotThrow(() => coord.onLiveStateChange(false), '同期呼び出しは例外を投げない');
});
