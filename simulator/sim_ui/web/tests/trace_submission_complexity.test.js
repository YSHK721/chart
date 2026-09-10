// 実行トレース投入経路の計算量テスト（絶対命令 2026-08-28・`tdd` スキル §4.1）。
//
// 何を測るか: **時間ではなく回数**。ここで数えるのは「日付の解釈を何回発行したか」であり、
//   Test Spy は `Date.parse`（front の日付解釈が唯一通る計算）に張る。実測で、面を組んで
//   トレース OFF のまま投入するまでの発行は 0 回である＝この spy は当該計算だけを見ている。
//
// 何を固定するか: **無駄の不在**であって実装詳細ではない。
//   `発行した解釈 − 出力に使った解釈 = 0`。期待値の回数そのものは焼き込まない
//   （「N 回呼ばれること」を固定すると浪費が仕様へ昇格する。ISSUE-450 の実例がある）。
//   使った数は**出力から数える**（本文に載った境界の本数）ため、実装が何回呼ぼうと
//   期待値は動かない。
//
// なぜ状態検証では足りないか: 「作ってから捨てる」形は出力が正しいままなので、本文を見る
//   検定では原理的に落ちない。トレースを OFF にした投入でも両端を解釈してから捨てる実装は
//   `trace_wiring_end_to_end.test.js` を 1 件も赤にしない（下の検出器自己検定が実証する）。
import { test } from "node:test";
import assert from "node:assert/strict";

import { fakeDoc, findById, IDLE_WATCH_TIMER } from "./_fakes.js";
import { settingsSchema } from "./_settings_schema_fixture.js";
import {
  buildSubmission, traceBlockOf, epochSecondsOfDate,
} from "../js/adapter/front/sim_submission_builder.js";
import { mountSimExecutionPanel } from "../js/adapter/front/composition_root_execution.js";

const flush = () => new Promise((r) => setTimeout(r, 0));

const PROFILE = Object.freeze({
  dataset: "jp225_m1", data_path: "/d/jp225_m1.csv", symbol: "JP225", period: "M1",
  contract_size: 10.0, digits: 1, point_size: 0.1, leverage: 10.0,
  volume_min: 0.01, volume_max: 100.0, volume_step: 0.01, stops_level: 0,
});
const SUBJECT = Object.freeze({ ea_name: "TC24051901", initial_deposit: 10000, settings: null });

const RUN_OPTIONS = {
  ok: true,
  datasets: [PROFILE],
  ea_names: ["PRO_fit_Band_EA", "TC24051901"],
};

/** Test Spy: `fn` の実行中に発行された日付解釈の回数を数える。 */
async function issuedDateReads(fn) {
  const real = Date.parse;
  let issued = 0;
  Date.parse = (...args) => { issued += 1; return real.apply(Date, args); };
  try {
    const value = await fn();
    return { value, issued };
  } finally {
    Date.parse = real;
  }
}

/** 出力が実際に使った境界の本数（期待値はここから採る＝回数を焼き込まない）。 */
function boundsUsedBy(block) {
  if (!block) return 0;
  return ["start", "end"].filter((k) => block[k] !== undefined && block[k] !== null).length;
}

/** EA パラメータを n 個持つ inputs（入力量を変える 2 点目のため）。 */
const inputsOfSize = (n) =>
  Object.fromEntries(Array.from({ length: n }, (_, i) => [`p${i}`, i]));

// --- 1. 無駄の不在（発行 − 使用 = 0）------------------------------------------

test("no date interpretation is issued that the submitted block does not use", async () => {
  const cases = [
    null,                                                     // 指定なし
    { enabled: false, from: "2025.01.06", to: "2025.01.10" }, // OFF（欄には値が残っている）
    { enabled: true, from: "", to: "" },                      // ON・窓なし
    { enabled: true, from: "2025.01.06", to: "2025.01.10" },  // ON・期間指定
  ];
  for (const trace of cases) {
    const { value, issued } = await issuedDateReads(() => traceBlockOf(trace));
    assert.equal(issued - boundsUsedBy(value), 0,
      `捨てられる日付解釈があります: ${JSON.stringify(trace)} → 発行 ${issued} / 使用 ${boundsUsedBy(value)}`);
  }
});

