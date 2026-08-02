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
    _renderer: {
      setCandles: (c) => calls.push(['setCandles', c]),
      // ISSUE-196: 旧足の指標系列を空にする入口（setCandles と同一同期ブロックで呼ばれる）。
      clearInstanceData: (id) => calls.push(['clearInstanceData', id]),
    },
    _state: { uiState: {}, applied: overrides._applied ?? [] },
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

// ISSUE-196（抜本対策・2026-07-29）: 旧仕様は「全指標 compute 完了後の同期バッチで setCandles」
//   （preRender 渡し）だった。実測で 2 つの不具合の原因と確定したため設計を変更した:
//   (a) 切替所要が最も遅い指標 compute に律速（実測 5.63 秒）、(b) 差し替え時点で旧足の指標系列が
//   残るため lwc が `Value is null` を throw しバッチが中断・指標が旧足で固着。
//   新仕様: candles 取得直後に「指標系列の空化 → setCandles」を同一同期ブロックで実行し、
//   recomputeAllApplied には preRender を渡さない（指標はフェーズ2 が描く）。
test('setTimeframe: loadCandles 有り（B方式）は取得直後に setCandles し preRender は渡さない', async () => {
  const candles = [{ time: 1, open: 1, high: 1, low: 1, close: 1 }];
  const { calls, make } = makeHost({ _loadCandles: async () => candles });
  const tf = make();
  await tf.setTimeframe('1W');
  assert.equal(calls.some((c) => c[0] === 'setCandles'), true, '取得直後に setCandles する');
  const rc = calls.find((c) => c[0] === 'recompute');
  assert.equal(rc[1].preRender, null, 'preRender は渡さない（メイン系列は既に差し替え済み）');
  // 順序: setCandles → recompute（旧仕様は recompute の内側で setCandles だった）。
  assert.ok(calls.findIndex((c) => c[0] === 'setCandles') < calls.findIndex((c) => c[0] === 'recompute'));
});

test('setTimeframe: 適用済み指標の系列を空にしてから setCandles する（lwc 不変条件・ISSUE-196）', async () => {
  const candles = [{ time: 1, open: 1, high: 1, low: 1, close: 1 }];
  const { calls, make } = makeHost({
    _loadCandles: async () => candles,
    _applied: [{ instanceId: 'ma_marod#1' }, { instanceId: 'btlm_trail#1' }],
  });
  const tf = make();
  await tf.setTimeframe('1W');
  const cleared = calls.filter((c) => c[0] === 'clearInstanceData').map((c) => c[1]);
  assert.deepEqual(cleared, ['ma_marod#1', 'btlm_trail#1'], '全適用指標の系列を空にする');
  const lastClear = calls.map((c) => c[0]).lastIndexOf('clearInstanceData');
  const setIdx = calls.map((c) => c[0]).indexOf('setCandles');
  assert.ok(lastClear < setIdx, '空化はローソク差し替えより前（同一同期ブロック内）');
});

test('setTimeframe: candles 取得が空なら setCandles も空化も行わない（メイン系列据え置き）', async () => {
  const { calls, make } = makeHost({
    _loadCandles: async () => [],
    _applied: [{ instanceId: 'ma_marod#1' }],
  });
  const tf = make();
  await tf.setTimeframe('1W');
  assert.equal(calls.some((c) => c[0] === 'setCandles'), false);
  assert.equal(calls.some((c) => c[0] === 'clearInstanceData'), false);
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

// ISSUE-231（リプレイの非同時描画・二重実行の恒久解消）: 時間足切替の「反映役」seam。
//   ライブの反映（ISSUE-196: ローソク先行 → 指標は compute 完了後）はリプレイの不変条件
//   （その時点 T のローソクと指標が同時に現れる）に反する。反映役が登録されているときは
//   ライブ反映を行わず委譲し、時間足の確定（_timeframe / 永続化 / 購読者通知）は共通のまま保つ。
test('setApplier: 反映役ありのとき candles 取得・setCandles・recompute を行わず委譲する（ISSUE-231）', async () => {
  const seen = [];
  const { calls, make } = makeHost({
    _loadCandles: async () => { calls.push(['loadCandles']); return [{ time: 1, open: 1, high: 1, low: 1, close: 1 }]; },
    _applied: [{ instanceId: 'ma_marod#1' }],
  });
  const tf = make();
  tf.setApplier(async (t) => seen.push(t));
  await tf.setTimeframe('1W');
  assert.deepEqual(seen, ['1W'], '反映役へ新時間足で委譲していない');
  assert.equal(calls.some((c) => c[0] === 'loadCandles'), false, 'ライブ経路の candles 取得が走っている（二重取得）');
  assert.equal(calls.some((c) => c[0] === 'setCandles'), false, 'ライブ経路のローソク先行差替えが走っている（非同時描画の原因）');
  assert.equal(calls.some((c) => c[0] === 'clearInstanceData'), false, 'ライブ経路の指標空化が走っている');
  assert.equal(calls.some((c) => c[0] === 'recompute'), false, 'ライブ経路の全指標再計算が走っている（二重計算）');
});

test('setApplier: 反映役ありでも時間足の確定・永続化・購読者通知は共通で行う（ISSUE-231）', async () => {
  const seen = [];
  const { host, calls, gate, make } = makeHost({ _timeframeObserver: (t) => seen.push(t) });
  const tf = make();
  tf.setApplier(async () => {});
  await tf.setTimeframe('1W');
  assert.equal(tf.current(), '1W');
  assert.equal(host._state.uiState.timeframe, '1W');
  assert.equal(calls.some((c) => c[0] === 'persist'), true);
  assert.deepEqual(seen, ['1W']);
  assert.equal(gate.depth, 0, '委譲後もゲート深さが 0 へ戻っていない');
});

test('setApplier(null): 既定のライブ経路へ戻る（登録解除・ISSUE-231）', async () => {
  const { calls, make } = makeHost({ _loadCandles: async () => [{ time: 1, open: 1, high: 1, low: 1, close: 1 }] });
  const tf = make();
  tf.setApplier(async () => { throw new Error('反映役が解除されていない'); });
  tf.setApplier(null);
  await tf.setTimeframe('1W');
  assert.equal(calls.some((c) => c[0] === 'setCandles'), true);
  assert.equal(calls.some((c) => c[0] === 'recompute'), true);
});

test('setApplier: 反映役が投げてもゲートは解放される（ISSUE-231）', async () => {
  const { gate, make } = makeHost();
  const tf = make();
  tf.setApplier(async () => { throw new Error('boom'); });
  await assert.rejects(() => tf.setTimeframe('1W'), /boom/);
  assert.equal(gate.depth, 0, '例外時にゲートが解放されていない');
});
