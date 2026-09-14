// report_source_client（report.json の取得・F-4）の単体テスト（fake fetch）。
//
// 固定する不変条件:
//   1. 取得先は **絶対パス** `/sim/data/{job_id}/report.json`。統合 UI の routedFetch /
//      Service Worker は既に prefix の付いたパスを書き換えない（冪等）ため、fetch の
//      注入は要らない（実測済み）。
//   2. job_id は `?job=<id>` からのみ得る。**自動選択しない**（ビュー自動介入の禁止）。
//   3. 取得できないとき（404 / 503 / 壊れた JSON）は **例外**にする。部分描画しない
//      （fail-stop）。理由コードは表示メッセージの出し分けに使う。
//   4. 単一 run の report.json（segments が 1 キー）でも読める。
import { test } from "node:test";
import assert from "node:assert/strict";

import {
  REPORT_FILENAME, SIM_DATA_BASE,
  createReportSourceClient, readJobId, firstSegment,
} from "../js/adapter/front/report_source_client.js";

function fakeFetch(response) {
  const calls = [];
  const fn = async (url, init) => { calls.push({ url, init }); return response; };
  fn.calls = calls;
  return fn;
}

function okResponse(payload) {
  return { ok: true, status: 200, json: async () => payload };
}

const PAYLOAD = {
  meta: { symbol: "JP225" },
  segments: { is: { label: "IS", bars: [], trades: [], agg: {} } },
};

// --- 1. 取得先パス --------------------------------------------------------------

test("load requests /sim/data/{job_id}/report.json", async () => {
  const fetchFn = fakeFetch(okResponse(PAYLOAD));
  const client = createReportSourceClient({ fetch: fetchFn });
  await client.load("job-42");
  assert.ok(fetchFn.calls[0].url.startsWith("/sim/data/job-42/report.json"),
    `想定外の URL: ${fetchFn.calls[0].url}`);
});

test("the base path and filename are the single source of the URL shape", () => {
  assert.equal(SIM_DATA_BASE, "/sim/data");
  assert.equal(REPORT_FILENAME, "report.json");
});

test("load asks for a fresh copy (no-store)", async () => {
  const fetchFn = fakeFetch(okResponse(PAYLOAD));
  await createReportSourceClient({ fetch: fetchFn }).load("job-42");
  assert.equal(fetchFn.calls[0].init.cache, "no-store");
});

test("load percent-encodes the job id (パス片への混入を防ぐ)", async () => {
  const fetchFn = fakeFetch(okResponse(PAYLOAD));
  await createReportSourceClient({ fetch: fetchFn }).load("a/b");
  assert.ok(fetchFn.calls[0].url.startsWith("/sim/data/a%2Fb/report.json"),
    `想定外の URL: ${fetchFn.calls[0].url}`);
});

test("load returns the parsed payload", async () => {
  const client = createReportSourceClient({ fetch: fakeFetch(okResponse(PAYLOAD)) });
  assert.deepEqual(await client.load("job-42"), PAYLOAD);
});

// --- 2. job_id は ?job= からのみ（自動選択しない）--------------------------------

test("readJobId picks the job query parameter", () => {
  assert.equal(readJobId("?job=20260811-abc"), "20260811-abc");
  assert.equal(readJobId("?a=1&job=xyz&b=2"), "xyz");
});

test("readJobId returns null when the query has no job (自動選択しない)", () => {
  assert.equal(readJobId(""), null);
  assert.equal(readJobId("?other=1"), null);
  assert.equal(readJobId(undefined), null);
});

test("readJobId treats a blank job value as absent", () => {
  assert.equal(readJobId("?job="), null);
  assert.equal(readJobId("?job=%20"), null);
});

// --- 3. 取れないときは例外（部分描画しない）-------------------------------------

test("load rejects when the job id is missing", async () => {
  const client = createReportSourceClient({ fetch: fakeFetch(okResponse(PAYLOAD)) });
  await assert.rejects(() => client.load(null), (e) => e.code === "no_job");
});

test("load rejects with 'not_ready' on 404 (結果未生成)", async () => {
  const client = createReportSourceClient({
    fetch: fakeFetch({ ok: false, status: 404, json: async () => ({}) }),
  });
  await assert.rejects(() => client.load("job-42"), (e) => {
    assert.equal(e.code, "not_ready");
    assert.equal(e.status, 404);
    return true;
  });
});

test("load rejects with 'not_ready' on 503 as well", async () => {
  const client = createReportSourceClient({
    fetch: fakeFetch({ ok: false, status: 503, json: async () => ({}) }),
  });
  await assert.rejects(() => client.load("job-42"), (e) => e.code === "not_ready");
});

test("load rejects with 'broken' when the body is not valid JSON", async () => {
  const client = createReportSourceClient({
    fetch: fakeFetch({ ok: true, status: 200, json: async () => { throw new SyntaxError("bad"); } }),
  });
  await assert.rejects(() => client.load("job-42"), (e) => e.code === "broken");
});

test("load rejects with 'unreachable' when fetch itself fails", async () => {
  const client = createReportSourceClient({
    fetch: async () => { throw new TypeError("network down"); },
  });
  await assert.rejects(() => client.load("job-42"), (e) => e.code === "unreachable");
});

// --- 4. 単一 run（segments 1 キー）でも読める ------------------------------------

// 承認 G の規則は「先頭の区間」＝ Object.keys(segments)[0]（キー名で分岐しない）。
// report_ui の払い出しは常に {"is", "oos"} の順（build_report_payload が is から組む）なので、
// 2 区間ペイロードでは先頭＝IS になる。
test("firstSegment takes the leading segment of a two-segment payload (= IS)", () => {
  const seg = firstSegment({ segments: { is: { label: "IS" }, oos: { label: "OOS" } } });
  assert.equal(seg.label, "IS");
});

test("firstSegment falls back to the only segment of a single run", () => {
  const seg = firstSegment({ segments: { run: { label: "RUN" } } });
  assert.equal(seg.label, "RUN");
});

test("firstSegment returns null when there is no segment at all", () => {
  assert.equal(firstSegment({ segments: {} }), null);
  assert.equal(firstSegment({}), null);
  assert.equal(firstSegment(null), null);
});
