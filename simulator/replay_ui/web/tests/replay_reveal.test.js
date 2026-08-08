// replay_reveal.test.js — 一括リビール（ISSUE-158 ②）の検証。
//
// 設計入力: causal_reveal_ids.js（実測ゲート済み登録リスト）＋ ReplayIndicatorController の
//   buildRevealBase / revealTo / hasRevealFor / revealNeedsBuild / clearRevealCache / 無効化。
//   値の同一性はサーバ側実測（乖離 0）で担保するため、本テストは機構（登録ゲート・スライス・
//   世代破棄・per-step スキップ）を固定する。構造: AAA・DOM/ネット非依存（全注入）。

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { ReplayIndicatorController } from '../js/adapter/front/replay_indicator_controller.js';
import { get } from '../js/usecase/catalog.js';
import { CAUSAL_REVEAL_IDS } from '../js/usecase/causal_reveal_ids.js';
import { IndicatorController } from '../js/adapter/front/indicator_controller.js';
import { TimeframeController } from '../js/adapter/front/timeframe_controller.js';
import { CausalSeriesLedger } from '../js/replay/causal_series_ledger.js';

const SERIES = [
  { name: 'btlm_trail_mean', kind: 'line', data: [
    { time: 10, value: 1 }, { time: 20, value: 2 }, { time: 30, value: 3 },
  ] },
  { name: 'btlm_trail_q5', kind: 'line', data: [
    { time: 20, value: 0.5 }, { time: 30, value: 0.6 },
  ] },
];

// ReplayIndicatorController の reveal 機構だけを通す最小フェイク束。
function newRevealCtrl({ applied, computeImpl } = {}) {
  const ctrl = Object.create(ReplayIndicatorController.prototype);
  ctrl._state = {
    applied: applied ?? [
      { instanceId: 'bt#1', indicatorId: 'btlm_trail', variant: 'default', params: {}, visible: true },
      { instanceId: 'pb#1', indicatorId: 'profit_band', variant: 'global', params: {}, visible: true },
    ],
  };
  ctrl._meta = new Map([
    ['bt#1', { def: { id: 'btlm_trail' } }],
    ['pb#1', { def: { id: 'profit_band' } }],
  ]);
  ctrl._isMarketProfile = () => false;
  // ISSUE-288: 送信規約（variant スコープ・計算.時間足）は本番と同じ経路を通す。カタログは
  //   本番に必ず在るため、実カタログを渡す（テスト専用の緩い形にしない）。
  ctrl._catalog = { get };
  ctrl._paramsObject = (p) => p ?? {};
  ctrl._defaultVariant = () => 'default';
  ctrl._validateSeriesNames = (series) => series;
  ctrl._datasetRef = 'jp225_tick';
  // ISSUE-181: 時間足ロールの状態は TimeframeController が所有する（host はフィールドを持たない）。
  //   prototype 直生成のため constructor を経ない協働子をここで用意する（host 面の読みは不変）。
  ctrl._tf = new TimeframeController(ctrl, { timeframe: '1m' });
  ctrl._revealCache = new Map();
  ctrl._revealEpoch = 0;
  // [ISSUE-293] 本番の合成形と同じ協働子を持たせる（prototype 直生成でも欠けさせない）。
  ctrl._ledger = new CausalSeriesLedger();
  ctrl._computeCalls = [];
  ctrl._compute = {
    compute: computeImpl ?? (async (req) => {
      ctrl._computeCalls.push(req);
      return { ok: true, generation: 0, series: SERIES };
    }),
  };
  ctrl._renderJobs = [];
  ctrl._renderInstance = (job) => ctrl._renderJobs.push(job);
  return ctrl;
}

test('buildRevealBase caches only registry indicators (fail-closed gate)', async () => {
  const ctrl = newRevealCtrl();
  assert.equal(ctrl.revealNeedsBuild(), true);
  await ctrl.buildRevealBase(30, 3);
  assert.equal(ctrl.hasRevealFor('bt#1'), true, '登録指標はキャッシュされる');
  assert.equal(ctrl.hasRevealFor('pb#1'), false, '未登録指標（profit_band）はキャッシュしない');
  assert.equal(ctrl.revealNeedsBuild(), false);
  // 基底リクエストは全レンジ（untilTime=tEnd・limit=totalBars・mode=full）。
  assert.equal(ctrl._computeCalls.length, 1);
  const req = ctrl._computeCalls[0];
  assert.equal(req.untilTime, 30);
  assert.equal(req.limit, 3);
  assert.equal(req.mode, 'full');
});

test('revealTo slices each series to time<=t and renders via the standard job path', async () => {
  const ctrl = newRevealCtrl();
  await ctrl.buildRevealBase(30, 3);
  ctrl.revealTo(20);
  assert.equal(ctrl._renderJobs.length, 1);
  const job = ctrl._renderJobs[0];
  assert.equal(job.instanceId, 'bt#1');
  assert.equal(job.wantLatest, false, 'full 経路（remove+redraw）で描画する');
  const byName = Object.fromEntries(job.series.map((s) => [s.name, s.data]));
  assert.deepEqual(byName.btlm_trail_mean.map((p) => p.time), [10, 20], 't<=20 へスライス');
  assert.deepEqual(byName.btlm_trail_q5.map((p) => p.time), [20]);
  // 全域より未来の t は全点、系列開始前の t は空。
  ctrl._renderJobs.length = 0;
  ctrl.revealTo(999);
  assert.deepEqual(ctrl._renderJobs[0].series[0].data.map((p) => p.time), [10, 20, 30]);
  ctrl._renderJobs.length = 0;
  ctrl.revealTo(5);
  assert.deepEqual(ctrl._renderJobs[0].series[0].data, []);
});

