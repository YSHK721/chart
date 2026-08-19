// sim_tester_settings_panel_view（MT5 Tester Settings 準拠の設定パネル・Phase 8 スライス 5）の単体テスト。
//
// 固定する不変条件:
//   1. `setSchema` 前は候補を 1 つも持たない（front に内蔵候補が無いことの実証）。
//   2. 選択肢・キー順・必須キー・非対象理由は**注入 schema からのみ**来る。期待値を
//      リテラルで書かず、注入した schema の要素から導く（写しがあれば検定は素通りする）。
//   3. `buildTesterMapping()` は key_order 順の**生トークン文字列**だけを返す（数値・日付も文字列）。
//   4. 期間は「Dates プリセット」⇄「FromDate/ToDate カスタム」の排他（規則 E をフォームが破らない）。
//   5. Symbol / Period の既定は選択 profile の値。食い違う選択は**投入前に警告**する（T-3）。
//   6. 非対象値を選ぶと schema.unsupported の reason / tbd を出す（T-5・沈黙失敗させない）。
//   7. EA inputs（`[TesterInputs]`）欄は出さない（T-2）。
import { test } from "node:test";
import assert from "node:assert/strict";

import { fakeDoc, findById, flatten } from "./_fakes.js";
import { runProfile, settingsSchema } from "./_settings_schema_fixture.js";
import { createSimTesterSettingsPanelView } from "../js/adapter/front/sim_tester_settings_panel_view.js";

const hasClass = (el, c) => String((el && el.className) || "").split(/\s+/).includes(c);
const byClass = (root, c) => flatten(root).filter((n) => hasClass(n, c));
const byTag = (root, t) => flatten(root).filter((n) => n.tagName === t);
const tokens = (el) => ((el && el.children) || []).map((o) => o.value);
/** `.ini` キー K の入力要素（id は `tester{Key}`）。 */
const field = (host, key) => findById(host, `tester${key}`);
/** change リスナを直接叩く（fake DOM は自動発火しない）。 */
const fire = (el, ev = "change") => (el._listeners[ev] || []).forEach((f) => f());

function mounted() {
  const doc = fakeDoc();
  const view = createSimTesterSettingsPanelView({ doc });
  view.mount(doc.body);
  return { doc, host: doc.body, view };
}

function ready() {
  const m = mounted();
  m.schema = settingsSchema();
  m.profile = runProfile();
  m.view.setSchema(m.schema);
  m.view.setRunProfile(m.profile);
  return m;
}

/** 本パネルが常に出さないキー（Expert 専用テスト＝規則 D）と、期間形式で出し分かれるキー。 */
const NEVER = ["Indicator"];
const PRESET_ONLY = ["Dates"];
const CUSTOM_ONLY = ["FromDate", "ToDate"];
const OPTIONAL = ["ForwardDate"];

/** 注入 schema の key_order から「この形式で出るはずのキー列」を導く（期待値を書かない）。 */
function expectedKeys(schema, { custom = false } = {}) {
  const dropped = new Set([...NEVER, ...OPTIONAL, ...(custom ? PRESET_ONLY : CUSTOM_ONLY)]);
  return schema.key_order.filter((k) => !dropped.has(k));
}

// --- 1. setSchema 前は候補 0 -------------------------------------------------

test("before setSchema the panel carries no built-in candidates at all", () => {
  const { host, view } = mounted();
  assert.ok(findById(host, "simTesterPanel"), "#simTesterPanel が無い");
  assert.equal(byTag(host, "OPTION").length, 0, "内蔵候補を持っています（候補源は schema だけ）");
  assert.deepEqual(view.buildTesterMapping(), {});
  assert.deepEqual(view.activeUnsupported(), []);
});

// --- 2. 選択肢は schema の要素と一致 ------------------------------------------

