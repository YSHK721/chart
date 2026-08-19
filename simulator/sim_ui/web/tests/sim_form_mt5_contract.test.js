// 画面契約: sim 設定画面の全要素が MT5 の対応物を持つ（Phase 9 S6・§19.2「画面契約の機械検査」）。
//
// 射程（先に明示する）: これは **MT5 実機との一致の証明ではない**。証明するのは「画面に出て
// いる入力が、宣言（schema の `.ini` キー / EA パラメータ宣言表 / 実行操作）のどれに対応する
// のか、1 つ残らず宣言されている」こと——**宣言接続の証明**である。MT5 実機の挙動と突き合わ
// せるのは別工程（段階 2 以降）の責務である。
//
// なぜ検定にするか: 「MT5 に無い入力を出さない」は目視では守れない。欄を 1 つ足すのは 1 行で
// でき、その 1 行がどの MT5 概念に対応するかは書き手の頭の中にしか無い。`data-mt5` を必須に
// すると、対応物を言えない欄は**そもそも足せなくなる**。
//
// 語彙（`data-mt5` の接頭辞）:
//   tester:<Key>   `[Tester]` の `.ini` キー。単一ソースは schema の `key_order`
//   inputs:<param> EA パラメータ。単一ソースは M2 の宣言表 EA_INPUT_FIELDS
//   action:<name>  実行操作（本文を持たない）
//   ui:<name>      表示制御。**本文に寄与しない**（自分の名前が本文のキーにならない）
//
// 固定する不変条件:
//   1. **host 全体**の全 INPUT / SELECT / BUTTON が `data-mt5` を持ち、かつ 4 面のいずれかの
//      配下にある（面の外に操作要素 0）。走査を面の内側に限ると、面の外へ生やした操作要素は
//      検定から**見えないまま**になる（実測: 面の外の `data-mt5` 無しボタンは素通りした）。
//   2. `tester:` の名前は schema の `key_order` の部分集合（この検定にキー名を写さない）。
//   3. `inputs:` の集合 == 宣言表の param 集合 == 投入本文の実行仕様キー差（双方向一致）。
//   4. `action:` の集合はちょうど {start}。
//   5. `ui:` の名前は本文のキーに現れない。
import { test } from "node:test";
import assert from "node:assert/strict";

import { fakeDoc, findById, flatten } from "./_fakes.js";
import { settingsSchema } from "./_settings_schema_fixture.js";
import { EA_INPUT_FIELDS } from "../js/adapter/front/sim_ea_inputs_panel_view.js";
import { PROFILE_KEYS } from "../js/adapter/front/sim_submission_builder.js";
import { mountSimExecutionPanel } from "../js/adapter/front/composition_root_execution.js";

/** 実行対象（SubjectSource）が供給する `backtest` のキー。宣言表にも `.ini` にも属さない。 */
const SUBJECT_BACKTEST_KEYS = ["ea_name", "initial_deposit"];

/** 契約が要求する接頭辞。 */
const PREFIXES = ["tester", "inputs", "action", "ui"];

/** 面の器（この 4 つの配下だけが投入フォームである）。
 *  次スライスで 4 面 id の単一ソース化を行う（現状は本検定と CSS ゲートが各自で列挙する）。 */
const PANEL_IDS = [
  "simTesterPanel", "simEaInputsPanel", "simRunActionPanel", "simSchemaFallbackPanel",
];

const CONTROL_TAGS = ["INPUT", "SELECT", "BUTTON"];

const RUN_OPTIONS = {
  ok: true,
  datasets: [{
    dataset: "jp225_m1", data_path: "/d/jp225_m1.csv", symbol: "JP225", period: "P2",
    contract_size: 10.0, digits: 1, point_size: 0.1, leverage: 10.0,
    volume_min: 0.01, volume_max: 100.0, volume_step: 0.01, stops_level: 0,
    settlement_currency: "XYZ",
  }],
  ea_names: ["PRO_fit_Band_EA", "TC24051901"],
};

function routerFetch(schema) {
  const calls = [];
  const fn = async (url, init) => {
    calls.push({ url, init });
    if (url === "/sim/settings-schema") {
      return schema
        ? { ok: true, status: 200, json: async () => schema }
        : { ok: false, status: 404, json: async () => ({ error: "no schema" }) };
    }
    if (url === "/sim/run-options") return { ok: true, status: 200, json: async () => RUN_OPTIONS };
    if (url === "/sim/jobs") return { ok: true, status: 202, json: async () => ({ job_id: "j1", status: "running" }) };
    return { ok: false, status: 404, json: async () => ({ error: "nope" }) };
  };
  fn.calls = calls;
  return fn;
}

