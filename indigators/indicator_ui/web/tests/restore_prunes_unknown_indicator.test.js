// ISSUE-245: 復元時に「カタログに存在しない指標」の保存済みインスタンスを状態から落とす。
//
// 真因（ISSUE-244 で tickvol_updown を UI から外した直後に実 UI で再現）:
//   `rebuildApplied` は def 無しを compute/描画から除外するが **状態からは落とさない**ため、
//   `_renderLegend` が `_state.applied` をそのまま描いて「系列もデータも無い凡例行」が残る
//   （ラベルは def が無いので raw な indicatorId が出る）。在席の権威はカタログ 1 つであり、
//   状態がそれと独立にカタログ外の指標を保持しているのが不整合そのもの。
//
// 本テストが固定すること:
//   1. 復元後の `_state.applied` にカタログ外の指標が残らない（凡例に出ない）。
//   2. 落とした結果が永続化へ書き戻される（次回起動でゴミが再登場しない）。
//   3. カタログにある指標は落とさない・保存の書き戻しも起こさない（不要な副作用を作らない）。
//
// 構造: Arrange-Act-Assert（AAA）。ports/renderer は最小 Fake・DOM 非依存。

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { IndicatorController } from '../js/adapter/front/indicator_controller.js';
import { get } from '../js/usecase/catalog.js';

const noop = () => {};

function instance(indicatorId, instanceId) {
  return {
    instanceId,
    indicatorId,
    variant: 'default',
    params: [],
    visible: true,
    generation: 0,
    styles: {},
  };
}

function controllerWith(saved) {
  const saves = [];
  const ctrl = new IndicatorController({
    catalog: { listIndicators: () => [], get },
    compute: { compute: async (req) => ({ ok: true, generation: req.generation ?? 0, series: [] }) },
    persistence: {
      loadApplied: () => saved,
      saveApplied: (list) => saves.push(list),
      loadFavorites: () => [], saveFavorites: noop,
      loadUiState: () => ({}), saveUiState: noop,
      nextSeq: () => 1,
    },
    renderer: {
      renderLine: noop, renderHorizontal: noop, setData: noop, setVisible: noop,
      remove: noop, setCandles: noop,
    },
    document: null, mode: 'b', datasetRef: 'jp225_m1', timeframe: '5m', recentBars: 1500,
  });
  return { ctrl, saves };
}

test('復元: カタログに無い指標の保存済みインスタンスは状態に残らない', async () => {
  // Arrange: 実在する指標 1 件 + 撤去済み指標 1 件（ISSUE-244 の tickvol_updown を想定）。
  const { ctrl } = controllerWith([
    instance('tickvol', 'tickvol#1'),
    instance('tickvol_updown', 'tickvol_updown#1'),
  ]);

  // Act
  await ctrl.restore();

  // Assert: 凡例の描画元（_state.applied）から消えている。
  const ids = ctrl._state.applied.map((i) => i.indicatorId);
  assert.deepEqual(ids, ['tickvol']);
});

test('復元: 落とした結果を永続化へ書き戻す（次回起動でゴミが再登場しない）', async () => {
  // Arrange
  const { ctrl, saves } = controllerWith([
    instance('tickvol', 'tickvol#1'),
    instance('tickvol_updown', 'tickvol_updown#1'),
  ]);

  // Act
  await ctrl.restore();

  // Assert: 除去直後に 1 度だけ保存され、内容は残した分だけ。
  assert.ok(saves.length >= 1, '除去したのに saveApplied が呼ばれていない');
  assert.deepEqual(saves[0].map((i) => i.indicatorId), ['tickvol']);
});

test('復元: すべてカタログにある場合は落とさず、書き戻しもしない', async () => {
  // Arrange
  const { ctrl, saves } = controllerWith([
    instance('tickvol', 'tickvol#1'),
    instance('moving_averages', 'moving_averages#1'),
  ]);

  // Act
  await ctrl.restore();

  // Assert: 件数不変・不要な永続化を発生させない（無関係な書込みで他の状態を壊さない）。
  assert.equal(ctrl._state.applied.length, 2);
  assert.equal(saves.length, 0);
});
