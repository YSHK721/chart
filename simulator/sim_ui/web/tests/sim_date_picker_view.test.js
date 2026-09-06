// sim_date_picker_view（日付選択カレンダー・依頼者参照デザイン 2026-09-06）の単体テスト。
//
// 固定する不変条件:
//   1. 参照デザインの構成要素（月送りヘッダ・曜日行 Mo〜Su・6×7 格子・隣接月の淡色・
//      Cancel / Choose Date）が過不足なく出る。
//   2. 格子は月曜始まりで正しい（参照画像と同じ 2021 年 4 月で先頭・末尾・隣接月数を固定）。
//   3. 確定（Choose Date）だけが onCommit(token) を発行し、Cancel は発行しない。
//   4. 計算量: 作った DOM − 取り付けた DOM = 0（作って捨てる要素なし）。月送り 1 回の
//      発行は格子 1 面ぶんで、月の長短（入力）に依存しない（オーダーの表明）。
import { test } from "node:test";
import assert from "node:assert/strict";

import { fakeDoc, flatten } from "./_fakes.js";
import { createSimDatePickerView } from "../js/adapter/front/sim_date_picker_view.js";

const hasClass = (el, c) => String((el && el.className) || "").split(/\s+/).includes(c);
const byClass = (root, c) => flatten(root).filter((n) => hasClass(n, c));
const fire = (el, ev = "click") => (el._listeners[ev] || []).forEach((f) => f());

/** createElement の発行数を数える doc（計算量テスト用の Test Spy）。 */
function countingDoc() {
  const doc = fakeDoc();
  const orig = doc.createElement;
  const counter = { created: 0 };
  doc.createElement = (tag) => { counter.created += 1; return orig(tag); };
  return { doc, counter };
}

function opened({ value = "", today = () => new Date(2021, 3, 15) } = {}) {
  const { doc, counter } = countingDoc();
  const view = createSimDatePickerView({ doc, today });
  const committed = [];
  view.openFor({ anchor: doc.body, value, onCommit: (t) => committed.push(t) });
  const pop = byClass(doc.body, "cal-pop")[0];
  return { doc, counter, view, committed, pop, host: doc.body };
}

// --- 1. 参照デザインの構成要素 -------------------------------------------------

test("the popup carries exactly the reference-design parts", () => {
  const { pop } = opened();
  assert.ok(pop, "カレンダーが開いていない");
  assert.equal(byClass(pop, "cal-prev").length, 1);
  assert.equal(byClass(pop, "cal-next").length, 1);
  assert.equal(byClass(pop, "cal-title").length, 1);
  assert.deepEqual(byClass(pop, "cal-weekday").map((n) => n.textContent),
    ["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"]);
  assert.equal(byClass(pop, "cal-day").length, 42, "6 週 × 7 曜の固定格子ではありません");
  assert.equal(byClass(pop, "cal-cancel")[0].textContent, "Cancel");
  assert.equal(byClass(pop, "cal-choose")[0].textContent, "Choose Date");
});

// --- 2. 格子の正当性（参照画像と同じ 2021 年 4 月）------------------------------

test("the grid is Monday-first and pads with adjacent months (April 2021)", () => {
  const { pop } = opened({ value: "2021.04.09" });
  assert.equal(byClass(pop, "cal-title")[0].textContent, "April 2021");
  const days = byClass(pop, "cal-day");
  // 2021-04-01 は木曜: 先頭は 3/29（月）・末尾は 42 枡目の 5/9
  assert.equal(days[0].dataset.token, "2021.03.29");
  assert.equal(days[41].dataset.token, "2021.05.09");
  // 隣接月は 3 月末 3 日＋ 5 月頭 9 日＝ 12 枡が淡色
  assert.equal(days.filter((d) => hasClass(d, "cal-out")).length, 12);
  // 欄の値の日が選択済みで出る
  const selected = days.filter((d) => hasClass(d, "cal-sel"));
  assert.deepEqual(selected.map((d) => d.dataset.token), ["2021.04.09"]);
});

