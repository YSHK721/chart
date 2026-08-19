// 銘柄による実行対象データセットの決定（Phase 9 S4・§19.2「吸収」）。
//
// データセット選択という sim 独自の概念を画面から落とし、MT5 が持つ概念（Symbol）だけを残す。
// 実行対象データセットは Symbol から**決定的に**引く（resolveProfile＝一致の先頭）。
//
// なぜ「解決できたときだけ」書き戻すか: 利用者が打った銘柄を UI が勝手に別の値へ戻すと、
// 「入れたはずの値が消える」画面になる（ビュー自動介入の禁止）。解決できない銘柄は
// そのまま残し、直前の profile を保ったうえで**警告を点ける**——不一致のまま投入すれば
// 実行時に失敗することは、投入前に画面へ出ていなければならない。
//
// 固定する不変条件:
//   1. 銘柄は候補付き select になる（候補は run-options の datasets 由来）。
//   2. 候補 0 件なら自由入力へ縮退する（候補が無いことを理由に投入不能にしない）。
//   3. 合成根はデータセット選択を出さない。
//   4. 解決できる銘柄を選ぶと profile が切り替わる。
//   5. 解決できない銘柄を選んでも欄は書き戻らず、警告が点く。
import { test } from "node:test";
import assert from "node:assert/strict";

import { fakeDoc, findById } from "./_fakes.js";
import { settingsSchema } from "./_settings_schema_fixture.js";
import { createSimTesterSettingsPanelView } from "../js/adapter/front/sim_tester_settings_panel_view.js";
import { createSimSchemaFallbackView } from "../js/adapter/front/sim_schema_fallback_view.js";
import { mountSimExecutionPanel } from "../js/adapter/front/composition_root_execution.js";

const flush = () => new Promise((r) => setTimeout(r, 0));
const fire = (el, ev = "change") => (el._listeners[ev] || []).forEach((f) => f());

/** 銘柄の異なる 2 つのデータセット（`period` は schema fixture のトークンに揃える）。 */
const DATASETS = [
  {
    dataset: "ds_a", data_path: "/d/a.csv", symbol: "AAA", period: "P2",
    contract_size: 10.0, digits: 1, point_size: 0.1, leverage: 10.0,
    volume_min: 0.01, volume_max: 100.0, volume_step: 0.01, stops_level: 0,
    settlement_currency: "XYZ",
  },
  {
    dataset: "ds_b", data_path: "/d/b.csv", symbol: "BBB", period: "P2",
    contract_size: 1.0, digits: 3, point_size: 0.001, leverage: 20.0,
    volume_min: 0.1, volume_max: 10.0, volume_step: 0.1, stops_level: 5,
    settlement_currency: "XYZ",
  },
];
const EA_NAMES = ["PRO_fit_Band_EA", "TC24051901"];

function routerFetch({ schema, datasets = DATASETS } = {}) {
  const calls = [];
  const fn = async (url, init) => {
    calls.push({ url, init });
    if (url === "/sim/settings-schema") {
      return schema
        ? { ok: true, status: 200, json: async () => schema }
        : { ok: false, status: 404, json: async () => ({ error: "no schema" }) };
    }
    if (url === "/sim/run-options") {
      return { ok: true, status: 200, json: async () => ({ ok: true, datasets, ea_names: EA_NAMES }) };
    }
    if (url === "/sim/jobs") return { ok: true, status: 202, json: async () => ({ job_id: "j1", status: "running" }) };
    return { ok: false, status: 404, json: async () => ({ error: "nope" }) };
  };
  fn.calls = calls;
  return fn;
}

async function mountRoot(opts = {}) {
  const doc = fakeDoc();
  const fetchFn = routerFetch(opts);
  const warn = console.warn;
  console.warn = () => {};
  try {
    await mountSimExecutionPanel({ doc, host: doc.body, fetch: fetchFn });
  } finally {
    console.warn = warn;
  }
  return { doc, host: doc.body, fetchFn };
}

/** 投入して本文を読む。 */
async function submitBody(host, fetchFn) {
  findById(host, "runStart")._listeners.click[0]();
  await flush();
  return JSON.parse(fetchFn.calls.find((c) => c.url === "/sim/jobs").init.body);
}

// --- 1/2. 銘柄は候補付き select・候補 0 件で自由入力へ縮退 ------------------------

test("the tester panel renders Symbol as a select over the injected candidates", () => {
  const doc = fakeDoc();
  const view = createSimTesterSettingsPanelView({ doc });
  view.mount(doc.body);
  view.setSymbolCandidates(["AAA", "BBB"]);
  view.setSchema(settingsSchema());
  const node = findById(doc.body, "testerSymbol");
  assert.equal(node.tagName, "SELECT");
  assert.deepEqual((node.children || []).map((o) => o.value), ["AAA", "BBB"]);
  assert.equal(view.selectedSymbol(), "AAA");
});

