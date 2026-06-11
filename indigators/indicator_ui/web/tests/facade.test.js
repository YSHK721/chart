// facade.js の仕様検証（node:test / node:assert）。
//
// 対象: usecase/facade.js（UC-01/02/03/04/05/06/07）。
// 設計入力: 内部設計書 §3.2.2（UC 一覧）、§4.6（検索論理積）、§6.6（generation 競合破棄）、
//   §6.1/§5.6（永続化スキーマ applied/favorites/seqCounters/uiState）。
// ComputeGateway はポート注入（Fake gateway をテストで渡す）。DOM/fetch 非依存。
// 構造: Arrange-Act-Assert（AAA）。

import { test } from 'node:test';
import assert from 'node:assert/strict';

import {
  listForView,
  toggleVisible,
  remove,
  toggleFavorite,
  apply,
  recompute,
  serialize,
  deserialize,
  emptyState,
} from '../js/usecase/facade.js';
import { AppliedInstance } from '../js/domain/domain_models.js';

// ===========================================================================
// UC-01 listForView（タブ∧カテゴリ∧検索∧お気に入りの論理積 §4.6）
// ===========================================================================

test('UC-01 listForView: empty filter returns all 4', () => {
  const result = listForView({});
  assert.equal(result.length, 4);
});

test('UC-01 listForView: filters by query (id/display partial, case-insensitive)', () => {
  // "tgp" は tgp_btlm のみ
  const result = listForView({ query: 'tgp' });
  assert.deepEqual(result.map((d) => d.id), ['tgp_btlm']);
});

test('UC-01 listForView: filters by category conjunctively', () => {
  // category=statistics → profit_band のみ
  const result = listForView({ category: 'cat.statistics' });
  assert.deepEqual(result.map((d) => d.id), ['profit_band']);
});

test('UC-01 listForView: filters by tab', () => {
  // 全指標 tab=indicator なので strategy では 0 件
  assert.equal(listForView({ tab: 'strategy' }).length, 0);
  assert.equal(listForView({ tab: 'indicator' }).length, 4);
});

test('UC-01 listForView: favoriteOnly intersects with favorites set', () => {
  // favoriteOnly=true かつ favorites=[profit_band] → profit_band のみ
  const result = listForView({ favoriteOnly: true, favorites: ['profit_band'] });
  assert.deepEqual(result.map((d) => d.id), ['profit_band']);
});

test('UC-01 listForView: conjunction of query and category yields empty when disjoint', () => {
  // query=tgp（tgp_btlm）∧ category=statistics（profit_band）→ 交わり無し
  const result = listForView({ query: 'tgp', category: 'cat.statistics' });
  assert.deepEqual(result, []);
});

// ===========================================================================
// UC-04 toggleVisible / UC-05 remove / UC-06 toggleFavorite
// ===========================================================================

function stateWith(instances = [], favorites = []) {
  const s = emptyState();
  s.applied = instances;
  s.favorites = favorites;
  return s;
}

function inst(indicatorId, seq, overrides = {}) {
  return new AppliedInstance({
    indicatorId,
    variant: 'default',
    params: [],
    visible: true,
    generation: 0,
    seq,
    createdAt: '2026-06-07T00:00:00Z',
    ...overrides,
  });
}

test('UC-04 toggleVisible: flips visible flag of the target instance', () => {
  // Arrange
  const i = inst('tgp_btlm', 1, { visible: true });
  const state = stateWith([i]);
  // Act
  const next = toggleVisible(state, 'tgp_btlm#1');
  // Assert
  const target = next.applied.find((x) => x.instanceId === 'tgp_btlm#1');
  assert.equal(target.visible, false);
});

test('UC-04 toggleVisible: leaves other instances unchanged', () => {
  const a = inst('tgp_btlm', 1, { visible: true });
  const b = inst('profit_band', 2, { visible: true });
  const state = stateWith([a, b]);
  const next = toggleVisible(state, 'tgp_btlm#1');
  assert.equal(next.applied.find((x) => x.instanceId === 'profit_band#2').visible, true);
});

test('UC-05 remove: drops the target instance from applied', () => {
  const a = inst('tgp_btlm', 1);
  const b = inst('profit_band', 2);
  const state = stateWith([a, b]);
  const next = remove(state, 'tgp_btlm#1');
  assert.deepEqual(next.applied.map((x) => x.instanceId), ['profit_band#2']);
});

