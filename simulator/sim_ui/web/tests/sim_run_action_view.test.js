// sim_run_action_view（実行指示面・Phase 9 S5 M3）の単体テスト（fake DOM）。
//
// この面の責務は 2 つだけである: 実行を開始させることと、出来上がった結果への導線を出すこと。
// 何を投入するか（本文の組み立て）は M5、どこへ遷移するか（URL の作り方）は合成根が持つ。
//
// 結果導線の DOM をこの面が所有する理由: S4 までは合成根が `doc.createElement` で直接
// ボタンを生やしていた。合成根が DOM を作り始めると、器の骨格が 2 箇所（View と合成根）に
// 散り、CSS の選択子もパネル id の外へはみ出す。DOM を作るのは View だけにする。
//
// 固定する不変条件:
//   1. 「スタート」ボタンを生成する。
//   2. 押すと onStart が呼ばれる（この面は本文も HTTP も知らない）。
//   3. showResultLink で結果導線が出る。**自動遷移はしない**（ビュー自動介入の禁止）。
//   4. 結果導線を押すと onViewResult に job_id が渡る（遷移先の決定は外）。
//   5. 2 回投入しても導線は 1 つのまま（押すたびにボタンが増えない）。
import { test } from "node:test";
import assert from "node:assert/strict";

import { fakeDoc, findById } from "./_fakes.js";
import { createSimRunActionView } from "../js/adapter/front/sim_run_action_view.js";

function mounted() {
  const doc = fakeDoc();
  const view = createSimRunActionView({ doc });
  view.mount(doc.body);
  return { doc, host: doc.body, view };
}

// --- 1. スタートボタン -------------------------------------------------------------

test("mount builds the start button", () => {
  const { host } = mounted();
  const btn = findById(host, "runStart");
  assert.ok(btn, "#runStart が無い");
  assert.equal(btn.tagName, "BUTTON");
  assert.equal(btn.textContent, "スタート");
});

test("mount ships no result affordance until a job exists (押す前に導線を出さない)", () => {
  const { host } = mounted();
  assert.equal(findById(host, "execViewResult"), null);
});

// --- 2. onStart --------------------------------------------------------------------

test("clicking start invokes onStart", () => {
  const { host, view } = mounted();
  const seen = [];
  view.onStart(() => seen.push(1));
  findById(host, "runStart")._listeners.click[0]();
  assert.equal(seen.length, 1);
});

test("clicking start without a subscriber does not throw", () => {
  const { host } = mounted();
  assert.doesNotThrow(() => findById(host, "runStart")._listeners.click[0]());
});

// --- 3/4. 結果導線（自動遷移しない）-------------------------------------------------

test("showResultLink adds the affordance inside this panel", () => {
  const { host, view } = mounted();
  view.showResultLink("abc");
  const link = findById(host, "execViewResult");
  assert.ok(link, "結果導線が出ていない");
  // 合成根が器の外へ生やすのではなく、この面の中に入っている
  assert.ok(findById(view.elements.root, "execViewResult"), "導線がこの面の外にあります");
});

test("showing the result link does not navigate on its own (ビュー自動介入の禁止)", () => {
  const { view } = mounted();
  const nav = [];
  view.onViewResult((jobId) => nav.push(jobId));
  view.showResultLink("abc");
  assert.deepEqual(nav, [], "投入しただけで遷移しています");
});

test("clicking the result link reports the job id to the subscriber", () => {
  const { host, view } = mounted();
  const nav = [];
  view.onViewResult((jobId) => nav.push(jobId));
  view.showResultLink("abc");
  findById(host, "execViewResult")._listeners.click[0]();
  assert.deepEqual(nav, ["abc"]);
});

// --- 5. 導線は 1 つのまま -----------------------------------------------------------

test("a second submission updates the same affordance (ボタンが増えない)", () => {
  const { host, view } = mounted();
  const nav = [];
  view.onViewResult((jobId) => nav.push(jobId));
  view.showResultLink("first");
  view.showResultLink("second");
  const links = (view.elements.root.children || []).filter((n) => n.id === "execViewResult");
  assert.equal(links.length, 1, "結果導線が増えています");
  findById(host, "execViewResult")._listeners.click[0]();
  assert.deepEqual(nav, ["second"], "導線が最後の job を指していません");
});
