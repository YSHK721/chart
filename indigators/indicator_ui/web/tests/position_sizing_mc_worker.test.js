// adapter/front/position_sizing_mc_worker.js（Worker 本体）の検証（ISSUE-368 スライス 5）。
//
// 設計入力: 設計書 §6「Adapter: McWorkerGateway」／§8 依存方向図（worker → domain のみ）。
// 観点:
//   - solve メッセージで domain の権威（edge_ruin_core）を回し、進捗と結果を投げ返す
//   - 計算中に投げられた例外は **error メッセージ**として返す（Worker が黙って死なない）
//   - モジュール読み込みだけでは何も起きない（self が無い環境＝node で副作用を持たない）
// 依存方向の施行（import が domain のみ・DOM/lwc/fetch を触らない）は
//   tests/worker_url_resolution.test.js が走査で固定する（本テストは振る舞いを見る）。
// 構造: Arrange-Act-Assert。post は fake（実 Worker は実 UI 検証へ）。

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { handleWorkerMessage } from '../js/adapter/front/position_sizing_mc_worker.js';

const SPEC = Object.freeze({
  win_rate: 0.38, payoff_ratio: 2.74, ruin_level: 0.5, alpha: 0.01,
  horizon: 20, split_count: 20, seed: 1, sims: 20,
});

function collect() {
  const sent = [];
  return { sent, post: (msg) => sent.push(msg) };
}

test('solve メッセージで進捗を流し、最後に result を返す', () => {
  // Arrange
  const { sent, post } = collect();
  // Act
  handleWorkerMessage({ type: 'solve', spec: SPEC }, post);
  // Assert
  const kinds = sent.map((m) => m.type);
  assert.equal(kinds.at(-1), 'result');
  assert.ok(kinds.filter((k) => k === 'progress').length >= 60, '格子ぶんの進捗が流れる');
  const result = sent.at(-1).result;
  assert.equal(typeof result.kellyFraction, 'number');
  assert.equal(result.rorCurve.length, 60);
});

test('進捗は最後に 1 へ到達する', () => {
  // Arrange
  const { sent, post } = collect();
  // Act
  handleWorkerMessage({ type: 'solve', spec: SPEC }, post);
  // Assert
  const ratios = sent.filter((m) => m.type === 'progress').map((m) => m.ratio);
  assert.equal(ratios.at(-1), 1);
});

test('計算中の例外は error メッセージとして返す（Worker が黙って死なない）', () => {
  // Arrange
  const { sent, post } = collect();
  // Act
  handleWorkerMessage({ type: 'solve', spec: null }, post);
  // Assert
  assert.equal(sent.length, 1);
  assert.equal(sent[0].type, 'error');
  assert.equal(typeof sent[0].message, 'string');
  assert.ok(sent[0].message.length > 0);
});

test('未知の種別・空メッセージは無視する（何も投げ返さない）', () => {
  // Arrange
  const { sent, post } = collect();
  // Act
  handleWorkerMessage({ type: 'chatter' }, post);
  handleWorkerMessage(null, post);
  handleWorkerMessage(undefined, post);
  // Assert
  assert.deepEqual(sent, []);
});

test('モジュールの読み込み自体は副作用を持たない（self の無い環境で安全）', () => {
  // Arrange / Act / Assert — ここまでの import で例外が出ていないこと自体が表明。
  assert.equal(typeof globalThis.self, 'undefined');
  assert.equal(typeof handleWorkerMessage, 'function');
});