test("an empty value opens at the injected today with nothing selected", () => {
  const { pop } = opened({ value: "" });
  assert.equal(byClass(pop, "cal-title")[0].textContent, "April 2021");
  assert.equal(byClass(pop, "cal-sel").length, 0);
  assert.equal(byClass(pop, "cal-choose")[0].disabled, true, "未選択で確定できています");
});

test("the arrows move one month per click (both directions)", () => {
  const { pop } = opened();
  fire(byClass(pop, "cal-next")[0]);
  assert.equal(byClass(pop, "cal-title")[0].textContent, "May 2021");
  fire(byClass(pop, "cal-prev")[0]);
  fire(byClass(pop, "cal-prev")[0]);
  assert.equal(byClass(pop, "cal-title")[0].textContent, "March 2021");
});

test("picking an adjacent-month day moves the view to that month", () => {
  const { pop } = opened({ value: "2021.04.09" });
  const march = byClass(pop, "cal-day").find((d) => d.dataset.token === "2021.03.30");
  fire(march);
  assert.equal(byClass(pop, "cal-title")[0].textContent, "March 2021");
  assert.deepEqual(byClass(pop, "cal-sel").map((d) => d.dataset.token), ["2021.03.30"]);
});

// --- 3. 確定と取消 --------------------------------------------------------------

test("Choose Date commits the token and closes; the popup does not linger", () => {
  const { view, committed, pop, host } = opened({ value: "2021.04.09" });
  fire(byClass(pop, "cal-day").find((d) => d.dataset.token === "2021.04.17"));
  fire(byClass(pop, "cal-choose")[0]);
  assert.deepEqual(committed, ["2021.04.17"]);
  assert.equal(view.isOpen(), false);
  assert.equal(byClass(host, "cal-pop").length, 0);
});

test("Cancel closes without committing", () => {
  const { view, committed, pop, host } = opened({ value: "2021.04.09" });
  fire(byClass(pop, "cal-day").find((d) => d.dataset.token === "2021.04.17"));
  fire(byClass(pop, "cal-cancel")[0]);
  assert.deepEqual(committed, []);
  assert.equal(view.isOpen(), false);
  assert.equal(byClass(host, "cal-pop").length, 0);
});

test("re-opening the same view replaces its popup instead of stacking (残骸ゼロ)", () => {
  const { view, host } = opened();
  view.openFor({ anchor: host, value: "" });
  assert.equal(byClass(host, "cal-pop").length, 1, "開き直しで popup が積み重なっています");
});

// --- 4. 計算量テスト（規約: 発行した計算 − 出力に使った計算 = 0）------------------
// 回数そのものを仕様に焼き込まない。固定するのは「作って捨てる DOM が無い」ことと、
// 「発行が入力（月の長短）に依存しない」ことである。

test("every element built while opening is attached to the popup (作った − 使った = 0)", () => {
  const { counter, pop } = opened();
  // openFor 中に作った要素数 ＝ popup 部分木の要素数（捨てた要素が 1 つも無い）
  assert.equal(counter.created - flatten(pop).length, 0,
    "組み立て中に作って捨てた DOM があります");
});

test("one navigation issues exactly one grid rebuild, independent of month length (オーダー)", () => {
  // 2 点で固定: 28 日の月（2021-02 非閏）と 31 日の月（2021-07）で発行数が同じ。
  const issues = [];
  for (const value of ["2021.02.09", "2021.07.09"]) {
    const { counter, pop } = opened({ value });
    counter.created = 0;
    fire(byClass(pop, "cal-next")[0]);
    issues.push(counter.created);
    // 発行した分はすべて格子に取り付いている（作って捨てる要素なし）
    assert.equal(counter.created, byClass(pop, "cal-day").length);
  }
  assert.equal(issues[0], issues[1], "月の長短で発行数が変わっています（固定格子のはず）");
});
