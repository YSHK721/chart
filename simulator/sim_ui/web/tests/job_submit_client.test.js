// job_submit_client（ジョブ投入・指標候補取得・Phase 6 F-8）の単体テスト（fake fetch）。
//
// 固定する不変条件:
//   1. submit は同一オリジン相対の POST /sim/jobs（JSON 本文 {backtest, strategy, sizing}）。
//      strategy / sizing は不在なら本文に載せない（OFF は既存 2 キー本文と byte 等価）。
//   2. submit は 2xx なら parse 済み JSON（job_id / status）を返す。
//   3. submit は非 2xx なら**サーバの error 文言つき**で throw する（無音にしない）。
//   4. loadEaSeries は GET /sim/ea-series/{ea_name} の payload をそのまま返す（候補源の単一
//      ソース＝その EA の registry 系列名。/sim/indicators は候補源に使わない）。
import { test } from "node:test";
import assert from "node:assert/strict";

import {
  JOBS_URL, EA_SERIES_URL, createJobSubmitClient,
} from "../js/adapter/front/job_submit_client.js";

function fakeFetch(response) {
  const calls = [];
  const fn = async (url, init) => { calls.push({ url, init }); return response; };
  fn.calls = calls;
  return fn;
}
const okResponse = (payload) => ({ ok: true, status: 202, json: async () => payload });
const errResponse = (payload, status = 400) => ({ ok: false, status, json: async () => payload });

const STRATEGY = { entry_long: [{ indicator: "close", shift: 1, op: ">", rhs: 1.0 }] };

test("URL constants are the single source of the endpoints", () => {
  assert.equal(JOBS_URL, "/sim/jobs");
  assert.equal(EA_SERIES_URL, "/sim/ea-series");
});

test("submit posts to /sim/jobs with method POST", async () => {
  const fetchFn = fakeFetch(okResponse({ job_id: "j1", status: "running" }));
  await createJobSubmitClient({ fetch: fetchFn }).submit({ backtest: { ea_name: "X" } });
  assert.equal(fetchFn.calls[0].url, "/sim/jobs");
  assert.equal(fetchFn.calls[0].init.method, "POST");
});

test("submit sends backtest and strategy in the JSON body", async () => {
  const fetchFn = fakeFetch(okResponse({ job_id: "j1", status: "running" }));
  await createJobSubmitClient({ fetch: fetchFn }).submit({
    backtest: { ea_name: "TC24051901", stop_loss_points: 100 },
    strategy: STRATEGY,
  });
  const body = JSON.parse(fetchFn.calls[0].init.body);
  assert.deepEqual(body.backtest, { ea_name: "TC24051901", stop_loss_points: 100 });
  assert.deepEqual(body.strategy, STRATEGY);
});

test("submit omits strategy when absent (OFF は既存本文と byte 等価)", async () => {
  const fetchFn = fakeFetch(okResponse({ job_id: "j1", status: "running" }));
  await createJobSubmitClient({ fetch: fetchFn }).submit({ backtest: { ea_name: "X" } });
  const body = JSON.parse(fetchFn.calls[0].init.body);
  assert.equal("strategy" in body, false);
  assert.equal("sizing" in body, false);
});

test("submit omits an empty strategy object", async () => {
  const fetchFn = fakeFetch(okResponse({ job_id: "j1", status: "running" }));
  await createJobSubmitClient({ fetch: fetchFn }).submit({ backtest: { ea_name: "X" }, strategy: {} });
  const body = JSON.parse(fetchFn.calls[0].init.body);
  assert.equal("strategy" in body, false);
});

test("submit includes sizing when provided", async () => {
  const fetchFn = fakeFetch(okResponse({ job_id: "j1", status: "running" }));
  await createJobSubmitClient({ fetch: fetchFn }).submit({
    backtest: { ea_name: "X" }, sizing: { enabled: true },
  });
  const body = JSON.parse(fetchFn.calls[0].init.body);
  assert.deepEqual(body.sizing, { enabled: true });
});

test("submit sends JSON content-type", async () => {
  const fetchFn = fakeFetch(okResponse({ job_id: "j1", status: "running" }));
  await createJobSubmitClient({ fetch: fetchFn }).submit({ backtest: { ea_name: "X" } });
  const ct = fetchFn.calls[0].init.headers["Content-Type"] || fetchFn.calls[0].init.headers["content-type"];
  assert.match(String(ct), /application\/json/);
});

test("submit returns the parsed job view", async () => {
  const fetchFn = fakeFetch(okResponse({ job_id: "j9", status: "running" }));
  const got = await createJobSubmitClient({ fetch: fetchFn }).submit({ backtest: { ea_name: "X" } });
  assert.deepEqual(got, { job_id: "j9", status: "running" });
});

test("submit throws with the server error message on non-2xx", async () => {
  const fetchFn = fakeFetch(errResponse({ error: "戦略条件が参照する指標系列 ['ema'] は無い" }, 400));
  await assert.rejects(
    () => createJobSubmitClient({ fetch: fetchFn }).submit({ backtest: { ea_name: "X" }, strategy: STRATEGY }),
    (e) => /ema/.test(e.message),
  );
});

test("loadEaSeries GETs /sim/ea-series/{ea_name} and returns the payload", async () => {
  const payload = { ok: true, ea_name: "PRO_fit_Band_EA", series: ["adx", "close", "ema"] };
  const fetchFn = fakeFetch({ ok: true, status: 200, json: async () => payload });
  const got = await createJobSubmitClient({ fetch: fetchFn }).loadEaSeries("PRO_fit_Band_EA");
  assert.equal(fetchFn.calls[0].url, "/sim/ea-series/PRO_fit_Band_EA");
  assert.deepEqual(got, payload);
});

test("loadEaSeries URL-encodes the ea_name segment", async () => {
  const fetchFn = fakeFetch({ ok: true, status: 200, json: async () => ({ series: [] }) });
  await createJobSubmitClient({ fetch: fetchFn }).loadEaSeries("MA Slope/EA");
  assert.equal(fetchFn.calls[0].url, "/sim/ea-series/MA%20Slope%2FEA");
});

test("loadEaSeries throws with the server message on non-2xx", async () => {
  const fetchFn = fakeFetch(errResponse({ error: "ea_name をパスに含めてください" }, 400));
  await assert.rejects(
    () => createJobSubmitClient({ fetch: fetchFn }).loadEaSeries(""),
    (e) => /ea_name/.test(e.message),
  );
});

// --- Phase 6 拡張: loadRunOptions（run config フォームの選択肢）---------------

test("RUN_OPTIONS_URL is the single source of the run-options endpoint", async () => {
  const { RUN_OPTIONS_URL } = await import("../js/adapter/front/job_submit_client.js");
  assert.equal(RUN_OPTIONS_URL, "/sim/run-options");
});

test("loadRunOptions GETs /sim/run-options and returns the payload", async () => {
  const payload = { ok: true, datasets: [{ dataset: "jp225_m1", symbol: "JP225" }], ea_names: ["TC24051901"] };
  const fetchFn = fakeFetch({ ok: true, status: 200, json: async () => payload });
  const got = await createJobSubmitClient({ fetch: fetchFn }).loadRunOptions();
  assert.equal(fetchFn.calls[0].url, "/sim/run-options");
  assert.deepEqual(got, payload);
});

test("loadRunOptions throws with the server message on non-2xx", async () => {
  const fetchFn = fakeFetch(errResponse({ error: "boom" }, 500));
  await assert.rejects(
    () => createJobSubmitClient({ fetch: fetchFn }).loadRunOptions(),
    /boom/,
  );
});
