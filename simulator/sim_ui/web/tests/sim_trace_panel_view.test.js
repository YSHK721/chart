// 実行トレースの指定面（View・ISSUE-508 段階 3 §6.6.2）の単体テスト。
//
// なぜ面を分けるか（SRP）: 実行指示面（M3）の責務は「実行を開始させることと結果への導線」
//   だけだと当のモジュールが明記している。「何を記録するか・どこを残すか」は改訂の動機が
//   別のアクター（運用の要求＝期間指定・記録量の抑制。基本設計 §3 の `trace_window` と同じ
//   動機）であり、同居させると実行操作の改訂とトレース運用の改訂が同じファイルを開く。
//
// 固定する不変条件:
//   1. 明示 ON のチェックと期間 2 欄が画面に出る（人が ON にできなければ機能は存在しない）。
//   2. 面は打たれた値をそのまま報告する（解釈も変換もしない＝規則は M5 が唯一持つ）。
//   3. 日付欄は front に既に在る表記（`YYYY.MM.DD`）とカレンダーを共有する
//      （同じ「日付」に画面上の呼び名を 2 つ作らない）。
import { test } from "node:test";
import assert from "node:assert/strict";

import { fakeDoc, findById, flatten } from "./_fakes.js";
import { createSimTracePanelView } from "../js/adapter/front/sim_trace_panel_view.js";

/** 面を組んで {doc, view, root} を返す。 */
function panel() {
  const doc = fakeDoc();
  const view = createSimTracePanelView({ doc });
  const root = view.mount(doc.body);
  return { doc, view, root };
}

// --- 1. 明示 ON と期間 2 欄が在る -------------------------------------------

test("mount ships the explicit on-switch and the two period fields", () => {
  const { doc } = panel();
  assert.ok(findById(doc.body, "simTracePanel"), "トレース指定面が生成されていない");
  const enabled = findById(doc.body, "traceEnabled");
  assert.ok(enabled, "トレース記録の ON チェックがない（人が点けられない＝機能が無い）");
  assert.equal(enabled.type, "checkbox");
  assert.ok(findById(doc.body, "traceFrom"), "開始日の欄がない");
  assert.ok(findById(doc.body, "traceTo"), "終了日の欄がない");
});

test("the panel names the switch and the two bounds on screen", () => {
  const { root } = panel();
  const texts = flatten(root).map((n) => n.textContent).filter(Boolean);
  for (const label of ["トレース記録", "開始日", "終了日"]) {
    assert.ok(texts.includes(label), `画面に「${label}」の表示がない: ${JSON.stringify(texts)}`);
  }
});

// --- 2. 面は打たれた値をそのまま報告する ------------------------------------

test("the panel reports the trace as off until somebody switches it on", () => {
  // 既定 OFF が「既存投入と byte 等価」の根拠である（M5 は OFF を本文へ載せない）。
  const { view } = panel();
  assert.deepEqual(view.traceSpec(), { enabled: false, from: "", to: "" });
});

test("the panel reports the switch and the two bounds as typed", () => {
  const { doc, view } = panel();
  findById(doc.body, "traceEnabled").checked = true;
  findById(doc.body, "traceFrom").value = "2025.01.06";
  findById(doc.body, "traceTo").value = "2025.01.10";
  assert.deepEqual(view.traceSpec(), {
    enabled: true, from: "2025.01.06", to: "2025.01.10",
  });
});

test("the panel interprets nothing (規則は投入契約が唯一持つ)", () => {
  // 逆転・片側・出鱈目のいずれもここでは落とさない。面が独自に判定を持つと、同じ規則が
  // 画面と投入契約の 2 箇所に生まれ、片方が腐る。
  const { doc, view } = panel();
  findById(doc.body, "traceEnabled").checked = true;
  findById(doc.body, "traceFrom").value = "とんでもない値";
  assert.deepEqual(view.traceSpec(), { enabled: true, from: "とんでもない値", to: "" });
});

test("the panel reports the same spec before it is mounted (器が無くても壊れない)", () => {
  // 合成根は mount 段で落ちうる（面が組めない構成）。そこで traceSpec が例外を投げると、
  // 投入そのものが道連れになる。組めていない面は「OFF」と報告する。
  const doc = fakeDoc();
  const view = createSimTracePanelView({ doc });
  assert.deepEqual(view.traceSpec(), { enabled: false, from: "", to: "" });
});

// --- 3. 日付欄は front に既に在るカレンダーと表記を共有する ------------------

/** 部分木から開いているカレンダーの popup を引く（無ければ null）。 */
const openCalendar = (root) =>
  flatten(root).find((n) => (n.dataset || {}).mt5 === "ui:date-picker") || null;

test("each period field opens the front's existing calendar (複製を作らない)", () => {
  const { doc, root } = panel();
  assert.equal(openCalendar(root), null, "何もしていないのにカレンダーが開いている");
  findById(doc.body, "traceFromCalBtn")._listeners.click[0]();
  assert.ok(openCalendar(root), "▾ を押してもカレンダーが開かない");
});

test("choosing a day writes the front's date token back into the field", () => {
  const { doc, root } = panel();
  findById(doc.body, "traceFrom").value = "2025.01.06";
  findById(doc.body, "traceFromCalBtn")._listeners.click[0]();
  const day = flatten(openCalendar(root)).find((n) => (n.dataset || {}).token === "2025.01.20");
  assert.ok(day, "表示月に 2025.01.20 の枡がない（開いた月が値に従っていない）");
  day._listeners.click[0]();
  assert.equal(findById(doc.body, "traceFrom").value, "2025.01.20");
  assert.equal(openCalendar(root), null, "日を選んでもカレンダーが閉じない");
});

test("the two fields do not share one bound (開始と終了は別の値)", () => {
  const { doc, root } = panel();
  findById(doc.body, "traceTo").value = "2025.01.06";
  findById(doc.body, "traceToCalBtn")._listeners.click[0]();
  flatten(openCalendar(root))
    .find((n) => (n.dataset || {}).token === "2025.01.31")._listeners.click[0]();
  assert.equal(findById(doc.body, "traceTo").value, "2025.01.31");
  // 正の対照: もう一方は動いていない。
  assert.equal(findById(doc.body, "traceFrom").value, "");
});

test("every control declares its MT5 counterpart under the ui: prefix", () => {
  // トレース記録は MT5 に対応物を持たない表示制御であり、本文のキー名にもならない
  // （画面契約 `sim_form_mt5_contract.test.js` の語彙規則 4・5 と同じ規律）。
  const { root } = panel();
  const controls = flatten(root).filter((n) => ["INPUT", "BUTTON", "SELECT"].includes(n.tagName));
  assert.ok(controls.length > 0, "操作要素が 1 つも無い＝この検定は空振りしています");
  for (const node of controls) {
    const declaration = (node.dataset || {}).mt5;
    assert.ok(declaration, `data-mt5 の無い操作要素があります: <${node.tagName} id=${node.id}>`);
    assert.ok(String(declaration).startsWith("ui:"),
      `トレース指定は表示制御である（ui: 以外の宣言: ${declaration}）`);
  }
});