const flush = () => new Promise((r) => setTimeout(r, 0));

/** 本番の合成根を組み、投入して、器と本文の両方を返す。 */
async function screen(schema) {
  const doc = fakeDoc();
  const fetchFn = routerFetch(schema);
  const warn = console.warn;
  console.warn = () => {};
  try {
    await mountSimExecutionPanel({ doc, host: doc.body, fetch: fetchFn });
  } finally {
    console.warn = warn;
  }
  findById(doc.body, "runStart")._listeners.click[0]();
  await flush();
  const post = fetchFn.calls.find((c) => c.url === "/sim/jobs");
  return { host: doc.body, body: JSON.parse(post.init.body) };
}

/** 要素が属する面の id を親方向にたどって返す（どの面にも属さなければ null）。 */
function panelOf(node) {
  let cursor = node;
  while (cursor) {
    if (PANEL_IDS.includes(cursor.id)) return cursor.id;
    cursor = cursor.parentNode;
  }
  return null;
}

/** **host 全体**の操作要素をすべて集める（面の外に生えた分も必ず拾う）。 */
function controlsOf(host) {
  return flatten(host)
    .filter((node) => CONTROL_TAGS.includes(node.tagName))
    .map((node) => ({ panel: panelOf(node), node }));
}

/** `data-mt5` を接頭辞ごとに集計する。 */
function declarationsOf(host) {
  const byPrefix = { tester: [], inputs: [], action: [], ui: [] };
  const undeclared = [];
  const malformed = [];
  for (const { panel, node } of controlsOf(host)) {
    const declaration = node.dataset && node.dataset.mt5;
    if (!declaration) {
      undeclared.push(`${panel || "面の外"}: <${node.tagName} id=${node.id || "?"}>`);
      continue;
    }
    const at = String(declaration).indexOf(":");
    const prefix = at < 0 ? "" : String(declaration).slice(0, at);
    const name = at < 0 ? "" : String(declaration).slice(at + 1);
    if (!PREFIXES.includes(prefix) || name === "") {
      malformed.push(`${panel || "面の外"}: ${declaration}`);
      continue;
    }
    byPrefix[prefix].push(name);
  }
  const outsidePanels = controlsOf(host)
    .filter(({ panel }) => panel === null)
    .map(({ node }) => `<${node.tagName} id=${node.id || "?"}>`);
  return { byPrefix, undeclared, malformed, outsidePanels };
}

const CONFIGS = [["settings 構成", () => settingsSchema()], ["縮退構成", () => null]];

// --- 1. 全操作要素が宣言を持つ ----------------------------------------------------

for (const [label, makeSchema] of CONFIGS) {
  test(`${label}: every input / select / button declares its MT5 counterpart`, async () => {
    const { host } = await screen(makeSchema());
    const { undeclared, malformed } = declarationsOf(host);
    assert.deepEqual(undeclared, [], "data-mt5 の無い操作要素があります（対応物を言えない欄）");
    assert.deepEqual(malformed, [], "data-mt5 の書式が契約外です（<接頭辞>:<名前>）");
  });

  test(`${label}: no control is mounted outside the four form panels`, async () => {
    const { host } = await screen(makeSchema());
    assert.deepEqual(declarationsOf(host).outsidePanels, [],
      "面の外に操作要素があります（面の内側だけを走査すると検定から見えなくなります）");
  });

  test(`${label}: the screen actually ships controls (空振り検定でないことの実証)`, async () => {
    const { host } = await screen(makeSchema());
    assert.ok(controlsOf(host).length > 0, "操作要素が 1 つも無い＝上の検定は空振りしています");
  });

  // --- 4. action: はちょうど {start} ----------------------------------------------

  test(`${label}: the only declared action is start`, async () => {
    const { host } = await screen(makeSchema());
    assert.deepEqual(declarationsOf(host).byPrefix.action.sort(), ["start"]);
  });

  // --- 5. ui: は本文に寄与しない ---------------------------------------------------

  test(`${label}: no ui-declared control names a submitted key`, async () => {
    const { host, body } = await screen(makeSchema());
    const bodyKeys = new Set([
      ...Object.keys(body.backtest),
      ...Object.keys((body.settings && body.settings.tester) || {}),
    ]);
    for (const name of declarationsOf(host).byPrefix.ui) {
      assert.equal(bodyKeys.has(name), false, `ui: の ${name} が本文のキーになっています`);
    }
  });

  // --- 3. inputs: は宣言表と本文の双方向一致 ---------------------------------------

  test(`${label}: the inputs declarations match the table and the submitted body both ways`, async () => {
    const { host, body } = await screen(makeSchema());
    const declared = declarationsOf(host).byPrefix.inputs.sort();
    const table = EA_INPUT_FIELDS.map((f) => f.param).sort();
    // 投入本文の実行仕様キーから、profile 由来と実行対象由来を引いた残り＝EA パラメータ
    const fromBody = Object.keys(body.backtest)
      .filter((k) => !PROFILE_KEYS.includes(k) && !SUBJECT_BACKTEST_KEYS.includes(k))
      .sort();
    assert.deepEqual(declared, table, "画面の宣言と宣言表が食い違っています");
    assert.deepEqual(declared, fromBody, "画面の宣言と投入本文が食い違っています");
  });
}

