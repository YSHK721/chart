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
import {
  createSimTesterSettingsPanelView,
  CUSTOM_RANGE_OPTION,
} from "../js/adapter/front/sim_tester_settings_panel_view.js";

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

/** 各列挙キーの既定トークン（先頭の選択肢）。活性判定の導出に使う。 */
function defaultTokens(schema) {
  const out = {};
  for (const [key, options] of Object.entries(schema.enum_options)) out[key] = options[0].token;
  return out;
}

/** 注入 schema の key_order から「この形式で出るはずのキー列」を導く（期待値を書かない）。
 *  活性宣言（schema.activation）で既定トークンのとき不活性になるキーも落とす。 */
function expectedKeys(schema, { custom = false } = {}) {
  const tokens = defaultTokens(schema);
  const dropped = new Set([...NEVER, ...OPTIONAL, ...(custom ? PRESET_ONLY : CUSTOM_ONLY)]);
  for (const [key, rule] of Object.entries(schema.activation || {})) {
    if (rule.effect === "display") continue;   // 表示だけ隠す宣言は本文に載り続ける
    const token = tokens[rule.key];
    const active = rule.mode === "on_tokens"
      ? rule.tokens.includes(token) : !rule.tokens.includes(token);
    if (!active) dropped.add(key);
  }
  return schema.key_order.filter((k) => !dropped.has(k));
}

/** 期間カスタム形へ切り替える（Dates の「カスタム期間」選択肢＝MT5 と同形）。 */
function chooseCustomRange(host) {
  const dates = field(host, "Dates");
  dates.value = CUSTOM_RANGE_OPTION.token;
  fire(dates);
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
    // Dates だけは末尾に UI 専用の「カスタム期間」が足される（MT5 の日付ドロップダウンと同形）
    const expected = key === "Dates"
      ? [...options.map((o) => o.token), CUSTOM_RANGE_OPTION.token]
      : options.map((o) => o.token);
    assert.deepEqual(tokens(el), expected, key);
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
  // OptimizationCriterion は既定（最適化が無効）では不活性＝載らない（activation 宣言）
  for (const key of ["Model", "Optimization", "Dates", "ForwardMode"]) {
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
  chooseCustomRange(host);
  field(host, "FromDate").value = "2025.01.06";
  field(host, "ToDate").value = "2025.01.10";
  const mapping = view.buildTesterMapping();
  assert.deepEqual(Object.keys(mapping), expectedKeys(schema, { custom: true }));
  assert.equal(mapping.FromDate, "2025.01.06");
  assert.equal(mapping.ToDate, "2025.01.10");
  assert.ok(!Object.values(mapping).includes(CUSTOM_RANGE_OPTION.token),
    "UI 専用トークンが投入本文に漏れています");
});

test("the custom range keys are greyed out while a preset is chosen (MT5 の不活性と同形)", () => {
  const { host } = ready();
  assert.equal(field(host, "FromDate").disabled, true, "プリセット中も FromDate が活性です");
  assert.equal(field(host, "ToDate").disabled, true);
  chooseCustomRange(host);
  assert.equal(field(host, "FromDate").disabled, false, "カスタム期間で FromDate が不活性のまま");
  assert.equal(field(host, "ToDate").disabled, false);
});

test("ForwardDate is emitted only under the activating ForwardMode (規則 F の宣言駆動)", () => {
  const { host, view, schema } = ready();
  assert.ok(!("ForwardDate" in view.buildTesterMapping()));
  // 不活性のうちは、値が入っていても送らない（MT5 のグレーアウトと同形）
  field(host, "ForwardDate").value = "2025.02.01";
  assert.ok(!("ForwardDate" in view.buildTesterMapping()),
    "不活性の ForwardDate が投入本文に載っています");
  assert.equal(field(host, "ForwardDate").disabled, true);
  // 活性化条件は schema.activation の宣言から引く（値を検定へ書き写さない）
  const rule = schema.activation.ForwardDate;
  const sel = field(host, rule.key);
  sel.value = rule.tokens[0];
  fire(sel);
  assert.equal(field(host, "ForwardDate").disabled, false);
  assert.equal(view.buildTesterMapping().ForwardDate, "2025.02.01");
});

