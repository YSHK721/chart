// sim_tester_settings_panel_view の**表示整形**の検定（Phase 8 スライス 7）。
//
// スライス 5 の検定（sim_tester_settings_panel_view.test.js）が固定するのは「何を投入するか」
// である。本ファイルが固定するのは「どう並べ、何を常時見せるか」——値の意味には触れない。
//
// 固定する不変条件:
//   1. schema.key_order の全キーが**いずれかの群**に描画される（取りこぼし 0）。
//   2. 群の割当表に無いキーも既定群へ落ちて消えない（新キーが増えても UI から欠落しない＝OCP）。
//   3. 期間形式の切替は期間キーと同じ群にいる（分岐と対象が離れて見えない）。
//   4. 非対象告知（N-xx）は既定で折りたたみ、**現在値が該当する分だけ**が常時出る（T-5 の完成形）。
//   5. 「詳細」トグルで全一覧が開閉する。
//
// 期待値のキー名は**注入 schema から導く**（リテラルで書けば、view 側に写しがあっても緑になる）。
// 見た目そのもの（CSS）は fake DOM で検証できない＝実ブラウザ確認は別（本検定の対象外）。
import { test } from "node:test";
import assert from "node:assert/strict";

import { fakeDoc, findById, flatten } from "./_fakes.js";
import { runProfile, settingsSchema } from "./_settings_schema_fixture.js";
import { createSimTesterSettingsPanelView } from "../js/adapter/front/sim_tester_settings_panel_view.js";

const hasClass = (el, c) => String((el && el.className) || "").split(/\s+/).includes(c);
const byClass = (root, c) => flatten(root).filter((n) => hasClass(n, c));
/** `.ini` キー K の入力要素（id は `tester{Key}`）。 */
const field = (host, key) => findById(host, `tester${key}`);
/** change リスナを直接叩く（fake DOM は自動発火しない）。 */
const fire = (el, ev = "change") => (el._listeners[ev] || []).forEach((f) => f());
/** 要素が属する群（`.tester-group`）を親方向にたどって返す。 */
function groupOf(el) {
  let node = el && el.parentNode;
  while (node) {
    if (hasClass(node, "tester-group")) return node;
    node = node.parentNode;
  }
  return null;
}

/** 本パネルが常に出さないキー（Expert 専用テスト＝規則 D）。 */
const NEVER = ["Indicator"];

function ready(schema = settingsSchema()) {
  const doc = fakeDoc();
  const view = createSimTesterSettingsPanelView({ doc });
  view.mount(doc.body);
  view.setSchema(schema);
  view.setRunProfile(runProfile());
  return { doc, host: doc.body, view, schema };
}

// --- 1. 群割当に取りこぼしが無い -------------------------------------------------

test("every schema key is rendered inside a labelled group (取りこぼし 0)", () => {
  const { host, schema } = ready();
  const offenders = [];
  for (const key of schema.key_order) {
    if (NEVER.includes(key)) continue;
    const el = field(host, key);
    if (!el) { offenders.push(`${key}: 入力要素が無い`); continue; }
    if (!groupOf(el)) offenders.push(`${key}: どの群にも属していない`);
  }
  assert.deepEqual(offenders, []);
});

test("every rendered group carries a non-empty heading", () => {
  const { host } = ready();
  const groups = byClass(host, "tester-group");
  assert.ok(groups.length > 0, "群が 1 つも描画されていません");
  for (const g of groups) {
    const title = flatten(g).find((n) => hasClass(n, "tester-group-title"));
    assert.ok(title, `群 ${g.dataset.group} に見出し要素がありません`);
    assert.ok(String(title.textContent).length > 0, `群 ${g.dataset.group} の見出しが空です`);
  }
});

// --- 2. 割当表に無いキーが消えない（OCP）-----------------------------------------
// schema が新しいキーを配ったとき、view の割当表を直し忘れても UI から欠落しない。
// 欠落すると「設定したつもりの値が投入されない」沈黙失敗になる。

test("a key absent from the group table still renders and still submits (OCP)", () => {
  const schema = settingsSchema();
  // 割当表のどの群にも載っていない架空のキー（schema 側にだけ現れた新キーの代役）。
  const NEW_KEY = "FutureKnob";
  schema.key_order = [...schema.key_order, NEW_KEY];
  const { host, view } = ready(schema);

  const el = field(host, NEW_KEY);
  assert.ok(el, "割当表に無いキーが描画されていません（新キーが UI から消えています）");
  assert.ok(groupOf(el), "割当表に無いキーが既定群へ落ちていません");
  assert.ok(NEW_KEY in view.buildTesterMapping(), "描画はされたが投入本文に載っていません");
});

