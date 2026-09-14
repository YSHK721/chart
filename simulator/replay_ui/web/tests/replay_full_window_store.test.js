// replay_full_window_store.test.js — 全長系列の単一保管庫（ISSUE-296）の検証。
//
// 設計入力: full_window_series_store.js ＋ ReplayIndicatorController の
//   _computeInstance / _draw（書き手）・storedInstanceIds / renderStored / seedRevealFromStore（読み手）。
//   実測（実 UI）で担保するのは所要時間、本テストが固定するのは**機構**:
//     - 記録するのはライブ（untilTime 未設定）の全長計算だけ（リプレイの per-step・足内は記録しない）
//     - 取り出せるのは入力（チャート足・計算足・variant・params）と**窓**がすべて一致するときだけ
//     - 一致すればモード切替は計算を発行しない（skip 集合＝描画済み集合）
//   構造: AAA・DOM/ネット非依存（全注入）。

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { FullWindowSeriesStore } from '../js/replay/full_window_series_store.js';
import { ReplayIndicatorController } from '../js/adapter/front/replay_indicator_controller.js';
import { TimeframeController } from '../js/adapter/front/timeframe_controller.js';
import { CausalSeriesLedger } from '../js/replay/causal_series_ledger.js';
import { get } from '../js/usecase/catalog.js';

const SERIES = [
  { name: 'MA', kind: 'line', data: [{ time: 10, value: 1 }, { time: 20, value: 2 }] },
];
const CANDLES = [{ time: 10 }, { time: 20 }];

// 保管庫の書き手・読み手だけを通す最小フェイク束（reveal テストと同型の合成）。
function newCtrl({ applied, computeSeries = SERIES, candles = CANDLES } = {}) {
  const ctrl = Object.create(ReplayIndicatorController.prototype);
  ctrl._state = {
    applied: applied ?? [
      { instanceId: 'ma#1', indicatorId: 'moving_averages', variant: 'default', params: {}, visible: true },
    ],
  };
  ctrl._meta = new Map([['ma#1', { def: { id: 'moving_averages' } }]]);
  ctrl._isMarketProfile = () => false;
  ctrl._catalog = { get };
  ctrl._paramsObject = (p) => p ?? {};
  ctrl._defaultVariant = () => 'default';
  ctrl._validateSeriesNames = (series) => series;
  ctrl._datasetRef = 'jp225_tick';
  ctrl._tf = new TimeframeController(ctrl, { timeframe: '1m' });
  ctrl._revealCache = new Map();
  ctrl._revealEpoch = 0;
  ctrl._ledger = new CausalSeriesLedger();
  ctrl._fullSeries = new FullWindowSeriesStore();
  ctrl._untilTime = undefined;
  ctrl._renderer = { getCandles: () => candles };
  ctrl._renderJobs = [];
  ctrl._renderInstance = (job) => { ctrl._renderJobs.push(job); };
  ctrl._series = computeSeries;
  return ctrl;
}

test('保管庫は入力と窓がすべて一致するときだけ返す（1 つでも違えば返さない）', () => {
  const store = new FullWindowSeriesStore();
  store.put('ma#1', { key: 'K', def: { id: 'x' }, params: {}, series: SERIES });

  assert.equal(store.get('ma#1', 'K').series, SERIES, '一致すれば同じ系列');
  assert.equal(store.get('ma#1', 'K2'), null, '鍵が違えば返さない（窓・params の変化）');
  assert.equal(store.get('other#1', 'K'), null, '別インスタンスには渡さない');
});

test('窓トークンはチャート足・本数・末尾足時刻で決まる', () => {
  const ctrl = newCtrl();
  const t = (tf, cs) => ctrl.windowTokenOf(tf, cs);

  assert.equal(t('1m', CANDLES), t('1m', [{ time: 10 }, { time: 20 }]), '同じ窓は同じ');
  assert.notEqual(t('5m', CANDLES), t('1m', CANDLES), 'チャート足が違えば別');
  assert.notEqual(t('1m', [...CANDLES, { time: 30 }]), t('1m', CANDLES), '足が増えれば別');
  assert.equal(t('1m', []), null, '窓が無ければトークンも無い');
});