// --- 2. tester: は schema の key_order の部分集合 ----------------------------------

test("settings 構成: every tester declaration names a key the schema declares", async () => {
  const schema = settingsSchema();
  const { host } = await screen(schema);
  const declared = declarationsOf(host).byPrefix.tester;
  assert.ok(declared.length > 0, "tester: の宣言が 1 つもありません");
  for (const name of declared) {
    assert.ok(schema.key_order.includes(name),
      `${name} は schema が配っていないキーです（front が語彙を発明しています）`);
  }
});

test("縮退構成: every tester declaration names a key the schema declares", async () => {
  // 縮退面は schema を持たないが、置いてある欄は MT5 のキーの縮退版である。名前が
  // 宣言側に無ければ、それは front が発明した語彙である。
  const schema = settingsSchema();
  const { host } = await screen(null);
  for (const name of declarationsOf(host).byPrefix.tester) {
    assert.ok(schema.key_order.includes(name),
      `${name} は schema が配っていないキーです（front が語彙を発明しています）`);
  }
});

test("settings 構成: the submitted tester keys are all declared on screen", async () => {
  const { host, body } = await screen(settingsSchema());
  const declared = new Set(declarationsOf(host).byPrefix.tester);
  for (const key of Object.keys(body.settings.tester)) {
    assert.ok(declared.has(key), `${key} を投入しているのに画面に宣言がありません`);
  }
});

// --- 検出器の自己検定（空振りしていないことの実証）----------------------------------

test("the declaration collector detects a control with no data-mt5 (自己検定)", async () => {
  const { host } = await screen(settingsSchema());
  const victim = controlsOf(host)[0].node;
  const saved = victim.dataset.mt5;
  delete victim.dataset.mt5;
  try {
    assert.ok(declarationsOf(host).undeclared.length > 0,
      "宣言を外しても検出されません（この検定は空振りしています）");
  } finally {
    victim.dataset.mt5 = saved;
  }
  assert.deepEqual(declarationsOf(host).undeclared, [], "復元できていません");
});

test("the declaration collector sees a control mounted outside every panel (自己検定)", async () => {
  const { host } = await screen(settingsSchema());
  const stray = { tagName: "BUTTON", id: "strayProbe", dataset: {}, children: [], parentNode: null };
  host.appendChild(stray);
  try {
    const found = declarationsOf(host);
    assert.deepEqual(found.outsidePanels, ["<BUTTON id=strayProbe>"],
      "面の外の操作要素を検出できません（この検定は空振りしています）");
    assert.ok(found.undeclared.length > 0, "面の外の宣言漏れも同時に検出できていません");
  } finally {
    host.removeChild(stray);
  }
  assert.deepEqual(declarationsOf(host).outsidePanels, [], "復元できていません");
});

test("the declaration collector rejects a malformed declaration (自己検定)", async () => {
  const { host } = await screen(settingsSchema());
  const victim = controlsOf(host)[0].node;
  const saved = victim.dataset.mt5;
  victim.dataset.mt5 = "bogus";
  try {
    assert.ok(declarationsOf(host).malformed.length > 0, "書式違反を検出できません");
  } finally {
    victim.dataset.mt5 = saved;
  }
});