test("the date-mode switch sits in the same group as the period keys", () => {
  const { host } = ready();
  const toggle = findById(host, "testerDateCustom");
  assert.ok(toggle, "#testerDateCustom が無い");
  assert.ok(groupOf(toggle), "期間形式の切替がどの群にも属していません");
  assert.equal(groupOf(toggle), groupOf(field(host, "Dates")),
    "切替が、出し分ける対象（期間キー）と別の群に置かれています");
});

// --- 3. 非対象告知の折りたたみ（T-5 の完成形）--------------------------------------
// N-xx をベタ張りし続けると、常時 16 行の壁が出て「今の選択に効いている告知」が埋もれる。
// 既定は折りたたみ、現在値が該当する分だけを常時出し、全一覧は「詳細」で開く。

test("the full unsupported list is collapsed by default", () => {
  const { host, schema } = ready();
  const list = findById(host, "simTesterUnsupported");
  assert.ok(list, "#simTesterUnsupported が無い");
  assert.equal(list.dataset.expanded, "0", "非対象の一覧が既定で開いています（常時ベタ張り）");
  // 一覧そのものは失われていない（開けば全件見える＝告知を捨てていない）。
  assert.equal(byClass(host, "tester-unsupported-line").length, schema.unsupported.length);
});

test("only the notice matching the current value is shown without expanding", () => {
  const { host, view, schema } = ready();
  assert.equal(byClass(host, "tester-unsupported-active-line").length, 0,
    "既定で該当告知が出ています（動かしていない値に効く告知は無い）");

  // 現在値が非対象に該当する状態を作る（該当判定は schema.unsupported の field 由来＝
  // activeUnsupported と同じ宣言。判定規則を検定側へ書き写さない）。
  const sel = field(host, "Optimization");
  sel.value = schema.enum_options.Optimization[1].token;
  fire(sel);
  const expected = view.activeUnsupported();
  assert.ok(expected.length > 0, "前提が崩れています（該当告知が 0 件）");

  const shown = byClass(host, "tester-unsupported-active-line");
  assert.deepEqual(shown.map((n) => n.dataset.unsupportedId), expected.map((n) => n.unsupported_id));
  for (const notice of expected) {
    const line = shown.find((n) => n.dataset.unsupportedId === notice.unsupported_id);
    assert.ok(String(line.textContent).includes(notice.reason), "該当告知に理由が出ていません");
  }
  // 常時表示は「該当分だけ」であり、全一覧は畳まれたままである。
  assert.equal(findById(host, "simTesterUnsupported").dataset.expanded, "0");
});

test("the details toggle opens and closes the full list", () => {
  const { host } = ready();
  const toggle = findById(host, "simTesterUnsupportedToggle");
  assert.ok(toggle, "#simTesterUnsupportedToggle が無い（全一覧を開く手段が無い）");
  const list = findById(host, "simTesterUnsupported");

  fire(toggle, "click");
  assert.equal(list.dataset.expanded, "1", "詳細トグルで全一覧が開きません");
  fire(toggle, "click");
  assert.equal(list.dataset.expanded, "0", "詳細トグルで全一覧が閉じません");
});

test("losing the schema clears the standing notices (取れない構成で古い告知を残さない)", () => {
  const { host, view, schema } = ready();
  const sel = field(host, "Optimization");
  sel.value = schema.enum_options.Optimization[1].token;
  fire(sel);
  assert.ok(byClass(host, "tester-unsupported-active-line").length > 0, "前提が崩れています");

  view.setSchema(null);   // schema を取れない構成（fail-open）へ落ちた
  assert.equal(byClass(host, "tester-unsupported-active-line").length, 0,
    "schema を失った後も古い該当告知が残っています（今の構成に対応しない表示）");
});

test("the details toggle reports how many notices there are (0 件なら出さない)", () => {
  const withSchema = ready();
  assert.equal(
    findById(withSchema.host, "simTesterUnsupportedToggle").dataset.count,
    String(withSchema.schema.unsupported.length),
  );
  // schema が無い構成では開く中身が無い＝件数 0（CSS がこの 0 でトグルを消す）。
  withSchema.view.setSchema(null);
  assert.equal(findById(withSchema.host, "simTesterUnsupportedToggle").dataset.count, "0");
});