test("every enum control offers exactly the schema tokens (リテラル期待値なし)", () => {
  const { host, schema } = ready();
  for (const [key, options] of Object.entries(schema.enum_options)) {
    const el = field(host, key);
    assert.ok(el, `${key} の入力要素が無い`);
    assert.deepEqual(tokens(el), options.map((o) => o.token), key);
  }
});

test("the Expert candidates come from schema.expert_options", () => {
  const { host, schema } = ready();
  assert.deepEqual(tokens(field(host, "Expert")), schema.expert_options.map((o) => o.token));
});

test("ExecutionMode offers the proven and provisional delays declared by the schema", () => {
  const { host, schema } = ready();
  const spec = schema.scalar_specs.ExecutionMode;
  const expected = [...spec.proven.map(String), ...Object.keys(spec.provisional)];
  const el = field(host, "ExecutionMode");
  assert.deepEqual(tokens(el), expected);
  // 実証状態（TBD）を表示に出す（沈黙で「実証済み」に見せない）
  const provisional = (el.children || []).find((o) => o.value === Object.keys(spec.provisional)[0]);
  assert.match(String(provisional.textContent), new RegExp(Object.values(spec.provisional)[0]));
});

// --- 3. buildTesterMapping は key_order 順の生トークン文字列 ---------------------

test("buildTesterMapping returns raw string tokens in the schema key order", () => {
  const { view, schema } = ready();
  const mapping = view.buildTesterMapping();
  assert.deepEqual(Object.keys(mapping), expectedKeys(schema));
  for (const [key, value] of Object.entries(mapping)) {
    assert.equal(typeof value, "string", `${key} が文字列ではありません（生トークンで渡すこと）`);
  }
});

test("the defaults come from the selected run profile (T-3)", () => {
  const { view, profile } = ready();
  const mapping = view.buildTesterMapping();
  assert.equal(mapping.Symbol, profile.symbol);
  assert.equal(mapping.Period, profile.period);
  assert.equal(mapping.Leverage, String(profile.leverage));
  assert.equal(mapping.Currency, profile.settlement_currency);
});

test("enum defaults are the first schema option (発明しない)", () => {
  const { view, schema } = ready();
  const mapping = view.buildTesterMapping();
  for (const key of ["Model", "Optimization", "Dates", "ForwardMode", "OptimizationCriterion"]) {
    assert.equal(mapping[key], schema.enum_options[key][0].token, key);
  }
  assert.equal(mapping.Expert, schema.expert_options[0].token);
});

// --- 4. 期間の排他（規則 E）----------------------------------------------------

test("the preset form emits Dates and never the custom range keys (規則 E)", () => {
  const { view, schema } = ready();
  const keys = Object.keys(view.buildTesterMapping());
  assert.deepEqual(keys, expectedKeys(schema, { custom: false }));
  for (const k of CUSTOM_ONLY) assert.ok(!keys.includes(k), `${k} が同時に載っています`);
});

test("switching to the custom range drops Dates and emits FromDate/ToDate (規則 E)", () => {
  const { host, view, schema } = ready();
  const toggle = findById(host, "testerDateCustom");
  assert.ok(toggle, "#testerDateCustom が無い（期間形式の切替が無い）");
  toggle.checked = true;
  fire(toggle);
  field(host, "FromDate").value = "2025.01.06";
  field(host, "ToDate").value = "2025.01.10";
  const mapping = view.buildTesterMapping();
  assert.deepEqual(Object.keys(mapping), expectedKeys(schema, { custom: true }));
  assert.equal(mapping.FromDate, "2025.01.06");
  assert.equal(mapping.ToDate, "2025.01.10");
});

test("ForwardDate is only emitted when it is filled in", () => {
  const { host, view } = ready();
  assert.ok(!("ForwardDate" in view.buildTesterMapping()));
  field(host, "ForwardDate").value = "2025.02.01";
  assert.equal(view.buildTesterMapping().ForwardDate, "2025.02.01");
});

