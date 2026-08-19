// sim_ea_inputs_panel_view（EA パラメータ面・Phase 9 S2・MT5 Inputs タブ相当）の単体テスト。
//
// このパネルが持つのは「EA が受け取る実行パラメータ」だけである（SL/TP・移動平均・ロット）。
// MT5 のテスタ設定（Tester Settings）とは変更要求の主体が別なので面を分ける（SRP）。
//
// 固定する不変条件（宣言表が唯一の宣言＝3 者一致）:
//   1. 宣言表 EA_INPUT_FIELDS の 1 行 1 行から DOM が導出される（id / ラベル / 型 / 初期値）。
//   2. values() の返すキー集合が宣言表の param 集合と**双方向に一致**する（片方向でない）。
//   3. 値の型変換は宣言表の type が決める（number / int / text）。
//   4. 初期値は宣言表が持つ（View にリテラルを書かない）。
import { test } from "node:test";
import assert from "node:assert/strict";

import { fakeDoc, findById, flatten } from "./_fakes.js";
import {
  EA_INPUT_FIELDS, createSimEaInputsPanelView,
} from "../js/adapter/front/sim_ea_inputs_panel_view.js";

function mounted() {
  const doc = fakeDoc();
  const view = createSimEaInputsPanelView({ doc });
  view.mount(doc.body);
  return { doc, host: doc.body, view };
}

const inputsOf = (host) => flatten(host).filter((n) => n.tagName === "INPUT");

// --- 1. 宣言表 → DOM -------------------------------------------------------------

test("the declaration table is non-empty and every row is fully specified", () => {
  assert.ok(EA_INPUT_FIELDS.length > 0, "宣言表が空です");
  for (const f of EA_INPUT_FIELDS) {
    for (const k of ["id", "param", "label", "type", "initial"]) {
      assert.ok(f[k] !== undefined && f[k] !== null && f[k] !== "", `${f.param}: ${k} が欠けています`);
    }
  }
});

test("every declared field is rendered exactly once (宣言表 → DOM)", () => {
  const { host } = mounted();
  for (const f of EA_INPUT_FIELDS) {
    const node = findById(host, f.id);
    assert.ok(node, `${f.param}: #${f.id} が描画されていません`);
    assert.equal(node.tagName, "INPUT");
  }
  // 宣言表に無い入力欄を勝手に足していない（DOM → 宣言表）
  assert.equal(inputsOf(host).length, EA_INPUT_FIELDS.length,
    "宣言表に無い入力欄が描画されています");
});

test("the rendered label text comes from the declaration table", () => {
  const { host } = mounted();
  for (const f of EA_INPUT_FIELDS) {
    const wrap = findById(host, f.id).parentNode;
    assert.equal(wrap.textContent, f.label, `${f.param}: ラベルが宣言表と違います`);
  }
});

test("the initial value comes from the declaration table (View にリテラルを持たない)", () => {
  const { host } = mounted();
  for (const f of EA_INPUT_FIELDS) {
    assert.equal(findById(host, f.id).value, f.initial, `${f.param}: 初期値が宣言表と違います`);
  }
});

// --- 2. DOM → 出力（キー集合の双方向一致）---------------------------------------

test("values() keys equal the declared param set (双方向一致)", () => {
  const { view } = mounted();
  assert.deepEqual(
    Object.keys(view.values()).sort(),
    EA_INPUT_FIELDS.map((f) => f.param).sort(),
  );
});

// --- 3. 型変換は宣言表の type が決める -------------------------------------------

test("declared types drive the coercion of the submitted values", () => {
  const { host, view } = mounted();
  // すべての欄に小数つきの文字列を入れ、type ごとの落ち方を見る。
  for (const f of EA_INPUT_FIELDS) findById(host, f.id).value = "12.7";
  const out = view.values();
  for (const f of EA_INPUT_FIELDS) {
    if (f.type === "int") assert.strictEqual(out[f.param], 12, `${f.param}: int でない`);
    else if (f.type === "number") assert.strictEqual(out[f.param], 12.7, `${f.param}: number でない`);
    else assert.strictEqual(out[f.param], "12.7", `${f.param}: text でない`);
  }
});

test("the initial values round-trip through values() unchanged", () => {
  const { view } = mounted();
  const out = view.values();
  for (const f of EA_INPUT_FIELDS) {
    const expected = f.type === "text" ? f.initial
      : (f.type === "int" ? Math.trunc(Number(f.initial)) : Number(f.initial));
    assert.strictEqual(out[f.param], expected, f.param);
  }
});
