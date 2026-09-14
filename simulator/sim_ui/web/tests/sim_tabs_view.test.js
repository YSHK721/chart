// sim_tabs_view（下部タブ帯＋ペイン器・F-7）の単体テスト（node:test・fake DOM）。
//
// 固定する不変条件:
//   1. 器（.mv-tabs＋.mv-body）と、**宣言 `SIM_TAB_NAMES` と過不足なく一致する**ペイン群を
//      View が生成する。graph/report は流用しない（YAGNI・doc §流用）。
//
// **名前の一覧をこのファイルへ書かない**（RUN_TRACE_BASIC_DESIGN §9.5 の根本是正）:
//   是正前はタブ名の配列リテラルと `panes.length === 4` を手書きで固定していた。`SIM_TAB_NAMES` は宣言駆動なのに検定側が写しを持っていたため、タブを
//   1 枚足すと「実装が正しくても検定が赤くなる」。赤くなること自体は良いが、赤の理由が
//   「宣言と生成物が食い違った」ではなく「**写しが古い**」だったのが欠陥である
//   （`file_job_ledger` の手書き列挙と同型・同じ是正）。
//   検定が表明すべきは **宣言 ⇔ 生成物の全単射**であって、宣言の中身ではない。
//   これで検出力は落ちない——むしろ上がる。是正前は「宣言に名前を足したがタブを生成
//   しない」実装が緑のままだった（写しが古いことしか見ていない）。
//
//   2. **.mv-body を生成する**。移植元 style.css:86 の `.mv-pane{position:absolute;inset:0}` は
//      `.mv-body{position:relative}` という位置指定祖先が無いとビューポート基準へ落ち、全面を
//      覆う（Phase 4 事故と同型・実測 2026-08-11）。器の中に閉じ込める祖先を必ず出す。
//   3. タブクリックで active 切替＋該当ペインのみ可視（他は hidden）。
//   4. activate(name) で同じ切替をコード側から起こせる（初期表示・E2E フック）。
import { test } from "node:test";
import assert from "node:assert/strict";

import { fakeDoc, findById, flatten } from "./_fakes.js";
import { createSimTabsView, SIM_TAB_NAMES } from "../js/adapter/front/sim_tabs_view.js";

function mounted() {
  const doc = fakeDoc();
  const view = createSimTabsView({ doc });
  const root = view.mount(doc.body);
  return { doc, view, root };
}

// 静的な基底 class は className 文字列に出る。active/hidden の**切替状態**は classList
//   （fake DOM では className と別集合）に出る——生成時の class と toggle の状態を混同しない。
const classesOf = (el) => String(el.className || "").split(/\s+/).filter(Boolean);
const hasClass = (el, c) => classesOf(el).includes(c);
const isOn = (el, c) => el.classList.contains(c);

// --- 1/2. 器とペインの構成 ------------------------------------------------------

test("the declaration is a frozen, non-empty, duplicate-free list of names", () => {
  // 宣言そのものの健全性だけを見る（中身は書かない）。
  assert.ok(Object.isFrozen(SIM_TAB_NAMES), "SIM_TAB_NAMES が凍結されていない");
  assert.ok(SIM_TAB_NAMES.length > 0, "宣言が空（以下の全単射が恒真になる）");
  assert.equal(new Set(SIM_TAB_NAMES).size, SIM_TAB_NAMES.length, "重複がある");
  assert.ok(SIM_TAB_NAMES.every((n) => typeof n === "string" && n.length > 0));
});

test("every declared tab carries a non-empty label (無ラベルのタブを出さない)", () => {
  // 宣言に名前を足してラベル表を更新し忘れると `textContent: undefined` の空タブが出る。
  // 生成物から見るので、ラベル表の中身をここへ書き写す必要がない。
  const { root } = mounted();
  const tabs = flatten(root).filter((n) => hasClass(n, "mv-tab"));
  assert.equal(tabs.length, SIM_TAB_NAMES.length);
  for (const tab of tabs) {
    assert.equal(typeof tab.textContent, "string", `${tab.dataset.tab} のラベルが文字列でない`);
    assert.ok(tab.textContent.length > 0, `${tab.dataset.tab} のラベルが空`);
  }
});

