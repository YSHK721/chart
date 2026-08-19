// composition_root_execution（投入フォームの合成根・Phase 6 F-8 / Phase 9 S1〜S6）の単体テスト。
//
// 固定する不変条件:
//   1. 各面（M1〜M4）と投入クライアント（job_submit_client）を結線する。
//   2. 実行条件（データセット profile・ea_name 候補）は GET /sim/run-options 由来。
//   3. Tester Settings の schema は GET /sim/settings-schema 由来（取得失敗は fail-open）。
//   4. 投入ボタン → client.submit（POST /sim/jobs）→ onSubmitted。
import { test } from "node:test";
import assert from "node:assert/strict";

import { fakeDoc, findById, flatten } from "./_fakes.js";
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

function routerFetch({ job, schema, schemaRaw, runOptions } = {}) {
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
    if (url === "/sim/run-options") {
      return { ok: true, status: 200, json: async () => (runOptions || RUN_OPTIONS) };
    }
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

// --- 実行条件の payload が壊れていても画面は立つ（fail-open・§19.4）-------------------
// `datasets` が配列でない payload（別実装・プロキシが object を返した等）でも mount は完走し、
// 操作できるフォームが出る。合成根が `datasets.map` を直に呼んでいた S6 時点では **TypeError で
// mount が中断し、欄が 1 つも描かれなかった**（実測: 操作要素 1 個 / 新 25 個）。M5 の
// `symbolCandidatesOf` が非配列を空候補へ畳むことで、銘柄が自由入力へ縮退して画面が残る。

test("a non-array datasets payload degrades instead of blanking the form (fail-open)", async () => {
  const doc = fakeDoc();
  const fetchFn = routerFetch({
    schema: settingsSchema(),
    runOptions: { ok: true, datasets: {}, ea_names: [] },   // 配列でない payload
  });
  // Act: mount が例外で中断しない
  await assert.doesNotReject(
    () => mountSimExecutionPanel({ doc, host: doc.body, fetch: fetchFn }),
    "非配列 payload で mount が中断しました（欄が 1 つも描かれません）",
  );
  // Assert: 3 面が立ち、操作できる状態で残る
  for (const id of ["simTesterPanel", "simEaInputsPanel", "simRunActionPanel"]) {
    assert.ok(findById(doc.body, id), `${id} が描かれていません`);
  }
  // 銘柄は候補 0 件＝自由入力へ縮退する（候補を出せないことを理由に投入不能にしない）
  const symbol = findById(doc.body, "testerSymbol");
  assert.equal(symbol.tagName, "INPUT", "銘柄が自由入力へ縮退していません");
});

// --- Phase 9 段階 3 S3: 投入フィードバックの結線（§19.6・ISSUE-423）-------------------
// 固定する不変条件:
//   1. 掲示面（M6）は実行指示面（M3）の**直下**に組まれる（§19.6 R2）。
//   2. 投入が通れば job_id と status がそのまま掲示される。
//   3. 投入が拒まれればサーバの理由文が掲示される（400 が画面に 1 文字も出ない状態の是正）。
//      既存の onError 呼出は**維持**する（後方互換）。
//   4. 投入前の本文組立で例外が出ても無音にしない（try の射程が本文組立を含む）。

/** 掲示枠のテキストを class から引く。 */
function statusTextOf(host, className) {
  const hit = flatten(host).find((n) => String(n.className || "").split(/\s+/).includes(className));
  return hit ? String(hit.textContent || "") : null;
}

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

test("the run status surface is mounted right below the run action surface (§19.6 R2)", async () => {
  // Arrange / Act
  const doc = fakeDoc();
  await mountSimExecutionPanel({ doc, host: doc.body, fetch: routerFetch({ schema: settingsSchema() }) });
  // Assert
  const ids = doc.body.children.map((c) => c.id);
  assert.ok(ids.includes("simRunStatusPanel"), "掲示面が組まれていない");
  assert.equal(ids[ids.indexOf("simRunActionPanel") + 1], "simRunStatusPanel",
    `掲示面がスタートの直下にありません: ${ids.join(",")}`);
});

test("a successful submit posts the job id and status on the status surface", async () => {
  // Arrange
  const doc = fakeDoc();
  const fetchFn = routerFetch({ job: { job_id: "j42", status: "received" } });
  await mountSimExecutionPanel({ doc, host: doc.body, fetch: fetchFn, eaCandidates: EA_LIST });
  // Act
  findById(doc.body, "runStart")._listeners.click[0]();
  await flush();
  // Assert
  assert.equal(statusTextOf(doc.body, "run-status-job"), "j42");
  assert.equal(statusTextOf(doc.body, "run-status-state"), "received");
});

test("a rejected submit posts the server reason and still calls onError (後方互換)", async () => {
  // Arrange: 受付が 400 で弾く構成
  const doc = fakeDoc();
  const fetchFn = (url, init) => {
    if (url === "/sim/settings-schema") return Promise.resolve({ ok: false, status: 404, json: async () => ({ error: "no schema" }) });
    if (url === "/sim/run-options") return Promise.resolve({ ok: true, status: 200, json: async () => RUN_OPTIONS });
    if (url === "/sim/jobs") {
      return Promise.resolve({ ok: false, status: 400, json: async () => ({ error: "実行対象が一致しません" }) });
    }
    return Promise.resolve({ ok: false, status: 404, json: async () => ({ error: "nope" }) });
  };
  const errors = [];
  const warn = console.warn;
  console.warn = () => {};
  try {
    await mountSimExecutionPanel({
      doc, host: doc.body, fetch: fetchFn, eaCandidates: EA_LIST, onError: (e) => errors.push(e),
    });
  } finally {
    console.warn = warn;
  }
  // Act
  const seen = await capturingErrors(async () => {
    findById(doc.body, "runStart")._listeners.click[0]();
    await flush();
  });
  // Assert: 画面に理由が出る（ISSUE-423 の沈黙の解消）
  assert.equal(statusTextOf(doc.body, "run-status-reason"), "実行対象が一致しません");
  assert.equal(statusTextOf(doc.body, "run-status-state"), "400");
  // 既存の購読口は従来どおり呼ばれる
  assert.equal(errors.length, 1, "onError の呼出が失われています（後方互換の破れ）");
  assert.match(errors[0].message, /実行対象が一致しません/);
  // 理由は開発者コンソールにも残す（握り潰し禁止）
  assert.ok(seen.some((line) => /実行対象が一致しません/.test(line)), `console.error に残っていません: ${JSON.stringify(seen)}`);
});

test("a throwing body assembly is posted instead of silently escaping (B2: try の射程)", async () => {
  // Arrange: 本文の組立段（投入の**前**）で落ちる構成
  const doc = fakeDoc();
  const fetchFn = routerFetch({ schema: settingsSchema() });
  const panel = await mountSimExecutionPanel({ doc, host: doc.body, fetch: fetchFn });
  panel.eaInputsView.values = () => { throw new Error("EA パラメータを読めません"); };
  // Act
  const seen = await capturingErrors(async () => {
    findById(doc.body, "runStart")._listeners.click[0]();
    await flush();
  });
  // Assert: 画面が無音にならない
  assert.match(String(statusTextOf(doc.body, "run-status-reason")), /EA パラメータを読めません/,
    "投入前の例外が画面に出ていません（try の射程が本文組立を含んでいない）");
  assert.ok(seen.some((line) => /EA パラメータを読めません/.test(line)),
    `console.error に残っていません: ${JSON.stringify(seen)}`);
  // 投入そのものは起きていない（壊れた本文を送らない）
  assert.equal(fetchFn.calls.filter((c) => c.url === "/sim/jobs").length, 0);
});

// --- Phase 9 段階 3 S4: 実行状態の監視（watch）の結線 ---------------------------------
// 固定する不変条件:
//   1. 投入が受理されたら状態監視が始まり、掲示が状態に追従する。
//   2. 終端（サーバの `terminal`）で監視が止まる。
//   3. 再投入では**前の監視を落とす**（同時 1 本）。落とさないと、前の run の状態が
//      新しい run の掲示を上書きし続ける。

/** 注入 timer のダブル（実時間を待たずに周期を進める）。 */
function fakeTimer() {
  const pending = new Map();
  let nextId = 1;
  return {
    pending,
    set(fn, ms) { const id = nextId; nextId += 1; pending.set(id, { fn, ms }); return id; },
    clear(id) { pending.delete(id); },
    async tick() {
      const [id, entry] = [...pending.entries()][0] || [];
      if (!entry) return false;
      pending.delete(id);
      await entry.fn();
      return true;
    },
  };
}

/** 投入は連番の job_id を返し、状態照会は与えた台本を返す fetch。 */
function watchFetch(jobIds, statesByJob) {
  const calls = [];
  let submitted = 0;
  const fn = async (url, init) => {
    calls.push({ url, init });
    if (url === "/sim/settings-schema") return { ok: true, status: 200, json: async () => settingsSchema() };
    if (url === "/sim/run-options") return { ok: true, status: 200, json: async () => RUN_OPTIONS };
    if (url === "/sim/jobs") {
      const jobId = jobIds[Math.min(submitted, jobIds.length - 1)];
      submitted += 1;
      return { ok: true, status: 202, json: async () => ({ job_id: jobId, status: "received" }) };
    }
    for (const jobId of jobIds) {
      if (url === `/sim/jobs/${jobId}`) {
        const queue = statesByJob[jobId];
        const state = queue.length > 1 ? queue.shift() : queue[0];
        return { ok: true, status: 200, json: async () => state };
      }
    }
    return { ok: false, status: 404, json: async () => ({ error: "nope" }) };
  };
  fn.calls = calls;
  return fn;
}

test("an accepted submit starts a watch that follows the job to its terminal state", async () => {
  // Arrange
  const doc = fakeDoc();
  const timer = fakeTimer();
  const fetchFn = watchFetch(["j1"], {
    j1: [
      { job_id: "j1", status: "running", failure_reason: null, terminal: false },
      { job_id: "j1", status: "failed", failure_reason: "N-05: 非対象トークン", terminal: true },
    ],
  });
  await mountSimExecutionPanel({
    doc, host: doc.body, fetch: fetchFn, setTimeout: timer.set, clearTimeout: timer.clear,
  });
  findById(doc.body, "runStart")._listeners.click[0]();
  await flush();
  // Act
  await timer.tick();
  // Assert: 実行中が掲示される
  assert.equal(statusTextOf(doc.body, "run-status-state"), "running");
  // Act: 終端まで進める
  await timer.tick();
  // Assert: 失敗理由（N-05）が画面に出て監視が止まる
  assert.equal(statusTextOf(doc.body, "run-status-state"), "failed");
  assert.equal(statusTextOf(doc.body, "run-status-reason"), "N-05: 非対象トークン");
  assert.equal(await timer.tick(), false, "終端に達しても監視が続いています");
});

test("re-submitting stops the previous watch (同時 1 本)", async () => {
  // Arrange
  const doc = fakeDoc();
  const timer = fakeTimer();
  const fetchFn = watchFetch(["j1", "j2"], {
    j1: [{ job_id: "j1", status: "running", terminal: false }],
    j2: [{ job_id: "j2", status: "running", terminal: false }],
  });
  await mountSimExecutionPanel({
    doc, host: doc.body, fetch: fetchFn, setTimeout: timer.set, clearTimeout: timer.clear,
  });
  const start = findById(doc.body, "runStart")._listeners.click[0];
  start();
  await flush();
  // Act: 2 回目の投入
  start();
  await flush();
  // Assert: 監視は 1 本だけで、指しているのは新しい job
  assert.equal(timer.pending.size, 1, "前の監視が落ちていません（掲示が古い run に上書きされます）");
  await timer.tick();
  const polled = fetchFn.calls.filter((c) => String(c.url).startsWith("/sim/jobs/")).map((c) => c.url);
  assert.deepEqual(polled, ["/sim/jobs/j2"], `古い job を監視しています: ${polled.join(",")}`);
  assert.equal(statusTextOf(doc.body, "run-status-job"), "j2");
});

test("a watch that gives up posts the reason instead of freezing (無音で監視を諦めない)", async () => {
  // Arrange: 状態照会が常に 502
  const doc = fakeDoc();
  const timer = fakeTimer();
  const fetchFn = async (url, init) => {
    if (url === "/sim/settings-schema") return { ok: true, status: 200, json: async () => settingsSchema() };
    if (url === "/sim/run-options") return { ok: true, status: 200, json: async () => RUN_OPTIONS };
    if (url === "/sim/jobs") return { ok: true, status: 202, json: async () => ({ job_id: "j1", status: "received" }) };
    return { ok: false, status: 502, json: async () => { throw new Error("no body"); } };
  };
  await mountSimExecutionPanel({
    doc, host: doc.body, fetch: fetchFn, setTimeout: timer.set, clearTimeout: timer.clear,
  });
  findById(doc.body, "runStart")._listeners.click[0]();
  await flush();
  // Act: 上限まで失敗させる
  const seen = await capturingErrors(async () => {
    while (await timer.tick());
  });
  // Assert
  assert.match(String(statusTextOf(doc.body, "run-status-reason")), /502/,
    "監視を諦めた理由が画面に出ていません");
  assert.ok(seen.some((line) => /502/.test(line)), `console.error に残っていません: ${JSON.stringify(seen)}`);
});

test("reportViewUrl builds the ?job= dispatch url", async () => {
  const { reportViewUrl } = await import("../js/adapter/front/composition_root_execution.js");
  assert.equal(reportViewUrl("abc"), "?job=abc");
});
