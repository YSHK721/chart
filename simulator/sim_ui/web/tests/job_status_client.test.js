// job_status_client（ジョブ状態の照会・Phase 9 段階 3 S2 M7・§19.6）の単体テスト（fake fetch）。
//
// 固定する不変条件:
//   1. fetchStatus は同一オリジン相対の `GET /sim/jobs/{id}`（`cache: "no-store"`）。
//   2. 2xx なら parse 済み JSON をそのまま返す（front は応答を組み替えない）。
//   3. 非 2xx は**サーバの error 文言つき**で throw する（無音にしない）。
//   4. 本文が JSON でなくても throw する（HTTP は 200 でも中身が読めない構成）。
import { test } from "node:test";
import assert from "node:assert/strict";

import {
  JobStatusError, MAX_CONSECUTIVE_FAILURES, POLL_INTERVAL_MS,
  createJobStatusClient, jobStatusUrl,
} from "../js/adapter/front/job_status_client.js";

function fakeFetch(response) {
  const calls = [];
  const fn = async (url, init) => { calls.push({ url, init }); return response; };
  fn.calls = calls;
  return fn;
}

/** 応答を 1 回ずつ順に返す fetch（監視の遷移を決定的に組む）。 */
function scriptedFetch(responses) {
  const calls = [];
  const fn = async (url, init) => {
    calls.push({ url, init });
    const next = responses[Math.min(calls.length - 1, responses.length - 1)];
    return typeof next === "function" ? next() : next;
  };
  fn.calls = calls;
  return fn;
}

/** 注入 timer のダブル（実時間を待たずに周期を進める）。 */
function fakeTimer() {
  const pending = new Map();
  let nextId = 1;
  const delays = [];
  return {
    delays,
    pending,
    set(fn, ms) {
      const id = nextId;
      nextId += 1;
      delays.push(ms);
      pending.set(id, fn);
      return id;
    },
    clear(id) { pending.delete(id); },
    /** 予約されている最も古いコールバックを 1 回だけ走らせる。 */
    async tick() {
      const [id, fn] = [...pending.entries()][0] || [];
      if (fn === undefined) return false;
      pending.delete(id);
      await fn();
      return true;
    },
  };
}

const okResponse = (payload) => ({ ok: true, status: 200, json: async () => payload });

test("jobStatusUrl is the single source of the status endpoint", () => {
  // Arrange / Act / Assert
  assert.equal(jobStatusUrl("j1"), "/sim/jobs/j1");
});

test("fetchStatus GETs the job endpoint without caching", async () => {
  // Arrange
  const fetchFn = fakeFetch(okResponse({ job_id: "j1", status: "running", terminal: false }));
  // Act
  await createJobStatusClient({ fetch: fetchFn }).fetchStatus("j1");
  // Assert
  assert.equal(fetchFn.calls[0].url, "/sim/jobs/j1");
  assert.equal(fetchFn.calls[0].init.cache, "no-store",
    "キャッシュを許すと実行中のまま固まった表示になります");
});

test("fetchStatus returns the parsed payload verbatim (200)", async () => {
  // Arrange
  const payload = { job_id: "j1", status: "completed", failure_reason: null, terminal: true };
  const fetchFn = fakeFetch(okResponse(payload));
  // Act
  const got = await createJobStatusClient({ fetch: fetchFn }).fetchStatus("j1");
  // Assert
  assert.deepEqual(got, payload);
});

// --- 応答の 4 形（200 / 404 / 非 JSON / 502）--------------------------------------

test("fetchStatus throws with the server message on 404 (未知のジョブ)", async () => {
  // Arrange
  const fetchFn = fakeFetch({
    ok: false, status: 404, json: async () => ({ error: "未知のジョブ識別子です: zz" }),
  });
  // Act / Assert
  await assert.rejects(
    () => createJobStatusClient({ fetch: fetchFn }).fetchStatus("zz"),
    (e) => e instanceof JobStatusError && /未知のジョブ識別子/.test(e.message) && e.status === 404,
  );
});

test("fetchStatus throws when the body is not JSON (200 でも中身が読めない構成)", async () => {
  // Arrange: HTTP は 200 だが本文が HTML（プロキシのエラーページ等）
  const fetchFn = fakeFetch({
    ok: true, status: 200, json: async () => { throw new Error("Unexpected token <"); },
  });
  // Act / Assert
  await assert.rejects(
    () => createJobStatusClient({ fetch: fetchFn }).fetchStatus("j1"),
    (e) => e instanceof JobStatusError,
  );
});

