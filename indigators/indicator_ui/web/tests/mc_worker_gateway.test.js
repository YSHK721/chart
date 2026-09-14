// adapter/front/mc_worker_gateway.js（MonteCarloPort の Worker 実装）の検証（ISSUE-368 スライス 5）。
//
// 設計入力: 設計書 §6「Adapter: McWorkerGateway」／出力 3 スライス 5。
// 観点:
//   - 契約（solve(spec, onProgress) -> Promise<EdgeResultDTO>）を満たす往復
//   - 進捗の中継
//   - **失敗はすべて McUnavailableError へ翻訳**する（Worker 非対応・生成失敗・
//     worker の error イベント・worker からの error メッセージ）。無音の縮退をしない
//   - 決着したら worker を必ず後片付けする（成功でも失敗でも残さない）
// 構造: Arrange-Act-Assert。Worker は fake を注入（実 Worker は実 UI 検証へ）。

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { McWorkerGateway } from '../js/adapter/front/mc_worker_gateway.js';
import { McUnavailableError } from '../js/usecase/mc_port.js';

const SPEC = Object.freeze({
  win_rate: 0.38, payoff_ratio: 2.74, ruin_level: 0.5, alpha: 0.01,
  horizon: 50, split_count: 20, seed: 1, sims: 50,
});

// Worker Fake（postMessage を記録し、テストから任意のメッセージ／エラーを差し戻せる）。
function fakeWorker() {
  const listeners = {};
  return {
    posted: [],
    terminated: 0,
    addEventListener(type, fn) { (listeners[type] ||= []).push(fn); },
    removeEventListener(type, fn) {
      listeners[type] = (listeners[type] || []).filter((f) => f !== fn);
    },
    postMessage(msg) { this.posted.push(msg); },
    terminate() { this.terminated += 1; },
    emit(type, ev) { (listeners[type] || []).slice().forEach((fn) => fn(ev)); },
  };
}

function build({ worker, factory } = {}) {
  const w = worker || fakeWorker();
  const gateway = new McWorkerGateway({ createWorker: factory || (() => w) });
  return { gateway, worker: w };
}

test('solve は spec を worker へ送り、result で解決する', async () => {
  // Arrange
  const { gateway, worker } = build();
  const expected = { kellyFraction: 0.15 };
  // Act
  const pending = gateway.solve(SPEC);
  worker.emit('message', { data: { type: 'result', result: expected } });
  const got = await pending;
  // Assert
  assert.deepEqual(worker.posted, [{ type: 'solve', spec: SPEC }]);
  assert.deepEqual(got, expected);
});

test('進捗メッセージを onProgress へ中継する', async () => {
  // Arrange
  const { gateway, worker } = build();
  const seen = [];
  // Act
  const pending = gateway.solve(SPEC, (r) => seen.push(r));
  worker.emit('message', { data: { type: 'progress', ratio: 0.5 } });
  worker.emit('message', { data: { type: 'progress', ratio: 0.9 } });
  worker.emit('message', { data: { type: 'result', result: {} } });
  await pending;
  // Assert
  assert.deepEqual(seen, [0.5, 0.9]);
});

test('onProgress 未指定でも進捗メッセージで壊れない', async () => {
  // Arrange
  const { gateway, worker } = build();
  // Act
  const pending = gateway.solve(SPEC);
  worker.emit('message', { data: { type: 'progress', ratio: 0.5 } });
  worker.emit('message', { data: { type: 'result', result: { ok: 1 } } });
  // Assert
  assert.deepEqual(await pending, { ok: 1 });
});

test('決着したら worker を後片付けする（成功時）', async () => {
  // Arrange
  const { gateway, worker } = build();
  // Act
  const pending = gateway.solve(SPEC);
  worker.emit('message', { data: { type: 'result', result: {} } });
  await pending;
  // Assert
  assert.equal(worker.terminated, 1);
});

test('worker の error イベントは McUnavailableError になり、後片付けもする', async () => {
  // Arrange
  const { gateway, worker } = build();
  // Act
  const pending = gateway.solve(SPEC);
  worker.emit('error', { message: 'boom' });
  // Assert
  await assert.rejects(() => pending, (err) => {
    assert.ok(err instanceof McUnavailableError);
    assert.match(err.message, /boom/);
    return true;
  });
  assert.equal(worker.terminated, 1);
});

test('worker からの error メッセージも McUnavailableError になる（計算側の失敗）', async () => {
  // Arrange
  const { gateway, worker } = build();
  // Act
  const pending = gateway.solve(SPEC);
  worker.emit('message', { data: { type: 'error', message: 'win_rate は [0,1] の比です' } });
  // Assert
  await assert.rejects(() => pending, (err) => {
    assert.ok(err instanceof McUnavailableError);
    assert.match(err.message, /win_rate/);
    return true;
  });
  assert.equal(worker.terminated, 1);
});

test('worker 生成に失敗したら McUnavailableError（原因を保持する）', async () => {
  // Arrange
  const cause = new Error('SecurityError');
  const { gateway } = build({ factory: () => { throw cause; } });
  // Act / Assert
  await assert.rejects(() => gateway.solve(SPEC), (err) => {
    assert.ok(err instanceof McUnavailableError);
    assert.equal(err.cause, cause, '原因を握り潰さない');
    return true;
  });
});

test('Worker 非対応の実行環境では McUnavailableError（既定ファクトリ）', async () => {
  // Arrange — node には DOM Worker が無い＝既定ファクトリはここで必ず失敗する
  assert.equal(typeof Worker, 'undefined', '前提: 実行環境に DOM Worker が無い');
  const gateway = new McWorkerGateway();
  // Act / Assert
  await assert.rejects(() => gateway.solve(SPEC), McUnavailableError);
});

test('未知の種別のメッセージは無視する（解決も棄却もしない）', async () => {
  // Arrange
  const { gateway, worker } = build();
  let settled = false;
  // Act
  const pending = gateway.solve(SPEC).then(() => { settled = true; });
  worker.emit('message', { data: { type: 'chatter' } });
  worker.emit('message', {});
  await Promise.resolve();
  // Assert
  assert.equal(settled, false);
  worker.emit('message', { data: { type: 'result', result: {} } });
  await pending;
  assert.equal(settled, true);
});

test('決着後に届いたメッセージで二重解決しない', async () => {
  // Arrange
  const { gateway, worker } = build();
  // Act
  const pending = gateway.solve(SPEC);
  worker.emit('message', { data: { type: 'result', result: { n: 1 } } });
  worker.emit('message', { data: { type: 'result', result: { n: 2 } } });
  worker.emit('error', { message: 'late' });
  // Assert
  assert.deepEqual(await pending, { n: 1 });
  assert.equal(worker.terminated, 1, '後片付けも 1 回だけ');
});
