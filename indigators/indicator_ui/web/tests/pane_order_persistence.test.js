// ペイン並び順の永続化（ユーザー指示「永続化しろ」2026-08-09）の仕様検証。
//
// 設計入力（実コードで確認した事実）:
//   復元は `IndicatorStateStore._restoreRun` → `rebuildApplied(state.applied)` が **applied 配列の
//   順に**指標を再適用し、`SeriesDrawer._ensurePane` が pane 指標 1 インスタンスにつき pane を
//   1 枚ずつ末尾へ追加する。つまり **並び順の表現は「applied 配列の順序」1 つだけ**が既に存在する。
//
//   よって永続化の抜本策は保存スキーマを増やすことではなく、ドラッグで並べ替えたときに
//   applied 配列の順序を実際のペイン順へ一致させること。順序の第 2 の表現を作らないので、
//   復元手順も保存キーも増えず、applied を保存するチャートテンプレートも自動で追従する。
//
// 構造: Arrange-Act-Assert（AAA）。実 DOM・lightweight-charts 非依存（Fake 注入）。

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { ChartRenderer } from '../js/adapter/front/chart_renderer.js';
import { emptyState, reorderApplied } from '../js/usecase/facade.js';
import { AppliedInstance } from '../js/domain/domain_models.js';

// ---- reorderApplied（純関数・状態遷移） ---- //

function stateWith(ids) {
  const state = emptyState();
  state.applied = ids.map((id, i) => new AppliedInstance({
    indicatorId: id.split('#')[0], variant: null, params: {}, visible: true,
    generation: 0, seq: i + 1, createdAt: i + 1,
  }));
  // instanceId は AppliedInstance が indicatorId#seq で導出する実装のため、検証では
  //   実際に生成された id を使う（テストが id の作り方に依存しないようにする）。
  return state;
}

function idsOf(state) {
  return state.applied.map((i) => i.instanceId);
}

test('reorderApplied: 指定された順序どおりに並べ替える', () => {
  const state = stateWith(['rsi', 'adx', 'vol']);
  const [a, b, c] = idsOf(state);

  const next = reorderApplied(state, [c, a, b]);

  assert.deepEqual(idsOf(next), [c, a, b]);
});

test('reorderApplied: 指定に無いインスタンス（overlay 指標）は元の位置から動かさない', () => {
  // 価格ペインの overlay 指標は pane を持たず並べ替えの対象外。位置が動くと価格ペインの
  //   凡例行の並び（＝適用順）が壊れるため、枠を固定して pane 指標だけを入れ替える。
  const state = stateWith(['ma', 'rsi', 'sma', 'adx']);
  const [ma, rsi, sma, adx] = idsOf(state);

  const next = reorderApplied(state, [adx, rsi]);   // pane 指標は rsi / adx のみ

  assert.deepEqual(idsOf(next), [ma, adx, sma, rsi], 'overlay（ma / sma）の添字が動いている');
});

test('reorderApplied: 未知の instanceId は無視し、指定から漏れた要素も落とさない', () => {
  // 指定に挙がった要素（rsi / vol）だけがその枠内で並べ替わり、挙がらなかった adx は
  //   自分の添字に留まる。未知 id は数にも入らない（枠を 1 つずらしてしまわない）。
  const state = stateWith(['rsi', 'adx', 'vol']);
  const [rsi, adx, vol] = idsOf(state);

  const next = reorderApplied(state, [vol, 'nonexistent#9', rsi]);

  assert.deepEqual(idsOf(next), [vol, adx, rsi]);
  assert.equal(next.applied.length, 3, '要素を落としてはいけない');
});

test('reorderApplied: 同じ順序を渡しても状態は変わらない（冪等）', () => {
  const state = stateWith(['rsi', 'adx']);
  const ids = idsOf(state);

  const next = reorderApplied(state, ids);

  assert.deepEqual(idsOf(next), ids);
});

test('reorderApplied: 元の state を破壊しない（純関数）', () => {
  const state = stateWith(['rsi', 'adx']);
  const [rsi, adx] = idsOf(state);

  reorderApplied(state, [adx, rsi]);

  assert.deepEqual(idsOf(state), [rsi, adx]);
});

// ---- ChartRenderer.paneOrderInstanceIds（現在のペイン順） ---- //

function fakeChartWithPanes(count) {
  const panes = [];
  for (let i = 0; i < count; i += 1) {
    const pane = {
      paneIndex() { return panes.indexOf(pane); },
      getHeight() { return i === 0 ? 400 : 160; },
      moveTo(to) {
        const from = panes.indexOf(pane);
        panes.splice(from, 1);
        panes.splice(to, 0, pane);
      },
    };
    panes.push(pane);
  }
  return {
    panes() { return panes; },
    subscribeCrosshairMove() {},
  };
}