test("the tester panel degrades Symbol to free input when no candidate exists", () => {
  const doc = fakeDoc();
  const view = createSimTesterSettingsPanelView({ doc });
  view.mount(doc.body);
  view.setSymbolCandidates([]);
  view.setSchema(settingsSchema());
  assert.equal(findById(doc.body, "testerSymbol").tagName, "INPUT");
});

test("the fallback surface renders Symbol as a select over the injected candidates", () => {
  const doc = fakeDoc();
  const view = createSimSchemaFallbackView({ doc });
  view.setSymbolCandidates(["AAA", "BBB"]);
  view.mount(doc.body);
  const node = findById(doc.body, "execSymbol");
  assert.equal(node.tagName, "SELECT");
  assert.deepEqual((node.children || []).map((o) => o.value), ["AAA", "BBB"]);
  assert.equal(view.selectedSymbol(), "AAA");
});

test("the fallback surface degrades Symbol to free input when no candidate exists", () => {
  const doc = fakeDoc();
  const view = createSimSchemaFallbackView({ doc });
  view.mount(doc.body);
  assert.equal(findById(doc.body, "execSymbol").tagName, "INPUT");
  assert.equal(view.selectedSymbol(), "");
});

test("the fallback surface reports a symbol change to its subscriber", () => {
  const doc = fakeDoc();
  const view = createSimSchemaFallbackView({ doc });
  view.setSymbolCandidates(["AAA", "BBB"]);
  view.mount(doc.body);
  const seen = [];
  view.onSymbolChange((s) => seen.push(s));
  const node = findById(doc.body, "execSymbol");
  node.value = "BBB";
  fire(node);
  assert.deepEqual(seen, ["BBB"]);
});

// --- 3. データセット選択は画面から消える -----------------------------------------

test("the composition root ships no dataset selector (sim 独自の概念を出さない)", async () => {
  const { host } = await mountRoot({ schema: settingsSchema() });
  assert.equal(findById(host, "execDataset"), null, "データセット選択が残っています");
});

test("the degraded configuration ships no dataset selector either", async () => {
  const { host } = await mountRoot();
  assert.equal(findById(host, "execDataset"), null, "データセット選択が残っています");
});

// --- 4. 銘柄から実行対象データセットが決まる -------------------------------------

test("the resolved profile follows the chosen Symbol (settings 構成)", async () => {
  const { host, fetchFn } = await mountRoot({ schema: settingsSchema() });
  const symbol = findById(host, "testerSymbol");
  assert.equal(symbol.value, "AAA");
  symbol.value = "BBB";
  fire(symbol);
  const body = await submitBody(host, fetchFn);
  assert.equal(body.backtest.symbol, "BBB");
  assert.equal(body.backtest.data_path, "/d/b.csv");
  assert.equal(body.backtest.contract_size, 1.0);
  assert.equal(body.backtest.stops_level, 5);
});

test("the resolved profile follows the chosen Symbol (縮退構成)", async () => {
  const { host, fetchFn } = await mountRoot();
  const symbol = findById(host, "execSymbol");
  symbol.value = "BBB";
  fire(symbol);
  const body = await submitBody(host, fetchFn);
  assert.equal(body.backtest.symbol, "BBB");
  assert.equal(body.backtest.data_path, "/d/b.csv");
});

// --- 5. 解決できない銘柄は書き戻さず警告を点ける ---------------------------------

test("an unresolvable Symbol is left in the field and lights the mismatch warning", async () => {
  const { host } = await mountRoot({ schema: settingsSchema() });
  const symbol = findById(host, "testerSymbol");
  symbol.value = "NOPE";           // 候補外（実 UI では自由入力の縮退時に起こる）
  fire(symbol);
  // 欄は書き戻らない（利用者の入力を UI が勝手に戻さない）
  assert.equal(symbol.value, "NOPE", "利用者の入力が書き戻されました");
  // 警告が点く（不一致のまま投入すれば実行時に失敗することを投入前に出す）
  assert.ok(findById(host, "simTesterWarn").textContent.includes("NOPE"),
    "不一致なのに警告が出ていません");
});

test("an unresolvable Symbol keeps the previously resolved profile (直前の profile を保つ)", async () => {
  const { host, fetchFn } = await mountRoot({ schema: settingsSchema() });
  const symbol = findById(host, "testerSymbol");
  symbol.value = "BBB";
  fire(symbol);
  symbol.value = "NOPE";
  fire(symbol);
  const body = await submitBody(host, fetchFn);
  // 直前に解決できた BBB の profile がそのまま使われる（既定へ勝手に戻さない）
  assert.equal(body.backtest.data_path, "/d/b.csv");
  // 投入本文の Symbol は利用者が打った値のまま（黙って別の銘柄で回さない）
  assert.equal(body.settings.tester.Symbol, "NOPE");
});
