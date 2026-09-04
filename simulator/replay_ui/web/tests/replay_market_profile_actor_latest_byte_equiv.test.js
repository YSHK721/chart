// MP 単一化 capture-compare: ReplayMarketProfileActor(to=MP_TO_LATEST) が出す全リクエストが、
//   base MarketProfileActor（現状ライブ・to 省略）と byte 一致することを実証する（ライブ MP byte 不変の
//   機構的ゲート）。3 状態 to のうち LATEST（ライブマーカー・非null）は各 override が super へ委譲し、
//   client が LATEST→clock 省略へ翻訳するため、全経路（/market_profile・/market_profile_forming）の
//   URL が現状ライブと一致する。int（リプレイ）・null（restore）は別テスト（従来）で担保。
// 構造: Arrange-Act-Assert。両アクターを同一 DI で構築し、同一操作の fetch 引数を URL 化して deepEqual。

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { ReplayMarketProfileActor } from '../js/adapter/front/replay_market_profile_actor.js';
import { MarketProfileActor } from '../js/adapter/front/market_profile_actor.js';
import { buildMarketProfileUrl, MP_TO_LATEST } from '../js/adapter/front/market_profile_client.js';
import { buildFormingUrl } from '../js/adapter/front/market_profile_forming_client.js';
// ISSUE-260: VA 比率の既定は Python 唯一源の生成物（テストも第 2 定義を持たない）。
import { VA_PCT_DEFAULT } from '../js/domain/mp_param_defaults_generated.js';

const BASE_FULL = {
  ok: true, formingStart: 1000, ticks: [],
  baseFine: [0, 0, 0], baseKmin: 100, activeTable: [[1]], priceMin: 1000, priceMax: 1100,
  nBins: 3, gridW: 10, vaPct: VA_PCT_DEFAULT, now: 1030,
};

function fakePrimitive() {
  return {
    setProfile() {}, setVisible() {}, setCursorTime() {}, setSnapshot() {}, setSessions() {},
  };
}

function makeAccumulator() {
  return {
    init(cfg) { this.cfg = cfg; this.ticks = []; },
    addTick(sec, mid) { this.ticks.push([sec, mid]); },
    snapshot() { return { poc: 0, bins: [] }; },
  };
}

// 同一 DI で actor を構築し、client/formingClient が受け取った引数（＝URL 素材）を記録する。
function captureActor(ActorClass, ctxTo) {
  const profileArgs = [];
  const formingArgs = [];
  const client = { async fetchProfile(ctx) { profileArgs.push(ctx); return { bins: [], poc: 1 }; } };
  const formingClient = { async fetchForming(args) { formingArgs.push(args); return BASE_FULL; } };
  const actor = new ActorClass({
    client,
    primitive: fakePrimitive(),
    mainSeries: { attachPrimitive() {} },
    getContext: () => ({ datasetRef: 'jp225_tick', timeframe: '1h', to: ctxTo }),
    formingClient,
    makeAccumulator,
    getCandles: () => [{ time: 1704074400, high: 1100, low: 1000, close: 1050 }],
    now: () => 0,
    throttleMs: 0,
  });
  return { actor, profileArgs, formingArgs };
}

async function driveLiveGrowth(h) {
  await h.actor.setEnabled(true);              // 既定 normal
  h.actor.applyGrowthState({ growing: true }); // FOLLOW（成長 ON＝ライブ増分経路）
  await h.actor.onLiveTick();
  await h.actor.refresh();
}

test('capture-compare: ReplayMarketProfileActor(to=LATEST) の /market_profile URL 列が base live(to省略) と byte 一致', async () => {
  // Arrange
  const base = captureActor(MarketProfileActor, undefined);        // 現状ライブ（to 省略）
  const latest = captureActor(ReplayMarketProfileActor, MP_TO_LATEST); // unified ライブ（LATEST マーカー）
  // Act
  await driveLiveGrowth(base);
  await driveLiveGrowth(latest);
  // Assert: 呼び出し回数・URL 列とも byte 一致。
  const baseUrls = base.profileArgs.map((a) => buildMarketProfileUrl(a));
  const latestUrls = latest.profileArgs.map((a) => buildMarketProfileUrl(a));
  assert.deepEqual(latestUrls, baseUrls, '/market_profile URL 列が byte 一致（LATEST→to 省略）');
});

