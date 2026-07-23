// update_scheduler.test.js — UpdateScheduler（ISSUE-157 クロック駆動設計）の単体テスト。
//
// indicator_controller.test.js のクロック駆動系テストから移設（SOLID 是正 🔴-1・
//   UpdateScheduler 抽出に伴う単体テスト化。アサーションの意味は移設前と同一）。
//   要求はフラグ＋_drive() で駆動し、実行中要求の完了を待たない。連発は 1 試行に畳む
//   （latest-wins）。ハングした試行は STALL_DEADLINE_MS 経過後のクロックが無視して
//   新試行を発行する＝凍結という吸収状態が存在しない。
//   controller 側の実体配線（runForming/runFull/isBlocked）と isRecomputing 時限は
//   indicator_controller.test.js が固定する。

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { UpdateScheduler, STALL_DEADLINE_MS } from '../js/adapter/front/update_scheduler.js';

function newScheduler(overrides = {}) {
  return new UpdateScheduler({
    runForming: async () => {},
    runFull: async () => {},
    ...overrides,
  });
}

test('requestForming coalesces burst requests (進行中は 1 試行に畳む)', async () => {
  let runs = 0;
  let release;
  const sched = newScheduler({
    runForming: () => new Promise((res) => { runs += 1; release = res; }),
  });
  // Act: 進行中に 4 連発 → 実行 1 回＋積み残し 1 回に畳まれる。
  sched.requestForming();
  sched.requestForming();
  sched.requestForming();
  sched.requestForming();
  assert.equal(runs, 1);
  release();                                   // 1 回目完了 → 積み残しが 1 回だけ走る
  await new Promise((r) => setTimeout(r, 0));
  assert.equal(runs, 2);
  release();                                   // 2 回目完了 → 要求なし＝追加実行なし
  await new Promise((r) => setTimeout(r, 0));
  assert.equal(runs, 2);
});

test('requestForming defers while blocked (外部バッチ isRecomputing 相当)', async () => {
  let blocked = true;                          // 全再計算バッチ中（健全＝開始直後）
  let runs = 0;
  const sched = newScheduler({
    runForming: async () => { runs += 1; },
    isBlocked: () => blocked,
  });
  sched.requestForming();
  assert.equal(runs, 0);                       // 衝突回避＝実行しない（フラグ保持）
  blocked = false;
  sched.requestForming();                      // 次クロックでフラグごと消化
  await new Promise((r) => setTimeout(r, 0));
  assert.equal(runs, 1);
});

// ISSUE-151: バー確定 full 要求は取り落とさない（forming 実行中は want 保持 → 完了時に消化）。
test('requestFull want is drained after a forming run (bar-close never lost)', async () => {
  let fullRuns = 0;
  let releaseForming;
  const sched = newScheduler({
    runForming: () => new Promise((res) => { releaseForming = res; }),
    runFull: async () => { fullRuns += 1; },
  });
  // forming 実行中にバー確定 → want 保持
  sched.requestForming();
  sched.requestFull();
  assert.equal(fullRuns, 0);
  // forming 完了 → full want が優先消化され必ず実行される
  releaseForming();
  await new Promise((r) => setTimeout(r, 0));
  await new Promise((r) => setTimeout(r, 0));
  assert.equal(fullRuns, 1);
});

test('requestFull coalesces (進行中の連発は 1 積み残しに畳む)', async () => {
  let runs = 0; let release;
  const sched = newScheduler({
    runFull: () => new Promise((res) => { runs += 1; release = res; }),
  });
  sched.requestFull();
  sched.requestFull();
  sched.requestFull();
  assert.equal(runs, 1);
  release();
  await new Promise((r) => setTimeout(r, 0));
  assert.equal(runs, 2);   // 積み残し分の 1 回だけ
  release();
  await new Promise((r) => setTimeout(r, 0));
  assert.equal(runs, 2);
});

// ISSUE-157 中核: 試行がハング（永久 pending）しても、STALL_DEADLINE_MS 経過後のクロックは
//   その完了を待たずに新試行を発行する＝1 本のハングが全体を凍結させない。
test('a hung attempt does not freeze the pipeline (clock issues a new attempt after the deadline)', async () => {
  let runs = 0;
  const sched = newScheduler({
    runForming: () => new Promise(() => { runs += 1; }),  // 永久 pending（実ストール再現）
  });
  sched.requestForming();
  assert.equal(runs, 1);
  // 健全期間内の要求は畳まれる（凍結ではなく coalesce）。
  sched.requestForming();
  assert.equal(runs, 1);
  // ハング判定: 開始時刻を STALL_DEADLINE_MS 超過へ戻す（実時間待ちなしで期限切れを再現）。
  sched.attemptStartedMs = Date.now() - (STALL_DEADLINE_MS + 1);
  sched.requestForming();                      // 次クロック＝ハングを無視して新試行
  assert.equal(runs, 2, 'ハングした試行の完了を待たずに新試行が発行される');
});

// ISSUE-151 追補2: full 再計算が一時障害で失敗したら want を立て直し、次のクロック
//   （次 tick の要求）で再試行する。即時自己リトライはしない（タイトループ防止）。
test('requestFull retries on the next clock after a transient failure (bar-close never dropped)', async () => {
  let calls = 0;
  const sched = newScheduler({
    runFull: async () => {
      calls += 1;
      if (calls === 1) throw new Error('Failed to fetch');   // 1 回目は一時障害
    },
  });
  sched.requestFull();
  await new Promise((r) => setTimeout(r, 0));
  assert.equal(calls, 1, '失敗直後に即時自己リトライしない');
  assert.equal(sched.wantFull, true, '失敗は want に立て直される（必達）');
  // 次のクロック（次 tick の forming 要求）で full が優先再試行される
  sched.requestForming();
  await new Promise((r) => setTimeout(r, 0));
  await new Promise((r) => setTimeout(r, 0));
  assert.equal(calls, 2, '失敗した full が再試行される');
});
