// composition_root_execution（投入フォームの合成根・Phase 6 F-8 / Phase 9 S1〜S6）の単体テスト。
//
// 固定する不変条件:
//   1. 各面（M1〜M4）と投入クライアント（job_submit_client）を結線する。
//   2. 実行条件（データセット profile・ea_name 候補）は GET /sim/run-options 由来。
//   3. Tester Settings の schema は GET /sim/settings-schema 由来（取得失敗は fail-open）。
//   4. 投入ボタン → client.submit（POST /sim/jobs）→ onSubmitted。
import { test } from "node:test";
import assert from "node:assert/strict";

import { fakeDoc, findById } from "./_fakes.js";
import { EA_INPUT_FIELDS } from "../js/adapter/front/sim_ea_inputs_panel_view.js";
import { settingsSchema } from "./_settings_schema_fixture.js";
import { mountSimExecutionPanel } from "../js/adapter/front/composition_root_execution.js";

const flush = () => new Promise((r) => setTimeout(r, 0));
/** EA パラメータ欄の id（所在の単一ソースは宣言表＝この検定へ写さない）。 */
const eaInputId = (param) => EA_INPUT_FIELDS.find((f) => f.param === param).id;

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

function routerFetch({ job, schema, schemaRaw } = {}) {
  const calls = [];
  const fn = async (url, init) => {
    calls.push({ url, init });
    // Phase 8: schema を渡さない呼び出しでは `/sim/settings-schema` は 404 に落ちる
    // （＝Tester パネルを結線できない構成）。既存の検定はこの経路のままで通る。
    if (url === "/sim/settings-schema") {
      // `schemaRaw`: HTTP は 200 なのに本文が JSON でない（プロキシのエラーページ等）。
      if (schemaRaw) {
        return { ok: true, status: 200, json: async () => { throw new Error("Unexpected token <"); } };
      }
      return schema
        ? { ok: true, status: 200, json: async () => schema }
        : { ok: false, status: 404, json: async () => ({ error: "no schema" }) };
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
  assert.ok(findById(doc.body, "simRunActionPanel"), "実行指示面が生成されていない");
  assert.ok(findById(doc.body, "runStart"));
});

test("submit posts the built body and notifies onSubmitted", async () => {
  const doc = fakeDoc();
  const fetchFn = routerFetch({ job: { job_id: "j7", status: "running" } });
  const submitted = [];
  await mountSimExecutionPanel({
    doc, host: doc.body, fetch: fetchFn, eaCandidates: EA_LIST, onSubmitted: (r) => submitted.push(r),
  });
  findById(doc.body, eaInputId("stop_loss_points")).value = "100";
  findById(doc.body, eaInputId("take_profit_points")).value = "200";
  findById(doc.body, "runStart")._listeners.click[0]();
  await flush();
  const post = fetchFn.calls.find((c) => c.url === "/sim/jobs");
  assert.ok(post, "POST /sim/jobs が呼ばれていない");
  const body = JSON.parse(post.init.body);
  assert.equal(body.backtest.ea_name, "PRO_fit_Band_EA");
  assert.equal(body.backtest.stop_loss_points, 100);
  assert.deepEqual(submitted, [{ job_id: "j7", status: "running" }]);
});

// --- Phase 6 拡張: run-options 結線 ＋ 結果導線（自動遷移禁止）-----------------

test("mount loads run-options and populates the symbol + ea candidates", async () => {
  const doc = fakeDoc();
  const fetchFn = routerFetch();
  const original = console.warn;
  console.warn = () => {};
  try {
    await mountSimExecutionPanel({ doc, host: doc.body, fetch: fetchFn });
  } finally {
    console.warn = original;
  }
  assert.ok(fetchFn.calls.some((c) => c.url === "/sim/run-options"), "run-options を取得していない");
  // 銘柄候補は run-options の datasets から（データセット選択は出さない）
  const symbol = findById(doc.body, "execSymbol");
  assert.deepEqual((symbol.children || []).map((o) => o.value),
    RUN_OPTIONS.datasets.map((d) => d.symbol));
  // ea 候補は run-options の ea_names から（eaCandidates 未指定でも埋まる）
  const eaSel = findById(doc.body, "execEaName");
  assert.deepEqual((eaSel.children || []).map((o) => o.value), ["PRO_fit_Band_EA", "TC24051901"]);
});

test("run-options profile drives the submitted 18-key body", async () => {
  const doc = fakeDoc();
  const fetchFn = routerFetch();
  const submitted = [];
  await mountSimExecutionPanel({ doc, host: doc.body, fetch: fetchFn, onSubmitted: (r) => submitted.push(r) });
  findById(doc.body, "runStart")._listeners.click[0]();
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
  findById(doc.body, "runStart")._listeners.click[0]();
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
//   4. schema がある構成では指標セット欄・初期資金欄を重複させない（T-4）。

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
  findById(doc.body, "runStart")._listeners.click[0]();
  await flush();
  const body = JSON.parse(fetchFn.calls.find((c) => c.url === "/sim/jobs").init.body);
  assert.equal("settings" in body, false);
});

test("submitting with a schema posts the settings block and the derived ea_name", async () => {
  const schema = settingsSchema();
  const doc = fakeDoc();
  const fetchFn = routerFetch({ schema });
  await mountSimExecutionPanel({ doc, host: doc.body, fetch: fetchFn });
  findById(doc.body, "runStart")._listeners.click[0]();
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

// --- fail-open の起動条件（🔴-1）: 200 でも schema でなければ結線しない ----------------
// 200＋非 JSON を成功として扱うと、空 schema の Tester パネルが settings の供給元として
// 結線され、EA 欄・初期資金欄が器から外れた**投入不能フォーム**になる。

test("a 200 non-JSON schema response leaves the legacy form authoritative (fail-open)", async () => {
  const doc = fakeDoc();
  const fetchFn = routerFetch({ schemaRaw: true });
  await mountSimExecutionPanel({ doc, host: doc.body, fetch: fetchFn, eaCandidates: EA_LIST });
  // 旧フォームの欄が残っている（＝Tester パネルは settings の供給元として結線されていない）
  assert.ok(findById(doc.body, "execEaName"), "指標セット欄が消えています（投入不能フォーム）");
  assert.ok(findById(doc.body, "execDeposit"), "初期資金欄が消えています（投入不能フォーム）");
  assert.ok(findById(doc.body, "simTesterPanel"), "パネルの器まで消えています");
  findById(doc.body, "runStart")._listeners.click[0]();
  await flush();
  const body = JSON.parse(fetchFn.calls.find((c) => c.url === "/sim/jobs").init.body);
  assert.equal("settings" in body, false, "空 schema のまま settings を載せています");
  assert.equal(body.backtest.ea_name, EA_LIST[0]);
});

test("the schema failure reason is reported, not swallowed", async () => {
  const doc = fakeDoc();
  const seen = [];
  const original = console.warn;
  console.warn = (...args) => seen.push(args.map(String).join(" "));
  try {
    await mountSimExecutionPanel({ doc, host: doc.body, fetch: routerFetch(), eaCandidates: EA_LIST });
  } finally {
    console.warn = original;
  }
  assert.equal(seen.length, 1, `取得失敗の理由が捨てられています: ${JSON.stringify(seen)}`);
  assert.match(seen[0], /settings-schema|schema/i);
});

// --- Phase 9 S3: 実行対象の供給元は M1 / M4 のどちらか 1 つだけ ---------------------
// 「作ってから消す」（欄を出してから removeChild する）を撤去した。schema が取れた構成では
// 縮退面をそもそも作らず、取れない構成では Tester 面を実行対象の供給元にしない。

test("the settings configuration mounts no degraded surface at all", async () => {
  const doc = fakeDoc();
  await mountSimExecutionPanel({ doc, host: doc.body, fetch: routerFetch({ schema: settingsSchema() }) });
  assert.ok(findById(doc.body, "simTesterPanel"), "Tester 面が出ていない");
  assert.equal(findById(doc.body, "simSchemaFallbackPanel"), null,
    "schema が取れているのに縮退面まで作っています（作ってから消す形の再発）");
  assert.equal(findById(doc.body, "execEaName"), null, "縮退面の EA 欄が残っています");
  assert.equal(findById(doc.body, "execDeposit"), null, "縮退面の初期資金欄が残っています");
});

test("the degraded configuration mounts the fallback surface and keeps the tester panel visible", async () => {
  const doc = fakeDoc();
  const seen = [];
  const original = console.warn;
  console.warn = (...args) => seen.push(args.map(String).join(" "));
  try {
    await mountSimExecutionPanel({ doc, host: doc.body, fetch: routerFetch(), eaCandidates: EA_LIST });
  } finally {
    console.warn = original;
  }
  // 縮退面が立つ（実行対象の供給元）
  assert.ok(findById(doc.body, "simSchemaFallbackPanel"), "縮退面が出ていない");
  assert.ok(findById(doc.body, "execEaName"), "縮退面の EA 欄が無い");
  // Tester 面の器は残る（なぜ設定を組めないのかを画面に出し続ける＝fail-open）
  assert.ok(findById(doc.body, "simTesterPanel"), "取得失敗でパネルの器ごと消えている");
  assert.equal(seen.length, 1, "取得失敗の理由が捨てられています");
});

test("the degraded ea candidates come from run-options (縮退面にも候補が届く)", async () => {
  const doc = fakeDoc();
  const original = console.warn;
  console.warn = () => {};
  try {
    await mountSimExecutionPanel({ doc, host: doc.body, fetch: routerFetch() });
  } finally {
    console.warn = original;
  }
  const sel = findById(doc.body, "execEaName");
  assert.deepEqual((sel.children || []).map((o) => o.value), RUN_OPTIONS.ea_names);
});

test("reportViewUrl builds the ?job= dispatch url", async () => {
  const { reportViewUrl } = await import("../js/adapter/front/composition_root_execution.js");
  assert.equal(reportViewUrl("abc"), "?job=abc");
});
