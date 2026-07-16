// host_role_contract.test.js — フロント front/adapter のロール契約（ISP・ISSUE-099 🟡-3/🟡-4）の回帰固定。
//
// 対象: TimeframeController / MarketProfileController が host（IndicatorController）へ要求する面を、
//   広い公開面（約40メソッド＋20超フィールド）ではなくロール専用の狭い契約
//   （TimeframeHost / MarketProfileHost）に限定したことを二方向で固定する:
//     (1) controller が実際に読む/呼ぶ host.X の集合 ⊆ ロール契約（依存面が契約を超えない）。
//     (2) IndicatorController（present 共有ベース）が契約の全メンバーを構造的に満たす（host 面 ⊇ 契約）。
//   ロール契約は indicator_controller.js に @typedef＋軽量ロール記述オブジェクトとして単一ソース定義。
//   symlink 単一ソースで継承される replay subclass の同契約充足は replay_ui 側テストで固定する。
// 構造: Arrange-Act-Assert（AAA）。

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

import {
  IndicatorController,
  TIMEFRAME_HOST_CONTRACT,
  MARKET_PROFILE_HOST_CONTRACT,
} from '../js/adapter/front/indicator_controller.js';
import { get } from '../js/usecase/catalog.js';

const noop = () => {};

// DOM/port を使わない純構造検証のため、ports は最小スタブで生成（既存 indicator_controller.test.js と同型）。
function makeController() {
  return new IndicatorController({
    catalog: { listIndicators: () => [], get },
    compute: { compute: async () => ({ ok: true, generation: 0, series: [] }) },
    persistence: { loadApplied: () => [], saveApplied: noop, loadFavorites: () => [], saveFavorites: noop, loadUiState: () => ({}), saveUiState: noop, nextSeq: () => 1 },
    renderer: { renderLine: noop, renderHorizontal: noop, setData: noop, setVisible: noop, remove: noop, setCandles: noop },
    document: null,
  });
}

// controller ソースから host.X / this._host.X で参照される host メンバー名の集合を抽出する。
function hostMemberUsage(relPath) {
  const src = readFileSync(fileURLToPath(new URL(relPath, import.meta.url)), 'utf8');
  const names = new Set();
  for (const m of src.matchAll(/host\.([_a-zA-Z][_a-zA-Z0-9]*)/g)) {
    names.add(m[1]);
  }
  return names;
}

// 契約が宣言する全メンバー名（必須 method + 必須 field + optional field）。
function contractMembers(contract) {
  return new Set([...contract.methods, ...contract.fields, ...contract.optionalFields]);
}

// ---- 契約記述オブジェクトの健全性 ----

test('TIMEFRAME_HOST_CONTRACT は role/methods/fields/optionalFields を凍結公開する', () => {
  assert.equal(TIMEFRAME_HOST_CONTRACT.role, 'TimeframeHost');
  assert.ok(Array.isArray(TIMEFRAME_HOST_CONTRACT.methods));
  assert.ok(Array.isArray(TIMEFRAME_HOST_CONTRACT.fields));
  assert.ok(Array.isArray(TIMEFRAME_HOST_CONTRACT.optionalFields));
  assert.ok(Object.isFrozen(TIMEFRAME_HOST_CONTRACT));
});

test('MARKET_PROFILE_HOST_CONTRACT は role/methods/fields/optionalFields を凍結公開する', () => {
  assert.equal(MARKET_PROFILE_HOST_CONTRACT.role, 'MarketProfileHost');
  assert.ok(Array.isArray(MARKET_PROFILE_HOST_CONTRACT.methods));
  assert.ok(Array.isArray(MARKET_PROFILE_HOST_CONTRACT.fields));
  assert.ok(Array.isArray(MARKET_PROFILE_HOST_CONTRACT.optionalFields));
  assert.ok(Object.isFrozen(MARKET_PROFILE_HOST_CONTRACT));
});

// ---- (1) 依存面 ⊆ 契約: controller が host から読む/呼ぶ面が契約を超えない ----

test('TimeframeController が参照する host.X 集合は TimeframeHost 契約の部分集合（広依存の遮断）', () => {
  const used = hostMemberUsage('../js/adapter/front/timeframe_controller.js');
  const allowed = contractMembers(TIMEFRAME_HOST_CONTRACT);
  const leaks = [...used].filter((n) => !allowed.has(n));
  assert.deepEqual(leaks, [], `契約外の host 参照: ${leaks.join(', ')}`);
});

test('MarketProfileController が参照する host.X 集合は MarketProfileHost 契約の部分集合（広依存の遮断）', () => {
  const used = hostMemberUsage('../js/adapter/front/market_profile_controller.js');
  const allowed = contractMembers(MARKET_PROFILE_HOST_CONTRACT);
  const leaks = [...used].filter((n) => !allowed.has(n));
  assert.deepEqual(leaks, [], `契約外の host 参照: ${leaks.join(', ')}`);
});

// 契約が過大（controller が使わないメンバーを要求）でないことも固定する（ISP: 最小面）。
test('TimeframeHost 契約の必須メンバーは全て TimeframeController が実際に使用する', () => {
  const used = hostMemberUsage('../js/adapter/front/timeframe_controller.js');
  const required = [...TIMEFRAME_HOST_CONTRACT.methods, ...TIMEFRAME_HOST_CONTRACT.fields];
  const unused = required.filter((n) => !used.has(n));
  assert.deepEqual(unused, [], `契約に含むが未使用の面: ${unused.join(', ')}`);
});

test('MarketProfileHost 契約の必須メンバーは全て MarketProfileController が実際に使用する', () => {
  const used = hostMemberUsage('../js/adapter/front/market_profile_controller.js');
  const required = [...MARKET_PROFILE_HOST_CONTRACT.methods, ...MARKET_PROFILE_HOST_CONTRACT.fields];
  const unused = required.filter((n) => !used.has(n));
  assert.deepEqual(unused, [], `契約に含むが未使用の面: ${unused.join(', ')}`);
});

// ---- (2) host 面 ⊇ 契約: IndicatorController（present 共有ベース）が構造的に契約を満たす ----

test('IndicatorController は TimeframeHost 契約を構造的に満たす（method=function / field=在席）', () => {
  const c = makeController();
  for (const m of TIMEFRAME_HOST_CONTRACT.methods) {
    assert.equal(typeof c[m], 'function', `method 欠落: ${m}`);
  }
  for (const f of TIMEFRAME_HOST_CONTRACT.fields) {
    assert.ok(f in c, `field 欠落: ${f}`);
  }
});

test('IndicatorController は MarketProfileHost 契約を構造的に満たす（method=function / field=在席）', () => {
  const c = makeController();
  for (const m of MARKET_PROFILE_HOST_CONTRACT.methods) {
    assert.equal(typeof c[m], 'function', `method 欠落: ${m}`);
  }
  for (const f of MARKET_PROFILE_HOST_CONTRACT.fields) {
    assert.ok(f in c, `field 欠落: ${f}`);
  }
});
