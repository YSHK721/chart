// sim_execution_panel_view（実行指示パネル・Phase 6 F-8 / Phase 9 S1）の単体テスト（fake DOM）。
//
// 固定する不変条件:
//   1. ea_name / SL(stop_loss_points) / TP(take_profit_points) / 投入ボタンを生成する。
//   2. buildSubmission は {backtest} を返す（実行仕様 18 キー完全）。
//   3. Tester パネルを結線すると settings が載り、重複欄（EA・初期資金）が器から消える。
//
// Phase 9 S1（§19.2）: 買い/売り条件ビルダーと建玉変更の入力欄は UI 出口として撤去した。
//   撤去語彙が残っていないことは tests/removed_ui_vocabulary_gate.test.js が機械強制する。
import { test } from "node:test";
import assert from "node:assert/strict";

import { fakeDoc, findById } from "./_fakes.js";
import {
  EA_INPUT_FIELDS, createSimEaInputsPanelView,
} from "../js/adapter/front/sim_ea_inputs_panel_view.js";
import { createSimExecutionPanelView } from "../js/adapter/front/sim_execution_panel_view.js";

function mounted() {
  const doc = fakeDoc();
  // EA パラメータ面（M2）は本番と同じ実体を注入する（値の供給元をダブルに差し替えると
  // 「宣言表 → 本文」の結線が検定から抜ける）。
  const inputs = createSimEaInputsPanelView({ doc });
  inputs.mount(doc.body);
  const view = createSimExecutionPanelView({ doc, inputs });
  view.mount(doc.body);
  return { doc, host: doc.body, view, inputs };
}

// --- 1. 生成 ------------------------------------------------------------------

test("mount builds the ea / deposit fields and a submit button", () => {
  const { host } = mounted();
  assert.ok(findById(host, "execEaName"), "#execEaName が無い");
  assert.ok(findById(host, "execDeposit"), "#execDeposit が無い");
  assert.ok(findById(host, "execSubmit"), "#execSubmit が無い");
});

// --- 5. buildSubmission -------------------------------------------------------

function setVal(el, v) { el.value = v; }

// Phase 6 拡張: フォームが供給する完全 18 キー body（profile 由来 11 ＋ フォーム 7）。
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
/** EA パラメータ面の欄（値の所在は宣言表が持つ＝id をこの検定へ写さない）。 */
const eaInputId = (param) => EA_INPUT_FIELDS.find((f) => f.param === param).id;

test("buildSubmission returns the full 18-key backtest body", () => {
  const { host, view } = mounted();
  view.setRunOptions([_PROFILE]);
  setVal(findById(host, "execEaName"), "TC24051901");
  setVal(findById(host, eaInputId("stop_loss_points")), "100");
  setVal(findById(host, eaInputId("take_profit_points")), "200");

  const body = view.buildSubmission();
  const bt = body.backtest;
  // 18 キー完全（profile 11 ＋ フォーム 7）
  assert.deepEqual(Object.keys(bt).sort(), [...PROFILE_KEYS, ...FORM_KEYS].sort());
  // profile 由来の 11 キーは注入 profile と一致（front リテラル 0）
  for (const k of PROFILE_KEYS) assert.strictEqual(bt[k], _PROFILE[k], k);
  assert.equal(bt.ea_name, "TC24051901");
  assert.equal(bt.stop_loss_points, 100);
  assert.equal(bt.take_profit_points, 200);
  // 投入本文は実行仕様 1 ブロックだけ（S1 で第 2 ブロックの UI 出口を撤去した）
  assert.deepEqual(Object.keys(body), ["backtest"]);
});

test("setRunOptions populates the dataset selector and profile drives backtest keys", () => {
  const { host, view } = mounted();
  const p2 = { ..._PROFILE, dataset: "other", symbol: "OTHER", contract_size: 1.0, point_size: 0.001 };
  view.setRunOptions([_PROFILE, p2]);
  const ds = findById(host, "execDataset");
  assert.ok(ds, "#execDataset が無い");
  assert.deepEqual((ds.children || []).map((o) => o.value), ["jp225_m1", "other"]);
  // 別データセットを選ぶと profile 由来キーが切り替わる（front リテラルでない証拠）
  setVal(ds, "other");
  const bt = view.buildSubmission().backtest;
  assert.equal(bt.symbol, "OTHER");
  assert.equal(bt.contract_size, 1.0);
  assert.equal(bt.point_size, 0.001);
});

test("the EA inputs panel and the deposit field feed the backtest keys", () => {
  const { host, view } = mounted();
  view.setRunOptions([_PROFILE]);
  setVal(findById(host, eaInputId("ma_period")), "20");
  setVal(findById(host, eaInputId("ma_method")), "ema");
  setVal(findById(host, "execDeposit"), "10000");
  setVal(findById(host, eaInputId("lot_size")), "0.1");
  const bt = view.buildSubmission().backtest;
  assert.strictEqual(bt.ma_period, 20);
  assert.strictEqual(bt.ma_method, "ema");
  assert.strictEqual(bt.initial_deposit, 10000);
  assert.strictEqual(bt.lot_size, 0.1);
});

