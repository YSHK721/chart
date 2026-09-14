// 分析タブ（口座推移・証拠金維持率・保有玉数・DD・事象一覧）の単体テスト。
//
// RUN_TRACE_BASIC_DESIGN §9.1/§9.5。fake DOM・fetch 注入。
//
// 固定する不変条件:
//   1. タブ名の宣言が `SIM_TAB_NAMES` と一致する（口だけ作って呼ばれない箇所を作らない）。
//   2. **列名を front で手書きしない**——サーバが `extent` で配る宣言をそのまま使う。
//   3. 窓はパスで運ぶ（sim core の GET はクエリを落とす・実測）。
//   4. 上限超過（413）は「窓を狭めよ」として見せる。**黙って間引かない**。
//   5. 取れなかったことを空表示にしない（「記録が無い run」と「取得に失敗」を混同しない）。
//   6. 時刻は epoch **ミリ秒**として扱う（§9.0・`Date` がそのまま受ける）。
import { test } from "node:test";
import assert from "node:assert/strict";

import { fakeDoc, flatten } from "./_fakes.js";
import { SIM_TAB_NAMES } from "../js/adapter/front/sim_tabs_view.js";
import {
  TRACE_ANALYSIS_BASE,
  TraceAnalysisError,
  createTraceAnalysisClient,
  extentUrl,
  pointsUrl,
} from "../js/adapter/front/trace_analysis_client.js";
import {
  SIM_TRACE_TAB_NAME,
  createSimTraceView,
} from "../js/adapter/front/sim_trace_view.js";

const JOB = "a".repeat(32);
const T0 = 1_704_067_200_000;

const classesOf = (el) => String(el.className || "").split(/\s+/).filter(Boolean);
const hasClass = (el, c) => classesOf(el).includes(c);

function extentPayload(over = {}) {
  return {
    ok: true, job_id: JOB, rows: 120, first_time: T0, last_time: T0 + 119_000,
    initial_deposit: 10_000.0, margin_level_floor: 99.95,
    columns: ["time", "balance", "equity", "margin", "margin_level", "open_count", "halted"],
    event_kinds: ["halt", "resume", "position_opened", "position_closed", "margin_floor_breach"],
    max_returned_rows: 20_000, time_unit: "epoch_millis", ...over,
  };
}

function pointsPayload(over = {}) {
  const n = 4;
  const time = Array.from({ length: n }, (_, i) => T0 + i * 250);
  return {
    ok: true, job_id: JOB, window: { start: T0, end: T0 + 1_000 }, rows: n,
    columns: {
      time,
      balance: [10_000, 10_000, 10_000, 10_000],
      equity: [10_000, 9_950, 9_900, 9_980],
      margin: [100, 100, 100, 100],
      margin_level: [1_000, 500, 90, 700],
      open_count: [0, 1, 1, 0],
      halted: [false, false, false, false],
    },
    events: [
      { time: T0 + 250, kind: "position_opened", previous: 0, value: 1 },
      { time: T0 + 500, kind: "margin_floor_breach", previous: false, value: 90 },
      { time: T0 + 750, kind: "position_closed", previous: 1, value: 0 },
    ],
    drawdown: {
      equity_dd_absolute: 100.0, equity_dd_maximal: 100.0,
      equity_dd_maximal_percent: 1.0,
    },
    time_unit: "epoch_millis", ...over,
  };
}

/** 呼ばれた URL を記録する fetch の代役。 */
function fakeFetch(routes) {
  const calls = [];
  const fn = async (url) => {
    calls.push(url);
    const hit = routes[url] !== undefined
      ? routes[url]
      : Object.entries(routes).find(([k]) => url.startsWith(k))?.[1];
    if (hit === undefined) return { ok: false, status: 404, json: async () => ({ error: "not found" }) };
    if (typeof hit === "function") return hit(url);
    return { ok: true, status: 200, json: async () => hit };
  };
  fn.calls = calls;
  return fn;
}

// --- 1. 宣言の一致（結線が切れていない）----------------------------------------

test("the analysis tab name is one of the declared tabs", () => {
  assert.ok(
    SIM_TAB_NAMES.includes(SIM_TRACE_TAB_NAME),
    `SIM_TAB_NAMES に ${SIM_TRACE_TAB_NAME} が無い（タブが出ない＝口だけの結線）`,
  );
});

// --- 2. URL の組み立て（窓はパスで運ぶ）-----------------------------------------

test("the urls carry the job id and the window in the path", () => {
  assert.equal(extentUrl(JOB), `${TRACE_ANALYSIS_BASE}/${JOB}/extent`);
  assert.equal(
    pointsUrl(JOB, T0, T0 + 1_000),
    `${TRACE_ANALYSIS_BASE}/${JOB}/points/${T0}/${T0 + 1_000}`,
  );
});