test("fetchStatus throws with the HTTP status when the gateway fails (502)", async () => {
  // Arrange: 本文に error 文言が無い上流障害
  const fetchFn = fakeFetch({ ok: false, status: 502, json: async () => { throw new Error("no body"); } });
  // Act / Assert
  await assert.rejects(
    () => createJobStatusClient({ fetch: fetchFn }).fetchStatus("j1"),
    (e) => e instanceof JobStatusError && /502/.test(e.message) && e.status === 502,
  );
});

// --- watch（周期照会・Phase 9 段階 3 S4）------------------------------------------
// 固定する不変条件:
//   1. 周期は NFR-04 の 1000ms（定数は 1 箇所・注入 timer で確かめる）。
//   2. `terminal === true` を受けたら**止まる**（front は終端集合を持たない）。
//   3. 連続失敗が上限に達したら止まり、理由を購読者へ渡す（無音で監視を諦めない）。
//   4. `stop()` で止まる（再投入時に前の監視を落とすため合成根が使う）。

const running = () => ({ ok: true, status: 200, json: async () => ({ job_id: "j1", status: "running", terminal: false }) });
const completed = () => ({ ok: true, status: 200, json: async () => ({ job_id: "j1", status: "completed", terminal: true }) });
const boom = () => ({ ok: false, status: 502, json: async () => { throw new Error("no body"); } });

test("POLL_INTERVAL_MS is the single source of the polling period (NFR-04)", () => {
  // Arrange / Act / Assert
  assert.equal(POLL_INTERVAL_MS, 1000);
});

test("watch polls on the NFR-04 period", async () => {
  // Arrange
  const timer = fakeTimer();
  const client = createJobStatusClient({ fetch: scriptedFetch([running()]), setTimeout: timer.set, clearTimeout: timer.clear });
  // Act
  client.watch("j1", () => {});
  // Assert
  assert.deepEqual(timer.delays, [POLL_INTERVAL_MS]);
});

test("watch stops once the server reports a terminal state (running x2 -> completed)", async () => {
  // Arrange
  const fetchFn = scriptedFetch([running(), running(), completed()]);
  const timer = fakeTimer();
  const seen = [];
  const client = createJobStatusClient({ fetch: fetchFn, setTimeout: timer.set, clearTimeout: timer.clear });
  // Act
  client.watch("j1", (u) => seen.push(u));
  await timer.tick();
  await timer.tick();
  await timer.tick();
  const more = await timer.tick();   // 終端後は予約が残っていない
  // Assert
  assert.deepEqual(seen.map((u) => u.status), ["running", "running", "completed"]);
  assert.equal(more, false, "終端に達しても監視が続いています");
  assert.equal(fetchFn.calls.length, 3, "終端後も照会しています");
});

test("stop() halts the watch (再投入で前の監視を落とせる)", async () => {
  // Arrange
  const fetchFn = scriptedFetch([running()]);
  const timer = fakeTimer();
  const client = createJobStatusClient({ fetch: fetchFn, setTimeout: timer.set, clearTimeout: timer.clear });
  const stop = client.watch("j1", () => {});
  // Act
  stop();
  const more = await timer.tick();
  // Assert
  assert.equal(more, false, "stop() 後も予約が残っています");
  assert.equal(fetchFn.calls.length, 0);
});

test("watch gives up after consecutive failures and reports why", async () => {
  // Arrange
  const fetchFn = scriptedFetch([boom()]);
  const timer = fakeTimer();
  const seen = [];
  const client = createJobStatusClient({ fetch: fetchFn, setTimeout: timer.set, clearTimeout: timer.clear });
  client.watch("j1", (u) => seen.push(u));
  // Act: 上限回数だけ失敗させる
  for (let i = 0; i < MAX_CONSECUTIVE_FAILURES; i += 1) await timer.tick();
  const more = await timer.tick();
  // Assert
  assert.equal(more, false, "上限に達しても監視が続いています");
  assert.equal(fetchFn.calls.length, MAX_CONSECUTIVE_FAILURES);
  assert.equal(seen.length, 1, "諦めたことを購読者へ伝えていません（無音の停止）");
  assert.match(String(seen[0].error), /502/);
});

