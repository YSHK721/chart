// composition_root_execution（実行指示パネルの合成根・Phase 6 F-8）の単体テスト。
//
// 固定する不変条件（名前空間結線・依頼者承認 2026-08-12）:
//   1. パネル（sim_execution_panel_view）と投入クライアント（job_submit_client）を結線する。
//   2. 指標候補は GET /sim/ea-series/{ea_name} 由来（**選択中の ea_name の registry 系列名**）。
//      /sim/indicators（因果カタログ・別名前空間）は候補源に使わない。候補は投入時の
//      受付検証（E-5）・GenericConditionStrategy と同一名前空間になる。
//   3. ea_name を変えると候補を選択 EA の系列へ入れ替える（再取得）。
//   4. 投入ボタン → client.submit（POST /sim/jobs・strategy 本文つき）→ onSubmitted。
import { test } from "node:test";
import assert from "node:assert/strict";

import { fakeDoc, findById, flatten } from "./_fakes.js";
import { settingsSchema } from "./_settings_schema_fixture.js";
import { mountSimExecutionPanel } from "../js/adapter/front/composition_root_execution.js";

const hasClass = (el, c) => String((el && el.className) || "").split(/\s+/).includes(c);
const byClass = (root, c) => flatten(root).filter((n) => hasClass(n, c));
const flush = () => new Promise((r) => setTimeout(r, 0));

// ea_name 別の registry 系列（backend の /sim/ea-series が返す形）。
const EA_SERIES = {
  PRO_fit_Band_EA: { ok: true, ea_name: "PRO_fit_Band_EA", series: ["adx", "close", "ema"] },
  TC24051901: { ok: true, ea_name: "TC24051901", series: ["close", "madiff"] },
};
const EA_LIST = ["PRO_fit_Band_EA", "TC24051901"];

// GET /sim/run-options が返す形（datasets プロファイル＋ea_names）。
const RUN_OPTIONS = {
  ok: true,
  datasets: [{
    dataset: "jp225_m1", data_path: "/d/jp225_m1.csv", symbol: "JP225", period: "M1",
    contract_size: 10.0, digits: 1, point_size: 0.1, leverage: 10.0,
    volume_min: 0.01, volume_max: 100.0, volume_step: 0.01, stops_level: 0,
  }],
  ea_names: ["PRO_fit_Band_EA", "TC24051901"],
};

function routerFetch({ job, schema } = {}) {
  const calls = [];
  const fn = async (url, init) => {
    calls.push({ url, init });
    // Phase 8: schema を渡さない呼び出しでは `/sim/settings-schema` は 404 に落ちる
    // （＝Tester パネルを結線できない構成）。既存の検定はこの経路のままで通る。
    if (url === "/sim/settings-schema") {
      return schema
        ? { ok: true, status: 200, json: async () => schema }
        : { ok: false, status: 404, json: async () => ({ error: "no schema" }) };
    }
    if (url.startsWith("/sim/ea-series/")) {
      const ea = decodeURIComponent(url.slice("/sim/ea-series/".length));
      const payload = EA_SERIES[ea] || { ok: true, ea_name: ea, series: [] };
      return { ok: true, status: 200, json: async () => payload };
    }
    if (url === "/sim/run-options") return { ok: true, status: 200, json: async () => RUN_OPTIONS };
    if (url === "/sim/jobs") return { ok: true, status: 202, json: async () => (job || { job_id: "j1", status: "running" }) };
    return { ok: false, status: 404, json: async () => ({ error: "nope" }) };
  };
  fn.calls = calls;
  return fn;
}

test("mount builds the execution panel", async () => {
  const doc = fakeDoc();
  await mountSimExecutionPanel({ doc, host: doc.body, fetch: routerFetch(), eaCandidates: EA_LIST });
  assert.ok(findById(doc.body, "simExecPanel"), "パネルが生成されていない");
  assert.ok(findById(doc.body, "execSubmit"));
});

test("indicator candidates come from GET /sim/ea-series/{selected ea}", async () => {
  const doc = fakeDoc();
  const fetchFn = routerFetch();
  await mountSimExecutionPanel({ doc, host: doc.body, fetch: fetchFn, eaCandidates: EA_LIST });
  // 選択中の ea_name（初期は先頭）の系列を GET している
  assert.ok(fetchFn.calls.some((c) => c.url === "/sim/ea-series/PRO_fit_Band_EA"));
  // /sim/indicators は候補源に使わない
  assert.ok(!fetchFn.calls.some((c) => c.url === "/sim/indicators"));
  // 新規行の指標セレクタが選択 EA の registry 系列だけを持つ
  findById(doc.body, "execAddLong")._listeners.click[0]();
  const indSel = byClass(doc.body, "exec-ind")[0];
  assert.deepEqual((indSel.children || []).map((o) => o.value), ["adx", "close", "ema"]);
});