test("mount builds a .mv-tabs bar and a .mv-body container", () => {
  const { root } = mounted();
  const nodes = flatten(root);
  assert.ok(nodes.some((n) => hasClass(n, "mv-tabs")), ".mv-tabs が無い");
  assert.ok(nodes.some((n) => hasClass(n, "mv-body")), ".mv-body が無い（絶対配置の祖先）");
});

test("the panes match the declaration exactly (過不足なく・順序も)", () => {
  const { root } = mounted();
  const body = flatten(root).find((n) => hasClass(n, "mv-body"));
  const panes = body.children.filter((c) => hasClass(c, "mv-pane"));
  // 過不足なし: 個数も並びも宣言から導く（数字を書かない）。
  assert.equal(panes.length, SIM_TAB_NAMES.length);
  assert.deepEqual(panes.map((p) => p.dataset.pane), [...SIM_TAB_NAMES]);
});

test("the tabs and the panes are in bijection (帯とペインが食い違わない)", () => {
  const { root } = mounted();
  const nodes = flatten(root);
  const tabNames = nodes.filter((n) => hasClass(n, "mv-tab")).map((t) => t.dataset.tab);
  const paneNames = nodes.filter((n) => hasClass(n, "mv-pane")).map((p) => p.dataset.pane);
  assert.deepEqual(tabNames, paneNames);
  assert.deepEqual(new Set(tabNames), new Set(SIM_TAB_NAMES));
});

test("each tab button maps to a pane name (data-tab)", () => {
  const { root } = mounted();
  const tabs = flatten(root).filter((n) => hasClass(n, "mv-tab"));
  assert.deepEqual(tabs.map((t) => t.dataset.tab), [...SIM_TAB_NAMES]);
});

test("graph and report tabs are not shipped (流用しない・YAGNI)", () => {
  const { root } = mounted();
  const names = flatten(root).filter((n) => hasClass(n, "mv-tab")).map((t) => t.dataset.tab);
  assert.ok(!names.includes("graph"));
  assert.ok(!names.includes("report"));
});

// --- 3. タブ切替（クリック）-----------------------------------------------------

test("clicking a tab activates it and shows only its pane", () => {
  const { root, view } = mounted();
  const tabs = flatten(root).filter((n) => hasClass(n, "mv-tab"));
  const heat = tabs.find((t) => t.dataset.tab === "heat");
  heat._listeners.click[0]();
  assert.ok(isOn(heat, "active"));
  const body = flatten(root).find((n) => hasClass(n, "mv-body"));
  for (const pane of body.children.filter((c) => hasClass(c, "mv-pane"))) {
    assert.equal(isOn(pane, "hidden"), pane.dataset.pane !== "heat");
  }
  // 他タブは active でない（単一活性）。
  assert.deepEqual(tabs.filter((t) => isOn(t, "active")).map((t) => t.dataset.tab), ["heat"]);
});

// --- 4. コード側からの活性化（初期表示）-----------------------------------------

test("activate(name) shows the named pane without a click", () => {
  const { root, view } = mounted();
  view.activate("compare");
  const body = flatten(root).find((n) => hasClass(n, "mv-body"));
  for (const pane of body.children.filter((c) => hasClass(c, "mv-pane"))) {
    assert.equal(isOn(pane, "hidden"), pane.dataset.pane !== "compare");
  }
  const tabs = flatten(root).filter((n) => hasClass(n, "mv-tab"));
  assert.deepEqual(tabs.filter((t) => isOn(t, "active")).map((t) => t.dataset.tab), ["compare"]);
});

test("elements exposes each pane by name (合成根が中身を挿す先)", () => {
  const { view } = mounted();
  for (const name of SIM_TAB_NAMES) {
    assert.ok(view.elements.panes[name], `pane[${name}] が公開されていない`);
    assert.equal(view.elements.panes[name].dataset.pane, name);
  }
});

test("mount returns the same root and is idempotent", () => {
  const { view, root, doc } = mounted();
  const again = view.mount(doc.body);
  assert.equal(again, root);
  assert.equal(doc.body.children.length, 1);
});

test("the view uses the injected doc only (no global document)", () => {
  const { root } = mounted();
  assert.ok(flatten(root).every((el) => typeof el.appendChild === "function"));
});