function rendererWith(count) {
  const chart = fakeChartWithPanes(count);
  const renderer = new ChartRenderer({ chart, mainSeries: {}, lwc: {} });
  return { chart, renderer };
}

test('paneOrderInstanceIds: pane 指標だけを現在のペイン順で返す（overlay は含めない）', () => {
  const { chart, renderer } = rendererWith(3);
  const panes = chart.panes();
  renderer._instances.set('ma#1', { lines: new Map(), visible: true, pane: null });      // overlay
  renderer._instances.set('rsi#1', { lines: new Map(), visible: true, pane: panes[1] });
  renderer._instances.set('adx#1', { lines: new Map(), visible: true, pane: panes[2] });

  assert.deepEqual(renderer.paneOrderInstanceIds(), ['rsi#1', 'adx#1']);
});

test('paneOrderInstanceIds: 並べ替えた後は新しいペイン順を返す', () => {
  const { chart, renderer } = rendererWith(3);
  const panes = chart.panes();
  renderer._instances.set('rsi#1', { lines: new Map(), visible: true, pane: panes[1] });
  renderer._instances.set('adx#1', { lines: new Map(), visible: true, pane: panes[2] });

  renderer.movePane(1, 2);

  assert.deepEqual(renderer.paneOrderInstanceIds(), ['adx#1', 'rsi#1']);
});

test('paneOrderInstanceIds: 既に外されたペイン（paneIndex()=-1）は含めない', () => {
  // 除去途中の slot が混じると、存在しない順序を永続化して復元を壊す。
  const { chart, renderer } = rendererWith(2);
  const panes = chart.panes();
  const detached = { paneIndex() { return -1; } };
  renderer._instances.set('rsi#1', { lines: new Map(), visible: true, pane: panes[1] });
  renderer._instances.set('gone#1', { lines: new Map(), visible: true, pane: detached });

  assert.deepEqual(renderer.paneOrderInstanceIds(), ['rsi#1']);
});

// ---- 並び順の変化 → 保存（結線） ---- //

test('movePane が成立したら、新しいペイン順を購読者へ通知する', () => {
  const { chart, renderer } = rendererWith(3);
  const panes = chart.panes();
  renderer._instances.set('rsi#1', { lines: new Map(), visible: true, pane: panes[1] });
  renderer._instances.set('adx#1', { lines: new Map(), visible: true, pane: panes[2] });
  const seen = [];
  renderer.setPaneOrderObserver((ids) => seen.push(ids));

  renderer.movePane(1, 2);

  assert.deepEqual(seen, [['adx#1', 'rsi#1']]);
});

test('movePane が拒否されたら通知しない（保存が実態から乖離しない）', () => {
  const { chart, renderer } = rendererWith(3);
  const panes = chart.panes();
  renderer._instances.set('rsi#1', { lines: new Map(), visible: true, pane: panes[1] });
  renderer._instances.set('adx#1', { lines: new Map(), visible: true, pane: panes[2] });
  const seen = [];
  renderer.setPaneOrderObserver((ids) => seen.push(ids));

  renderer.movePane(0, 2);   // 価格ペインは動かせない
  renderer.movePane(1, 1);   // 同一

  assert.deepEqual(seen, []);
});

test('購読者が未設定でも movePane は成立する（後方互換）', () => {
  const { renderer } = rendererWith(3);

  assert.equal(renderer.movePane(1, 2), true);
});

// ---- controller: 並び順の確定と永続化 ---- //

test('applyPaneOrder は applied を並べ替えて永続化する（新しい保存キーを作らない）', async () => {
  const { IndicatorController } = await import('../js/adapter/front/indicator_controller.js');
  const saved = [];
  const controller = Object.create(IndicatorController.prototype);
  controller._state = stateWith(['rsi', 'adx']);
  const [rsi, adx] = idsOf(controller._state);
  controller._persistAll = () => saved.push(idsOf(controller._state));

  controller.applyPaneOrder([adx, rsi]);

  assert.deepEqual(idsOf(controller._state), [adx, rsi]);
  assert.deepEqual(saved, [[adx, rsi]], '永続化が呼ばれていない／回数が違う');
});

test('applyPaneOrder は空・非配列を無視する（保存を汚さない）', async () => {
  const { IndicatorController } = await import('../js/adapter/front/indicator_controller.js');
  const saved = [];
  const controller = Object.create(IndicatorController.prototype);
  controller._state = stateWith(['rsi', 'adx']);
  const before = idsOf(controller._state);
  controller._persistAll = () => saved.push(idsOf(controller._state));

  controller.applyPaneOrder([]);
  controller.applyPaneOrder(null);

  assert.deepEqual(idsOf(controller._state), before);
  assert.deepEqual(saved, []);
});
