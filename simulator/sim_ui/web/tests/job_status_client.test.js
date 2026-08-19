// job_status_client（ジョブ状態の照会・Phase 9 段階 3 S2 M7・§19.6）の単体テスト（fake fetch）。
//
// 固定する不変条件:
//   1. fetchStatus は同一オリジン相対の `GET /sim/jobs/{id}`（`cache: "no-store"`）。
//   2. 2xx なら parse 済み JSON をそのまま返す（front は応答を組み替えない）。
//   3. 非 2xx は**サーバの error 文言つき**で throw する（無音にしない）。
//   4. 本文が JSON でなくても throw する（HTTP は 200 でも中身が読めない構成）。
import { test } from "node:test";
import assert from "node:assert/strict";

import { JobStatusError, createJobStatusClient, jobStatusUrl } from "../js/adapter/front/job_status_client.js";

function fakeFetch(response) {
  const calls = [];
  const fn = async (url, init) => { calls.push({ url, init }); return response; };
  fn.calls = calls;
  return fn;
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
