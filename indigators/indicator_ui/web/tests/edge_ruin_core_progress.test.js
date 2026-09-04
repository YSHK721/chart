// edge_ruin_core.solveEdgeRuin の進捗通知（任意）の検証（ISSUE-368 スライス 5）。
//
// 設計入力: 設計書 §5 Output Boundary（MonteCarloPort.solve(spec, onProgress)）／
//   出力 3 スライス 5 実 UI 検証（「MC 実行中もチャート操作が固まらない／**進捗が進む**」）。
//
// なぜ権威の鏡へ追加するのか（設計からの逸脱にあたるため理由を残す）:
//   進捗は MC のループの**内側**でしか観測できない。`solveEdgeRuin` は grid 60 点 ×
//   sims × T を 1 回の呼び出しで回すため、外側からは「開始」と「終了」しか分からない。
//   偽の進捗（時間ベースの見せかけ）は「進んでいるように見えて実は止まっている」を
//   隠すので採らない。よって**計算に一切影響しない観測フック**だけを追加する。
//   Python 権威（edge_ruin.py）にはこのフックが無い。UI を持たない権威側に進捗の
//   関心が無いためで、数値は変わらないので golden 一致検定の射程外である。
//
// 本テストが固定すること: フックの有無で**結果が 1 bit も変わらない**こと（＝鏡が壊れない）。
// 構造: Arrange-Act-Assert。

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { solveEdgeRuin } from '../js/domain/edge_ruin_core.js';

const SPEC = Object.freeze({
  win_rate: 0.38, payoff_ratio: 2.74, ruin_level: 0.5, alpha: 0.01,
  horizon: 20, split_count: 20, seed: 1, sims: 20,
});

test('onProgress を渡しても結果は 1 bit も変わらない（権威の鏡を壊さない）', () => {
  // Arrange
  const plain = solveEdgeRuin(SPEC);
  // Act
  const withHook = solveEdgeRuin(SPEC, () => {});
  // Assert
  assert.deepEqual(withHook, plain);
});

test('進捗は 0 より大きく 1 以下の単調増加で、最後は必ず 1 で終わる', () => {
  // Arrange
  const seen = [];
  // Act
  solveEdgeRuin(SPEC, (r) => seen.push(r));
  // Assert
  assert.ok(seen.length >= 60, `格子 60 点ぶん以上の通知がある（実際 ${seen.length}）`);
  assert.equal(seen.at(-1), 1, '完了で 1 に到達する（途中で止まったように見えない）');
  for (let i = 0; i < seen.length; i += 1) {
    assert.ok(seen[i] > 0 && seen[i] <= 1, `範囲 [${i}]=${seen[i]}`);
    if (i > 0) {
      assert.ok(seen[i] >= seen[i - 1], `単調 [${i}]: ${seen[i - 1]} → ${seen[i]}`);
    }
  }
});

test('onProgress 未指定・非関数でも例外を投げない（任意の引数）', () => {
  // Arrange / Act / Assert
  assert.doesNotThrow(() => solveEdgeRuin(SPEC));
  assert.doesNotThrow(() => solveEdgeRuin(SPEC, null));
  assert.doesNotThrow(() => solveEdgeRuin(SPEC, 'nope'));
});