// --- 5. profile 不一致の警告（T-3）---------------------------------------------

test("choosing a Period that differs from the profile warns before submission", () => {
  const { host, view, profile, schema } = ready();
  assert.deepEqual(view.warnings(), [], "既定で警告が出ています");
  const other = schema.enum_options.Period.find((o) => o.token !== profile.period);
  const sel = field(host, "Period");
  sel.value = other.token;
  fire(sel);
  const warnings = view.warnings();
  assert.equal(warnings.length, 1, `不一致の警告が出ていません: ${JSON.stringify(warnings)}`);
  assert.match(warnings[0], new RegExp(other.token));
  assert.match(warnings[0], new RegExp(profile.period));
  // 警告は DOM にも出る（投入前に見える）
  assert.match(String(findById(host, "simTesterWarn").textContent), new RegExp(other.token));
  // 選択そのものは殺さない（サーバ側 Fail-Stop が権威・front は黙って書き換えない）
  assert.equal(view.buildTesterMapping().Period, other.token);
});

test("a Symbol that differs from the profile warns as well", () => {
  const { host, view, profile } = ready();
  const sym = field(host, "Symbol");
  sym.value = `${profile.symbol}X`;
  fire(sym, "input");
  assert.equal(view.warnings().length, 1);
  assert.match(view.warnings()[0], new RegExp(`${profile.symbol}X`));
});

// --- 6. 非対象の告知（T-5）-----------------------------------------------------

test("every unsupported notice is rendered with the schema reason (沈黙させない)", () => {
  const { host, schema } = ready();
  const lines = byClass(host, "tester-unsupported-line");
  assert.equal(lines.length, schema.unsupported.length);
  const text = lines.map((n) => String(n.textContent)).join("\n");
  for (const notice of schema.unsupported) {
    assert.ok(text.includes(notice.reason), `${notice.unsupported_id} の理由が出ていません`);
    assert.ok(text.includes(notice.unsupported_id));
    if (notice.tbd) assert.ok(text.includes(notice.tbd), `${notice.unsupported_id} の TBD が出ていません`);
  }
});

const activeIds = (view) => view.activeUnsupported().map((n) => n.unsupported_id);

test("selecting a value outside the supported set activates that notice (except_tokens)", () => {
  const { host, view, schema } = ready();
  assert.deepEqual(activeIds(view), []);
  const sel = field(host, "Optimization");
  sel.value = schema.enum_options.Optimization[1].token;
  fire(sel);
  assert.deepEqual(activeIds(view), ["X-01"]);
  const line = byClass(host, "tester-unsupported-line").find((n) => n.dataset.unsupportedId === "X-01");
  assert.equal(line.dataset.active, "1");
  const other = byClass(host, "tester-unsupported-line").find((n) => n.dataset.unsupportedId === "X-02");
  assert.equal(other.dataset.active, "0");
});

// --- 6c. 該当判定は**宣言駆動**（R-9）------------------------------------------
// front はキー名の正規表現でも既定値スナップショットでも判定しない。schema が配る
// `keys` × `trigger`（+`tokens`）だけを照合する。以下は宣言 6 形すべての発火検定。

test("a declared firing token activates its notice (on_tokens・T-5 が名指しした Dates)", () => {
  const { host, view, schema } = ready();
  const notice = schema.unsupported.find((n) => n.unsupported_id === "X-03");
  const sel = field(host, notice.keys[0]);
  sel.value = notice.tokens[0];
  fire(sel);
  assert.ok(activeIds(view).includes("X-03"), `宣言したトークンで発火していません: ${JSON.stringify(activeIds(view))}`);
});

test("a declared firing token on another key activates its notice (on_tokens)", () => {
  const { host, view, schema } = ready();
  const notice = schema.unsupported.find((n) => n.unsupported_id === "X-04");
  const sel = field(host, notice.keys[0]);
  sel.value = notice.tokens[0];
  fire(sel);
  assert.ok(activeIds(view).includes("X-04"));
});

