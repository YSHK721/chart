// param_variant_scope.test.js — variant ごとの受理 param（ISSUE-278 #8）。
//
// 背景（実測・実 HTTP :8000）: param 既定値の宣言粒度が compute_id、実契約（add_* のシグネチャ）が
//   variant だったため、差分を back が無言で捨てていた。UI は効かないコントロールを出し続けた。
//     profit_band variant=global  … normalize / window / atr_period / min_obs を動かしても応答 byte 同一
//     profit_band variant=robust  … require_full が同上
//     profit_hlband variant=overlay … draw_levels が同上
//   （逆に受理する variant では応答が変わる＝識別力あり）。
//
// 是正: back が variant ごとの受理集合を GET /catalog の paramScopes として配信し、front は
//   (a) 受理しない param の行を出さない (b) /compute・/live_ticks へ送らない。
//
// 構造: Arrange-Act-Assert（AAA）。DOM/ネット非依存（全注入）。

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { get, applyServerParamScopes, scopedParams } from '../js/usecase/catalog.js';
import { computeVisible } from '../js/usecase/form_model.js';
import { IndicatorController } from '../js/adapter/front/indicator_controller.js';

// back（call_binding._TABLE）の宣言に対応する配信形。
const SCOPES = {
  profit_band: {
    global: ['probabilities', 'buckets', 'legend', 'require_full', 'timeframe'],
    robust: ['probabilities', 'buckets', 'legend', 'normalize', 'window', 'atr_period', 'min_obs', 'timeframe'],
  },
  profit_hlband: {
    separate: ['draw_levels', 'timeframe'],
    overlay: ['timeframe'],
  },
};

// レジストリはモジュール共有のため、overlay を掛けた試験の後は必ず戻す。
function withScopes(fn) {
  applyServerParamScopes(SCOPES);
  try {
    fn();
  } finally {
    for (const id of Object.keys(SCOPES)) {
      for (const p of get(id).params) {
        p.variants = null;
      }
    }
  }
}

// --------------------------------------------------------------------------- #
// catalog: 配信された受理集合の overlay
// --------------------------------------------------------------------------- #

test('applyServerParamScopes marks variant-specific params and leaves shared params unscoped', () => {
  withScopes(() => {
    const byName = new Map(get('profit_band').params.map((p) => [p.name, p]));
    assert.deepEqual(byName.get('require_full').variants, ['global']);
    assert.deepEqual(byName.get('normalize').variants, ['robust']);
    assert.deepEqual(byName.get('min_obs').variants, ['robust']);
    // 全 variant が受理する param は null（＝variant 非依存・従来どおり常時表示）。
    assert.equal(byName.get('probabilities').variants, null);
    assert.equal(byName.get('legend').variants, null);
  });
});

test('applyServerParamScopes is a no-op for missing / malformed payloads (旧サーバ前方互換)', () => {
  assert.equal(applyServerParamScopes(undefined), 0);
  assert.equal(applyServerParamScopes(null), 0);
  assert.equal(applyServerParamScopes({ unknown_indicator: { default: ['x'] } }), 0);
  assert.equal(get('profit_band').params.find((p) => p.name === 'normalize').variants, null);
});

// --------------------------------------------------------------------------- #
// form_model: 受理しない param は UI に出さない
// --------------------------------------------------------------------------- #

test('computeVisible hides params the selected variant does not accept', () => {
  withScopes(() => {
    const def = get('profit_band');
    const globalVis = computeVisible(def, {}, { variant: 'global' });
    assert.equal(globalVis.require_full, true);
    for (const name of ['normalize', 'window', 'atr_period', 'min_obs']) {
      assert.equal(globalVis[name], false, `${name} は global では効かない＝出さない`);
    }
    const robustVis = computeVisible(def, {}, { variant: 'robust' });
    assert.equal(robustVis.require_full, false);
    assert.equal(robustVis.normalize, true);
    // 共有 param はどちらでも出る。
    assert.equal(globalVis.probabilities, true);
    assert.equal(robustVis.probabilities, true);
  });
});

test('computeVisible without a variant context keeps the previous behaviour (全表示)', () => {
  withScopes(() => {
    const vis = computeVisible(get('profit_band'), {}, {});
    assert.equal(vis.normalize, true);
    assert.equal(vis.require_full, true);
  });
});