test("a one-sided period is refused without interpreting anything (読む前に分かる)", async () => {
  // 「残す範囲が決まらない」ことは値を解釈しなくても分かる。解釈してから捨てるのは無駄。
  for (const trace of [{ enabled: true, from: "2025.01.06" }, { enabled: true, to: "2025.01.10" }]) {
    const { issued } = await issuedDateReads(() => {
      try { return traceBlockOf(trace); } catch (_e) { return null; }
    });
    assert.equal(issued, 0, `片側だけの期間で日付解釈が発行されています: ${JSON.stringify(trace)}`);
  }
});

// --- 2. オーダーの表明（発行は出力量だけで決まる）-------------------------------

test("the issued count follows the number of bounds, not the length of the period", async () => {
  // 2 点で固定する: 1 日の窓と 10 年の窓。期間が伸びても解釈は増えない。
  const short = await issuedDateReads(
    () => traceBlockOf({ enabled: true, from: "2025.01.06", to: "2025.01.07" }));
  const long = await issuedDateReads(
    () => traceBlockOf({ enabled: true, from: "2015.01.06", to: "2025.01.06" }));
  assert.equal(short.issued, long.issued,
    "期間を伸ばすと日付解釈が増えています（期間長に比例する計算が混ざっています）");
  assert.equal(short.issued - boundsUsedBy(short.value), 0);
});

test("the issued count does not follow the size of the rest of the body", async () => {
  // 2 点で固定する: EA パラメータ 2 個と 40 個。本文が太っても日付解釈は増えない。
  const trace = { enabled: true, from: "2025.01.06", to: "2025.01.10" };
  const small = await issuedDateReads(
    () => buildSubmission({ profile: PROFILE, subject: SUBJECT, inputs: inputsOfSize(2), trace }));
  const large = await issuedDateReads(
    () => buildSubmission({ profile: PROFILE, subject: SUBJECT, inputs: inputsOfSize(40), trace }));
  assert.equal(small.issued, large.issued);
  assert.equal(small.issued - boundsUsedBy(small.value.trace), 0);
});

// --- 3. 画面から投入までの経路（変換は投入時に 1 回だけ）-------------------------

async function mountForm() {
  const doc = fakeDoc();
  const calls = [];
  const fetchFn = async (url, init) => {
    calls.push({ url, init });
    if (url === "/sim/settings-schema") return { ok: true, status: 200, json: async () => settingsSchema() };
    if (url === "/sim/run-options") return { ok: true, status: 200, json: async () => RUN_OPTIONS };
    if (url === "/sim/jobs") return { ok: true, status: 202, json: async () => ({ job_id: "j1", status: "running" }) };
    return { ok: false, status: 404, json: async () => ({ error: "nope" }) };
  };
  const refs = await mountSimExecutionPanel({
    ...IDLE_WATCH_TIMER, doc, host: doc.body, fetch: fetchFn,
  });
  return { doc, calls, refs };
}

test("typing a period issues no interpretation until the run is submitted", async () => {
  const { doc, refs } = await mountForm();
  const { issued } = await issuedDateReads(async () => {
    findById(doc.body, "traceEnabled").checked = true;
    for (const token of ["2025.0", "2025.01", "2025.01.0", "2025.01.06"]) {
      findById(doc.body, "traceFrom").value = token;   // 打鍵のたびに解釈してはならない
    }
    findById(doc.body, "traceTo").value = "2025.01.10";
  });
  refs.dispose();
  assert.equal(issued, 0, "投入前に日付解釈が発行されています（打鍵ごとの変換）");
});

