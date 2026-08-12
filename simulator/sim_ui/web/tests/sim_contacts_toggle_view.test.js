// sim_contacts_toggle_view（接点マーカー表示トグル・F-7 FR-18）の単体テスト。
//
// 固定する不変条件（移植元 main.js wireContactsToggle と同流儀）:
//   1. 真実源は **renderer**。初期の .on は renderer.contactsVisible() に同期する。
//   2. クリックで renderer.setContactsVisible(!現在) を呼び、その戻り（新 state）で .on を張り替える。
//   3. renderer 側で state を持つ（View は独自 state を持たない・ISSUE-379 の教訓と同型）。
import { test } from "node:test";
import assert from "node:assert/strict";

import { fakeDoc } from "./_fakes.js";
import { createSimContactsToggleView } from "../js/adapter/front/sim_contacts_toggle_view.js";

/** contactsVisible / setContactsVisible だけを持つ renderer ダブル（真実源）。 */
function fakeRenderer(initial = true) {
  return {
    _v: initial,
    contactsVisible() { return this._v; },
    setContactsVisible(v) { this._v = !!v; return this._v; },
  };
}

function wired(initial = true) {
  const doc = fakeDoc();
  const btn = doc.createElement("button");
  btn.id = "toggleContacts";
  const renderer = fakeRenderer(initial);
  const view = createSimContactsToggleView();
  view.wire({ btn, renderer });
  return { btn, renderer, view };
}

const isOn = (el) => el.classList.contains("on");

// --- 1. 初期同期 ----------------------------------------------------------------

test("the button starts .on when the renderer shows contacts", () => {
  assert.equal(isOn(wired(true).btn), true);
});

test("the button starts off when the renderer hides contacts", () => {
  assert.equal(isOn(wired(false).btn), false);
});

// --- 2. クリックでトグル --------------------------------------------------------

test("clicking hides contacts and drops .on", () => {
  const { btn, renderer } = wired(true);
  btn.onclick();
  assert.equal(renderer.contactsVisible(), false);
  assert.equal(isOn(btn), false);
});

test("clicking again shows contacts and restores .on", () => {
  const { btn, renderer } = wired(true);
  btn.onclick();
  btn.onclick();
  assert.equal(renderer.contactsVisible(), true);
  assert.equal(isOn(btn), true);
});

// --- 3. 真実源は renderer（View は独自 state を持たない）------------------------

test("the .on marker follows the renderer's returned state, not a view-local flag", () => {
  const { btn, renderer } = wired(true);
  // renderer が state を持つ。View 側にコピーを作っていれば、外から renderer を変えた
  //   ときに追従できない（ここでは setContactsVisible の戻りを唯一の真実として使う）。
  btn.onclick();
  assert.equal(isOn(btn), renderer.contactsVisible());
});

test("wire without a button does not throw (呼び出し順に依存しない)", () => {
  const view = createSimContactsToggleView();
  assert.doesNotThrow(() => view.wire({ btn: null, renderer: fakeRenderer() }));
});
