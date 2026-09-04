// collaborator_state_ownership.test.js — 協働子の状態所有（ISSUE-181）の回帰固定。
//
// 対象の問題（ISSUE-181 実測）: 「抽出済み協働子が host の private フィールドを直接代入しており、
//   クラスは分割されたが状態所有は host のまま＝責務は未分離」だった
//   （旧 timeframe_controller.js:38,55,58,74 / 旧 market_profile_controller.js:125,152,171,187）。
//   分割不全は再発しやすい（協働子から host の状態を書けば動いてしまう）ため、構造で固定する。
//
// 固定する不変条件:
//   (1) 協働子は host の private フィールドへ再代入しない（`host._x = / += / -=` が 0 件）。
//       状態は協働子が所有するか、host へ更新を依頼する（例: host._commitState(next)）。
//   (2) 時間足ロールの状態（現在足・直近本数・candles ローダ）は TimeframeController が所有する
//       （host のフィールドとして初期化されない）。
//   (3) 再計算バッチの深さカウンタは RecomputeGate が所有する（host のフィールドではない）。
// 構造: Arrange-Act-Assert（AAA）。

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const FRONT_DIR = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', 'js', 'adapter', 'front');

// 協働子（host を受け取り、そのロールを担うクラス）の一覧。
const COLLABORATORS = [
  'timeframe_controller.js',
  'market_profile_controller.js',
  'indicator_dialog_controller.js',
  'series_render_router.js',
  'indicator_state_store.js',
  'series_style_applier.js',
];

// `host._x = ...` / `host._x += ...` / `host._x -= ...`（フィールド再代入）を抽出する。
//   `host._state.uiState = ...`（host 所有オブジェクトのプロパティ更新）は対象外＝フィールド
//   そのものの差し替えのみを違反とする。`==` / `===` の比較は除外する。
function hostFieldAssignments(source) {
  return [...source.matchAll(/host\._[A-Za-z0-9_]+\s*(?:\+=|-=|=(?!=))/g)].map((m) => m[0].trim());
}

test('協働子は host の private フィールドへ直接代入しない（ISSUE-181 分割不全の回帰固定）', () => {
  // Arrange
  const found = {};
  // Act
  for (const file of COLLABORATORS) {
    const src = readFileSync(path.join(FRONT_DIR, file), 'utf8');
    const hits = hostFieldAssignments(src);
    if (hits.length > 0) {
      found[file] = hits;
    }
  }
  // Assert
  assert.deepEqual(
    found,
    {},
    '協働子が host のフィールドへ直接代入している（状態所有が host のまま＝責務未分離）。'
    + ' 状態は協働子へ移すか、host へ更新を依頼するメソッド（例 _commitState）経由にすること。',
  );
});

test('時間足ロールの状態は TimeframeController が所有する（host に初期化されない）', () => {
  // Arrange
  const controller = readFileSync(path.join(FRONT_DIR, 'indicator_controller.js'), 'utf8');
  const tf = readFileSync(path.join(FRONT_DIR, 'timeframe_controller.js'), 'utf8');
  // Act / Assert: host 側の constructor で当該フィールドを初期化していない。
  for (const field of ['_timeframe', '_recentBars', '_loadCandles']) {
    assert.equal(
      new RegExp(`this\\.${field} = (timeframe|recentBars|loadCandles|null)`).test(controller),
      false,
      `IndicatorController が ${field} を自身のフィールドとして初期化している（状態が host のまま）`,
    );
    assert.equal(
      new RegExp(`this\\.${field} = `).test(tf),
      true,
      `TimeframeController が ${field} を所有していない`,
    );
  }
});

test('再計算バッチの深さカウンタは RecomputeGate が所有する（host のフィールドではない）', () => {
  // Arrange
  const controller = readFileSync(path.join(FRONT_DIR, 'indicator_controller.js'), 'utf8');
  const gate = readFileSync(path.join(FRONT_DIR, 'recompute_gate.js'), 'utf8');
  // Act / Assert
  assert.equal(/this\._recomputeDepth \+= 1/.test(controller), false,
    'IndicatorController が深さカウンタを直接増減している（ゲートへ移送されていない）');
  assert.equal(/this\._depth \+= 1/.test(gate), true, 'RecomputeGate が深さカウンタを所有していない');
});
