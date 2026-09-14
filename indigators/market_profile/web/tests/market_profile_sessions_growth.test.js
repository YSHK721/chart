// market_profile_sessions_growth.test.js — Phase 3: sessions 因果成長（機構A: refresh(to, sessions)）検証。
//
// 設計入力: Model A 統一成長モデル Phase 3。growing×sessions は forming 単一プロファイル
//   （_enterTicklive→setProfile）を sessions 描画へ被せず、refresh(to=cursor, sessions=1) で backend の
//   因果 sessions 分割（当日=[session_start, to)・過去日静的）を取得する（Phase0-2 review🔵4 の破綻状態
//   ＝「FOLLOW+sessions で _sessions=true 保持のまま onLiveTick が forming を描く新到達状態」を正しく解消）。
//   accumulator は sessions では使わない（自前グリッド↔共有グリッド不整合の回避＝DwellAccumulator/
//   sessions primitive 無改修）。成長経路の分岐: sessions+growing→refresh(to)／normal/replay+growing→
//   forming/accumulator。未来リーク禁止（to<=cursor・当日完成を先出ししない）を golden 固定。
// 構造: Arrange-Act-Assert。client/formingClient/accumulator は Fake 注入（実 fetch/lwc 非依存）。

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { MarketProfileActor } from '../js/adapter/front/market_profile_actor.js';
// ISSUE-260: VA 比率の既定は Python 唯一源の生成物（テストも第 2 定義を持たない）。
import { VA_PCT_DEFAULT } from '../js/domain/mp_param_defaults_generated.js';

const PROFILE = {
  bins: [{ price: 1, tpo: 1, norm: 1 }], poc: 1, va_low: 1, va_high: 1,
  sessions: [{ date: '2024-01-01', tpo: [1] }, { date: '2024-01-02', tpo: [2] }],
};

function fakeClient(result = PROFILE) {
  const calls = [];
  return { calls, async fetchProfile(ctx) { calls.push(ctx); return result; } };
}

function fakePrimitive() {
  return {
    profiles: [], visibles: [], sessionsCalls: [],
    setProfile(p) { this.profiles.push(p); },
    setVisible(v) { this.visibles.push(v); },
    setSessions(s) { this.sessionsCalls.push(s); },
  };
}

// base=1 応答は _hasBaseFields を満たす最小 forming（normal 成長の _enterTicklive を通す）。
function fakeForming() {
  const calls = [];
  return {
    calls,
    async fetchForming(args) {
      calls.push(args);
      return {
        ok: true, formingStart: 1000, ticks: [],
        baseFine: [0], baseKmin: 1, activeTable: [[1]],
        priceMin: 1, priceMax: 2, nBins: 1, gridW: 1, vaPct: VA_PCT_DEFAULT, now: 1,
      };
    },
  };
}

function fakeAcc() {
  return { init() {}, addTick() {}, snapshot() { return { bins: [], poc: 0 }; } };
}

function makeActor({ ctx } = {}) {
  const client = fakeClient();
  const primitive = fakePrimitive();
  const forming = fakeForming();
  const actor = new MarketProfileActor({
    client,
    primitive,
    getContext: () => (ctx || { datasetRef: 'jp225_tick', timeframe: '1h', limit: 1500 }),
    formingClient: forming,
    makeAccumulator: () => fakeAcc(),
  });
  return { actor, client, primitive, forming };
}

// --- sessions + growing: refresh(sessions) で育て、forming 単一プロファイルを描かない（review🔵4 解消） ---
test('growing + sessions: onLiveTick は refresh(sessions:true) で当日タイルを育て、forming/accumulator を使わない', async () => {
  // Arrange
  const { actor, client, forming } = makeActor();
  await actor.setEnabled(true); // 初回 refresh（sessions 未設定）。
  actor.setParams({ mode: 'sessions' });
  actor.applyGrowthState({ growing: true });
  const beforeC = client.calls.length;
  const beforeF = forming.calls.length;
  // Act
  await actor.onLiveTick();
  // Assert: sessions 成長は forming（accumulator）を呼ばず、refresh(client.fetchProfile, sessions:true) へ。
  assert.equal(forming.calls.length, beforeF, 'sessions 成長は forming（accumulator）を使わない');
  assert.equal(client.calls.length, beforeC + 1, 'onLiveTick は refresh(client.fetchProfile) へ委譲');
  assert.equal(client.calls[client.calls.length - 1].sessions, true, 'refresh は sessions:true を載せる（因果 sessions 分割）');
});

// --- normal + growing: forming/accumulator（現 ticklive 機構）で育つ（sessions 分岐の非波及・回帰ゼロ） ---
test('growing + normal: onLiveTick は forming/accumulator で育つ（normal 成長経路は不変）', async () => {
  // Arrange
  const { actor, forming } = makeActor();
  await actor.setEnabled(true);
  actor.setParams({ mode: 'normal' });
  actor.applyGrowthState({ growing: true });
  const beforeF = forming.calls.length;
  // Act
  await actor.onLiveTick();
  // Assert: normal 成長は forming（base=1 取得→accumulator）を使う（不変）。
  assert.ok(forming.calls.length > beforeF, 'normal 成長は forming（accumulator）を使う（不変）');
});

// --- 未来リーク golden: sessions+growing の refresh は cursor(=to) を超えない（to<=cursor・先出し禁止） ---
test('未来リーク golden: growing + sessions で refresh の to は getContext の cursor をそのまま送る（to<=cursor）', async () => {
  // Arrange: getContext が因果カーソル to=cursor を供給する。
  const cursor = 5000;
  const { actor, client } = makeActor({ ctx: { datasetRef: 'jp225_tick', timeframe: '1h', limit: 1500, to: cursor } });
  await actor.setEnabled(true);
  actor.setParams({ mode: 'sessions' });
  actor.applyGrowthState({ growing: true });
  // Act
  await actor.onLiveTick();
  // Assert: refresh は cursor=to をそのまま送り、未来へ進めない（当日完成を先出ししない）。
  const last = client.calls[client.calls.length - 1];
  assert.equal(last.to, cursor, 'refresh は cursor=to をそのまま送る（先出ししない）');
  assert.ok(last.to <= cursor, 'to<=cursor（未来リーク禁止）');
});
