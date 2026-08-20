// usecase/mc_port.js（MonteCarloPort の契約）の検証（ISSUE-368 スライス 5）。
//
// 設計入力: 設計書 §5 Output Boundary（MonteCarloPort: solve(spec, onProgress) -> Promise）／
//   §10 YAGNI 検証（MonteCarloPort は「維持」＝実行場所が ISSUE-362/364 で実際に一度棄却・変更
//   されており、変更要因が実在する）。
// 観点: 契約に反する実装を注入したら**その場で大きく失敗する**こと。無音で縮退すると
//   「押しても何も起きない」＝原因不明の不具合になる（設計書 §6 の「無音の縮退をしない」）。
// 構造: Arrange-Act-Assert。

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { McUnavailableError, assertMonteCarloPort } from '../js/usecase/mc_port.js';

test('McUnavailableError は Error であり、名前と原因を保持する', () => {
  // Arrange
  const cause = new Error('worker boom');
  // Act
  const err = new McUnavailableError('起動できない', { cause });
  // Assert
  assert.ok(err instanceof Error);
  assert.equal(err.name, 'McUnavailableError');
  assert.match(err.message, /起動できない/);
  assert.equal(err.cause, cause, '原因を握り潰さない（切り分けの入口を残す）');
});

test('McUnavailableError は原因なしでも作れる（cause は任意）', () => {
  // Arrange / Act
  const err = new McUnavailableError('Worker 非対応');
  // Assert
  assert.equal(err.cause, undefined);
  assert.equal(err.name, 'McUnavailableError');
});

test('assertMonteCarloPort は契約を満たす実装をそのまま返す', () => {
  // Arrange
  const port = { solve: async () => ({}) };
  // Act / Assert
  assert.equal(assertMonteCarloPort(port), port);
});

test('assertMonteCarloPort は契約違反を即座に投げる（無音で縮退しない）', () => {
  // Arrange / Act / Assert
  assert.throws(() => assertMonteCarloPort(null), /MonteCarloPort/);
  assert.throws(() => assertMonteCarloPort({}), /solve/);
  assert.throws(() => assertMonteCarloPort({ solve: 1 }), /solve/);
});
