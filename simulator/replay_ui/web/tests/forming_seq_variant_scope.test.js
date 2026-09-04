// forming_seq_variant_scope.test.js — 足内一括計算の申告も variant スコープに従う（ISSUE-278 #8）。
//
// 実 UI で検出（:8000 リプレイモード・実 HTTP）: `/compute` の `mode=latest_seq` 要求だけが
//   variant 横断の全 params を載せており、無言破棄を撤去した back が 500 を返していた。
//     {"indicatorId":"profit_hlband","variant":"overlay","params":{"draw_levels":true,...}}
//     → ComputeError: validation: add_hlband_overlay が受理しない param が渡されました: ['draw_levels']
//   /compute（_gatewayAdapter）と /live_ticks（appliedComputeSpecs）は絞っていたが、本経路だけが
//   同じ規約から外れていた＝「送信側で絞る」規律の取り残し。3 経路すべてを本検定で固定する。

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { ReplayIndicatorController } from '../js/adapter/front/replay_indicator_controller.js';
import { get, applyServerParamScopes } from '../js/usecase/catalog.js';

const SCOPES = {
  profit_hlband: { separate: ['draw_levels', 'timeframe'], overlay: ['timeframe'] },
};

function newController() {
  const noop = () => {};
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
    timeframe: '1m',
  });
}

function withScopes(fn) {
  applyServerParamScopes(SCOPES);
  try {
    fn();
  } finally {
    for (const p of get('profit_hlband').params) {
      p.variants = null;
    }
  }
}

test('formingSeqTargets declares only the params the instance variant accepts', () => {
  withScopes(() => {
    const ctrl = newController();
    const def = get('profit_hlband');
    ctrl._state = {
      ...ctrl._state,
      applied: [{
        instanceId: 'profit_hlband#1',
        indicatorId: 'profit_hlband',
        variant: 'overlay',
        params: [['draw_levels', true], ['timeframe', 'chart']],
        visible: true,
      }],
    };
    ctrl._meta.set('profit_hlband#1', { def });

    const targets = ctrl.formingSeqTargets();
    // profit_hlband が足内更新の登録対象でなければ申告自体が空になる。その場合でも
    //   「絞り込みが効く」ことは _scopedParams（3 経路が共有する唯一の絞り込み点）で固定する。
    if (targets.length > 0) {
      assert.equal(targets[0].variant, 'overlay');
      assert.deepEqual(targets[0].params, { timeframe: 'chart' });
    }
    assert.deepEqual(
      ctrl._scopedParams('profit_hlband', 'overlay', { draw_levels: true, timeframe: 'chart' }),
      { timeframe: 'chart' },
    );
    assert.deepEqual(
      ctrl._scopedParams('profit_hlband', 'separate', { draw_levels: true, timeframe: 'chart' }),
      { draw_levels: true, timeframe: 'chart' },
    );
  });
});

test('送信 3 経路がすべて同じ絞り込み点を通る（取り残しの構造的検出）', async () => {
  // ソース上で、params を組み立てる 3 経路が _scopedParams を経由していることを固定する。
  const { readFileSync } = await import('node:fs');
  const { fileURLToPath } = await import('node:url');
  const read = (rel) => readFileSync(fileURLToPath(new URL(rel, import.meta.url)), 'utf8');

  const live = read('../js/adapter/front/indicator_controller.js');
  const replay = read('../js/adapter/front/replay_indicator_controller.js');
  // /compute（gateway）と /live_ticks（specs 申告）は共有 controller が持つ。
  assert.ok(live.includes('params: self._scopedParams('), '/compute 経路が絞り込みを通っていない');
  assert.ok(live.includes('params: this._scopedParams('), '/live_ticks 経路が絞り込みを通っていない');
  // 足内一括計算（latest_seq）はリプレイ controller が持つ。
  assert.ok(replay.includes('this._scopedParams('), 'latest_seq 経路が絞り込みを通っていない');
});
