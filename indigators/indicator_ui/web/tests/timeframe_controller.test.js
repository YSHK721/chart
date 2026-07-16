// timeframe_controller.test.js — 時間足取得・切替コントローラ（A3）の単体テスト。
//
// 対象: js/adapter/front/timeframe_controller.js（ISSUE-094 🔴-4 抽出）。
//   indicator_controller.js（A6）へ混在していた時間足（A3）の関心事——setTimeframe（candles 再取得・
//   差替＋全指標再計算）・時間足ボタン同期・_gatewayAdapter の timeframe/limit 注入——を host 参照で
//   操作する協働子へ外出しした対象。挙動は抽出前の controller メソッドと byte 等価。

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { TimeframeController } from '../js/adapter/front/timeframe_controller.js';

function makeHost(overrides = {}) {
  const calls = [];
  const host = {
    _timeframe: overrides._timeframe ?? '1D',
    _recentBars: overrides._recentBars ?? null,
    _datasetRef: 'sample',
    _recomputeDepth: 0,
    _loadCandles: overrides._loadCandles ?? null,
    _renderer: { setCandles: (c) => calls.push(['setCandles', c]) },
    _state: { uiState: {} },
    _el: overrides._el,
    _persistAll: () => calls.push(['persist']),
    _timeframeObserver: overrides._timeframeObserver ?? null,
    recomputeAllApplied: async (opts) => calls.push(['recompute', opts]),
  };
  return { host, calls };
}

test('setTimeframe: 同一時間足は no-op（recompute も persist もしない）', async () => {
  const { host, calls } = makeHost({ _timeframe: '1D' });
  const tf = new TimeframeController(host);
  await tf.setTimeframe('1D');
  assert.equal(calls.length, 0);
});

test('setTimeframe: 空値は no-op', async () => {
  const { host, calls } = makeHost();
  const tf = new TimeframeController(host);
  await tf.setTimeframe('');
  assert.equal(calls.length, 0);
});

test('setTimeframe: 新時間足を host に反映し recompute→persist→observer 通知する', async () => {
  const seen = [];
  const { host, calls } = makeHost({ _timeframeObserver: (t) => seen.push(t) });
  const tf = new TimeframeController(host);
  await tf.setTimeframe('1W');
  assert.equal(host._timeframe, '1W');
  assert.equal(calls.some((c) => c[0] === 'recompute'), true);
  assert.equal(calls.some((c) => c[0] === 'persist'), true);
  assert.equal(host._state.uiState.timeframe, '1W');
  assert.deepEqual(seen, ['1W']);
});

test('setTimeframe: loadCandles 有り（B方式）は候補を取得し preRender で setCandles を渡す', async () => {
  const candles = [{ time: 1, open: 1, high: 1, low: 1, close: 1 }];
  const { host, calls } = makeHost({ _loadCandles: async () => candles });
  const tf = new TimeframeController(host);
  await tf.setTimeframe('1W');
  const rc = calls.find((c) => c[0] === 'recompute');
  assert.equal(typeof rc[1].preRender, 'function');
  rc[1].preRender();
  assert.equal(calls.some((c) => c[0] === 'setCandles'), true);
});

test('effectiveTimeframe: chart/未指定は host._timeframe に追従し、特定足はそのまま', () => {
  const { host } = makeHost({ _timeframe: '1D' });
  const tf = new TimeframeController(host);
  assert.equal(tf.effectiveTimeframe(undefined), '1D');
  assert.equal(tf.effectiveTimeframe('chart'), '1D');
  assert.equal(tf.effectiveTimeframe('1h'), '1h');
});

test('limit: host._recentBars を返す（未設定は undefined）', () => {
  const a = makeHost({ _recentBars: 500 });
  assert.equal(new TimeframeController(a.host).limit(), 500);
  const b = makeHost({ _recentBars: null });
  assert.equal(new TimeframeController(b.host).limit(), undefined);
});

test('syncButtons: 現在時間足のボタンのみ is-active を付与する', () => {
  const toggled = [];
  const btns = [
    { dataset: { timeframe: '1D' }, classList: { toggle: (c, on) => toggled.push(['1D', on]) } },
    { dataset: { timeframe: '1W' }, classList: { toggle: (c, on) => toggled.push(['1W', on]) } },
  ];
  const { host } = makeHost({ _timeframe: '1W', _el: { timeframeBtns: btns } });
  const tf = new TimeframeController(host);
  tf.syncButtons();
  assert.deepEqual(toggled, [['1D', false], ['1W', true]]);
});
