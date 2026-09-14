// sim_schema_fallback_view（縮退面・Phase 9 S3 M4）の単体テスト（fake DOM）。
//
// この面は「Tester Settings の schema を取れなかった構成」でだけ立つ。MT5 の設定を組めない
// ので、実行に最低限要る 3 つ（銘柄・実行対象 EA・初期資金）だけを受け取り、`settings` ブロックは
// **組まない**（null）。旧フォーム投入と byte 等価な本文になる。
//
// id を `execEaName` / `execDeposit` のまま保つのは意図的である: この 2 つは Phase 8 以前から
// 縮退経路の入力欄であり、python 側の実 UI 検定（test_settings_ui_end_to_end.py）が
// 「縮退構成でこの欄が権威である」ことをこの id で観測している。
//
// 固定する不変条件:
//   1. 縮退面は `execEaName` / `execDeposit` を持つ（縮退経路の観測点を保つ）。
//   2. buildSettings() は常に null（schema が無いのに設定ブロックを組まない）。
//   3. derivedBacktest() は画面の値をそのまま実行対象として返す。
//   4. selectedSymbol() は銘柄欄の値（候補は注入・この面は銘柄を発明しない）。
//   5. SubjectSource Port の 5 つのメンバをすべて備える（M1 と同型）。
import { test } from "node:test";
import assert from "node:assert/strict";

import { fakeDoc, findById } from "./_fakes.js";
import { createSimSchemaFallbackView } from "../js/adapter/front/sim_schema_fallback_view.js";

/** SubjectSource Port（M1 Tester Settings 面と M4 縮退面が共に実装する契約）。 */
const SUBJECT_SOURCE_PORT = [
  "derivedBacktest", "buildSettings", "selectedSymbol", "setRunProfile", "onSymbolChange",
];

const PROFILE = Object.freeze({
  dataset: "ds1", data_path: "/d/ds1.csv", symbol: "SYM", period: "P2",
  contract_size: 10.0, digits: 1, point_size: 0.1, leverage: 10.0,
  volume_min: 0.01, volume_max: 100.0, volume_step: 0.01, stops_level: 0,
});

function mounted() {
  const doc = fakeDoc();
  const view = createSimSchemaFallbackView({ doc });
  view.mount(doc.body);
  return { doc, host: doc.body, view };
}

// --- 1. 縮退経路の観測点（id）を保つ ---------------------------------------------

test("the degraded surface keeps the legacy ea / deposit fields", () => {
  const { host } = mounted();
  assert.ok(findById(host, "execEaName"), "#execEaName が無い（縮退経路の観測点が消えている）");
  assert.ok(findById(host, "execDeposit"), "#execDeposit が無い（縮退経路の観測点が消えている）");
});

test("the ea field is populated from the injected candidates (front リテラル 0)", () => {
  const { host, view } = mounted();
  view.setEaCandidates(["PRO_fit_Band_EA", "TC24051901"]);
  const sel = findById(host, "execEaName");
  assert.deepEqual((sel.children || []).map((o) => o.value), ["PRO_fit_Band_EA", "TC24051901"]);
  assert.equal(sel.value, "PRO_fit_Band_EA");
});

// --- 2. settings ブロックを組まない ----------------------------------------------

test("buildSettings is always null (schema が無いのに設定ブロックを組まない)", () => {
  const { view } = mounted();
  assert.strictEqual(view.buildSettings(), null);
  view.setEaCandidates(["X"]);
  view.setRunProfile(PROFILE);
  assert.strictEqual(view.buildSettings(), null);
});

// --- 3. derivedBacktest は画面の値をそのまま返す ---------------------------------

test("derivedBacktest reports the ea name and the deposit typed as a number", () => {
  const { host, view } = mounted();
  view.setEaCandidates(["PRO_fit_Band_EA", "TC24051901"]);
  findById(host, "execEaName").value = "TC24051901";
  findById(host, "execDeposit").value = "250000";
  assert.deepEqual(view.derivedBacktest(), {
    ea_name: "TC24051901",
    initial_deposit: 250000,
  });
});

test("the deposit field carries an initial value (空欄から始めない)", () => {
  const { host, view } = mounted();
  assert.notEqual(findById(host, "execDeposit").value, "");
  assert.ok(Number.isFinite(view.derivedBacktest().initial_deposit),
    "初期表示のまま投入すると initial_deposit が数値になりません");
});

// --- 4. selectedSymbol は注入 profile 由来 ---------------------------------------

test("selectedSymbol is empty before any candidate arrives (銘柄を発明しない)", () => {
  const { view } = mounted();
  assert.strictEqual(view.selectedSymbol(), "");
});

test("selectedSymbol comes from the symbol field (候補は注入)", () => {
  const doc = fakeDoc();
  const view = createSimSchemaFallbackView({ doc });
  view.setSymbolCandidates(["SYM", "OTHER"]);
  view.mount(doc.body);
  assert.strictEqual(view.selectedSymbol(), "SYM");
  findById(doc.body, "execSymbol").value = "OTHER";
  assert.strictEqual(view.selectedSymbol(), "OTHER");
});

test("accepting a run profile does not overwrite the symbol field (書き戻さない)", () => {
  const doc = fakeDoc();
  const view = createSimSchemaFallbackView({ doc });
  view.setSymbolCandidates(["SYM", "OTHER"]);
  view.mount(doc.body);
  findById(doc.body, "execSymbol").value = "OTHER";
  view.setRunProfile(PROFILE);
  assert.strictEqual(view.selectedSymbol(), "OTHER");
});

// --- 5. SubjectSource Port を備える（M1 と同型）----------------------------------

test("the degraded surface implements the whole SubjectSource port", () => {
  const { view } = mounted();
  for (const member of SUBJECT_SOURCE_PORT) {
    assert.equal(typeof view[member], "function", `Port の ${member} が無い`);
  }
});

test("onSymbolChange accepts a subscriber without throwing (Port の全域性)", () => {
  const { view } = mounted();
  assert.doesNotThrow(() => view.onSymbolChange(() => {}));
});
