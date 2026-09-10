// 実行トレースの投入口（ISSUE-508 段階 3・RUN_TRACE_BASIC_DESIGN §6.4 / §6.6.2）。
//
// なぜ front が範囲に含まれるか: 依頼者裁定は「**明示 ON** ＋期間指定」である。
// 明示 ON を人が表現できなければ機能は存在しない（サーバ側だけ作っても誰も点けられない）。
//
// 固定する不変条件:
//   1. trace が不在・OFF なら本文へ載せない（既存投入と byte 等価）。
//   2. trace ON は `{enabled: true[, start, end]}` を本文へ載せる。
//   3. **日付 → epoch 秒の変換は front の責務**（§6.4）。サーバは整数しか受けず、
//      受付側で `strptime` / `fromisoformat` を新しく書かない（同じ文字列が経路で
//      違う時刻に化ける。ISSUE-401 で 32,400 秒差を実測済みの同型）。
//   4. 変換は **UTC 解釈**である。ローカル TZ で解釈すると、同じ入力でも実行環境で
//      記録される期間が変わる（`datawindow/half_open.py` が naive=UTC と定めた理由と同じ）。
//   5. 片側だけの指定・逆転した期間は**本文を組む前に**落とす（サーバまで運ばない）。
import { test } from "node:test";
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

import {
  buildSubmission, traceBlockOf, epochSecondsOfDate,
} from "../js/adapter/front/sim_submission_builder.js";
import { createJobSubmitClient } from "../js/adapter/front/job_submit_client.js";

const PROFILE = Object.freeze({
  dataset: "jp225_m1", data_path: "/d/jp225_m1.csv", symbol: "JP225", period: "M1",
  contract_size: 10.0, digits: 1, point_size: 0.1, leverage: 10.0,
  volume_min: 0.01, volume_max: 100.0, volume_step: 0.01, stops_level: 0,
});
const SUBJECT = Object.freeze({ ea_name: "TC24051901", initial_deposit: 10000, settings: null });
const INPUTS = Object.freeze({ stop_loss_points: 100, take_profit_points: 200 });

function fakeFetch(response) {
  const calls = [];
  const fn = async (url, init) => { calls.push({ url, init }); return response; };
  fn.calls = calls;
  return fn;
}
const okResponse = (payload) => ({ ok: true, status: 202, json: async () => payload });

// --- 3/4. 日付 → epoch 秒（UTC） --------------------------------------------

test("epochSecondsOfDate converts an ISO date to UTC midnight epoch seconds", () => {
  // 2024-01-01T00:00:00Z
  assert.equal(epochSecondsOfDate("2024-01-01"), 1704067200);
  // 半開区間の上端は「翌日の 0 時」を指定する（規則そのものはサーバが持つ）。
  assert.equal(epochSecondsOfDate("2024-01-02"), 1704153600);
});

test("epochSecondsOfDate accepts an explicit time of day", () => {
  assert.equal(epochSecondsOfDate("2024-01-01T01:30"), 1704067200 + 5400);
  assert.equal(epochSecondsOfDate("2024-01-01T00:00:59"), 1704067200 + 59);
});

test("epochSecondsOfDate agrees with an explicit-Z Date in this process' TZ", () => {
  // 射程の限定（工程 5 レビュー 🔴-4）: 本検定が測るのは「同一プロセスの TZ で
  // 明示 Z の Date と一致すること」までである。**既定 TZ が UTC の環境では
  // ローカル TZ 解釈を 1 件も検出しない**（実測: Z 付与を外す変異が TZ=UTC で
  // 全 515 件緑）。「ローカル TZ で解釈しない」という主張は、非 UTC の TZ を
  // 明示した子プロセスで回す下の検定が担う。
  const first = epochSecondsOfDate("2024-06-15");
  const second = new Date("2024-06-15T00:00:00Z").getTime() / 1000;
  assert.equal(first, second);
  assert.equal(Number.isInteger(first), true);
});

