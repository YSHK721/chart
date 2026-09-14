// sim_date_picker_view（日付選択カレンダー・依頼者参照デザイン 2026-09-06）の単体テスト。
//
// 固定する不変条件:
//   1. 参照デザインの構成要素（月送りヘッダ・曜日行 Mo〜Su・6×7 格子・隣接月の淡色）が
//      過不足なく出る。確定ボタン・Cancel は出さない（依頼者裁定 2026-09-06: 即時確定）。
//   2. 格子は月曜始まりで正しい（参照画像と同じ 2021 年 4 月で先頭・末尾・隣接月数を固定）。
//   3. 日付クリックが即時に onCommit(token) を発行して閉じる。外側の押下は無発行で閉じる。
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

/** 文書全体のイベントを発火する（外側クリック判定は doc のリスナで受ける）。 */
const fireDoc = (doc, ev, event) => (doc._listeners[ev] || []).slice().forEach((f) => f(event));

function opened({ value = "", today = () => new Date(2021, 3, 15) } = {}) {
  const { doc, counter } = countingDoc();
  const view = createSimDatePickerView({ doc, today });
  const committed = [];
  // anchor は body 直下の 1 要素（外側クリックの「外側」を body 上に作れるようにする）
  const anchor = doc.createElement("div");
  doc.body.appendChild(anchor);
  counter.created = 0;
  view.openFor({ anchor, value, onCommit: (t) => committed.push(t) });
  const pop = byClass(doc.body, "cal-pop")[0];
  return { doc, counter, view, committed, pop, anchor, host: doc.body };
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
  // 確定ボタン・Cancel は出さない（日付クリックで即時確定・依頼者裁定 2026-09-06）
  assert.equal(byClass(pop, "cal-choose").length, 0, "確定ボタンが残っています");
  assert.equal(byClass(pop, "cal-cancel").length, 0, "Cancel が残っています");
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
});

test("the arrows move one month per click (both directions)", () => {
  const { pop } = opened();
  fire(byClass(pop, "cal-next")[0]);
  assert.equal(byClass(pop, "cal-title")[0].textContent, "May 2021");
  fire(byClass(pop, "cal-prev")[0]);
  fire(byClass(pop, "cal-prev")[0]);
  assert.equal(byClass(pop, "cal-title")[0].textContent, "March 2021");
});

test("picking an adjacent-month day commits that month's token (隣接月も 1 クリック)", () => {
  const { committed, pop, view } = opened({ value: "2021.04.09" });
  fire(byClass(pop, "cal-day").find((d) => d.dataset.token === "2021.03.30"));
  assert.deepEqual(committed, ["2021.03.30"]);
  assert.equal(view.isOpen(), false);
});

// --- 3. 即時確定と外側クリック ---------------------------------------------------

test("clicking a day commits immediately and closes (確定ボタンなし)", () => {
  const { view, committed, pop, host } = opened({ value: "2021.04.09" });
  fire(byClass(pop, "cal-day").find((d) => d.dataset.token === "2021.04.17"));
  assert.deepEqual(committed, ["2021.04.17"]);
  assert.equal(view.isOpen(), false);
  assert.equal(byClass(host, "cal-pop").length, 0);
});

test("a pointer-down outside closes without committing", () => {
  const { doc, view, committed, host } = opened({ value: "2021.04.09" });
  const outside = doc.createElement("div");
  host.appendChild(outside);
  fireDoc(doc, "mousedown", { target: outside });
  assert.deepEqual(committed, []);
  assert.equal(view.isOpen(), false);
  assert.equal(byClass(host, "cal-pop").length, 0);
});

test("a pointer-down inside the calendar or on the opening box keeps it open", () => {
  const { doc, view, pop, anchor } = opened();
  fireDoc(doc, "mousedown", { target: byClass(pop, "cal-next")[0] });  // 月送りは操作の途中
  assert.equal(view.isOpen(), true);
  fireDoc(doc, "mousedown", { target: anchor });                        // 箱＝トグルの担当
  assert.equal(view.isOpen(), true);
});

test("closing removes the document listener (残置リスナゼロ)", () => {
  const { doc, view } = opened();
  view.close();
  assert.equal((doc._listeners.mousedown || []).length, 0, "閉じた後も doc リスナが残っています");
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
