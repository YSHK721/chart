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
  // ISSUE-181: 競合ガードの深さカウンタは host のフィールドではなく RecomputeGate が所有する。
  //   host は recomputeGate() でゲートを渡すのみ（協働子は host フィールドへ直接代入しない）。
  const gate = {
    depth: 0,
    enter() { this.depth += 1; calls.push(['gate-enter']); },
    exit() { this.depth -= 1; calls.push(['gate-exit']); },
  };
  // ISSUE-181: 時間足ロールの状態（現在足・直近本数・candles ローダ・変更購読者）は協働子が
  //   所有する。host 面に残るのは他アクターの持ち物（_datasetRef / _state / _renderer / _el）と
  //   委譲メソッド（recomputeAllApplied / _persistAll / recomputeGate）のみ。
  const host = {
    _datasetRef: 'sample',
    recomputeGate: () => gate,
    _renderer: { setCandles: (c) => calls.push(['setCandles', c]) },
    _state: { uiState: {} },
    _el: overrides._el,
    _persistAll: () => calls.push(['persist']),
    recomputeAllApplied: async (opts) => calls.push(['recompute', opts]),
  };
  const state = {
    timeframe: overrides._timeframe ?? '1D',
    recentBars: overrides._recentBars ?? null,
    loadCandles: overrides._loadCandles ?? null,
  };
  const make = () => {
    const tf = new TimeframeController(host, state);
    if (overrides._timeframeObserver) {
      tf.setObserver(overrides._timeframeObserver);
    }
    return tf;
  };
  return { host, calls, gate, make };
}

// host 面に時間足ロールの状態フィールドが残っていないこと（分割不全の回帰固定・ISSUE-181）。
test('host は時間足ロールの状態フィールドを持たない（所有者は TimeframeController）', () => {
  const { host } = makeHost();
  for (const f of ['_timeframe', '_recentBars', '_loadCandles', '_timeframeObserver', '_recomputeDepth']) {
    assert.equal(f in host, false, `host に ${f} が残っている（状態所有が host のまま）`);
  }
});

test('setTimeframe: 同一時間足は no-op（recompute も persist もしない）', async () => {
  const { calls, make } = makeHost({ _timeframe: '1D' });
  const tf = make();
  await tf.setTimeframe('1D');
  assert.equal(calls.length, 0);
});

test('setTimeframe: 空値は no-op', async () => {
  const { calls, make } = makeHost();
  const tf = make();
  await tf.setTimeframe('');
  assert.equal(calls.length, 0);
});

test('setTimeframe: 新時間足を host に反映し recompute→persist→observer 通知する', async () => {
  const seen = [];
  const { host, calls, make } = makeHost({ _timeframeObserver: (t) => seen.push(t) });
  const tf = make();
  await tf.setTimeframe('1W');
  assert.equal(tf.current(), '1W');
  assert.equal(calls.some((c) => c[0] === 'recompute'), true);
  assert.equal(calls.some((c) => c[0] === 'persist'), true);
  assert.equal(host._state.uiState.timeframe, '1W');
  assert.deepEqual(seen, ['1W']);
});

test('setTimeframe: loadCandles 有り（B方式）は候補を取得し preRender で setCandles を渡す', async () => {
  const candles = [{ time: 1, open: 1, high: 1, low: 1, close: 1 }];
  const { calls, make } = makeHost({ _loadCandles: async () => candles });
  const tf = make();
  await tf.setTimeframe('1W');
  const rc = calls.find((c) => c[0] === 'recompute');
  assert.equal(typeof rc[1].preRender, 'function');
  rc[1].preRender();
  assert.equal(calls.some((c) => c[0] === 'setCandles'), true);
});

test('effectiveTimeframe: chart/未指定は host._timeframe に追従し、特定足はそのまま', () => {
  const { make } = makeHost({ _timeframe: '1D' });
  const tf = make();
  assert.equal(tf.effectiveTimeframe(undefined), '1D');
  assert.equal(tf.effectiveTimeframe('chart'), '1D');
  assert.equal(tf.effectiveTimeframe('1h'), '1h');
});

test('limit: 所有する recentBars を返す（未設定は undefined）', () => {
  assert.equal(makeHost({ _recentBars: 500 }).make().limit(), 500);
  assert.equal(makeHost({ _recentBars: null }).make().limit(), undefined);
});

test('syncButtons: 現在時間足のボタンのみ is-active を付与する', () => {
  const toggled = [];
  const btns = [
    { dataset: { timeframe: '1D' }, classList: { toggle: (c, on) => toggled.push(['1D', on]) } },
    { dataset: { timeframe: '1W' }, classList: { toggle: (c, on) => toggled.push(['1W', on]) } },
  ];
  const { make } = makeHost({ _timeframe: '1W', _el: { timeframeBtns: btns } });
  const tf = make();
  tf.syncButtons();
  assert.deepEqual(toggled, [['1D', false], ['1W', true]]);
});

test('setTimeframe: バッチ全体を RecomputeGate で包む（enter→recompute→exit・深さは 0 へ戻る）', async () => {
  // Arrange
  const { calls, gate, make } = makeHost({ _loadCandles: async () => [] });
  const tf = make();
  // Act
  await tf.setTimeframe('1W');
  // Assert: enter が candles 取得/再計算より先、exit が後、最終深さは 0。
  const names = calls.map((c) => c[0]);
  assert.equal(names[0], 'gate-enter', 'バッチ先頭で enter していない（tick 割り込みガードが効かない）');
  assert.ok(names.indexOf('gate-enter') < names.indexOf('recompute'), 'recompute より前に enter していない');
  assert.ok(names.indexOf('recompute') < names.indexOf('gate-exit'), 'recompute より後に exit していない');
  assert.equal(gate.depth, 0, 'バッチ終了後に深さが 0 へ戻っていない');
});