test("Visual and OptimizationCriterion follow the optimization activation (規則 B/H・MT5 同形)", () => {
  const { host, view, schema } = ready();
  // 既定（最適化が無効）: Visual は活性で載る。criterion は**表示だけ**隠れて値は載り続ける
  // （規則 H: Expert 専用キーは常に必須。effect:"display" の宣言）。
  assert.ok("Visual" in view.buildTesterMapping());
  assert.ok("OptimizationCriterion" in view.buildTesterMapping(),
    "criterion が本文から落ちています（規則 H で必須・E-08 で必ず失敗する）");
  assert.equal(field(host, "OptimizationCriterion").dataset.inactive, "1",
    "最適化が無効なのに criterion が表示上も活性です（MT5 は出さない）");
  // 最適化を有効へ: Visual が落ち（規則 B）、criterion は表示にも出る
  const rule = schema.activation.Visual;
  const sel = field(host, rule.key);
  const enabling = tokens(sel).find((t) => !rule.tokens.includes(t));
  sel.value = enabling;
  fire(sel);
  assert.ok(!("Visual" in view.buildTesterMapping()),
    "最適化が有効なのに Visual が載っています（規則 B 違反の本文）");
  assert.ok("OptimizationCriterion" in view.buildTesterMapping());
  assert.equal(field(host, "OptimizationCriterion").dataset.inactive, "0");
});

