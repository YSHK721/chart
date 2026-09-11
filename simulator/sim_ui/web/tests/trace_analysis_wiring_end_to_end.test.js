// 分析タブが front の**端から端まで**結線されていることの機械強制
// （RUN_TRACE_BASIC_DESIGN §9.1/§9.2・ISSUE-291 の再発防止）。
//
// なぜ在るか: タブを 1 枚足し、View と HTTP クライアントに口を作っただけでは機能は
//   存在しない。**合成根が実際に呼んでいる**ことを誰も確かめていなければ、タブは開くが
//   中身が永遠に空のまま無言で死ぬ——ISSUE-291 と同型である。したがって純関数を素で
//   呼ばない。**本番の合成根（`mountTraceAnalysis`）を組み、ペインの中身が実際の HTTP
//   応答から描かれるところまで**を通す。
//
//   表示側の合成根（`composition_root_front.js`）がその合成根を実際に呼んでいることは
//   `import_source.test.js` の静的ゲートが固定する（同ファイルは `/sim/report-js/*` を
//   絶対 URL で import するため node からは読み込めない——`composition_root_execution.js`
//   が別合成根に分かれているのと同じ理由で、分析の結線も別合成根に置く）。
//
// 固定する不変条件:
//   1. 合成根が分析 API を叩く（`/sim/trace/{job}/extent` と `.../points/...`）。
//   2. 応答が分析ペインの中身になる（系列行・事象行が出る）。
//   3. 取得できないときは**空のペインにせず**掲示を出す（413 のサーバ文言をそのまま）。
//   4. 失敗しても throw しない（表示の失敗で成功した表示を捨てない・先例 run_job.py）。
//   5. **窓は extent から導く**（front が期間を発明しない）。
import { test } from "node:test";
import assert from "node:assert/strict";

import { fakeDoc, flatten } from "./_fakes.js";
import { mountTraceAnalysis } from "../js/adapter/front/composition_root_analysis.js";
import { extentUrl } from "../js/adapter/front/trace_analysis_client.js";

const JOB = "b".repeat(32);
const T0 = 1_704_067_200_000;

const classesOf = (el) => String(el.className || "").split(/\s+/).filter(Boolean);
const hasClass = (el, c) => classesOf(el).includes(c);

const EXTENT = {
  ok: true, job_id: JOB, rows: 120, first_time: T0, last_time: T0 + 119_000,
  initial_deposit: 10_000, margin_level_floor: 99.95,
  columns: ["time", "balance", "equity", "margin", "margin_level", "open_count", "halted"],
  event_kinds: ["halt", "resume", "position_opened", "position_closed", "margin_floor_breach"],
  max_returned_rows: 20_000, time_unit: "epoch_millis",
  // サーバが測って答える窓（front は比を掛け直さない）。
  suggested_window: { start: T0, end: T0 + 119_001 },
};

const POINTS = {
  ok: true, job_id: JOB, window: { start: T0, end: T0 + 119_001 }, rows: 3,
  columns: {
    time: [T0, T0 + 250, T0 + 500],
    balance: [10_000, 10_000, 10_000],
    equity: [10_000, 9_900, 9_950],
    margin: [100, 100, 100],
    margin_level: [1_000, 90, 700],
    open_count: [0, 1, 0],
    halted: [false, false, false],
  },
  events: [{ time: T0 + 250, kind: "position_opened", previous: 0, value: 1 }],
  drawdown: { equity_dd_absolute: 100, equity_dd_maximal: 100,
              equity_dd_maximal_percent: 1 },
  time_unit: "epoch_millis",
};

function routerFetch({ status = 200, payload = null, extentOverride = null } = {}) {
  const calls = [];
  const fn = async (url) => {
    const u = String(url);
    calls.push(u);
    if (status !== 200) {
      return { ok: false, status, json: async () => payload || { error: "だめ" } };
    }
    if (u === extentUrl(JOB)) {
      return { ok: true, status: 200,
               json: async () => ({ ...EXTENT, ...(extentOverride || {}) }) };
    }
    return { ok: true, status: 200, json: async () => POINTS };
  };
  fn.calls = calls;
  return fn;
}

async function mounted(opts = {}) {
  const doc = fakeDoc();
  const pane = doc.createElement("div");
  doc.body.appendChild(pane);
  const fetchFn = routerFetch(opts);
  const handle = await mountTraceAnalysis({
    doc, pane, jobId: JOB, fetch: fetchFn,
  });
  return { doc, pane, handle, fetchFn };
}

