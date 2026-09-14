// param_layout_convention.test.js — 設定項目の配置・呼称規約の台帳テスト（node:test / node:assert）。
//
// 依頼者指示（2026-09-05）:
//   1. 「そのパラメーターに直接的に影響を与える項目は上部に配置しろ。時間足, ソース, 期間」
//   2. 「項目名は全て統一しろ。指標によって変えるな」（期間の統一名は「期間」＝AskUserQuestion 裁定。
//      2026-07-30 の「移動期間」統一を改称で更新。副次的な窓は「期間（用途）」の型）
//
// 規約は宣言でなく機械的検査で強制する（CLAUDE.md）。並びは buildFormModel の実効順
// （グループ初出順 × グループ内 order 昇順）で判定する＝ダイアログの実表示と同じ規則。

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { list } from '../js/usecase/catalog.js';
import { buildFormModel } from '../js/usecase/form_model.js';

// ソース概念のパラメータ名（同一概念＝同一ラベル「ソース」）。
const SOURCE_NAMES = new Set(['source', 'price', 'src', 'apply']);

function fieldsOf(def) {
  // ダイアログの実表示順＝groups（グループ初出順 × グループ内 order 昇順）を平坦化する。
  return buildFormModel(def, {}).groups.flatMap((g) => g.fields);
}

test('呼称: 期間概念（isPeriod）のラベルは「期間」または「期間（用途）」で統一（「移動期間」は残さない）', () => {
  for (const def of list()) {
    for (const p of def.params ?? []) {
      if (p.isPeriod !== true) continue;
      assert.ok(p.label, `${def.id}.${p.name}: 期間パラメータにラベルが無い（生の英語名が表示される）`);
      assert.ok(
        p.label === '期間' || p.label.startsWith('期間（'),
        `${def.id}.${p.name}: ラベル ${p.label} が「期間」/「期間（用途）」型でない`,
      );
    }
  }
});

test('呼称: ソース概念（source/price/src/apply）のラベルは「ソース」で統一', () => {
  for (const def of list()) {
    for (const p of def.params ?? []) {
      if (!SOURCE_NAMES.has(p.name)) continue;
      assert.equal(p.label, 'ソース', `${def.id}.${p.name}`);
    }
  }
});

test('呼称: 共有概念（q_low/q_high/color）のラベルは指標間で同一', () => {
  const UNIFIED = { q_low: '下側分位', q_high: '上側分位', color: '色' };
  for (const def of list()) {
    for (const p of def.params ?? []) {
      const want = UNIFIED[p.name];
      if (want === undefined) continue;
      assert.equal(p.label, want, `${def.id}.${p.name}`);
    }
  }
});

test('配置: 時間足 → ソース → 期間 が実効順の先頭に並ぶ', () => {
  for (const def of list()) {
    const fields = fieldsOf(def);
    const names = fields.map((f) => f.name);
    let cursor = 0;
    // 時間足（注入指標のみ）。在れば必ず先頭。
    if (names.includes('timeframe')) {
      assert.equal(names[0], 'timeframe', `${def.id}: 時間足が先頭でない（実際: ${names[0]}）`);
      cursor = 1;
    }
    // ソース（在れば時間足の直後）。
    const sourceIndex = names.findIndex((n) => SOURCE_NAMES.has(n));
    if (sourceIndex >= 0) {
      assert.equal(sourceIndex, cursor,
        `${def.id}: ソースが上部に無い（実効順: ${names.slice(0, 4).join(' → ')}）`);
      cursor = sourceIndex + 1;
    }
    // 期間（最初の isPeriod）。calc グループに在るものはソースの直後。
    //   variant 専用グループ（profit_band の robust）や表示グループの窓は対象外
    //   （そのグループの文脈でのみ意味を持つため）。
    const firstPeriod = fields.findIndex((f) => f.isPeriod === true);
    if (firstPeriod >= 0 && (fields[firstPeriod].group ?? 'group.calc') === 'group.calc') {
      assert.equal(firstPeriod, cursor,
        `${def.id}: 期間が上部に無い（実効順: ${names.slice(0, 5).join(' → ')}）`);
    }
  }
});
