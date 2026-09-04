// sim_tabs_view（下部タブ帯＋ペイン器・F-7）の単体テスト（node:test・fake DOM）。
//
// 固定する不変条件:
//   1. 器（.mv-tabs＋.mv-body）と 4 ペイン（detail/heat/compare/glossary）を View が生成する。
//      graph/report は流用しない（YAGNI・doc §流用）。移植元 6 タブではなく共通 4 タブのみ。
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

test("the tab set is the common four (detail/heat/compare/glossary)", () => {
  assert.deepEqual(SIM_TAB_NAMES, ["detail", "heat", "compare", "glossary"]);
});

test("mount builds a .mv-tabs bar and a .mv-body container", () => {
  const { root } = mounted();
  const nodes = flatten(root);
  assert.ok(nodes.some((n) => hasClass(n, "mv-tabs")), ".mv-tabs が無い");
  assert.ok(nodes.some((n) => hasClass(n, "mv-body")), ".mv-body が無い（絶対配置の祖先）");
});

test("the .mv-body carries exactly four .mv-pane children (共通4タブ)", () => {
  const { root } = mounted();
  const body = flatten(root).find((n) => hasClass(n, "mv-body"));
  const panes = body.children.filter((c) => hasClass(c, "mv-pane"));
  assert.equal(panes.length, 4);
  assert.deepEqual(panes.map((p) => p.dataset.pane), SIM_TAB_NAMES);
});

test("each tab button maps to a pane name (data-tab)", () => {
  const { root } = mounted();
  const tabs = flatten(root).filter((n) => hasClass(n, "mv-tab"));
  assert.deepEqual(tabs.map((t) => t.dataset.tab), SIM_TAB_NAMES);
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