// --- 1. 合成根が分析 API を叩く ---------------------------------------------------

test("the composition root asks the analysis api for the extent and the points", async () => {
  const { fetchFn } = await mounted();
  assert.ok(
    fetchFn.calls.includes(extentUrl(JOB)),
    `extent が呼ばれていない: ${JSON.stringify(fetchFn.calls)}`,
  );
  assert.ok(
    fetchFn.calls.some((u) => u.includes(`/${JOB}/points/`)),
    `points が呼ばれていない: ${JSON.stringify(fetchFn.calls)}`,
  );
});

test("the window is the one the server suggested, not one the front computes", async () => {
  const { fetchFn } = await mounted();
  const points = fetchFn.calls.find((u) => u.includes("/points/"));
  const [start, end] = points.split("/points/")[1].split("/");
  assert.equal(Number(start), EXTENT.suggested_window.start);
  assert.equal(Number(end), EXTENT.suggested_window.end);
});

test("the front does not re-derive the width from the row count", async () => {
  // 実測（2026-09-10）: 「上限 ÷ 全行数」の比で幅を決める形は、ティック密度が一様でない
  // 実 run で 59,030 行の窓を作り初回表示が 413 になった。行数を変えても front の問う窓は
  // サーバの答えのままであること＝比を掛け直していないことの表明。
  const { fetchFn } = await mounted({ extentOverride: { rows: 1_036_394 } });
  const points = fetchFn.calls.find((u) => u.includes("/points/"));
  const [start, end] = points.split("/points/")[1].split("/");
  assert.equal(Number(start), EXTENT.suggested_window.start);
  assert.equal(Number(end), EXTENT.suggested_window.end);
});

test("no suggested window means asking without bounds (値を発明しない)", async () => {
  const { fetchFn } = await mounted({
    extentOverride: { rows: 0, suggested_window: { start: null, end: null } },
  });
  const points = fetchFn.calls.find((u) => u.includes("/points/"));
  assert.match(points, /\/points\/-\/-$/, points);
});

// --- 2. 応答がペインの中身になる ---------------------------------------------------

test("the served columns and events land in the pane", async () => {
  const { pane } = await mounted();
  const nodes = flatten(pane);
  const series = nodes.filter((n) => n.dataset && n.dataset.series).map((n) => n.dataset.series);
  assert.deepEqual(series, EXTENT.columns.filter((c) => c !== "time"));
  const events = nodes.filter((n) => n.dataset && n.dataset.eventKind);
  assert.deepEqual(events.map((n) => n.dataset.eventKind), ["position_opened"]);
});

test("the view is mounted under the given pane (別の場所へ挿さない)", async () => {
  const { pane } = await mounted();
  assert.ok(flatten(pane).some((n) => hasClass(n, "trace-analysis")));
});

// --- 3. 取れないときは掲示（空のペインにしない）------------------------------------

test("a refused window is shown with the server reason, not as an empty pane", async () => {
  const { pane } = await mounted({
    status: 413,
    payload: { error: "窓に入る点が 1036394 行あり上限 20000 行を超えます" },
  });
  const problem = flatten(pane).find((n) => hasClass(n, "trace-problem"));
  assert.ok(problem, "掲示が無い");
  assert.match(String(problem.textContent), /20000/);
  // 系列は 1 つも描かれていない（半端な絵を残さない）。
  assert.equal(flatten(pane).filter((n) => n.dataset && n.dataset.series).length, 0);
});

test("a run without a trace is shown as such", async () => {
  const { pane } = await mounted({
    status: 404,
    payload: { error: "ジョブに実行トレースの成果物がありません" },
  });
  const problem = flatten(pane).find((n) => hasClass(n, "trace-problem"));
  assert.match(String(problem.textContent), /トレース/);
});

// --- 4. 失敗しても呼出側を巻き込まない ---------------------------------------------

test("a failure does not reject (表示の失敗で成功した表示を捨てない)", async () => {
  await assert.doesNotReject(() => mounted({ status: 500 }));
});

test("the handle reports whether it could draw", async () => {
  const ok = await mounted();
  const bad = await mounted({ status: 500 });
  assert.equal(ok.handle.drawn(), true);
  assert.equal(bad.handle.drawn(), false);
});