test("a notice bound by presence fires once its keys are actually submitted (on_presence)", () => {
  const { host, view } = ready();
  assert.equal(activeIds(view).includes("X-05"), false, "既定（プリセット期間）で発火しています");
  const toggle = findById(host, "testerDateCustom");
  toggle.checked = true;
  fire(toggle);
  // 投入本文に載るキーと発火が一致する（載らないのに警告しない・載るのに黙らない）
  assert.ok("FromDate" in view.buildTesterMapping());
  assert.ok(activeIds(view).includes("X-05"));
});

test("a value outside the offered candidates fires its notice (off_candidates)", () => {
  const { host, view } = ready();
  assert.equal(activeIds(view).includes("X-06"), false);
  const sel = field(host, "Expert");
  sel.value = "NOT_A_CANDIDATE";
  fire(sel);
  assert.ok(activeIds(view).includes("X-06"));
});

test("a value differing from the run profile authority fires its notice (off_profile)", () => {
  const { host, view, profile } = ready();
  assert.equal(activeIds(view).includes("X-07"), false, "既定は profile の値なので発火しない");
  const input = field(host, "Currency");
  input.value = `${profile.settlement_currency}Z`;
  fire(input, "input");
  assert.ok(activeIds(view).includes("X-07"));
});

test("a notice declared as not evaluable from raw tokens never fires (none)", () => {
  const { host, view, profile } = ready();
  const input = field(host, "Symbol");
  input.value = `${profile.symbol}X`;
  fire(input, "input");
  assert.equal(activeIds(view).includes("X-08"), false,
    "生トークンでは判定できないと宣言された告知を発火させています（過剰発火）");
});

test("re-applying the same run profile does not change the active notices", () => {
  // 既定値スナップショットを該当判定の代理にすると、profile 再適用で**該当が消える**
  // （＝データセットを選び直しただけで警告が黙って消える）。
  const { host, view, schema, profile } = ready();
  const notice = schema.unsupported.find((n) => n.unsupported_id === "X-03");
  const sel = field(host, notice.keys[0]);
  sel.value = notice.tokens[0];
  fire(sel);
  const before = activeIds(view);
  assert.ok(before.includes("X-03"));
  view.setRunProfile(profile);
  assert.deepEqual(activeIds(view), before, "profile 再適用で該当集合が変わりました");
});

// --- 7. EA inputs は出さない（T-2）---------------------------------------------

test("no EA inputs editor is shipped (T-2)", () => {
  const { host, view } = ready();
  assert.equal(byTag(host, "TEXTAREA").length, 0, "EA 入力欄を出しています（T-2 で対象外）");
  assert.deepEqual(view.buildSettings().inputs, []);
  assert.deepEqual(view.buildSettings().tester, view.buildTesterMapping());
});

// --- 8. backtest への導出（T-4）------------------------------------------------

test("derivedBacktest takes ea_name from the Expert label and initial_deposit from Deposit", () => {
  const { host, view, schema } = ready();
  const derived = view.derivedBacktest();
  assert.equal(derived.ea_name, schema.expert_options[0].label);
  assert.equal(derived.initial_deposit, Number(field(host, "Deposit").value));
  // 別の EA を選ぶと導出も変わる（front が語幹を自作していない証拠）
  const sel = field(host, "Expert");
  sel.value = schema.expert_options[1].token;
  fire(sel);
  assert.equal(view.derivedBacktest().ea_name, schema.expert_options[1].label);
});

test("onExpertChange reports the selected EA name (指標候補の取得起点)", () => {
  const { host, view, schema } = ready();
  const seen = [];
  view.onExpertChange((ea) => seen.push(ea));
  const sel = field(host, "Expert");
  sel.value = schema.expert_options[1].token;
  fire(sel);
  assert.deepEqual(seen, [schema.expert_options[1].label]);
});