test('capture-compare: ReplayMarketProfileActor(to=LATEST) の /market_profile_forming URL 列が base live と byte 一致', async () => {
  // Arrange
  const base = captureActor(MarketProfileActor, undefined);
  const latest = captureActor(ReplayMarketProfileActor, MP_TO_LATEST);
  // Act
  await driveLiveGrowth(base);
  await driveLiveGrowth(latest);
  // Assert: forming URL も byte 一致（now/from の replay override が LATEST では載らず base ローカル導出）。
  const baseUrls = base.formingArgs.map((a) => buildFormingUrl(a));
  const latestUrls = latest.formingArgs.map((a) => buildFormingUrl(a));
  assert.deepEqual(latestUrls, baseUrls, '/market_profile_forming URL 列が byte 一致（LATEST→now 省略）');
});

test('dwell-growing×LATEST: refresh/onLiveTick が例外を投げず base へ委譲（enterBar/forming クラッシュ経路へ入らない）', async () => {
  // 実UI回帰の再現: live（to=LATEST）で src=dwell・growing（isGrowingPush=true・incremental）だと、ガードが
  //   効かないと refresh()→enterBar(LATEST)→_buildFormingArgs→GrowthWindow.forCurrent(Number(LATEST)) で例外。
  //   別リテラル（≒別モジュール実体）の value 等価センチネルでガードが効くことも同時に確認する。
  const freshLatest = '__MP_TO_LATEST__'; // MP_TO_LATEST と value 等価だが別リテラル（Symbol なら identity 不一致）。
  assert.equal(freshLatest, MP_TO_LATEST, '前提: value 等価（文字列センチネル）');
  const base = captureActor(MarketProfileActor, undefined);
  const latest = captureActor(ReplayMarketProfileActor, freshLatest);
  for (const h of [base, latest]) {
    h.actor.setParams({ src: 'dwell', mode: 'normal' });
    await h.actor.setEnabled(true);
    h.actor.applyGrowthState({ growing: true }); // isGrowingPush=true・dwell（incremental）
  }
  // Act: 例外を投げないこと（Symbol 版はここでクラッシュしていた）。
  await assert.doesNotReject(() => latest.actor.refresh(), 'dwell-growing×LATEST の refresh が例外を投げない');
  await assert.doesNotReject(() => latest.actor.onLiveTick(), 'dwell-growing×LATEST の onLiveTick が例外を投げない');
  await assert.doesNotReject(() => base.actor.refresh());
  await assert.doesNotReject(() => base.actor.onLiveTick());
  // Assert: base と全リクエスト byte 一致（super 委譲＝base 経路・LATEST→clock 省略）。
  const bMp = base.profileArgs.map((a) => buildMarketProfileUrl(a));
  const lMp = latest.profileArgs.map((a) => buildMarketProfileUrl(a));
  assert.deepEqual(lMp, bMp, 'dwell-growing×LATEST でも /market_profile URL 列が base と byte 一致');
  const bForm = base.formingArgs.map((a) => buildFormingUrl(a));
  const lForm = latest.formingArgs.map((a) => buildFormingUrl(a));
  assert.deepEqual(lForm, bForm, 'dwell-growing×LATEST でも /market_profile_forming URL 列が base と byte 一致');
});

test('int cursor（リプレイ）は base と異なる（&to= 付与）＝LATEST 限定のライブ委譲であること', async () => {
  // LATEST ガードがライブ限定であり、int（リプレイ）では従来の as-of/pull 経路（&to= 付与）が生きることを確認。
  const replay = captureActor(ReplayMarketProfileActor, 1704074400);
  await replay.actor.setEnabled(true); // normal・非成長＝refresh(as-of-T)
  await replay.actor.refresh();
  const urls = replay.profileArgs.map((a) => buildMarketProfileUrl(a));
  assert.ok(urls.some((u) => u.includes('&to=1704074400')), 'int cursor は &to= を送る（リプレイ as-of・無退行）');
});
