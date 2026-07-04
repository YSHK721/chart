// domain_models.js の仕様検証（node:test / node:assert）。
//
// 対象: SeriesDef / AppliedInstance / Favorite / IndicatorDef（純ロジック）。
// 設計入力: 内部設計書 §3.1.2（series）、§3.1.3（IndicatorDef matches/validateParams）、
//   §3.1.4（generation 不変ルール）、§4.6（検索）、§6.6（accepts）。
// 移植元 Python: series_def.py / applied_instance.py / favorite.py / indicator_def.py。
// 構造: Arrange-Act-Assert（AAA）。

import { test } from 'node:test';
import assert from 'node:assert/strict';

import {
  SeriesKind,
  SeriesDef,
  AppliedInstance,
  Favorite,
  IndicatorDef,
} from '../js/domain/domain_models.js';
import { ParamType, ConstraintKind } from '../js/domain/constraint_eval.js';

// ===========================================================================
// SeriesDef（§3.1.2 — source_column と series_name を別保持）
// ===========================================================================

test('SeriesDef: holds source_column and series_name separately', () => {
  // Arrange / Act（profit_band: pOL_99 ↔ pOL 99%）
  const s = new SeriesDef({
    kind: SeriesKind.LINE,
    sourceColumn: 'pOL_99',
    seriesName: 'pOL 99%',
    dynamic: false,
  });
  // Assert
  assert.equal(s.sourceColumn, 'pOL_99');
  assert.equal(s.seriesName, 'pOL 99%');
  assert.equal(s.kind, SeriesKind.LINE);
});

test('SeriesDef: resolveSeriesName returns series_name not source_column', () => {
  // Arrange（F3 照合基準は series_name 固定。引数 column は dynamic 用予約）
  const s = new SeriesDef({
    kind: SeriesKind.LINE,
    sourceColumn: 'pOL_99',
    seriesName: 'pOL 99%',
    dynamic: false,
  });
  // Act
  const name = s.resolveSeriesName('pOL_99');
  // Assert（series_name を返す。source_column ではない）
  assert.equal(name, 'pOL 99%');
});

test('SeriesDef: horizontal_line kind supported', () => {
  const s = new SeriesDef({
    kind: SeriesKind.HORIZONTAL_LINE,
    sourceColumn: null,
    seriesName: 'BULL',
    dynamic: false,
  });
  assert.equal(s.kind, SeriesKind.HORIZONTAL_LINE);
});

// ===========================================================================
// AppliedInstance（§3.1.4 / §6.6 — generation 不変ルール）
// ===========================================================================

function makeInstance(overrides = {}) {
  return new AppliedInstance({
    indicatorId: 'profit_band',
    variant: 'global',
    params: [['probabilities', [0.95, 0.99]]],
    visible: true,
    generation: 0,
    seq: 3,
    createdAt: '2026-06-07T00:00:00Z',
    ...overrides,
  });
}

test('AppliedInstance: instanceId is indicatorId#seq', () => {
  // Arrange / Act
  const inst = makeInstance({ seq: 3 });
  // Assert（§5.7）
  assert.equal(inst.instanceId, 'profit_band#3');
});

test('AppliedInstance: holds identity and generation state', () => {
  const inst = makeInstance({ generation: 2, seq: 3 });
  assert.equal(inst.indicatorId, 'profit_band');
  assert.equal(inst.variant, 'global');
  assert.equal(inst.visible, true);
  assert.equal(inst.generation, 2);
  assert.equal(inst.seq, 3);
  assert.equal(inst.createdAt, '2026-06-07T00:00:00Z');
});

test('AppliedInstance: nextGeneration increments by one', () => {
  const inst = makeInstance({ generation: 0 });
  const nxt = inst.nextGeneration();
  assert.equal(nxt.generation, 1);
});

test('AppliedInstance: nextGeneration returns new instance without mutating', () => {
  // Arrange（元は不変）
  const inst = makeInstance({ generation: 5 });
  // Act
  const nxt = inst.nextGeneration();
  // Assert
  assert.equal(inst.generation, 5);
  assert.notEqual(nxt, inst);
});

test('AppliedInstance: accepts is equality (current generation only)', () => {
  // Arrange（generation=2）
  const inst = makeInstance({ generation: 2 });
  // Act / Assert（等値のみ採用＝§6.6 レース対策）
  assert.equal(inst.accepts(2), true);
});

