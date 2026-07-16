// host_role_contract.test.js（replay_ui）— symlink 単一ソース継承先 ReplayIndicatorController が
//   フロントロール契約（TimeframeHost / MarketProfileHost・ISSUE-099 🟡-3/🟡-4）を構造的に満たすことを固定する。
//
// 共有ベース IndicatorController は本 replay_ui へ symlink 単一ソースで継承され、ReplayIndicatorController が
//   extends する。ロール契約（indicator_controller.js に単一ソース定義）は present/replay 双方に等しく適用される。
//   present base 側の充足は indicator_ui/web/tests/host_role_contract.test.js が、replay subclass 側の
//   非回帰は本テストが固定する。replay 固有の reveal seam（_untilTime）は MarketProfileHost の optional 面。
// 構造: Arrange-Act-Assert（AAA）。

import { test } from 'node:test';
import assert from 'node:assert/strict';

import {
  TIMEFRAME_HOST_CONTRACT,
  MARKET_PROFILE_HOST_CONTRACT,
} from '../js/adapter/front/indicator_controller.js';
import { ReplayIndicatorController } from '../js/adapter/front/replay_indicator_controller.js';
import { get } from '../js/usecase/catalog.js';

const noop = () => {};

// 既存 replay tests と同型の最小 opts（present composition root と同型の growth 注入）。
function makeReplayController() {
  return new ReplayIndicatorController({
    catalog: { listIndicators: () => [], get },
    compute: { compute: async () => ({ ok: true, generation: 0, series: [] }) },
    persistence: { loadApplied: () => [], saveApplied: noop, loadFavorites: () => [], saveFavorites: noop, loadUiState: () => ({}), saveUiState: noop, nextSeq: () => 1 },
    renderer: { renderLine: noop, renderHorizontal: noop, renderHistogram: noop, setData: noop, setVisible: noop, remove: noop, setCandles: noop },
    marketProfile: null,
    document: null,
    mpGrowthResolver: () => true,
  });
}

test('ReplayIndicatorController は TimeframeHost 契約を構造的に満たす', () => {
  const c = makeReplayController();
  for (const m of TIMEFRAME_HOST_CONTRACT.methods) {
    assert.equal(typeof c[m], 'function', `method 欠落: ${m}`);
  }
  for (const f of TIMEFRAME_HOST_CONTRACT.fields) {
    assert.ok(f in c, `field 欠落: ${f}`);
  }
});

test('ReplayIndicatorController は MarketProfileHost 契約を構造的に満たす', () => {
  const c = makeReplayController();
  for (const m of MARKET_PROFILE_HOST_CONTRACT.methods) {
    assert.equal(typeof c[m], 'function', `method 欠落: ${m}`);
  }
  for (const f of MARKET_PROFILE_HOST_CONTRACT.fields) {
    assert.ok(f in c, `field 欠落: ${f}`);
  }
});

test('ReplayIndicatorController は MarketProfileHost の optional 面 _untilTime（reveal seam）を持つ', () => {
  const c = makeReplayController();
  // present base は _untilTime を持たない（optional）が、replay subclass は constructor で在席させる。
  assert.ok(MARKET_PROFILE_HOST_CONTRACT.optionalFields.includes('_untilTime'));
  assert.ok('_untilTime' in c, 'replay subclass は _untilTime を在席させる');
});