test('revealTo skips instances removed from state (zombie guard)', async () => {
  const ctrl = newRevealCtrl();
  await ctrl.buildRevealBase(30, 3);
  ctrl._state = { applied: [] };   // 凡例 close 相当
  ctrl.revealTo(20);
  assert.equal(ctrl._renderJobs.length, 0);
});

test('clearRevealCache discards a base that resolves after invalidation (epoch guard)', async () => {
  let release;
  const ctrl = newRevealCtrl({
    computeImpl: () => new Promise((res) => { release = () => res({ ok: true, generation: 0, series: SERIES }); }),
  });
  const p = ctrl.buildRevealBase(30, 3);
  ctrl.clearRevealCache();                    // 時間足切替相当（構築中に無効化）
  release();
  await p;
  assert.equal(ctrl.hasRevealFor('bt#1'), false, '無効化後に届いた基底は破棄される');
});

test('_invalidateReveal drops a cached base; latest-mode recompute must not (forming 無害)', async () => {
  const ctrl = newRevealCtrl();
  await ctrl.buildRevealBase(30, 3);
  assert.equal(ctrl.hasRevealFor('bt#1'), true);
  ctrl._invalidateReveal('bt#1');             // gear（params 変更）相当
  assert.equal(ctrl.hasRevealFor('bt#1'), false);
  assert.equal(ctrl.revealNeedsBuild(), true, '次フレームで再構築される');
});

test('registry contains exactly the measured-exact indicator set', () => {
  assert.deepEqual(
    [...CAUSAL_REVEAL_IDS].sort(),
    ['btlm_trail', 'btlm_trail_marod', 'ma_marod', 'moving_averages'],
  );
});

// 基底クラス recomputeAllApplied の skip 述語（additive・present は不使用＝挙動不変）。
test('recomputeAllApplied honors the skip predicate (revealed instances are not recomputed)', async () => {
  const ctrl = Object.create(IndicatorController.prototype);
  ctrl._state = {
    applied: [
      { instanceId: 'a#1', indicatorId: 'x', params: {} },
      { instanceId: 'b#1', indicatorId: 'y', params: {} },
    ],
  };
  ctrl._meta = new Map([['a#1', { def: {} }], ['b#1', { def: {} }]]);
  ctrl._isMarketProfile = () => false;
  // ISSUE-288: 送信規約（variant スコープ・計算.時間足）は本番と同じ経路を通す。カタログは
  //   本番に必ず在るため、実カタログを渡す（テスト専用の緩い形にしない）。
  ctrl._catalog = { get };
  ctrl._paramsObject = (p) => p;
  const computed = [];
  ctrl._computeInstance = async (id) => { computed.push(id); return { accepted: false }; };
  ctrl._persistAll = () => {};
  await ctrl.recomputeAllApplied({ mode: 'full', skip: (inst) => inst.instanceId === 'a#1' });
  assert.deepEqual(computed, ['b#1'], 'skip 該当は計算しない・非該当は従来どおり');
});

// ---- 上位足計算も基底キャッシュの対象（ISSUE-294） ----
//
// ISSUE-292 では対象外にしていた（基底は tEnd で 1 回計算して時刻でスライスするだけであり、
//   当時の投影は進行中期間の点にその期間終了後の確定値を焼き込んでいたため）。ISSUE-294 で
//   サーバの返す系列を「各バー τ の点＝τ 時点で計算できた値」＝**時刻不変**へ変えたので、
//   基底＋スライスの前提が回復した（実測: T を進めても重なり 1238 点すべて不変）。

test('上位足計算のインスタンスも基底キャッシュの対象（計算.時間足を載せて 1 回で作る）', async () => {
  const ctrl = newRevealCtrl({
    applied: [
      { instanceId: 'chart#1', indicatorId: 'btlm_trail', variant: 'default',
        params: { timeframe: 'chart' }, visible: true },
      { instanceId: 'mtf#1', indicatorId: 'btlm_trail', variant: 'default',
        params: { timeframe: '1D' }, visible: true },
    ],
  });
  ctrl._meta = new Map([
    ['chart#1', { def: { id: 'btlm_trail' } }],
    ['mtf#1', { def: { id: 'btlm_trail' } }],
  ]);

  await ctrl.buildRevealBase(30, 3);

  assert.equal(ctrl.hasRevealFor('chart#1'), true);
  assert.equal(ctrl.hasRevealFor('mtf#1'), true, '時刻不変になったのでキャッシュしてよい');
  const sent = new Map(ctrl._computeCalls.map((r) => [r.params.timeframe, r.computeTimeframe]));
  assert.equal(sent.get('1D'), '1D', '上位足には計算.時間足を載せて送る');
  assert.equal(sent.get('chart'), undefined, 'チャート足には載せない（従来ボディ）');
});
