// series_kind 能力台帳の検証（ISSUE-134 OCP）。
//
// 台帳の各能力が従来の kind 直接比較（=== 'line' / === 'histogram' / !== 'histogram'）と
// 1:1 で一致することを固定し（挙動変更ゼロ）、消費 3 ファイルが raw kind 文字列比較を台帳参照へ
// 置換したこと（OCP・新種別追加を 1 箇所化）を構造的に固定する。

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import { SERIES_KINDS, seriesKind } from '../js/domain/series_kind.js';

const FRONT = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)), '..', 'js', 'adapter', 'front',
);

const KINDS = ['line', 'histogram', 'horizontal_line'];

test('capabilities match legacy kind comparisons 1:1', () => {
  for (const kind of KINDS) {
    const cap = seriesKind(kind);
    // 旧: isTailUpdatable = kind==='line' || kind==='histogram'
    assert.equal(cap.tailUpdatable, kind === 'line' || kind === 'histogram', kind);
    // 旧 renderer: kind==='line' で lineWidth/lineStyle 適用・overlay 読取欄。
    assert.equal(cap.appliesLineStyle, kind === 'line', kind);
    assert.equal(cap.overlayReadout, kind === 'line', kind);
    // 旧 renderer/dialog: heat は kind==='histogram'。
    assert.equal(cap.supportsHeat, kind === 'histogram', kind);
    // 旧 dialog: 線幅/線種編集は kind!=='histogram'。
    assert.equal(cap.editableLineStyle, kind !== 'histogram', kind);
    // 旧 renderer: definition は kind==='histogram'?Histogram:Line（seriesType で表現）。
    if (kind === 'histogram') {
      assert.equal(cap.seriesType, 'histogram', kind);
    }
  }
});

test('unknown kind falls back to legacy comparison semantics', () => {
  const cap = seriesKind('__unknown__');
  assert.equal(cap.tailUpdatable, false); // 'line'||'histogram' → false
  assert.equal(cap.appliesLineStyle, false); // ==='line' → false
  assert.equal(cap.supportsHeat, false); // ==='histogram' → false
  assert.equal(cap.overlayReadout, false);
  assert.equal(cap.editableLineStyle, true); // !=='histogram' → true
});

test('registry is the only kind ledger (consumers reference it, no raw kind literals for capability branches)', () => {
  // 消費 3 ファイルは能力台帳を import し、能力分岐は seriesKind()/SERIES_KINDS 参照であること。
  // ISSUE-181: kind 別の描画振分は indicator_controller.js から series_render_router.js へ
  //   移設した（消費者＝実際に kind で分岐するファイルを対象にする）。
  for (const f of ['series_render_router.js', 'chart_renderer.js', 'properties_dialog.js']) {
    const src = readFileSync(path.join(FRONT, f), 'utf8');
    assert.ok(
      /from '\.\.\/\.\.\/domain\/series_kind\.js'/.test(src),
      `${f} が series_kind 台帳を import していない`,
    );
    // capability を表す raw 文字列比較（=== 'histogram' / === 'line' / !== 'histogram'）が
    //   残存しないこと（コメント/データ既定値 s.kind ?? 'line' 等の非比較用途は許容）。
    assert.ok(
      !/kind\s*===\s*'histogram'/.test(src),
      `${f} に kind === 'histogram' の直接比較が残存`,
    );
    assert.ok(
      !/kind\s*!==\s*'histogram'/.test(src),
      `${f} に kind !== 'histogram' の直接比較が残存`,
    );
    assert.ok(
      !/kind\s*===\s*'line'/.test(src),
      `${f} に kind === 'line' の直接比較が残存`,
    );
  }
});

test('SERIES_KINDS is frozen and covers the three kinds', () => {
  assert.deepEqual(Object.keys(SERIES_KINDS).sort(), [...KINDS].sort());
  assert.ok(Object.isFrozen(SERIES_KINDS));
});