// --- watch の生存が購読者に依存しないこと（監視の無音死の禁止）------------------------
// `onUpdate` は front の掲示側（M6 を呼ぶ合成根）である。そこが例外を投げると `poll` の
// promise が reject するが、timer コールバックの戻り値は誰も待っていないため unhandled
// rejection として消え、**次の照会が予約されないまま監視が黙って死ぬ**（実行中のジョブの
// 状態が二度と更新されない＝ISSUE-423 が是正したはずの沈黙の再発）。
// 監視ループの継続・終端停止・諦めの判断は、いずれも購読者の成否と独立でなければならない。

/** console.error を採取しながら関数を走らせる。 */
async function capturingErrors(fn) {
  const seen = [];
  const original = console.error;
  console.error = (...args) => seen.push(args.map(String).join(" "));
  try {
    await fn();
  } finally {
    console.error = original;
  }
  return seen;
}

test("a throwing subscriber does not kill the watch (購読者の例外で無音停止しない)", async () => {
  // Arrange: 1 回目の掲示で例外を投げる購読者
  const fetchFn = scriptedFetch([running(), running()]);
  const timer = fakeTimer();
  const seen = [];
  const client = createJobStatusClient({ fetch: fetchFn, setTimeout: timer.set, clearTimeout: timer.clear });
  client.watch("j1", (u) => {
    seen.push(u);
    if (seen.length === 1) throw new Error("掲示できません");
  });
  // Act
  const errors = await capturingErrors(async () => {
    await assert.doesNotReject(() => timer.tick(),
      "購読者の例外が監視ループへ抜けています（unhandled rejection）");
  });
  // Assert: 次の照会が予約されており、監視は続く
  assert.equal(timer.pending.size, 1, "購読者の例外で次の照会が予約されていません（監視が無音で死んでいます）");
  await timer.tick();
  assert.equal(seen.length, 2, "例外の後に監視が続いていません");
  // 理由は握り潰さない（掲示側の不具合が誰にも見えなくなる）
  assert.ok(errors.some((line) => /掲示できません/.test(line)),
    `購読者の例外が console.error に残っていません: ${JSON.stringify(errors)}`);
});

test("a throwing subscriber does not defeat the terminal stop (終端停止は購読者に依存しない)", async () => {
  // Arrange: 終端応答の掲示で例外を投げる購読者
  const fetchFn = scriptedFetch([completed()]);
  const timer = fakeTimer();
  const client = createJobStatusClient({ fetch: fetchFn, setTimeout: timer.set, clearTimeout: timer.clear });
  client.watch("j1", () => { throw new Error("掲示できません"); });
  // Act
  await capturingErrors(async () => {
    await assert.doesNotReject(() => timer.tick());
  });
  // Assert: 終端で止まる（購読者が落ちても照会し続けない）
  assert.equal(await timer.tick(), false, "終端に達しても監視が続いています");
  assert.equal(fetchFn.calls.length, 1, "終端後も照会しています");
});

test("a throwing subscriber on the give-up report does not escape as a rejection", async () => {
  // Arrange: 照会が常に失敗し、諦めの通知でも購読者が例外を投げる
  const fetchFn = scriptedFetch([boom()]);
  const timer = fakeTimer();
  const client = createJobStatusClient({ fetch: fetchFn, setTimeout: timer.set, clearTimeout: timer.clear });
  client.watch("j1", () => { throw new Error("掲示できません"); });
  // Act
  const errors = await capturingErrors(async () => {
    for (let i = 0; i < MAX_CONSECUTIVE_FAILURES; i += 1) {
      await assert.doesNotReject(() => timer.tick(),
        "諦めの通知で投げられた例外が監視ループへ抜けています");
    }
  });
  // Assert
  assert.equal(await timer.tick(), false, "上限に達しても監視が続いています");
  assert.ok(errors.some((line) => /掲示できません/.test(line)),
    `購読者の例外が console.error に残っていません: ${JSON.stringify(errors)}`);
});

test("a single failure between successes does not stop the watch (境界値: 連続でない失敗)", async () => {
  // Arrange
  const fetchFn = scriptedFetch([boom(), running(), running()]);
  const timer = fakeTimer();
  const seen = [];
  const client = createJobStatusClient({ fetch: fetchFn, setTimeout: timer.set, clearTimeout: timer.clear });
  client.watch("j1", (u) => seen.push(u));
  // Act
  await timer.tick();   // 失敗 1 回目
  await timer.tick();   // 成功（連続失敗カウンタが戻る）
  await timer.tick();   // 成功
  // Assert
  assert.deepEqual(seen.map((u) => u.status), ["running", "running"]);
  assert.equal(timer.pending.size, 1, "成功したのに監視が止まっています");
});
