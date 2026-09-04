// update_scheduler.test.js — UpdateScheduler（ISSUE-157 クロック駆動設計）の単体テスト。
//
// indicator_controller.test.js のクロック駆動系テストから移設（SOLID 是正 🔴-1・
//   UpdateScheduler 抽出に伴う単体テスト化。アサーションの意味は移設前と同一）。
//   要求はフラグ＋_drive() で駆動し、実行中要求の完了を待たない。連発は 1 試行に畳む
//   （latest-wins）。ハングした試行は STALL_DEADLINE_MS 経過後のクロックが無視して
//   新試行を発行する＝凍結という吸収状態が存在しない。
//   controller 側の実体配線（runFull/isBlocked）と isRecomputing 時限は
//   indicator_controller.test.js が固定する。
//
// ISSUE-250 Phase 1: 足内（forming）要求は本スケジューラから廃止された。tick 粒度の末尾値は
//   /live_ticks 同梱を LiveTickPlayer が同期に描く（往復も coalesce も無い）ため、
//   スケジューラが扱うのはバー確定 full 再計算（必達）のみ。旧 requestForming 系のテストは
//   同じ意味（coalesce・blocked 遅延・ハング非凍結）を requestFull で固定し直す。

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { UpdateScheduler, STALL_DEADLINE_MS } from '../js/adapter/front/update_scheduler.js';

function newScheduler(overrides = {}) {
  return new UpdateScheduler({
    runFull: async () => {},
    ...overrides,
  });
}

test('requestFull coalesces burst requests (進行中は 1 試行に畳む)', async () => {
  let runs = 0;
  let release;
  const sched = newScheduler({
    runFull: () => new Promise((res) => { runs += 1; release = res; }),
  });
  // Act: 進行中に 4 連発 → 実行 1 回＋積み残し 1 回に畳まれる。
  sched.requestFull();
  sched.requestFull();
  sched.requestFull();
  sched.requestFull();
  assert.equal(runs, 1);
  release();                                   // 1 回目完了 → 積み残しが 1 回だけ走る
  await new Promise((r) => setTimeout(r, 0));
  assert.equal(runs, 2);
  release();                                   // 2 回目完了 → 要求なし＝追加実行なし
  await new Promise((r) => setTimeout(r, 0));
  assert.equal(runs, 2);
});

test('requestFull defers while blocked (外部バッチ isRecomputing 相当)', async () => {
  let blocked = true;                          // 全再計算バッチ中（健全＝開始直後）
  let runs = 0;
  const sched = newScheduler({
    runFull: async () => { runs += 1; },
    isBlocked: () => blocked,
  });
  sched.requestFull();
  assert.equal(runs, 0);                       // 衝突回避＝実行しない（フラグ保持）
  blocked = false;
  sched.requestFull();                         // 次クロックでフラグごと消化
  await new Promise((r) => setTimeout(r, 0));
  assert.equal(runs, 1);
});

// ISSUE-151: バー確定 full 要求は取り落とさない（実行中に積まれた要求は完了時に消化）。
test('requestFull want is drained after the in-flight run (bar-close never lost)', async () => {
  let fullRuns = 0;
  let release;
  const sched = newScheduler({
    runFull: () => new Promise((res) => { fullRuns += 1; release = res; }),
  });
  // 実行中にバー確定 → want 保持
  sched.requestFull();
  assert.equal(fullRuns, 1);
  sched.requestFull();
  assert.equal(fullRuns, 1, '進行中は畳まれる');
  // 完了 → 積み残しの full want が必ず消化される
  release();
  await new Promise((r) => setTimeout(r, 0));
  assert.equal(fullRuns, 2);
});

// ISSUE-157 中核: 試行がハング（永久 pending）しても、STALL_DEADLINE_MS 経過後のクロックは
//   その完了を待たずに新試行を発行する＝1 本のハングが全体を凍結させない。
test('a hung attempt does not freeze the pipeline (clock issues a new attempt after the deadline)', async () => {
  let runs = 0;
  const sched = newScheduler({
    runFull: () => new Promise(() => { runs += 1; }),  // 永久 pending（実ストール再現）
  });
  sched.requestFull();
  assert.equal(runs, 1);
  // 健全期間内の要求は畳まれる（凍結ではなく coalesce）。
  sched.requestFull();
  assert.equal(runs, 1);
  // ハング判定: 開始時刻を STALL_DEADLINE_MS 超過へ戻す（実時間待ちなしで期限切れを再現）。
  sched.attemptStartedMs = Date.now() - (STALL_DEADLINE_MS + 1);
  sched.requestFull();                         // 次クロック＝ハングを無視して新試行
  assert.equal(runs, 2, 'ハングした試行の完了を待たずに新試行が発行される');
});

// ISSUE-151 追補2: full 再計算が一時障害で失敗したら want を立て直し、次のクロック
//   （次のバー確定検知）で再試行する。即時自己リトライはしない（タイトループ防止）。
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
  // 次のクロック（次のバー確定要求）で full が再試行される
  sched.requestFull();
  await new Promise((r) => setTimeout(r, 0));
  await new Promise((r) => setTimeout(r, 0));
  assert.equal(calls, 2, '失敗した full が再試行される');
});

// ISSUE-250 Phase 1: 足内要求の面そのものが存在しない（残っていると tick と無関係な回数の
//   指標更新が復活し「指標更新回数 == ローソク更新回数」が崩れる）。
test('the forming-request surface is gone (tick-path round trips are structurally impossible)', () => {
  const sched = newScheduler();
  assert.equal(typeof sched.requestForming, 'undefined');
  assert.equal(sched.wantForming, undefined);
});
