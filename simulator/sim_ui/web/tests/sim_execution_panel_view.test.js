// sim_execution_panel_view（実行指示パネル・Phase 6 F-8 / Phase 9 S1・S3）の単体テスト（fake DOM）。
//
// この面に残っているのは「実行対象データセットの選択」と「投入」だけである。EA パラメータは
// M2（sim_ea_inputs_panel_view）、実行対象（EA・口座・設定ブロック）は SubjectSource（M1 か
// M4）、本文の組み立ては M5（sim_submission_builder）が所有する。
//
// 固定する不変条件:
//   1. データセット選択と投入ボタンを生成する。
//   2. buildSubmission は SubjectSource と M2 と選択 profile から本文を組む（分岐を持たない）。
//   3. SubjectSource が settings を出せば本文へ載り、null なら載らない——この面は
//      供給元がどちらの面かを見分けない。
//   4. 選択したデータセットの profile を SubjectSource へ渡す（既定値の供給元は 1 つ）。
import { test } from "node:test";
import assert from "node:assert/strict";

import { fakeDoc, findById } from "./_fakes.js";
import {
  EA_INPUT_FIELDS, createSimEaInputsPanelView,
} from "../js/adapter/front/sim_ea_inputs_panel_view.js";
import { createSimExecutionPanelView } from "../js/adapter/front/sim_execution_panel_view.js";

/** SubjectSource（M1 / M4）のダブル。契約は subject_source_port.test.js が実体で固定する。 */
function fakeSubjectSource(overrides) {
  const state = {
    derived: { ea_name: "AAA", initial_deposit: 777 },
    settings: null,
    profiles: [],
    symbol: "",
    ...overrides,
  };
  return {
    state,
    derivedBacktest() { return state.derived; },
    buildSettings() { return state.settings; },
    selectedSymbol() { return state.symbol; },
    setRunProfile(p) { state.profiles.push(p); },
    onSymbolChange(cb) { state.symbolCb = cb; },
  };
}

function mounted(source = fakeSubjectSource()) {
  const doc = fakeDoc();
  // EA パラメータ面（M2）は本番と同じ実体を注入する（値の供給元をダブルに差し替えると
  // 「宣言表 → 本文」の結線が検定から抜ける）。
  const inputs = createSimEaInputsPanelView({ doc });
  inputs.mount(doc.body);
  const view = createSimExecutionPanelView({ doc, inputs });
  view.mount(doc.body);
  view.setSubjectSource(source);
  return { doc, host: doc.body, view, inputs, source };
}

const setVal = (el, v) => { el.value = v; };
/** EA パラメータ面の欄（所在の単一ソースは宣言表＝この検定へ id を写さない）。 */
const eaInputId = (param) => EA_INPUT_FIELDS.find((f) => f.param === param).id;

const _PROFILE = Object.freeze({
  dataset: "jp225_m1", data_path: "/d/jp225_m1.csv", symbol: "JP225", period: "M1",
  contract_size: 10.0, digits: 1, point_size: 0.1, leverage: 10.0,
  volume_min: 0.01, volume_max: 100.0, volume_step: 0.01, stops_level: 0,
});
const PROFILE_KEYS = [
  "data_path", "symbol", "period", "contract_size", "digits", "point_size",
  "leverage", "volume_min", "volume_max", "volume_step", "stops_level",
];
const FORM_KEYS = [
  "ea_name", "stop_loss_points", "take_profit_points",
  "ma_period", "ma_method", "initial_deposit", "lot_size",
];

// --- 1. 生成 ------------------------------------------------------------------

test("mount builds the dataset selector and a submit button", () => {
  const { host } = mounted();
  assert.ok(findById(host, "execDataset"), "#execDataset が無い");
  assert.ok(findById(host, "execSubmit"), "#execSubmit が無い");
});

test("mount owns no ea / deposit field of its own (供給元は SubjectSource 1 つ)", () => {
  const { host } = mounted();
  assert.equal(findById(host, "execEaName"), null, "実行対象の欄をこの面が持っています");
  assert.equal(findById(host, "execDeposit"), null, "初期資金の欄をこの面が持っています");
});

// --- 2. buildSubmission -----------------------------------------------------------