test("changing ea_name refetches and repopulates the candidates", async () => {
  const doc = fakeDoc();
  const fetchFn = routerFetch();
  await mountSimExecutionPanel({ doc, host: doc.body, fetch: fetchFn, eaCandidates: EA_LIST });
  // ea_name を TC24051901 へ変更 → change 発火
  const eaSel = findById(doc.body, "execEaName");
  eaSel.value = "TC24051901";
  eaSel._listeners.change[0]();
  await flush();
  // 選択 EA の系列を再取得している
  assert.ok(fetchFn.calls.some((c) => c.url === "/sim/ea-series/TC24051901"));
  // 既存行があっても候補が入れ替わる
  findById(doc.body, "execAddLong")._listeners.click[0]();
  const indSel = byClass(doc.body, "exec-ind")[0];
  assert.deepEqual((indSel.children || []).map((o) => o.value), ["close", "madiff"]);
});

test("submit posts the built body and notifies onSubmitted", async () => {
  const doc = fakeDoc();
  const fetchFn = routerFetch({ job: { job_id: "j7", status: "running" } });
  const submitted = [];
  await mountSimExecutionPanel({
    doc, host: doc.body, fetch: fetchFn, eaCandidates: EA_LIST, onSubmitted: (r) => submitted.push(r),
  });
  findById(doc.body, "execSl").value = "100";
  findById(doc.body, "execTp").value = "200";
  findById(doc.body, "execAddLong")._listeners.click[0]();
  const row = byClass(doc.body, "exec-cond-row")[0];
  byClass(row, "exec-ind")[0].value = "ema";
  byClass(row, "exec-shift")[0].value = "1";
  byClass(row, "exec-op")[0].value = ">";
  byClass(row, "exec-rhs")[0].value = "0.5";
  findById(doc.body, "execSubmit")._listeners.click[0]();
  await flush();
  const post = fetchFn.calls.find((c) => c.url === "/sim/jobs");
  assert.ok(post, "POST /sim/jobs が呼ばれていない");
  const body = JSON.parse(post.init.body);
  assert.equal(body.backtest.ea_name, "PRO_fit_Band_EA");
  assert.deepEqual(body.strategy.entry_long, [{ indicator: "ema", shift: 1, op: ">", rhs: 0.5 }]);
  assert.deepEqual(submitted, [{ job_id: "j7", status: "running" }]);
});

// --- Phase 6 拡張: run-options 結線 ＋ 結果導線（自動遷移禁止）-----------------

test("mount loads run-options and populates the dataset selector + ea candidates", async () => {
  const doc = fakeDoc();
  const fetchFn = routerFetch();
  await mountSimExecutionPanel({ doc, host: doc.body, fetch: fetchFn });
  assert.ok(fetchFn.calls.some((c) => c.url === "/sim/run-options"), "run-options を取得していない");
  const ds = findById(doc.body, "execDataset");
  assert.deepEqual((ds.children || []).map((o) => o.value), ["jp225_m1"]);
  // ea 候補は run-options の ea_names から（eaCandidates 未指定でも埋まる）
  const eaSel = findById(doc.body, "execEaName");
  assert.deepEqual((eaSel.children || []).map((o) => o.value), ["PRO_fit_Band_EA", "TC24051901"]);
});

test("run-options profile drives the submitted 18-key body", async () => {
  const doc = fakeDoc();
  const fetchFn = routerFetch();
  const submitted = [];
  await mountSimExecutionPanel({ doc, host: doc.body, fetch: fetchFn, onSubmitted: (r) => submitted.push(r) });
  findById(doc.body, "execSubmit")._listeners.click[0]();
  await flush();
  const post = fetchFn.calls.find((c) => c.url === "/sim/jobs");
  const body = JSON.parse(post.init.body);
  assert.equal(body.backtest.symbol, "JP225");
  assert.equal(body.backtest.contract_size, 10.0);
  assert.equal(body.backtest.data_path, "/d/jp225_m1.csv");
});

