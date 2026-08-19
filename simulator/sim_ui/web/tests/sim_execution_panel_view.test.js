// sim_execution_panel_view（実行指示パネル・Phase 6 F-8・TBD-11）の単体テスト（fake DOM）。
//
// 固定する不変条件:
//   1. ea_name / SL(stop_loss_points) / TP(take_profit_points) / entry_long・entry_short の
//      条件行 / 投入ボタンを生成する。
//   2. op セレクタの選択肢は**厳密に [">","<"]**（TBD-11: OR/グループ化/>=,<=,== を出さない）。
//   3. 指標セレクタの候補は**注入された候補**から作る（ハードコードしない）。
//   4. 条件行は追加/削除できる（件数が増減する）。
//   5. buildSubmission は {backtest:{ea_name,stop_loss_points,take_profit_points}, strategy:{entry_long,entry_short}}
//      を返す。shift/rhs は数値。行の無い側は省く。両側とも空なら strategy を丸ごと省く（OFF＝byte 等価）。
import { test } from "node:test";
import assert from "node:assert/strict";

import { fakeDoc, findById, flatten } from "./_fakes.js";
import { createSimExecutionPanelView } from "../js/adapter/front/sim_execution_panel_view.js";

// 既存 view と同じ流儀: className は文字列で持つ（classList.add ではなく node.className）。
const hasClass = (el, c) => String((el && el.className) || "").split(/\s+/).includes(c);
const byClass = (root, c) => flatten(root).filter((n) => hasClass(n, c));

function mounted() {
  const doc = fakeDoc();
  const view = createSimExecutionPanelView({ doc });
  view.mount(doc.body);
  return { doc, host: doc.body, view };
}

// --- 1. 生成 ------------------------------------------------------------------

test("mount builds ea/sl/tp inputs and a submit button", () => {
  const { host } = mounted();
  assert.ok(findById(host, "execEaName"), "#execEaName が無い");
  assert.ok(findById(host, "execSl"), "#execSl が無い");
  assert.ok(findById(host, "execTp"), "#execTp が無い");
  assert.ok(findById(host, "execSubmit"), "#execSubmit が無い");
  assert.ok(findById(host, "execAddLong"), "#execAddLong が無い");
  assert.ok(findById(host, "execAddShort"), "#execAddShort が無い");
});

// --- 2. op は厳密に [">","<"]（TBD-11）---------------------------------------

test("op selector options are exactly > and < (TBD-11)", () => {
  const { host, view } = mounted();
  view.addCondition("long");
  const opSel = byClass(host, "exec-op")[0];
  const values = (opSel.children || []).map((o) => o.value);
  assert.deepEqual(values, [">", "<"]);
});

// --- 3. 指標候補は注入から ---------------------------------------------------

test("indicator options come from injected candidates", () => {
  const { host, view } = mounted();
  view.setIndicatorCandidates(["ema", "madiff", "close"]);
  view.addCondition("long");
  const indSel = byClass(host, "exec-ind")[0];
  const values = (indSel.children || []).map((o) => o.value);
  assert.deepEqual(values, ["ema", "madiff", "close"]);
});

test("setting candidates after rows exist updates existing selectors", () => {
  const { host, view } = mounted();
  view.addCondition("long");
  view.setIndicatorCandidates(["adx", "plus_di"]);
  const indSel = byClass(host, "exec-ind")[0];
  const values = (indSel.children || []).map((o) => o.value);
  assert.deepEqual(values, ["adx", "plus_di"]);
});

// --- 4. 行の追加/削除 --------------------------------------------------------

test("adding conditions increases the row count per side", () => {
  const { host, view } = mounted();
  view.addCondition("long");
  view.addCondition("long");
  view.addCondition("short");
  assert.equal(byClass(host, "exec-cond-row").length, 3);
});

test("delete removes a condition row", () => {
  const { host, view } = mounted();
  view.addCondition("long");
  view.addCondition("long");
  const del = byClass(host, "exec-del")[0];
  del._listeners.click[0]();
  assert.equal(byClass(host, "exec-cond-row").length, 1);
});