test("buildSubmission returns the full 18-key backtest body", () => {
  const { host, view } = mounted(fakeSubjectSource({
    derived: { ea_name: "TC24051901", initial_deposit: 10000 },
  }));
  view.setRunOptions([_PROFILE]);
  setVal(findById(host, eaInputId("stop_loss_points")), "100");
  setVal(findById(host, eaInputId("take_profit_points")), "200");

  const body = view.buildSubmission();
  const bt = body.backtest;
  assert.deepEqual(Object.keys(bt).sort(), [...PROFILE_KEYS, ...FORM_KEYS].sort());
  for (const k of PROFILE_KEYS) assert.strictEqual(bt[k], _PROFILE[k], k);
  assert.equal(bt.ea_name, "TC24051901");
  assert.equal(bt.initial_deposit, 10000);
  assert.equal(bt.stop_loss_points, 100);
  assert.equal(bt.take_profit_points, 200);
  // 投入本文は実行仕様 1 ブロックだけ（S1 で第 2 ブロックの UI 出口を撤去した）
  assert.deepEqual(Object.keys(body), ["backtest"]);
});

test("selecting another dataset switches the profile-derived keys (front リテラルでない証拠)", () => {
  const { host, view } = mounted();
  const p2 = { ..._PROFILE, dataset: "other", symbol: "OTHER", contract_size: 1.0, point_size: 0.001 };
  view.setRunOptions([_PROFILE, p2]);
  const ds = findById(host, "execDataset");
  assert.deepEqual((ds.children || []).map((o) => o.value), ["jp225_m1", "other"]);
  setVal(ds, "other");
  const bt = view.buildSubmission().backtest;
  assert.equal(bt.symbol, "OTHER");
  assert.equal(bt.contract_size, 1.0);
  assert.equal(bt.point_size, 0.001);
});

test("the EA inputs panel feeds its declared params into the backtest", () => {
  const { host, view } = mounted();
  view.setRunOptions([_PROFILE]);
  setVal(findById(host, eaInputId("ma_period")), "20");
  setVal(findById(host, eaInputId("ma_method")), "ema");
  setVal(findById(host, eaInputId("lot_size")), "0.1");
  const bt = view.buildSubmission().backtest;
  assert.strictEqual(bt.ma_period, 20);
  assert.strictEqual(bt.ma_method, "ema");
  assert.strictEqual(bt.lot_size, 0.1);
});

test("no strategy block is ever built (S1 で UI 出口を撤去した)", () => {
  const { view } = mounted();
  assert.equal("strategy" in view.buildSubmission(), false);
});

// --- 3. settings は SubjectSource が決める（この面は見分けない）---------------------

test("a SubjectSource without settings yields a body with no settings block", () => {
  const { view } = mounted(fakeSubjectSource({ settings: null }));
  view.setRunOptions([_PROFILE]);
  assert.equal("settings" in view.buildSubmission(), false);
});

test("a SubjectSource with settings puts them on the body verbatim", () => {
  const settings = { tester: { Expert: "AAA.zzz", Symbol: "SYM" }, inputs: [] };
  const { view } = mounted(fakeSubjectSource({ settings }));
  view.setRunOptions([_PROFILE]);
  const body = view.buildSubmission();
  assert.deepEqual(body.settings, settings);
  assert.deepEqual(Object.keys(body.backtest).sort(), [...PROFILE_KEYS, ...FORM_KEYS].sort());
});

// --- 4. 既定値の供給元は 1 つ -----------------------------------------------------

test("the selected dataset profile is pushed to the SubjectSource", () => {
  const { host, view, source } = mounted();
  const p2 = { ..._PROFILE, dataset: "other", symbol: "OTHER" };
  view.setRunOptions([_PROFILE, p2]);
  assert.deepEqual(source.state.profiles.at(-1), _PROFILE);
  const ds = findById(host, "execDataset");
  setVal(ds, "other");
  ds._listeners.change[0]();
  assert.deepEqual(source.state.profiles.at(-1), p2);
});

// --- 5. onSubmit ------------------------------------------------------------------

test("clicking submit invokes onSubmit with the built body", () => {
  const { host, view } = mounted(fakeSubjectSource({
    derived: { ea_name: "TC24051901", initial_deposit: 10000 },
  }));
  const seen = [];
  view.onSubmit((b) => seen.push(b));
  setVal(findById(host, eaInputId("stop_loss_points")), "50");
  findById(host, "execSubmit")._listeners.click[0]();
  assert.equal(seen.length, 1);
  assert.equal(seen[0].backtest.ea_name, "TC24051901");
  assert.equal(seen[0].backtest.stop_loss_points, 50);
});