test('ライブの全長計算は記録し、リプレイの per-step 計算は記録しない', async () => {
  const ctrl = newCtrl();
  const token = ctrl.windowTokenOf('1m', CANDLES);

  ctrl._recordFullSeries('ma#1', { id: 'moving_averages' }, {}, SERIES, token);
  assert.deepEqual([...ctrl.storedInstanceIds(token)], ['ma#1'], 'ライブの全長系列は取り出せる');

  // リプレイ（untilTime 設定）は窓トークンを採らない＝記録されない。
  ctrl._untilTime = 20;
  ctrl._recordFullSeries('ma#1', { id: 'moving_averages' }, {}, SERIES, null);
  assert.deepEqual([...ctrl.storedInstanceIds(token)], ['ma#1'], '記録は上書きも削除もされない');
});

test('窓が動いていれば取り出せない（fail-closed＝古い系列を流用しない）', () => {
  const ctrl = newCtrl();
  ctrl._recordFullSeries('ma#1', { id: 'moving_averages' }, {}, SERIES,
    ctrl.windowTokenOf('1m', CANDLES));

  const moved = ctrl.windowTokenOf('1m', [...CANDLES, { time: 30 }]);
  assert.equal(ctrl.storedInstanceIds(moved).size, 0, '足が確定していれば従来どおり計算へ回す');
  assert.equal(ctrl.renderStored(moved).size, 0);
  assert.equal(ctrl._renderJobs.length, 0, '描画もしない');
});

test('renderStored は保管庫の系列を同期描画し、描けた集合を返す（skip 述語の材料）', () => {
  const ctrl = newCtrl();
  const token = ctrl.windowTokenOf('1m', CANDLES);
  ctrl._recordFullSeries('ma#1', { id: 'moving_averages' }, {}, SERIES, token);

  const drawn = ctrl.renderStored(token);

  assert.deepEqual([...drawn], ['ma#1']);
  assert.equal(ctrl._renderJobs.length, 1);
  assert.equal(ctrl._renderJobs[0].series, SERIES, '保管庫の系列がそのまま描かれる');
  assert.equal(ctrl._renderJobs[0].wantLatest, false);
});

test('一括リビール基底も保管庫から埋まる（HTTP を発行しない）', () => {
  const ctrl = newCtrl();
  const token = ctrl.windowTokenOf('1m', CANDLES);
  ctrl._recordFullSeries('ma#1', { id: 'moving_averages' }, {}, SERIES, token);
  assert.equal(ctrl.revealNeedsBuild(), true, '前提: 基底は未構築');

  ctrl.seedRevealFromStore(token);

  assert.equal(ctrl.hasRevealFor('ma#1'), true);
  assert.equal(ctrl.revealNeedsBuild(), false, '構築（計算）は不要になる');
  assert.deepEqual(ctrl._revealCache.get('ma#1').series, SERIES);
});

test('リビール基底を持つ指標は renderStored が描かない（revealTo と二重に描かない）', () => {
  const ctrl = newCtrl();
  const token = ctrl.windowTokenOf('1m', CANDLES);
  ctrl._recordFullSeries('ma#1', { id: 'moving_averages' }, {}, SERIES, token);
  ctrl.seedRevealFromStore(token);

  const drawn = ctrl.renderStored(token);

  assert.equal(drawn.size, 0);
  assert.equal(ctrl._renderJobs.length, 0);
});

test('params が変われば記録は手放される（_invalidateReveal）', () => {
  const ctrl = newCtrl();
  const token = ctrl.windowTokenOf('1m', CANDLES);
  ctrl._recordFullSeries('ma#1', { id: 'moving_averages' }, {}, SERIES, token);

  ctrl._invalidateReveal('ma#1');

  assert.equal(ctrl.storedInstanceIds(token).size, 0);
});