test("after submit a 'see results' affordance appears and does NOT auto-navigate", async () => {
  const doc = fakeDoc();
  const nav = [];
  await mountSimExecutionPanel({
    doc, host: doc.body, fetch: routerFetch({ job: { job_id: "abc", status: "running" } }),
    navigate: (url) => nav.push(url),
  });
  findById(doc.body, "execSubmit")._listeners.click[0]();
  await flush();
  // 自動遷移しない（ビュー自動介入禁止）
  assert.deepEqual(nav, []);
  const link = findById(doc.body, "execViewResult");
  assert.ok(link, "結果導線が出ていない");
  // ユーザークリックで初めて ?job=<id> へ遷移
  link._listeners.click[0]();
  assert.deepEqual(nav, ["?job=abc"]);
});

// --- Phase 8: Tester Settings パネルの結線（スライス 5）---------------------------
// 固定する不変条件:
//   1. 合成根は `GET /sim/settings-schema` を取りに行き、取れた schema をパネルへ注入する。
//   2. 取得に失敗してもパネル自体は出る（fail-open・run-options の既存流儀）。その場合は
//      settings を本文に載せず、旧フォーム投入がそのまま成立する（併存）。
//   3. 投入本文に `settings.tester`（生トークン）が載り、`backtest.ea_name` は Expert 由来。
//   4. 指標候補の取得起点は Expert 選択（schema がある構成では指標セット欄を重複させない）。

test("mount fetches the settings schema and feeds the tester panel", async () => {
  const schema = settingsSchema();
  const doc = fakeDoc();
  const fetchFn = routerFetch({ schema });
  await mountSimExecutionPanel({ doc, host: doc.body, fetch: fetchFn });
  assert.ok(fetchFn.calls.some((c) => c.url === "/sim/settings-schema"), "schema を取得していない");
  assert.ok(findById(doc.body, "simTesterPanel"), "Tester パネルが出ていない");
  const expert = findById(doc.body, "testerExpert");
  assert.deepEqual((expert.children || []).map((o) => o.value),
    schema.expert_options.map((o) => o.token));
});

test("the tester panel is still mounted when the schema fetch fails (fail-open)", async () => {
  const doc = fakeDoc();
  const fetchFn = routerFetch();
  await mountSimExecutionPanel({ doc, host: doc.body, fetch: fetchFn, eaCandidates: EA_LIST });
  assert.ok(findById(doc.body, "simTesterPanel"), "取得失敗でパネルが消えている（fail-open ではない）");
  // 候補が無いので settings は組めない。旧フォーム（指標セット欄）がそのまま権威。
  assert.ok(findById(doc.body, "execEaName"), "旧フォームの指標セット欄まで消えている");
  findById(doc.body, "execSubmit")._listeners.click[0]();
  await flush();
  const body = JSON.parse(fetchFn.calls.find((c) => c.url === "/sim/jobs").init.body);
  assert.equal("settings" in body, false);
});

test("submitting with a schema posts the settings block and the derived ea_name", async () => {
  const schema = settingsSchema();
  const doc = fakeDoc();
  const fetchFn = routerFetch({ schema });
  await mountSimExecutionPanel({ doc, host: doc.body, fetch: fetchFn });
  findById(doc.body, "execSubmit")._listeners.click[0]();
  await flush();
  const body = JSON.parse(fetchFn.calls.find((c) => c.url === "/sim/jobs").init.body);
  assert.ok(body.settings, "settings ブロックが本文に載っていない");
  assert.equal(body.settings.tester.Expert, schema.expert_options[0].token);
  assert.equal(body.backtest.ea_name, schema.expert_options[0].label);
  // profile 由来キーは run-options のまま（front リテラル 0）
  assert.equal(body.backtest.symbol, RUN_OPTIONS.datasets[0].symbol);
  // 生トークンのみ（数値・日付も文字列）
  for (const [k, v] of Object.entries(body.settings.tester)) {
    assert.equal(typeof v, "string", k);
  }
});

test("changing the Expert refetches the indicator candidates for that EA", async () => {
  const schema = settingsSchema();
  const doc = fakeDoc();
  const fetchFn = routerFetch({ schema });
  await mountSimExecutionPanel({ doc, host: doc.body, fetch: fetchFn });
  assert.ok(fetchFn.calls.some((c) => c.url === "/sim/ea-series/PRO_fit_Band_EA"));
  const expert = findById(doc.body, "testerExpert");
  expert.value = schema.expert_options[1].token;
  expert._listeners.change[0]();
  await flush();
  assert.ok(fetchFn.calls.some((c) => c.url === "/sim/ea-series/TC24051901"),
    "Expert 変更で候補を取り直していない");
});

test("reportViewUrl builds the ?job= dispatch url", async () => {
  const { reportViewUrl } = await import("../js/adapter/front/composition_root_execution.js");
  assert.equal(reportViewUrl("abc"), "?job=abc");
});
