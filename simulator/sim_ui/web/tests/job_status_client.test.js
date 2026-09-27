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
  JobStatusError, MAX_CONSECUTIVE_FAILURES, POLL_INTERVAL_MS, WAIT_MS,
  createJobStatusClient, jobStatusUrl,
} from "../js/adapter/front/job_status_client.js";

function fakeFetch(response) {
  const calls = [];
  const fn = async (url, init) => { calls.push({ url, init }); return response; };
  fn.calls = calls;
  return fn;
}

/** 応答を 1 回ずつ順に返す fetch（監視の遷移を決定的に組む）。
 *  台本が尽きたら**解決しない Promise** を返す（保留照会の直列は成功のたび直ちに次を
 *  張るため、末尾を繰り返すと非終端の台本がテストの中で無限ループになる）。 */
function scriptedFetch(responses) {
  const calls = [];
  const fn = async (url, init) => {
    calls.push({ url, init });
    if (calls.length > responses.length) return new Promise(() => {});
    const next = responses[calls.length - 1];
    return typeof next === "function" ? next() : next;
  };
  fn.calls = calls;
  return fn;
}

/** 直列の保留照会が今回のぶんまで進むのを待つ（マイクロタスクを刻む）。 */
async function settle(times = 6) {
  for (let i = 0; i < times; i += 1) await Promise.resolve();
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

test("POLL_INTERVAL_MS is the retry interval and WAIT_MS matches the server cap (NFR-04 改訂)", () => {
  // Arrange / Act / Assert
  assert.equal(POLL_INTERVAL_MS, 1000);
  assert.equal(WAIT_MS, 25000);
});

test("watch issues held queries back to back without a timer (保留照会の直列・NFR-04 改訂)", async () => {
  // Arrange
  const fetchFn = scriptedFetch([running(), running(), completed()]);
  const timer = fakeTimer();
  const seen = [];
  const client = createJobStatusClient({ fetch: fetchFn, setTimeout: timer.set, clearTimeout: timer.clear });
  // Act
  client.watch("j1", (u) => seen.push(u));
  await settle(12);
  // Assert
  assert.deepEqual(seen.map((u) => u.status), ["running", "running", "completed"]);
  assert.deepEqual(timer.delays, [], "成功応答の間にタイマーを使っています（周期ポーリングの再発）");
  assert.match(fetchFn.calls[0].url, /\?wait_ms=25000$/, "保留を要求していません");
  // 計算量: 発行した照会 − 受けた応答 = 0（周期に比例しない）。
  assert.equal(fetchFn.calls.length - seen.length, 0);
});

test("watch stops once the server reports a terminal state (終端後は照会しない)", async () => {
  // Arrange
  const fetchFn = scriptedFetch([completed()]);
  const timer = fakeTimer();
  const seen = [];
  const client = createJobStatusClient({ fetch: fetchFn, setTimeout: timer.set, clearTimeout: timer.clear });
  // Act
  client.watch("j1", (u) => seen.push(u));
  await settle();
  // Assert
  assert.deepEqual(seen.map((u) => u.status), ["completed"]);
  assert.equal(fetchFn.calls.length, 1, "終端後も照会しています");
  assert.equal(timer.pending.size, 0, "終端後に予約が残っています");
});

test("stop() halts the watch and aborts the held connection (再投入で前の監視を落とせる)", async () => {
  // Arrange: 解決しない保留照会（サーバが握っている状態）
  let aborted = false;
  const fetchFn = async (url, init) => {
    if (init && init.signal) init.signal.addEventListener("abort", () => { aborted = true; });
    return new Promise(() => {});
  };
  const timer = fakeTimer();
  const seen = [];
  const client = createJobStatusClient({ fetch: fetchFn, setTimeout: timer.set, clearTimeout: timer.clear });
  const stop = client.watch("j1", (u) => seen.push(u));
  await settle();
  // Act
  stop();
  // Assert
  assert.equal(aborted, true, "保留中の接続を切っていません（接続が溜まる）");
  assert.equal(seen.length, 0);
  assert.equal(timer.pending.size, 0);
});

test("stop() の後に切れた保留照会は失敗として数えない（諦めの誤発火なし）", async () => {
  // Arrange: stop() で reject する fetch（AbortError 相当）
  let rejectHeld = null;
  const fetchFn = async (url, init) => new Promise((_res, rej) => {
    rejectHeld = rej;
    if (init && init.signal) init.signal.addEventListener("abort", () => rej(new Error("aborted")));
  });
  const timer = fakeTimer();
  const seen = [];
  const client = createJobStatusClient({ fetch: fetchFn, setTimeout: timer.set, clearTimeout: timer.clear });
  const stop = client.watch("j1", (u) => seen.push(u));
  await settle();
  // Act
  stop();
  await settle();
  // Assert: 購読者へは何も届かない（error 掲示もしない）
  assert.deepEqual(seen, []);
  assert.equal(typeof rejectHeld, "function");
});

test("watch retries failures on POLL_INTERVAL_MS and gives up after the cap", async () => {
  // Arrange
  const fetchFn = scriptedFetch([boom(), boom(), boom()]);
  const timer = fakeTimer();
  const seen = [];
  const client = createJobStatusClient({ fetch: fetchFn, setTimeout: timer.set, clearTimeout: timer.clear });
  client.watch("j1", (u) => seen.push(u));
  await settle();
  // Act: 失敗の再試行はタイマー経由（保留照会の直列とは違う経路であることも固定）
  await timer.tick(); await settle();
  await timer.tick(); await settle();
  // Assert
  assert.equal(fetchFn.calls.length, MAX_CONSECUTIVE_FAILURES);
  assert.deepEqual(timer.delays, [POLL_INTERVAL_MS, POLL_INTERVAL_MS]);
  assert.equal(timer.pending.size, 0, "上限に達しても監視が続いています");
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
  // Act
  const errors = await capturingErrors(async () => {
    client.watch("j1", (u) => {
      seen.push(u);
      if (seen.length === 1) throw new Error("掲示できません");
    });
    await settle(12);
  });
  // Assert: 例外の後も保留照会の直列が続く（2 回目の掲示まで届く）
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
  // Act
  await capturingErrors(async () => {
    client.watch("j1", () => { throw new Error("掲示できません"); });
    await settle();
  });
  // Assert: 終端で止まる（購読者が落ちても照会し続けない）
  assert.equal(fetchFn.calls.length, 1, "終端後も照会しています");
  assert.equal(timer.pending.size, 0, "終端後に予約が残っています");
});

test("a throwing subscriber on the give-up report does not escape as a rejection", async () => {
  // Arrange: 照会が常に失敗し、諦めの通知でも購読者が例外を投げる
  const fetchFn = scriptedFetch([boom(), boom(), boom()]);
  const timer = fakeTimer();
  const client = createJobStatusClient({ fetch: fetchFn, setTimeout: timer.set, clearTimeout: timer.clear });
  // Act: 初回は即時・以降の再試行はタイマー経由
  const errors = await capturingErrors(async () => {
    client.watch("j1", () => { throw new Error("掲示できません"); });
    await settle();
    for (let i = 1; i < MAX_CONSECUTIVE_FAILURES; i += 1) {
      await assert.doesNotReject(() => timer.tick(),
        "諦めの通知で投げられた例外が監視ループへ抜けています");
      await settle();
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
  await settle();       // 失敗 1 回目（即時の初回照会）→ 再試行をタイマーへ予約
  // Act
  await timer.tick();   // 成功（連続失敗カウンタが戻る）→ 直列で次の保留照会 → 成功 → 保留
  await settle(12);
  // Assert
  assert.deepEqual(seen.map((u) => u.status), ["running", "running"]);
  assert.deepEqual(timer.delays, [POLL_INTERVAL_MS], "再試行以外でタイマーを使っています");
  assert.equal(fetchFn.calls.length, 4, "保留照会の直列が続いていません（4 回目が保留中のはず）");
});