test("an absent bound is expressed by the token, not by an empty segment", () => {
  // 空セグメントにするとパスが壊れる（`//` で別のルートになる）。
  const url = pointsUrl(JOB, null, null);
  assert.ok(!url.includes("//points"), url);
  assert.match(url, /\/points\/[^/]+\/[^/]+$/);
});

test("the client throws with the server reason when the window is too wide", async () => {
  const client = createTraceAnalysisClient({
    fetch: fakeFetch({
      [`${TRACE_ANALYSIS_BASE}/${JOB}/points`]: () => ({
        ok: false, status: 413,
        json: async () => ({ error: "窓に入る点が 1036394 行あり上限 20000 行を超えます" }),
      }),
    }),
  });
  await assert.rejects(
    () => client.points(JOB, T0, T0 + 1_000),
    (err) => {
      assert.ok(err instanceof TraceAnalysisError);
      assert.equal(err.status, 413);
      assert.match(err.message, /20000/);
      return true;
    },
  );
});

test("the client throws rather than returning an empty payload", async () => {
  const client = createTraceAnalysisClient({
    fetch: fakeFetch({}), // すべて 404
  });
  await assert.rejects(() => client.extent(JOB), TraceAnalysisError);
});

// --- 3. 描画（列名を手書きしない）------------------------------------------------

function mounted() {
  const doc = fakeDoc();
  const view = createSimTraceView({ doc });
  const root = view.mount(doc.body);
  return { doc, view, root };
}

test("mount builds the pane skeleton without any data", () => {
  const { root } = mounted();
  const nodes = flatten(root);
  assert.ok(nodes.some((n) => hasClass(n, "trace-analysis")), "器が無い");
});

test("render lays out one row per served column, taking the names from the payload", () => {
  const { root, view } = mounted();
  view.render({ extent: extentPayload(), points: pointsPayload() });
  const rows = flatten(root).filter((n) => n.dataset && n.dataset.series);
  const names = rows.map((r) => r.dataset.series);
  // 系列は payload の列から導く（`time` は横軸なので系列にしない）。
  const served = extentPayload().columns.filter((c) => c !== "time");
  assert.deepEqual(names, served);
});

test("a served column the front has never seen still gets a row", () => {
  // 列が増えたとき front を触らずに出る（宣言の写しを持っていないことの表明）。
  const { root, view } = mounted();
  const extent = extentPayload({ columns: ["time", "equity", "brand_new_column"] });
  const points = pointsPayload();
  points.columns = { time: points.columns.time, equity: points.columns.equity,
                     brand_new_column: [1, 2, 3, 4] };
  view.render({ extent, points });
  const names = flatten(root)
    .filter((n) => n.dataset && n.dataset.series)
    .map((r) => r.dataset.series);
  assert.deepEqual(names, ["equity", "brand_new_column"]);
});

test("the drawdown figures are shown", () => {
  const { root, view } = mounted();
  view.render({ extent: extentPayload(), points: pointsPayload() });
  const dd = flatten(root).find((n) => hasClass(n, "trace-dd"));
  assert.ok(dd, "DD の表示が無い");
  assert.match(String(dd.textContent), /100/);
});

test("the events are listed with their time and kind", () => {
  const { root, view } = mounted();
  view.render({ extent: extentPayload(), points: pointsPayload() });
  const items = flatten(root).filter((n) => n.dataset && n.dataset.eventKind);
  assert.deepEqual(
    items.map((n) => n.dataset.eventKind),
    ["position_opened", "margin_floor_breach", "position_closed"],
  );
  // 時刻は epoch ミリ秒としてそのまま `Date` に渡せる（§9.0）。
  assert.ok(items.every((n) => Number(n.dataset.eventTime) > 1e12));
});

test("zero events is shown as such, not as a blank list", () => {
  const { root, view } = mounted();
  const points = pointsPayload({ events: [] });
  view.render({ extent: extentPayload(), points });
  const list = flatten(root).find((n) => hasClass(n, "trace-events"));
  assert.ok(String(list.textContent).length > 0 || list.children.length > 0);
});

// --- 4. 失敗は空表示にしない -----------------------------------------------------

test("a failure is shown as a message, not as an empty pane", () => {
  const { root, view } = mounted();
  view.showProblem("窓に入る点が多すぎます。窓を狭めてください");
  const msg = flatten(root).find((n) => hasClass(n, "trace-problem"));
  assert.ok(msg, "掲示が無い");
  assert.match(String(msg.textContent), /窓を狭め/);
});

test("showing a problem clears any previously drawn series (半端な絵を残さない)", () => {
  const { root, view } = mounted();
  view.render({ extent: extentPayload(), points: pointsPayload() });
  view.showProblem("取得に失敗しました");
  const rows = flatten(root).filter((n) => n.dataset && n.dataset.series);
  assert.equal(rows.length, 0);
});

// --- 5. 語彙（front の禁止語を持ち込まない）---------------------------------------

test("the event kinds served by the server avoid the removed vocabulary", () => {
  for (const kind of extentPayload().event_kinds) {
    assert.ok(!/trailing|partial/.test(kind), kind);
  }
});