// --- 5d. Tester Settings パネルとの一本化（Phase 8 スライス 5・T-4）----------------
// 固定する不変条件:
//   - パネル未接続（＝schema を取れない構成）では本文に `settings` が載らない。旧フォーム
//     投入（Phase 6/7 の 2〜3 キー本文）と byte 等価のまま併存する。
//   - 接続すると本文へ `settings`（生トークン Mapping）が載り、`backtest` の
//     `ea_name` / `initial_deposit` は **settings 側の値から導出**される。
//   - 同一概念の入力欄を 2 つ持たない（EA・初期資金の重複欄は器から消える）。
//   - profile 由来 11 キー・SL/TP/MA/ロットは従来どおり。

/** Tester パネルの契約（buildSettings / derivedBacktest / setRunProfile / onExpertChange）のダブル。 */
function fakeTesterPanel(overrides) {
  const state = {
    tester: { Expert: "AAA.zzz", Symbol: "SYM" },
    derived: { ea_name: "AAA", initial_deposit: 777 },
    profiles: [],
    expertCb: null,
    ...overrides,
  };
  return {
    state,
    buildSettings() { return { tester: state.tester, inputs: [] }; },
    derivedBacktest() { return state.derived; },
    setRunProfile(p) { state.profiles.push(p); },
    onExpertChange(cb) { state.expertCb = cb; },
  };
}

test("without a tester panel the body carries no settings block (旧フォーム投入の併存)", () => {
  const { host, view } = mounted();
  view.setRunOptions([_PROFILE]);
  setVal(findById(host, "execEaName"), "TC24051901");
  const body = view.buildSubmission();
  assert.equal("settings" in body, false);
  assert.equal(body.backtest.ea_name, "TC24051901");
});

test("attaching a tester panel puts settings on the body and derives the backtest keys (T-4)", () => {
  const { host, view } = mounted();
  view.setRunOptions([_PROFILE]);
  const panel = fakeTesterPanel();
  view.setTesterPanel(panel);
  setVal(findById(host, eaInputId("stop_loss_points")), "100");
  const body = view.buildSubmission();
  assert.deepEqual(body.settings, { tester: panel.state.tester, inputs: [] });
  assert.equal(body.backtest.ea_name, panel.state.derived.ea_name);
  assert.equal(body.backtest.initial_deposit, panel.state.derived.initial_deposit);
  // 18 キー完全のまま（profile 由来 11 ＋ フォーム 7）
  assert.deepEqual(Object.keys(body.backtest).sort(), [...PROFILE_KEYS, ...FORM_KEYS].sort());
  for (const k of PROFILE_KEYS) assert.strictEqual(body.backtest[k], _PROFILE[k], k);
  assert.strictEqual(body.backtest.stop_loss_points, 100);
});

test("attaching a tester panel removes the duplicated ea / deposit fields (1 概念 1 欄)", () => {
  const { host, view } = mounted();
  view.setRunOptions([_PROFILE]);
  view.setTesterPanel(fakeTesterPanel());
  assert.equal(findById(host, "execEaName"), null, "指標セット欄が重複したまま残っています");
  assert.equal(findById(host, "execDeposit"), null, "初期資金欄が重複したまま残っています");
  // 一本化の対象外（EA パラメータ面・投入ボタン）は残る
  for (const id of [...EA_INPUT_FIELDS.map((f) => f.id), "execSubmit"]) {
    assert.ok(findById(host, id), `${id} が消えています`);
  }
});

test("the selected dataset profile is pushed to the tester panel (既定値の供給元)", () => {
  const { host, view } = mounted();
  const p2 = { ..._PROFILE, dataset: "other", symbol: "OTHER" };
  view.setRunOptions([_PROFILE, p2]);
  const panel = fakeTesterPanel();
  view.setTesterPanel(panel);
  assert.deepEqual(panel.state.profiles.at(-1), _PROFILE);
  const ds = findById(host, "execDataset");
  setVal(ds, "other");
  ds._listeners.change[0]();
  assert.deepEqual(panel.state.profiles.at(-1), p2);
});

// --- 6. onSubmit --------------------------------------------------------------

test("clicking submit invokes onSubmit with the built body", () => {
  const { host, view } = mounted();
  const seen = [];
  view.onSubmit((b) => seen.push(b));
  setVal(findById(host, "execEaName"), "TC24051901");
  setVal(findById(host, eaInputId("stop_loss_points")), "50");
  findById(host, "execSubmit")._listeners.click[0]();
  assert.equal(seen.length, 1);
  assert.equal(seen[0].backtest.ea_name, "TC24051901");
  assert.equal(seen[0].backtest.stop_loss_points, 50);
});