// front の日付表記は 1 つだけ（工程 5・§6.4 の「第 2 の日付規則を作らない」）。
// 画面に既に在る日付入力（Tester Settings の `Dates` / `FromDate` / `ToDate` と、それが
// 開くカレンダー `sim_date_picker_view`）は `YYYY.MM.DD` トークンを唯一の表記として
// 持っている（`sim_date_picker_view.js` の `parseToken` / `formatToken` が実体）。
// トレースの期間欄だけ別表記にすると、同じ「日付」という概念に画面上の呼び名が 2 つ
// できる。変換規則そのものは増やさず、**この 1 関数**が両表記を受ける。
test("epochSecondsOfDate accepts the token the front's date fields already use", () => {
  // `sim_date_picker_view` が確定するトークン形（`YYYY.MM.DD`）。
  assert.equal(epochSecondsOfDate("2024.01.01"), 1704067200);
  assert.equal(epochSecondsOfDate("2024.01.02"), 1704153600);
});

test("the dotted token and the ISO token name the same instant (規則は 1 つ)", () => {
  // 表記が 2 つあっても解釈は 1 つ＝同じ日は必ず同じ整数になる（UTC 解釈も共通）。
  assert.equal(epochSecondsOfDate("2024.06.15"), epochSecondsOfDate("2024-06-15"));
});

// 存在しない日を**黙って**繰り上げない（実測 2026-09-10: `Date.parse` は
// `2024-02-30T00:00:00Z` を 1709251200000＝2024-03-01 として受理する）。打った日と違う日が
// 記録される形であり、§6.4 が禁じた「同じ文字列が経路で違う時刻に化ける」そのものである。
// 繰り上げは front で完結してしまうため、サーバの `TraceWindow` は原理的に検出できない
// ——epoch 整数として矛盾が無いからである。したがって唯一の変換点であるここで落とす。
test("epochSecondsOfDate refuses a day that does not exist instead of rolling it over", () => {
  for (const bad of ["2024.02.30", "2024-02-30", "2023.02.29", "2025.04.31", "2024.02.30T09:00"]) {
    assert.throws(() => epochSecondsOfDate(bad), /trace/, `繰り上がりを見逃しました: ${bad}`);
  }
});

test("a real day is still accepted (繰り上がり検査で通常の日付を禁じない)", () => {
  // 正の対照: 閏年の 2/29・月末・年末年始は実在するので通る。
  for (const good of ["2024.02.29", "2025.01.31", "2024.12.31", "2025.04.30"]) {
    assert.equal(
      epochSecondsOfDate(good),
      Date.parse(`${good.replace(/\./g, "-")}T00:00:00Z`) / 1000,
    );
  }
});

test("epochSecondsOfDate rejects an uninterpretable value instead of guessing", () => {
  for (const bad of ["", "not-a-date", null, undefined, {}]) {
    assert.throws(() => epochSecondsOfDate(bad), /trace/);
  }
});

// 非 UTC の TZ を明示した子プロセスで回す（工程 5 レビュー 🔴-4）。
//
// ISSUE-401 は「同じ文字列が経路で違う時刻に化ける」欠陥を TZ=Asia/Tokyo で 32,400 秒差
// として実測している。同型を front で防ぐには、**TZ を固定して**変換を走らせるしかない。
// 同一プロセス内の比較は既定 TZ（UTC）では恒真であり、検出力を持たない。
test("epochSecondsOfDate never falls back to local time (pinned TZ=Asia/Tokyo)", () => {
  const probe = fileURLToPath(new URL("./_tz_date_probe.mjs", import.meta.url));
  const run = spawnSync(process.execPath, [probe], {
    env: { ...process.env, TZ: "Asia/Tokyo" },
    encoding: "utf8",
  });
  assert.equal(
    run.status, 0,
    `TZ=Asia/Tokyo で日付変換が UTC 基準からずれました。\n${run.stdout}\n${run.stderr}`,
  );
  // 正の対照: 実行体が実際に走って表明を通したこと（起動失敗を成功と読まない）。
  assert.match(run.stdout, /tz-probe: ok/);
});

test("the pinned-TZ probe would catch a local-time fallback", () => {
  // 検出器の自己検査: 同じ実行体を **UTC** で起動すると、実行体自身が
  // 「非 UTC で起動されていない」と言って失敗する（TZ の渡し忘れを検出できる）。
  const probe = fileURLToPath(new URL("./_tz_date_probe.mjs", import.meta.url));
  const run = spawnSync(process.execPath, [probe], {
    env: { ...process.env, TZ: "UTC" },
    encoding: "utf8",
  });
  assert.notEqual(run.status, 0);
  assert.match(run.stderr, /非 UTC の TZ で起動される必要があります/);
});

// --- 1/2/5. trace ブロックの組み立て ----------------------------------------

