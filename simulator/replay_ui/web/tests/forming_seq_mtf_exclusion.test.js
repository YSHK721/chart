// forming_seq_mtf_exclusion.test.js — 足内一括計算は上位足計算の指標を対象にしない（ISSUE-288）。
//
// 実 UI で検出（:8000 リプレイ）: 計算.時間足=1D の移動平均が **5m の値**で描かれ、投影済みの
//   階段が消えていた。/compute（full）の応答は正しく投影されていた（1D 値・1500 点）が、
//   足内一括計算（mode=latest_seq）が同じインスタンスをチャート足で計算し、その結果で
//   系列を上書きしていた。＝「確定時は正しく、足内で壊れる」という時間差のある破壊。
//
// 規約: 足内一括計算はチャート足の窓で計算する経路であり、上位足へ投影できない。よって
//   上位足計算のインスタンスは**対象に含めない**（足内では動かず、バー確定の full 計算で
//   追いつく）。ライブ側 ISSUE-274 の「段全体を毎 tick 動かすのは費用に見合わない」と同じ判断。
// 構造: Arrange-Act-Assert（AAA）。

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { ReplayIndicatorController } from '../js/adapter/front/replay_indicator_controller.js';
import { get } from '../js/usecase/catalog.js';

const noop = () => {};

function newController(timeframe = '5m') {
  return new ReplayIndicatorController({
    catalog: { listIndicators: () => [], get },
    compute: { compute: async () => ({ ok: true, generation: 0, series: [] }) },
    persistence: {
      loadApplied: () => [], saveApplied: noop, loadFavorites: () => [], saveFavorites: noop,
      loadUiState: () => ({}), saveUiState: noop, nextSeq: () => 1,
    },
    renderer: {},
    document: null,
    datasetRef: 'jp225_tick',
    timeframe,
  });
}

function addInstance(ctrl, instanceId, params) {
  ctrl._state = {
    ...ctrl._state,
    applied: [...ctrl._state.applied,
      { instanceId, indicatorId: 'moving_averages', variant: 'default', params, visible: true }],
  };
  ctrl._meta.set(instanceId, { def: get('moving_averages') });
}

test('上位足計算のインスタンスも足内一括計算の対象（ライブと同一設計・ISSUE-290）', () => {
  const ctrl = newController('5m');
  addInstance(ctrl, 'chart#1', { timeframe: 'chart', ma_type: 'ema', length: 9 });
  addInstance(ctrl, 'same#1', { timeframe: '5m', ma_type: 'ema', length: 9 });
  addInstance(ctrl, 'mtf#1', { timeframe: '1D', ma_type: 'ema', length: 9 });

  const ids = ctrl.formingSeqTargets().map((t) => t.instanceId);

  assert.deepEqual(ids.sort(), ['chart#1', 'mtf#1', 'same#1'],
    'サーバが計算足ごとに H 形成足で計算するため、除外しない');
});

test('チャート足と同値でも対象（従来どおり）', () => {
  const ctrl = newController('1D');
  addInstance(ctrl, 'mtf#1', { timeframe: '1D', ma_type: 'ema', length: 9 });

  const ids = ctrl.formingSeqTargets().map((t) => t.instanceId);

  assert.deepEqual(ids, ['mtf#1'], 'チャート足と同値なら投影は起きない＝対象で良い');
});

test('params 未指定（従来の指標）は従来どおり対象', () => {
  const ctrl = newController('5m');
  addInstance(ctrl, 'plain#1', { ma_type: 'ema', length: 9 });

  assert.deepEqual(ctrl.formingSeqTargets().map((t) => t.instanceId), ['plain#1']);
});

// ---- 一括リビール（ISSUE-158 ②）の送信も同一規約に従う（ISSUE-288 の本体） ----
//
// 実 UI で検出: リプレイの一括リビール経路だけが `computeTimeframe` を載せず、variant スコープも
//   掛けずに送っていた。上位足計算の指標がチャート足で計算され、確定時に描いた投影済みの階段を
//   上書きして「上位足指標が消える」ように見えた（実測: 1D 計算 EMA が 66,2xx＝5m 値で描画）。

test('一括リビールの compute も計算.時間足と variant スコープを載せる', async () => {
  const ctrl = newController('5m');
  addInstance(ctrl, 'mtf#1', { timeframe: '1D', ma_type: 'ema', length: 9, source: 'close' });
  const sent = [];
  ctrl._compute = { compute: async (req) => { sent.push(req); return { ok: true, generation: 0, series: [] }; } };
  ctrl._validateSeriesNames = (series) => series;
  ctrl._revealTargets = () => [...ctrl._state.applied];

  await ctrl.buildRevealBase(1786133400, 1500);

  assert.equal(sent.length, 1);
  assert.equal(sent[0].computeTimeframe, '1D', '計算.時間足が載っていない（投影されない）');
  assert.equal('timeframe' in sent[0].params, true, 'params の時間足は従来どおり送る');
  assert.equal(sent[0].timeframe, '5m', 'timeframe はチャート足');
});

// ---- 足内一括計算の対象も計算.時間足を申告する（ISSUE-291） ----
//
// 対象に含めるだけでは足りない。要求へ `computeTimeframe` を載せなければサーバは
//   チャート足で計算する（実 UI 実測: 足内の値が 5m の値のままリビール値と段差になった）。

test('対象は計算.時間足を申告する（未指定・chart は undefined）', () => {
  const ctrl = newController('5m');
  addInstance(ctrl, 'mtf#1', { timeframe: '1D', ma_type: 'ema', length: 9 });
  addInstance(ctrl, 'chart#1', { timeframe: 'chart', ma_type: 'ema', length: 9 });
  addInstance(ctrl, 'plain#1', { ma_type: 'ema', length: 9 });

  const byId = new Map(ctrl.formingSeqTargets().map((t) => [t.instanceId, t.computeTimeframe]));

  assert.equal(byId.get('mtf#1'), '1D', 'これが無いとサーバはチャート足で計算する');
  assert.equal(byId.get('chart#1'), undefined);
  assert.equal(byId.get('plain#1'), undefined);
});
