// SubjectSource Port の対称性（Phase 9 S3）。
//
// 「実行対象（どの EA を・いくらの口座で・どの銘柄で回すか）」の供給元は 2 つある——
// MT5 Tester Settings 面（M1）と、schema を取れないときの縮退面（M4）である。合成根は
// **どちらか 1 つだけ**を実行対象の供給元として使う。したがって両者は同じ契約でなければ
// ならない。片方にしか無いメンバがあると、合成根が「どちらが来ているか」を見て分岐し始め、
// 三項分岐が戻ってくる（S2 まではまさにそうなっていた）。
//
// 固定する不変条件:
//   1. 両者が Port の 5 メンバをすべて備える。
//   2. buildSettings() の型が契約どおり（設定ブロック or null）で、null は本文へ載らない。
//   3. selectedSymbol() は常に文字列（未確定でも null / undefined を返さない）。
import { test } from "node:test";
import assert from "node:assert/strict";

import { fakeDoc } from "./_fakes.js";
import { runProfile, settingsSchema } from "./_settings_schema_fixture.js";
import { createSimTesterSettingsPanelView } from "../js/adapter/front/sim_tester_settings_panel_view.js";
import { createSimSchemaFallbackView } from "../js/adapter/front/sim_schema_fallback_view.js";

/** 実行対象の供給元が満たす契約。 */
const PORT = ["derivedBacktest", "buildSettings", "selectedSymbol", "setRunProfile", "onSymbolChange"];

/** M1: schema を注入した Tester Settings 面。 */
function testerSource() {
  const doc = fakeDoc();
  const view = createSimTesterSettingsPanelView({ doc });
  view.mount(doc.body);
  view.setSchema(settingsSchema());
  view.setRunProfile(runProfile());
  return view;
}

/** M4: 縮退面。 */
function fallbackSource() {
  const doc = fakeDoc();
  const view = createSimSchemaFallbackView({ doc });
  view.mount(doc.body);
  view.setEaCandidates(["PRO_fit_Band_EA"]);
  view.setRunProfile(runProfile());
  return view;
}

const SOURCES = [["tester settings panel", testerSource], ["schema fallback", fallbackSource]];

// --- 1. Port の 5 メンバ ----------------------------------------------------------

for (const [name, make] of SOURCES) {
  test(`${name} implements every SubjectSource port member`, () => {
    const view = make();
    for (const member of PORT) {
      assert.equal(typeof view[member], "function", `${name}: Port の ${member} が無い`);
    }
  });

  test(`${name} derives an ea_name and a numeric initial_deposit`, () => {
    const derived = make().derivedBacktest();
    assert.equal(typeof derived.ea_name, "string", `${name}: ea_name が文字列でない`);
    assert.ok(Number.isFinite(derived.initial_deposit), `${name}: initial_deposit が数値でない`);
  });

  test(`${name} reports selectedSymbol as a string`, () => {
    assert.equal(typeof make().selectedSymbol(), "string", `${name}: selectedSymbol が文字列でない`);
  });

  test(`${name} builds settings as an object or null (契約の型)`, () => {
    const settings = make().buildSettings();
    assert.ok(settings === null || (settings && typeof settings === "object"),
      `${name}: buildSettings が設定ブロックでも null でもない`);
  });
}

// --- 2. 面ごとの settings の有無（縮退は組まない）---------------------------------

test("the tester settings panel supplies a settings block", () => {
  const settings = testerSource().buildSettings();
  assert.ok(settings, "schema があるのに設定ブロックが出ていない");
  assert.ok(settings.tester && Object.keys(settings.tester).length > 0);
});

test("the schema fallback supplies no settings block", () => {
  assert.strictEqual(fallbackSource().buildSettings(), null);
});

test("the tester settings panel supplies no settings block before a schema arrives", () => {
  // schema 未注入の Tester 面を実行対象の供給元にしても、空の設定ブロックを組まない
  // （組むと候補 0 の Expert から投入不能な本文が出来る＝Phase 8 で実測した壊れ方）。
  const doc = fakeDoc();
  const view = createSimTesterSettingsPanelView({ doc });
  view.mount(doc.body);
  assert.strictEqual(view.buildSettings(), null);
});