test("one submission issues exactly the interpretations its body uses", async () => {
  const { doc, calls, refs } = await mountForm();
  findById(doc.body, "traceEnabled").checked = true;
  findById(doc.body, "traceFrom").value = "2025.01.06";
  findById(doc.body, "traceTo").value = "2025.01.10";
  const { issued } = await issuedDateReads(async () => {
    findById(doc.body, "runStart")._listeners.click[0]();
    await flush();
  });
  refs.dispose();
  const body = JSON.parse(calls.find((c) => c.url === "/sim/jobs").init.body);
  assert.equal(issued - boundsUsedBy(body.trace), 0,
    `投入 1 回で捨てられる日付解釈があります: 発行 ${issued} / 使用 ${boundsUsedBy(body.trace)}`);
});

test("an off trace submits without issuing any interpretation at all", async () => {
  const { doc, calls, refs } = await mountForm();
  findById(doc.body, "traceFrom").value = "2025.01.06";   // 欄には値が残っている
  findById(doc.body, "traceTo").value = "2025.01.10";
  const { issued } = await issuedDateReads(async () => {
    findById(doc.body, "runStart")._listeners.click[0]();
    await flush();
  });
  refs.dispose();
  const body = JSON.parse(calls.find((c) => c.url === "/sim/jobs").init.body);
  assert.equal("trace" in body, false);
  assert.equal(issued, 0, "OFF なのに日付解釈が発行されています（作ってから捨てる形）");
});

// --- 4. 検出器の自己検定（空振りしていないことの実証）---------------------------
// 走査が機能していなければ、上の 3 節は「違反 0 件」に見えたまま無意味になる。
// 実際に起こりうる浪費の形を 1 つ注入し、指標がそれを捕まえることを固定する。

/** 浪費を注入した `traceBlockOf`（両端を必ず解釈してから、要らなければ捨てる）。 */
function wastefulTraceBlockOf(trace) {
  const from = trace && trace.from;
  const to = trace && trace.to;
  // 先に解釈する——出力が正しいままなので、状態検証では永久に落ちない形である。
  // 上端の +1 日は本番と同じにする（この写しが本番と違ってよいのは**浪費の有無だけ**。
  // 出力まで違えたら「状態検証では落ちない」という主張そのものが崩れる）。
  const start = from ? epochSecondsOfDate(from) : null;
  const end = to ? epochSecondsOfDate(to) + 24 * 60 * 60 : null;
  if (!trace || !trace.enabled) return null;
  if (start === null && end === null) return { enabled: true };
  return { enabled: true, start, end };
}

test("the waste detector actually catches a build-then-discard implementation (自己検定)", async () => {
  // OFF なのに両端を解釈して捨てる＝発行 2 / 使用 0。
  const off = await issuedDateReads(
    () => wastefulTraceBlockOf({ enabled: false, from: "2025.01.06", to: "2025.01.10" }));
  assert.ok(off.issued - boundsUsedBy(off.value) > 0,
    "指標が浪費を見逃しています（この検定が空振りなら上の 3 節は無意味です）");
  // 正の対照: 本番の実装は同じ入力で 0 のままである。
  const real = await issuedDateReads(
    () => traceBlockOf({ enabled: false, from: "2025.01.06", to: "2025.01.10" }));
  assert.equal(real.issued - boundsUsedBy(real.value), 0);
});

test("the build-then-discard implementation still passes the state-based checks (状態検証の限界)", () => {
  // 浪費版でも本文は正しい＝出力を見る検定では原理的に落ちない。だから回数を測る。
  assert.deepEqual(
    wastefulTraceBlockOf({ enabled: true, from: "2025.01.06", to: "2025.01.10" }),
    traceBlockOf({ enabled: true, from: "2025.01.06", to: "2025.01.10" }),
  );
  assert.equal(wastefulTraceBlockOf({ enabled: false, from: "2025.01.06", to: "2025.01.10" }), null);
});