test("traceBlockOf returns null when the trace is not switched on", () => {
  assert.equal(traceBlockOf(null), null);
  assert.equal(traceBlockOf(undefined), null);
  assert.equal(traceBlockOf({ enabled: false }), null);
  assert.equal(traceBlockOf({ enabled: false, from: "2024-01-01", to: "2024-01-02" }), null);
});

test("traceBlockOf builds an unwindowed block when no period is given", () => {
  assert.deepEqual(traceBlockOf({ enabled: true }), { enabled: true });
});

test("traceBlockOf converts the period into epoch seconds", () => {
  // 上端は**終了日の翌日 0 時**（裁定 2026-09-10・終了日を含む。根拠は下の節を参照）。
  assert.deepEqual(
    traceBlockOf({ enabled: true, from: "2024-01-01", to: "2024-01-02" }),
    { enabled: true, start: 1704067200, end: 1704240000 },
  );
});

// --- 終了日は「その日を含む」（裁定 2026-09-10）--------------------------------
//
// なぜ +1 日か: 本リポジトリには「終了日」の意味が既に 1 つ確定して在る。`.ini` の
//   `FromDate` / `ToDate` を解決する唯一の場所 `main/tester_settings/window.py:155-156` が
//       start = _midnight_utc(from_date)
//       end   = _midnight_utc(to_date) + timedelta(days=1)
//   であり、docstring も `[from 00:00Z, to+1day 00:00Z)` と明記する（＝終了日を**含む**）。
//   トレース面だけ「含まない」にすると、**同じ画面の同じ語「終了日」が 2 つの意味を持つ**。
//   注意書きを足す案は 2 義性を残したまま利用者に覚えさせるので採らない。
//
// 変換の実体は増やさない: `epochSecondsOfDate` の意味（日付トークン → その日の 0 時）は
//   変えず、上端の +1 日は**呼出側**で足す。`window.py` も `_midnight_utc` は素のままで
//   `+ timedelta(days=1)` を呼出側に置いている。同じ形にする。
//
// 開始日は変えない（`_midnight_utc(from_date)` と同じく、その日の 0 時）。

test("the end date is inclusive: the upper bound is midnight of the following day", () => {
  const block = traceBlockOf({ enabled: true, from: "2025.01.06", to: "2025.01.10" });
  assert.equal(block.start, Date.parse("2025-01-06T00:00:00Z") / 1000, "開始日は動かさない");
  assert.equal(block.end, Date.parse("2025-01-11T00:00:00Z") / 1000, "終了日がその日を含んでいない");
  // 裁定で名指しされた実測値（前回の送出は 1736467200 だった）。
  assert.deepEqual(block, { enabled: true, start: 1736121600, end: 1736553600 });
});

test("a single-day window (start === end date) is valid and covers exactly that day", () => {
  // 「開始 2025.01.10 / 終了 2025.01.10」は**その 1 日だけ**を意味する。半開区間なので
  // 上端は翌日 0 時であり、幅はちょうど 1 日になる。
  const block = traceBlockOf({ enabled: true, from: "2025.01.10", to: "2025.01.10" });
  assert.equal(block.end - block.start, 24 * 60 * 60, "1 日窓の幅が 1 日になっていない");
  assert.equal(block.start, Date.parse("2025-01-10T00:00:00Z") / 1000);
  assert.equal(block.end, Date.parse("2025-01-11T00:00:00Z") / 1000);
});

test("the +1 day upper bound crosses month, year and leap-day boundaries correctly", () => {
  // 回帰錨（境界値）: +1 日を日付の**文字列**や日成分の加算で作ると、月末・年末・閏日で
  // 壊れる（`2025.01.31` → `2025.01.32`）。上端は必ず「翌日の 0 時」でなければならない。
  const upperOf = (day) => new Date(traceBlockOf({ enabled: true, from: day, to: day }).end * 1000)
    .toISOString();
  assert.equal(upperOf("2025.01.31"), "2025-02-01T00:00:00.000Z", "月をまたげていない");
  assert.equal(upperOf("2024.12.31"), "2025-01-01T00:00:00.000Z", "年をまたげていない");
  assert.equal(upperOf("2024.02.28"), "2024-02-29T00:00:00.000Z", "閏年の 2/29 を飛ばしている");
  assert.equal(upperOf("2023.02.28"), "2023-03-01T00:00:00.000Z", "平年で 2/29 を作っている");
});