test('AppliedInstance: accepts discards older response generation', () => {
  const inst = makeInstance({ generation: 3 });
  // 古い応答 (2 < 3) は破棄
  assert.equal(inst.accepts(2), false);
});

test('AppliedInstance: accepts discards future response generation', () => {
  const inst = makeInstance({ generation: 3 });
  // 未来の応答 (4 > 3) も破棄（範囲比較でない＝核心）
  assert.equal(inst.accepts(4), false);
});

// ===========================================================================
// Favorite（§3.1 — 指標 id 単位）
// ===========================================================================

test('Favorite: holds indicatorId', () => {
  const f = new Favorite({ indicatorId: 'tgp_btlm' });
  assert.equal(f.indicatorId, 'tgp_btlm');
});

// ===========================================================================
// IndicatorDef（§3.1.3 — matches / validateParams / series>=1 不変条件）
// ===========================================================================

function makeIndicatorDef(overrides = {}) {
  return new IndicatorDef({
    id: 'tgp_btlm',
    displayNameKey: 'ind.tgp_btlm',
    category: { group: 'builtin', nameKey: 'cat.technical' },
    tab: 'indicator',
    placement: 'overlay',
    params: [],
    series: [new SeriesDef({ kind: SeriesKind.LINE, sourceColumn: 'btlm_mean', seriesName: 'btlm_mean', dynamic: false })],
    compute: { computeId: 'tgp_btlm', requiredColumns: ['open', 'high', 'low', 'close'], timeRequired: true },
    ...overrides,
  });
}

test('IndicatorDef: throws when series is empty (>=1 invariant)', () => {
  // Arrange / Act / Assert（§3.1.3 series>=1）
  assert.throws(() => makeIndicatorDef({ series: [] }), /series/);
});

test('IndicatorDef: matches empty query passes all', () => {
  // Arrange（空クエリは論理積真＝全件通過）
  const d = makeIndicatorDef();
  // Act / Assert
  assert.equal(d.matches('', 'TGP BTLM'), true);
});

test('IndicatorDef: matches is case-insensitive partial on display name', () => {
  const d = makeIndicatorDef();
  // "btlm" は display_name "TGP BTLM" の部分一致（小文字化）
  assert.equal(d.matches('btlm', 'TGP BTLM'), true);
});

test('IndicatorDef: matches on id as well as display name', () => {
  const d = makeIndicatorDef();
  // id "tgp_btlm" 部分一致（display_name に無くても id で一致）
  assert.equal(d.matches('tgp_', 'Regression Channel'), true);
});

test('IndicatorDef: matches multiple terms conjunctively (AND)', () => {
  const d = makeIndicatorDef();
  // "tgp" AND "channel" 両方 haystack（display+id）に含まれる必要
  assert.equal(d.matches('tgp channel', 'Regression Channel'), true);
  // 片方しか無ければ false
  assert.equal(d.matches('tgp missingword', 'Regression Channel'), false);
});

test('IndicatorDef: validateParams delegates to constraint evaluator (valid)', () => {
  // Arrange（q_low<q_high 制約）
  const d = makeIndicatorDef({
    params: [
      { name: 'q_low', labelKey: 'label.q_low', type: ParamType.FLOAT, default: 0.05, enumValues: null,
        constraints: [{ kind: ConstraintKind.LT, operands: ['q_low', 'q_high'], messageKey: 'err.q_order' }] },
      { name: 'q_high', labelKey: 'label.q_high', type: ParamType.FLOAT, default: 0.95, enumValues: null, constraints: [] },
    ],
  });
  // Act
  const result = d.validateParams({ q_low: 0.05, q_high: 0.95 });
  // Assert
  assert.deepEqual(result, []);
});

test('IndicatorDef: validateParams delegates to constraint evaluator (violation)', () => {
  const d = makeIndicatorDef({
    params: [
      { name: 'q_low', labelKey: 'label.q_low', type: ParamType.FLOAT, default: 0.05, enumValues: null,
        constraints: [{ kind: ConstraintKind.LT, operands: ['q_low', 'q_high'], messageKey: 'err.q_order' }] },
      { name: 'q_high', labelKey: 'label.q_high', type: ParamType.FLOAT, default: 0.95, enumValues: null, constraints: [] },
    ],
  });
  // Act（逆転 0.96 > 0.5 → lt 違反）
  const result = d.validateParams({ q_low: 0.96, q_high: 0.5 });
  // Assert
  assert.equal(result.length, 1);
  assert.equal(result[0].param, 'q_low');
  assert.equal(result[0].constraint, 'lt(q_low,q_high)');
});