test('computeVisible still honours conditionalVisible for accepted params', () => {
  withScopes(() => {
    // atr_period は robust が受理し、かつ conditionalEnable（normalize==atr）で制御される。
    //   variant スコープは conditionalVisible/Enable と直交する（両立を固定）。
    const vis = computeVisible(get('profit_band'), { normalize: 'return' }, { variant: 'robust' });
    assert.equal(vis.atr_period, true);
  });
});

// --------------------------------------------------------------------------- #
// scopedParams: 送信側の絞り込み
// --------------------------------------------------------------------------- #

test('scopedParams drops keys the variant does not accept and keeps the rest', () => {
  withScopes(() => {
    const def = get('profit_band');
    const stored = {
      probabilities: [0.95], buckets: ['nOH'], legend: false, require_full: true,
      normalize: 'atr', window: 'expanding', atr_period: 14, min_obs: 30, timeframe: 'chart',
    };
    assert.deepEqual(scopedParams(def, 'global', stored), {
      probabilities: [0.95], buckets: ['nOH'], legend: false, require_full: true, timeframe: 'chart',
    });
    assert.deepEqual(Object.keys(scopedParams(def, 'robust', stored)).sort(), [
      'atr_period', 'buckets', 'legend', 'min_obs', 'normalize', 'probabilities', 'timeframe', 'window',
    ]);
  });
});

test('scopedParams passes params through when scopes were never overlaid (オフライン耐性)', () => {
  const stored = { require_full: true, normalize: 'atr' };
  assert.deepEqual(scopedParams(get('profit_band'), 'global', stored), stored);
});

// --------------------------------------------------------------------------- #
// indicator_controller: 実際に /compute へ載るボディ（欠陥が現れていた層）
// --------------------------------------------------------------------------- #

function newController(seen) {
  const noop = () => {};
  return new IndicatorController({
    catalog: { listIndicators: () => [], get },
    compute: {
      compute: async (req) => { seen.push(req); return { ok: true, generation: 0, series: [] }; },
    },
    persistence: {
      loadApplied: () => [], saveApplied: noop, loadFavorites: () => [], saveFavorites: noop,
      loadUiState: () => ({}), saveUiState: noop, nextSeq: () => 1,
    },
    renderer: {},
    document: null,
    datasetRef: 'jp225_tick',
    timeframe: '5m',
  });
}

test('/compute body carries only the params the requested variant accepts', async () => {
  await withScopesAsync(async () => {
    const seen = [];
    const ctrl = newController(seen);
    const gw = ctrl._gatewayAdapter('global', 'full');
    await gw.compute({
      indicatorId: 'profit_band',
      variant: 'robust',                 // override が優先される（第1引数）
      params: { probabilities: [0.95], require_full: true, normalize: 'atr', min_obs: 30, timeframe: 'chart' },
      datasetRef: 'jp225_tick',
    });
    assert.equal(seen.length, 1);
    assert.equal(seen[0].variant, 'global');
    assert.deepEqual(seen[0].params, {
      probabilities: [0.95], require_full: true, timeframe: 'chart',
    });
  });
});

test('appliedComputeSpecs declares only the params the instance variant accepts', () => {
  withScopes(() => {
    const seen = [];
    const ctrl = newController(seen);
    ctrl._state = {
      ...ctrl._state,
      applied: [{
        instanceId: 'profit_band#1',
        indicatorId: 'profit_band',
        variant: 'global',
        params: [['require_full', true], ['normalize', 'atr'], ['timeframe', 'chart']],
        visible: true,
      }],
    };
    ctrl._meta.set('profit_band#1', { def: get('profit_band') });
    const specs = ctrl.appliedComputeSpecs();
    // profit_band は足内更新の登録外（INTRABAR_FORMING_IDS）なので申告されない。
    //   ここでは「申告されるときに絞られる」ことを _scopedParams で直接固定する。
    assert.deepEqual(specs, []);
    assert.deepEqual(
      ctrl._scopedParams('profit_band', 'global', { require_full: true, normalize: 'atr' }),
      { require_full: true },
    );
  });
});

async function withScopesAsync(fn) {
  applyServerParamScopes(SCOPES);
  try {
    await fn();
  } finally {
    for (const id of Object.keys(SCOPES)) {
      for (const p of get(id).params) {
        p.variants = null;
      }
    }
  }
}