test("the inversion check compares the dates the user typed, not the +1 day upper bound", () => {
  // 境界: 開始が終了の**翌日**（1 日だけ逆転）。上端に +1 日を足した後の値で比べると
  // start === end になって**すり抜ける**。比較は利用者が打った日付同士で行う。
  assert.throws(
    () => traceBlockOf({ enabled: true, from: "2025.01.11", to: "2025.01.10" }),
    /trace/,
    "1 日だけ逆転した期間がすり抜けています（+1 日の後で比較しています）",
  );
  // 正の対照: 同じ日（幅 1 日）は有効であり、弾いてはならない。
  assert.ok(traceBlockOf({ enabled: true, from: "2025.01.10", to: "2025.01.10" }));
});

test("traceBlockOf refuses a one-sided period instead of inventing the other bound", () => {
  assert.throws(() => traceBlockOf({ enabled: true, from: "2024-01-01" }), /trace/);
  assert.throws(() => traceBlockOf({ enabled: true, to: "2024-01-02" }), /trace/);
});

test("traceBlockOf refuses an inverted period before it reaches the server", () => {
  assert.throws(
    () => traceBlockOf({ enabled: true, from: "2024-01-02", to: "2024-01-01" }),
    /trace/,
  );
  // 正の対照: 同じ形で順序だけ正しいものは通る（何を出しても throw ではない）。
  assert.ok(traceBlockOf({ enabled: true, from: "2024-01-01", to: "2024-01-02" }));
});

test("the smallest valid period is one day, not an empty window (ISO 表記)", () => {
  // 裁定 2026-09-10 で更新: 終了日を含むようになったため、**幅 0 の窓はもう作れない**。
  // 同じ日を上下端に打つと「その 1 日」になる（旧仕様では start === end の空窓だった）。
  assert.deepEqual(
    traceBlockOf({ enabled: true, from: "2024-01-01", to: "2024-01-01" }),
    { enabled: true, start: 1704067200, end: 1704153600 },
  );
});

// --- buildSubmission への結線 ------------------------------------------------

test("buildSubmission omits the trace key when the trace is off", () => {
  const body = buildSubmission({ profile: PROFILE, subject: SUBJECT, inputs: INPUTS });
  assert.equal("trace" in body, false);
  // 正の対照: 本文そのものは組まれている。
  assert.equal(body.backtest.ea_name, "TC24051901");
});

test("buildSubmission carries the trace block when it is switched on", () => {
  const body = buildSubmission({
    profile: PROFILE, subject: SUBJECT, inputs: INPUTS,
    trace: { enabled: true, from: "2024-01-01", to: "2024-01-02" },
  });
  // 上端は終了日の翌日 0 時（裁定 2026-09-10）。
  assert.deepEqual(body.trace, { enabled: true, start: 1704067200, end: 1704240000 });
});

test("buildSubmission leaves the rest of the body untouched when tracing", () => {
  const off = buildSubmission({ profile: PROFILE, subject: SUBJECT, inputs: INPUTS });
  const on = buildSubmission({
    profile: PROFILE, subject: SUBJECT, inputs: INPUTS, trace: { enabled: true },
  });
  assert.deepEqual(on.backtest, off.backtest);
  assert.deepEqual(Object.keys(on).sort(), ["backtest", "trace"]);
});

// --- クライアントの口 --------------------------------------------------------

test("submit sends the trace block in the JSON body", async () => {
  const fetchFn = fakeFetch(okResponse({ job_id: "j1", status: "running" }));
  await createJobSubmitClient({ fetch: fetchFn }).submit({
    backtest: { ea_name: "X" },
    trace: { enabled: true, start: 1704067200, end: 1704153600 },
  });
  const body = JSON.parse(fetchFn.calls[0].init.body);
  assert.deepEqual(body.trace, { enabled: true, start: 1704067200, end: 1704153600 });
});

test("submit omits the trace key when it is absent or empty", async () => {
  for (const trace of [undefined, null, {}]) {
    const fetchFn = fakeFetch(okResponse({ job_id: "j1", status: "running" }));
    await createJobSubmitClient({ fetch: fetchFn }).submit({
      backtest: { ea_name: "X" }, trace,
    });
    const body = JSON.parse(fetchFn.calls[0].init.body);
    assert.equal("trace" in body, false);
    // 正の対照: 本文そのものは送られている。
    assert.deepEqual(body.backtest, { ea_name: "X" });
  }
});