test("flag keys render as checkboxes emitting 0/1 tokens (MT5 のチェックボックスと同形)", () => {
  const { host, view, schema } = ready();
  const flagKeys = Object.entries(schema.scalar_specs)
    .filter(([, spec]) => spec.value_type === "flag").map(([key]) => key);
  assert.ok(flagKeys.length, "fixture に旗キーが無い（検定が空振り）");
  for (const key of flagKeys) {
    assert.equal(field(host, key).type, "checkbox", key);
  }
  // 既定は 0、チェックで 1 の生トークンになる（Visual は既定で活性）
  assert.equal(view.buildTesterMapping().Visual, "0");
  const visual = field(host, "Visual");
  visual.checked = true;
  fire(visual);
  assert.equal(view.buildTesterMapping().Visual, "1");
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
  chooseCustomRange(host);
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

// --- 9. 日付キーはカレンダー付きのトークン欄（value_type 宣言駆動）------------------
// どのキーが日付かは schema の `scalar_specs[key].value_type` だけで決める（キー名からの
// 推測は宣言と食い違っても静かに素の欄へ縮退する）。欄の値は `.ini` トークン
// `YYYY.MM.DD` そのもの（変換層なし）。カレンダー本体の検定は sim_date_picker_view.test.js。

test("date-typed keys get a token field with a calendar button (宣言駆動)", () => {
  const { host, schema } = ready();
  const dateKeys = Object.entries(schema.scalar_specs)
    .filter(([, spec]) => spec.value_type === "date").map(([key]) => key);
  assert.ok(dateKeys.length, "fixture に日付キーが無い（検定が空振りしています）");
  for (const key of dateKeys) {
    const node = field(host, key);
    assert.equal(node.type, "text", `${key} はトークンをそのまま持つ text 欄で出す`);
    assert.ok(hasClass(node.parentNode, "tester-date-wrap"), `${key} の日付箱が無い`);
    assert.ok(findById(host, `tester${key}CalBtn`), `${key} のカレンダーボタンが無い`);
  }
  // 日付でないスカラーにはボタンを付けない（宣言に無い所へ発明しない）
  assert.equal(findById(host, "testerDepositCalBtn"), null);
});

test("the calendar opens at the field's month; a day click commits instantly", () => {
  const { host, view } = ready();
  chooseCustomRange(host);
  field(host, "FromDate").value = "2025.01.06";
  fire(findById(host, "testerFromDateCalBtn"), "click");
  const pop = byClass(host, "cal-pop")[0];
  assert.ok(pop, "カレンダーが開いていない");
  assert.equal(byClass(pop, "cal-title")[0].textContent, "January 2025");
  // 日付クリックで即時確定（確定ボタンは無い・依頼者裁定 2026-09-06）
  fire(byClass(pop, "cal-day").find((d) => d.dataset.token === "2025.01.10"), "click");
  assert.equal(field(host, "FromDate").value, "2025.01.10");
  assert.equal(view.buildTesterMapping().FromDate, "2025.01.10");
  assert.equal(byClass(host, "cal-pop").length, 0, "確定後もカレンダーが残っています");
});

test("an outside pointer-down closes the calendar and leaves the field untouched", () => {
  const { doc, host } = ready();
  chooseCustomRange(host);
  field(host, "FromDate").value = "2025.02.01";
  const btn = findById(host, "testerFromDateCalBtn");
  fire(btn, "click");
  assert.equal(byClass(host, "cal-pop").length, 1);
  // カレンダーの外（別の欄）を押すと閉じ、値は変わらない
  (doc._listeners.mousedown || []).slice().forEach((f) => f({ target: field(host, "Deposit") }));
  assert.equal(byClass(host, "cal-pop").length, 0);
  assert.equal(field(host, "FromDate").value, "2025.02.01");
  // 同じボタンの 2 度押しは開いて閉じる（トグル・外側判定が日付箱を除外している証拠）
  fire(btn, "click");
  assert.equal(byClass(host, "cal-pop").length, 1);
  fire(btn, "click");
  assert.equal(byClass(host, "cal-pop").length, 0);
});

// --- 11. プリセットの解決期間表示（MT5 実測 ss20260906204441/204651）---------------
// プリセット選択時、解決済み期間を不活性の From/To ボックスへ表示する（表示専用）。
// 種別は schema の Dates 選択肢（range_kind）・データ範囲は run profile が供給する。

test("choosing a preset shows its resolved period in the greyed date boxes", () => {
  const { host, view, schema, profile } = ready();
  // 既定（entire）: データ範囲そのもの（期待値は fixture の profile から導く）
  assert.equal(field(host, "FromDate").value, profile.data_first_date);
  assert.equal(field(host, "ToDate").value, profile.data_last_date);
  // year_to_date のプリセットへ: データ最終日の年の 1/1 〜 データ最終日
  const ytd = schema.enum_options.Dates.find((o) => o.range_kind === "year_to_date");
  const dates = field(host, "Dates");
  dates.value = ytd.token;
  fire(dates);
  const year = profile.data_last_date.split(".")[0];
  assert.equal(field(host, "FromDate").value, `${year}.01.01`);
  assert.equal(field(host, "ToDate").value, profile.data_last_date);
  // 表示は表示だけ——不活性キーは投入本文に載らない（規則 E は破らない）
  assert.ok(!("FromDate" in view.buildTesterMapping()));
  assert.ok(!("ToDate" in view.buildTesterMapping()));
});

test("the displayed preset period seeds the custom range and stays editable", () => {
  const { host, view, profile } = ready();
  chooseCustomRange(host);
  // 直前のプリセット表示（entire）が手入力の起点として残り、そのまま投入できる
  assert.equal(view.buildTesterMapping().FromDate, profile.data_first_date);
  assert.equal(view.buildTesterMapping().ToDate, profile.data_last_date);
  // 期間指定の間は選び直し以外で上書きされない（手入力が生きる）
  field(host, "FromDate").value = "2016.01.02";
  fire(field(host, "FromDate"), "input");
  assert.equal(field(host, "FromDate").value, "2016.01.02");
  assert.equal(view.buildTesterMapping().FromDate, "2016.01.02");
});

test("a profile without a data range leaves the display boxes blank (縮退)", () => {
  const m = mounted();
  m.view.setSchema(settingsSchema());
  const bare = runProfile();
  delete bare.data_first_date;
  delete bare.data_last_date;
  m.view.setRunProfile(bare);
  assert.equal(field(m.host, "FromDate").value, "");
  assert.equal(field(m.host, "ToDate").value, "");
});

test("a disabled date field's calendar button does not open (不活性の欄へ書かせない)", () => {
  const { host } = ready();
  const btn = findById(host, "testerForwardDateCalBtn");
  assert.equal(btn.disabled, true, "不活性の ForwardDate のカレンダーボタンが活性です");
  fire(btn, "click");
  assert.equal(byClass(host, "cal-pop").length, 0, "不活性の欄からカレンダーが開いています");
});

test("offered candidates survive a real-DOM HTMLCollection (children に .map が無くても動く)", () => {
  // 実ブラウザの `Element.children` は HTMLCollection＝Array メソッドを持たない。
  // fake DOM の配列 children を getter 経由の array-like（length と添字のみ）へ差し替えて
  // 実 DOM の意味論を再現する（実測 2026-08-19: `.map` 直呼びが TypeError で
  // Tester パネル構築を落とし、fail-open の縮退フォームに落ちていた＝ISSUE-425）。
  const { host, view } = ready();
  const sel = field(host, "Expert");
  const kids = sel.children;
  const collection = { length: kids.length };
  kids.forEach((c, i) => { collection[i] = c; });
  Object.defineProperty(sel, "children", { get: () => collection });
  sel.value = "NOT_A_CANDIDATE";
  fire(sel);
  assert.ok(activeIds(view).includes("X-06"), "HTMLCollection 相当の children で告知判定が動いていません");
});
