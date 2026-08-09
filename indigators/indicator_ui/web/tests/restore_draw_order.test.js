// 復元時の描画順＝宣言順（applied 配列の順）であることの検証（ISSUE-149 の並び順保証）。
//
// 背景（実測 2026-08-09・ライブ :8000）:
//   保存状態は applied=[profit_rsi#1, profit_adx_needle#1] なのに、復元後の画面は
//   ADXNeedle が上・RSI が下だった。`rebuildApplied` は「描画は宣言順に直列化する
//   （完了順に描くと pane の並びが起動ごとに変わる）」と自ら宣言しているが、実装は
//   **自分の compute が解決してから** `drawChain = drawChain.then(...)` を継ぎ足すため、
//   チェーンが組まれる順序＝compute の完了順になっていた（ISSUE-202 の並列化で混入）。
//
//   pane は初回描画時に生成されるため、描画順がそのまま pane の並びになる。したがって
//   この欠陥がある限り、ペインの並び順は起動のたびに変わり、永続化も成立しない。
//
// 構造: Arrange-Act-Assert（AAA）。compute の完了順を宣言順と**逆**にして、描画順が
//   それに引きずられないことを確かめる（同順だと欠陥があっても偶然通ってしまう）。

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { IndicatorStateStore } from '../js/adapter/front/indicator_state_store.js';

// 宣言順 [slow, fast] に対し compute は fast が先に解決する host。
function fakeHost({ order, delays }) {
  const drawn = [];
  return {
    drawn,
    _catalog: { get: (id) => ({ id, category: { nameKey: 'oscillator' } }) },
    _meta: new Map(),
    _datasetRef: 'sample',
    _renderer: { setVisible() {} },
    _isMarketProfile: () => false,
    _paramsObject: (p) => p ?? {},
    _commitLastSeries() {},
    _draw(instanceId) { drawn.push(instanceId); },
    _gatewayAdapter: () => ({
      async compute({ indicatorId }) {
        // 宣言順とは無関係な完了順を作る（宣言の後ろにあるものほど速い）。
        await new Promise((r) => setTimeout(r, delays[indicatorId] ?? 0));
        return { series: [] };
      },
    }),
    _appliedList: order,
  };
}

test('復元の描画順は宣言順（applied 配列の順）で、compute の完了順に引きずられない', async () => {
  // Arrange: 宣言順 slow → fast。compute は fast(0ms) が slow(30ms) より先に解決する。
  const list = [
    { instanceId: 'slow#1', indicatorId: 'slow', variant: 'default', params: {}, visible: true, generation: 0 },
    { instanceId: 'fast#1', indicatorId: 'fast', variant: 'default', params: {}, visible: true, generation: 0 },
  ];
  const host = fakeHost({ order: list, delays: { slow: 30, fast: 0 } });
  const store = new IndicatorStateStore(host);

  // Act
  await store.rebuildApplied(list);

  // Assert: pane は初回描画で生成されるため、描画順がペインの並びそのものになる。
  assert.deepEqual(host.drawn, ['slow#1', 'fast#1'],
    '完了順（fast が先）で描かれている＝ペインの並びが起動ごとに変わる');
});

test('compute が失敗した指標は飛ばし、残りは宣言順を保つ', async () => {
  const list = [
    { instanceId: 'a#1', indicatorId: 'a', variant: 'default', params: {}, visible: true, generation: 0 },
    { instanceId: 'bad#1', indicatorId: 'bad', variant: 'default', params: {}, visible: true, generation: 0 },
    { instanceId: 'c#1', indicatorId: 'c', variant: 'default', params: {}, visible: true, generation: 0 },
  ];
  const host = fakeHost({ order: list, delays: { a: 20, bad: 0, c: 0 } });
  host._gatewayAdapter = () => ({
    async compute({ indicatorId }) {
      await new Promise((r) => setTimeout(r, indicatorId === 'a' ? 20 : 0));
      if (indicatorId === 'bad') {
        throw new Error('compute failed');
      }
      return { series: [] };
    },
  });
  const store = new IndicatorStateStore(host);

  await store.rebuildApplied(list);

  assert.deepEqual(host.drawn, ['a#1', 'c#1']);
});

test('compute は並列に発行する（直列化するのは描画だけ）', async () => {
  // ISSUE-202 の恒久対策（起動所要）を壊していないことを固定する。直列だと合計 60ms、
  //   並列なら最も遅い 1 件（30ms）で済む。余裕を見て 55ms 未満を通過条件にする。
  const list = ['x', 'y'].map((id) => ({
    instanceId: `${id}#1`, indicatorId: id, variant: 'default', params: {}, visible: true, generation: 0,
  }));
  const host = fakeHost({ order: list, delays: { x: 30, y: 30 } });
  const store = new IndicatorStateStore(host);

  const started = process.hrtime.bigint();
  await store.rebuildApplied(list);
  const elapsedMs = Number(process.hrtime.bigint() - started) / 1e6;

  assert.ok(elapsedMs < 55, `compute が直列化している（${Math.round(elapsedMs)}ms）`);
  assert.deepEqual(host.drawn, ['x#1', 'y#1']);
});