test("add-long button click adds a long row", () => {
  const { host } = mounted();
  findById(host, "execAddLong")._listeners.click[0]();
  assert.equal(byClass(host, "exec-cond-row").length, 1);
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

test("buildSubmission returns the full 18-key backtest body and typed strategy", () => {
  const { host, view } = mounted();
  view.setRunOptions([_PROFILE]);
  view.setIndicatorCandidates(["ema", "close"]);
  setVal(findById(host, "execEaName"), "TC24051901");
  setVal(findById(host, "execSl"), "100");
  setVal(findById(host, "execTp"), "200");
  view.addCondition("long");
  const row = byClass(host, "exec-cond-row")[0];
  setVal(byClass(row, "exec-ind")[0], "close");
  setVal(byClass(row, "exec-shift")[0], "1");
  setVal(byClass(row, "exec-op")[0], ">");
  setVal(byClass(row, "exec-rhs")[0], "1.5");

  const bt = view.buildSubmission().backtest;
  // 18 キー完全（profile 11 ＋ フォーム 7）
  assert.deepEqual(Object.keys(bt).sort(), [...PROFILE_KEYS, ...FORM_KEYS].sort());
  // profile 由来の 11 キーは注入 profile と一致（front リテラル 0）
  for (const k of PROFILE_KEYS) assert.strictEqual(bt[k], _PROFILE[k], k);
  assert.equal(bt.ea_name, "TC24051901");
  assert.equal(bt.stop_loss_points, 100);
  assert.equal(bt.take_profit_points, 200);

  const body = view.buildSubmission();
  assert.deepEqual(body.strategy.entry_long, [
    { indicator: "close", shift: 1, op: ">", rhs: 1.5 },
  ]);
  assert.equal("entry_short" in body.strategy, false);
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

test("new form fields (ma_period/ma_method/initial_deposit/lot_size) are present and typed", () => {
  const { host, view } = mounted();
  view.setRunOptions([_PROFILE]);
  setVal(findById(host, "execMaPeriod"), "20");
  setVal(findById(host, "execMaMethod"), "ema");
  setVal(findById(host, "execDeposit"), "10000");
  setVal(findById(host, "execLot"), "0.1");
  const bt = view.buildSubmission().backtest;
  assert.strictEqual(bt.ma_period, 20);
  assert.strictEqual(bt.ma_method, "ema");
  assert.strictEqual(bt.initial_deposit, 10000);
  assert.strictEqual(bt.lot_size, 0.1);
});

test("buildSubmission types shift as int and rhs as number", () => {
  const { host, view } = mounted();
  view.setIndicatorCandidates(["ema"]);
  view.addCondition("short");
  const row = byClass(host, "exec-cond-row")[0];
  setVal(byClass(row, "exec-ind")[0], "ema");
  setVal(byClass(row, "exec-shift")[0], "2");
  setVal(byClass(row, "exec-op")[0], "<");
  setVal(byClass(row, "exec-rhs")[0], "0");
  const cond = view.buildSubmission().strategy.entry_short[0];
  assert.strictEqual(cond.shift, 2);
  assert.strictEqual(cond.rhs, 0);
  assert.strictEqual(cond.op, "<");
});

test("empty strategy is omitted (OFF は byte 等価)", () => {
  const { host, view } = mounted();
  setVal(findById(host, "execEaName"), "TC24051901");
  setVal(findById(host, "execSl"), "100");
  setVal(findById(host, "execTp"), "200");
  const body = view.buildSubmission();
  assert.equal("strategy" in body, false);
});

// --- 5c. 建玉変更（トレーリング FR-07 / 部分決済 FR-08・Phase 7）------------------
// 固定する不変条件:
//   - mount は trailing/partial_close の入力欄（ON トグル＋各フィールド）を生成する。
//   - 既定 OFF（トグル未チェック）では strategy に trailing/partial_close を載せない
//     （＝Phase 6 本文と byte 等価・回帰ゼロ）。両側条件も無ければ strategy 自体を省く。
//   - trailing ON で strategy.trailing = {granularity, trigger_points, distance_points,
//     step_points}（数値・granularity は文字列）を載せる。granularity 選択肢は厳密に [bar, tick]。
//   - partial ON で strategy.partial_close = {trigger:{profit_points}, close_fraction}（数値）を載せる。

test("mount builds trailing/partial_close inputs (Phase 7)", () => {
  const { host } = mounted();
  assert.ok(findById(host, "execTrailingOn"), "#execTrailingOn が無い");
  assert.ok(findById(host, "execTrailGran"), "#execTrailGran が無い");
  assert.ok(findById(host, "execTrailTrigger"), "#execTrailTrigger が無い");
  assert.ok(findById(host, "execTrailDistance"), "#execTrailDistance が無い");
  assert.ok(findById(host, "execTrailStep"), "#execTrailStep が無い");
  assert.ok(findById(host, "execPartialOn"), "#execPartialOn が無い");
  assert.ok(findById(host, "execPartialTrigger"), "#execPartialTrigger が無い");
  assert.ok(findById(host, "execPartialFraction"), "#execPartialFraction が無い");
});

test("trailing granularity options are exactly bar and tick", () => {
  const { host } = mounted();
  const gran = findById(host, "execTrailGran");
  assert.deepEqual((gran.children || []).map((o) => o.value), ["bar", "tick"]);
});

test("position-change blocks are omitted when toggles are OFF (byte 等価)", () => {
  const { host, view } = mounted();
  view.setRunOptions([_PROFILE]);
  setVal(findById(host, "execEaName"), "TC24051901");
  // トグル未チェック（既定 OFF）。値だけ入っていても spec には載せない。
  setVal(findById(host, "execTrailTrigger"), "300");
  setVal(findById(host, "execTrailDistance"), "150");
  setVal(findById(host, "execPartialFraction"), "0.5");
  const body = view.buildSubmission();
  // 両側条件が無いので strategy 自体を省く（Phase 6 と同じ OFF＝byte 等価）。
  assert.equal("strategy" in body, false);
});

test("trailing ON loads strategy.trailing with typed fields (bar 粒度)", () => {
  const { host, view } = mounted();
  view.setRunOptions([_PROFILE]);
  findById(host, "execTrailingOn").checked = true;
  setVal(findById(host, "execTrailGran"), "bar");
  setVal(findById(host, "execTrailTrigger"), "300");
  setVal(findById(host, "execTrailDistance"), "150");
  setVal(findById(host, "execTrailStep"), "10");
  const strategy = view.buildSubmission().strategy;
  assert.deepEqual(strategy.trailing, {
    granularity: "bar",
    trigger_points: 300,
    distance_points: 150,
    step_points: 10,
  });
  // partial は OFF なので載らない。
  assert.equal("partial_close" in strategy, false);
});

test("partial_close ON loads strategy.partial_close with typed fields", () => {
  const { host, view } = mounted();
  view.setRunOptions([_PROFILE]);
  findById(host, "execPartialOn").checked = true;
  setVal(findById(host, "execPartialTrigger"), "200");
  setVal(findById(host, "execPartialFraction"), "0.5");
  const strategy = view.buildSubmission().strategy;
  assert.deepEqual(strategy.partial_close, {
    trigger: { profit_points: 200 },
    close_fraction: 0.5,
  });
  assert.equal("trailing" in strategy, false);
});

test("tick granularity is carried through when selected", () => {
  const { host, view } = mounted();
  view.setRunOptions([_PROFILE]);
  findById(host, "execTrailingOn").checked = true;
  setVal(findById(host, "execTrailGran"), "tick");
  setVal(findById(host, "execTrailTrigger"), "0");
  setVal(findById(host, "execTrailDistance"), "120");
  setVal(findById(host, "execTrailStep"), "0");
  const t = view.buildSubmission().strategy.trailing;
  assert.strictEqual(t.granularity, "tick");
  assert.strictEqual(t.trigger_points, 0);
  assert.strictEqual(t.step_points, 0);
});

test("position-change blocks coexist with entry conditions", () => {
  const { host, view } = mounted();
  view.setRunOptions([_PROFILE]);
  view.setIndicatorCandidates(["ema", "close"]);
  view.addCondition("long");
  const row = byClass(host, "exec-cond-row")[0];
  setVal(byClass(row, "exec-ind")[0], "close");
  findById(host, "execTrailingOn").checked = true;
  setVal(findById(host, "execTrailDistance"), "150");
  const strategy = view.buildSubmission().strategy;
  assert.ok(Array.isArray(strategy.entry_long), "entry_long が消えた");
  assert.ok(strategy.trailing, "trailing が entry と併存していない");
});

// --- 5d. Tester Settings パネルとの一本化（Phase 8 スライス 5・T-4）----------------
// 固定する不変条件:
//   - パネル未接続（＝schema を取れない構成）では本文に `settings` が載らない。旧フォーム
//     投入（Phase 6/7 の 2〜3 キー本文）と byte 等価のまま併存する。
//   - 接続すると本文へ `settings`（生トークン Mapping）が載り、`backtest` の
//     `ea_name` / `initial_deposit` は **settings 側の値から導出**される。
//   - 同一概念の入力欄を 2 つ持たない（EA・初期資金の重複欄は器から消える）。
//   - profile 由来 11 キー・SL/TP/MA/ロット・条件・建玉変更は従来どおり。

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
  setVal(findById(host, "execSl"), "100");
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
  // 一本化の対象外（SL/TP/MA/ロット・条件・建玉変更）は残る
  for (const id of ["execSl", "execTp", "execMaPeriod", "execMaMethod", "execLot",
    "execAddLong", "execTrailingOn", "execPartialOn", "execSubmit"]) {
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

// --- 5b. onEaChange（ea_name 変更で候補を選択 EA の系列へ入れ替える結線）--------

test("changing ea_name fires onEaChange with the new ea (候補の再取得起点)", () => {
  const { host, view } = mounted();
  const seen = [];
  view.onEaChange((ea) => seen.push(ea));
  const eaSel = findById(host, "execEaName");
  eaSel.value = "PRO_fit_Band_EA";
  // fake DOM は change を自動発火しないので、登録された change リスナを直接叩く
  eaSel._listeners.change[0]();
  assert.deepEqual(seen, ["PRO_fit_Band_EA"]);
});

// --- 6. onSubmit --------------------------------------------------------------

test("clicking submit invokes onSubmit with the built body", () => {
  const { host, view } = mounted();
  const seen = [];
  view.onSubmit((b) => seen.push(b));
  setVal(findById(host, "execEaName"), "TC24051901");
  setVal(findById(host, "execSl"), "50");
  setVal(findById(host, "execTp"), "150");
  findById(host, "execSubmit")._listeners.click[0]();
  assert.equal(seen.length, 1);
  assert.equal(seen[0].backtest.ea_name, "TC24051901");
  assert.equal(seen[0].backtest.stop_loss_points, 50);
});