test('UC-05 remove: does not decrement seqCounters (seq not reused §5.7)', () => {
  const a = inst('tgp_btlm', 1);
  const state = stateWith([a]);
  state.seqCounters = { tgp_btlm: 1 };
  const next = remove(state, 'tgp_btlm#1');
  assert.equal(next.seqCounters.tgp_btlm, 1);
});

test('UC-06 toggleFavorite: adds then removes an indicator id', () => {
  let state = stateWith([], []);
  state = toggleFavorite(state, 'tgp_btlm');
  assert.deepEqual(state.favorites, ['tgp_btlm']);
  // もう一度で解除
  state = toggleFavorite(state, 'tgp_btlm');
  assert.deepEqual(state.favorites, []);
});

test('UC-06 toggleFavorite: no duplicate when toggled on twice via distinct ids', () => {
  let state = stateWith([], ['profit_band']);
  state = toggleFavorite(state, 'tgp_btlm');
  assert.deepEqual([...state.favorites].sort(), ['profit_band', 'tgp_btlm']);
});

// ===========================================================================
// UC-02 apply / UC-03 recompute（ComputeGateway ポート注入・generation 競合破棄 §6.6）
// ===========================================================================

// Fake gateway: 指定 generation をそのまま返す（正常応答）。
function fakeGatewayEcho() {
  return {
    compute(req) {
      return { ok: true, generation: req.generation, series: [{ seriesName: 's', points: [] }] };
    },
  };
}

test('UC-02 apply: assigns seq, generation=0 and registers instance', async () => {
  // Arrange
  const state = stateWith([], []);
  const gateway = fakeGatewayEcho();
  // Act
  const { state: next, instance } = await apply(state, {
    indicatorId: 'tgp_btlm', variant: 'default', params: { fitter: 'ols' }, datasetRef: 'ds',
  }, gateway);
  // Assert
  assert.equal(instance.seq, 1);
  assert.equal(instance.generation, 0);
  assert.equal(instance.instanceId, 'tgp_btlm#1');
  assert.equal(next.applied.length, 1);
  assert.equal(next.seqCounters.tgp_btlm, 1);
});

test('UC-03 recompute: accepts current-generation response and updates instance', async () => {
  // Arrange（既存 instance gen=0 を recompute → gen=1）
  const i = inst('tgp_btlm', 1, { generation: 0 });
  const state = stateWith([i], []);
  const gateway = fakeGatewayEcho();
  // Act
  const { state: next, accepted } = await recompute(state, 'tgp_btlm#1', { fitter: 'ols' }, 'ds', gateway);
  // Assert
  assert.equal(accepted, true);
  assert.equal(next.applied.find((x) => x.instanceId === 'tgp_btlm#1').generation, 1);
});

test('UC-03 recompute: discards a stale (older) generation response (§6.6)', async () => {
  // Arrange: gateway が古い世代（generation-1）を返す＝遅延応答シミュレーション
  const i = inst('tgp_btlm', 1, { generation: 0 });
  const state = stateWith([i], []);
  const staleGateway = {
    compute(req) {
      return { ok: true, generation: req.generation - 1, series: [] };
    },
  };
  // Act（next_generation で gen=1 を期待するが応答は gen=0 → accepts(0) は false）
  const { state: next, accepted } = await recompute(state, 'tgp_btlm#1', { fitter: 'ols' }, 'ds', staleGateway);
  // Assert（破棄: instance は gen を進めるが応答は採用しない）
  assert.equal(accepted, false);
  // 破棄時は applied の当該 instance は変更されない（古い応答を反映しない §6.6）
  assert.equal(next.applied.find((x) => x.instanceId === 'tgp_btlm#1').generation, 0);
});

// ===========================================================================
// UC-07 永続化（serialize / deserialize 往復一致・§6.1/§5.6）
// ===========================================================================

test('UC-07 serialize/deserialize: roundtrip preserves applied, favorites, seqCounters, uiState', () => {
  // Arrange
  const i = inst('profit_band', 3, { generation: 2, variant: 'global', visible: true, params: [['probabilities', [0.95, 0.99]]] });
  const state = stateWith([i], ['tgp_btlm']);
  state.seqCounters = { profit_band: 3 };
  state.uiState = { lastTab: 'indicator', lastCategory: 'cat.statistics', dialogOpen: false };
  // Act（純粋なオブジェクト⇔JSON 変換。localStorage は触らない）
  const json = serialize(state);
  const restored = deserialize(json);
  // Assert（往復一致）
  assert.equal(restored.favorites.length, 1);
  assert.equal(restored.favorites[0], 'tgp_btlm');
  assert.deepEqual(restored.seqCounters, { profit_band: 3 });
  assert.deepEqual(restored.uiState, { lastTab: 'indicator', lastCategory: 'cat.statistics', dialogOpen: false });
  assert.equal(restored.applied.length, 1);
  const ri = restored.applied[0];
  assert.ok(ri instanceof AppliedInstance);
  assert.equal(ri.instanceId, 'profit_band#3');
  assert.equal(ri.generation, 2);
  assert.equal(ri.variant, 'global');
  assert.deepEqual(ri.params, [['probabilities', [0.95, 0.99]]]);
});

test('UC-07 deserialize: reconstructs seqCounters from applied (prevents instanceId collision after reload)', () => {
  // Arrange: コントローラ restore と同様に seqCounters 抜き（空）の永続 JSON を復元する。
  //   同一指標 'ma' を seq=2 まで使った状態を applied のみで表現（カウンタは永続化されない）。
  const json = JSON.stringify({
    applied: [
      { indicatorId: 'ma', variant: 'default', params: [['period', 50]], visible: true, generation: 0, seq: 1, createdAt: '2026-06-07T00:00:00Z' },
      { indicatorId: 'ma', variant: 'default', params: [['period', 30]], visible: true, generation: 0, seq: 2, createdAt: '2026-06-07T00:00:00Z' },
    ],
    favorites: [],
    seqCounters: {},
    uiState: {},
  });
  // Act
  const restored = deserialize(json);
  // Assert: カウンタは既存最大 seq（2）まで底上げされ、次回 apply は seq=3（衝突なし）となる。
  assert.equal(restored.seqCounters.ma, 2);
});

test('UC-07 deserialize: seqCounters takes max of persisted counter and applied seq', () => {
  // Arrange: 永続カウンタ（5）が applied 最大 seq（2）より大きい場合は減らさない（§5.7 単調）。
  const json = JSON.stringify({
    applied: [
      { indicatorId: 'ma', variant: 'default', params: [], visible: true, generation: 0, seq: 2, createdAt: '2026-06-07T00:00:00Z' },
    ],
    favorites: [],
    seqCounters: { ma: 5 },
    uiState: {},
  });
  // Act
  const restored = deserialize(json);
  // Assert
  assert.equal(restored.seqCounters.ma, 5);
});

test('UC-07 deserialize: heals duplicate (indicatorId, seq) by re-sequencing to unique instanceIds', () => {
  // Arrange: バグ版が保存した破損データ（同一指標が seq=1 で2件・instanceId 衝突）。
  const json = JSON.stringify({
    applied: [
      { indicatorId: 'ma', variant: 'default', params: [['length', 50]], visible: true, generation: 0, seq: 1, createdAt: '2026-06-07T00:00:00Z' },
      { indicatorId: 'ma', variant: 'default', params: [['length', 30]], visible: true, generation: 0, seq: 1, createdAt: '2026-06-07T00:00:00Z' },
    ],
    favorites: [],
    seqCounters: {},
    uiState: {},
  });
  // Act
  const restored = deserialize(json);
  // Assert: instanceId が一意化され、params は保持、カウンタは最大 seq(2) まで底上げ。
  assert.deepEqual(restored.applied.map((i) => i.instanceId), ['ma#1', 'ma#2']);
  assert.deepEqual(restored.applied[0].params, [['length', 50]]);
  assert.deepEqual(restored.applied[1].params, [['length', 30]]);
  assert.equal(restored.seqCounters.ma, 2);
});

test('UC-07 serialize: output JSON is a string parseable to schema keys', () => {
  const state = emptyState();
  const json = serialize(state);
  assert.equal(typeof json, 'string');
  const parsed = JSON.parse(json);
  // §6.1 物理スキーマのキー
  assert.ok('applied' in parsed);
  assert.ok('favorites' in parsed);
  assert.ok('seqCounters' in parsed);
  assert.ok('uiState' in parsed);
});

test('UC-07 deserialize: corrupt/missing keys initialize empty without throwing', () => {
  // §6.2 当該キーのみ初期化・全消去しない（破壊的変更回避）
  const restored = deserialize('{"favorites":{"broken":true}}');
  assert.ok(Array.isArray(restored.applied));
  assert.equal(restored.applied.length, 0);
  assert.ok(Array.isArray(restored.favorites));
});
