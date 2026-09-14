// sim_segment_view（区間トグル・F-7 点15）の単体テスト（node:test・fake DOM）。
//
// 固定する不変条件（**縮退規則の唯一の所有者**）:
//   1. 区間が 2 つ以上のときだけ #segSel＋.segbtn を生成する。
//   2. 区間が 1 つ（sim 実ジョブ＝single）なら**何も生成しない**（P9: #segSel 不在）。
//      縮退の判断はここ 1 か所だけが持つ（sim_display_view も合成根も別途判断しない）。
//   3. 現在区間の segbtn だけが .on。非現在をクリックすると onSelect(key)。現在の
//      クリックは通知しない（移植元 main.js buildSegToggle: `b.dataset.seg !== CUR_SEG`）。
//   4. setCurrent(key) で .on を張り替える（移植元 selectSegment の segbtn 部）。
import { test } from "node:test";
import assert from "node:assert/strict";

import { fakeDoc, findById, flatten } from "./_fakes.js";
import { createSimSegmentView } from "../js/adapter/front/sim_segment_view.js";

const hasClass = (el, c) => String(el.className || "").split(/\s+/).includes(c);
const isOn = (el, c) => el.classList.contains(c);

function render(segKeys, current, onSelect = () => {}) {
  const doc = fakeDoc();
  const host = doc.body;
  const view = createSimSegmentView({ doc });
  view.render({ host, segKeys, current, onSelect });
  return { doc, host, view };
}

const segbtns = (host) => flatten(host).filter((n) => hasClass(n, "segbtn"));

// --- 1/2. 縮退規則 --------------------------------------------------------------

test("a single-segment run builds no #segSel (P9: 区間トグル不在)", () => {
  const { host } = render(["single"], "single");
  assert.equal(findById(host, "segSel"), null);
  assert.equal(host.children.length, 0);
});

test("two segments build the #segSel toggle", () => {
  const { host } = render(["is", "oos"], "is");
  assert.ok(findById(host, "segSel"), "#segSel が生成されていない");
  assert.equal(segbtns(host).length, 2);
});

test("elements.root is null in the degenerate case", () => {
  const { view } = render(["single"], "single");
  assert.equal(view.elements.root, null);
});

// --- 3. 現在区間の .on と選択通知 ------------------------------------------------

test("only the current segment button carries .on", () => {
  const { host } = render(["is", "oos"], "oos");
  const on = segbtns(host).filter((b) => isOn(b, "on")).map((b) => b.dataset.seg);
  assert.deepEqual(on, ["oos"]);
});

test("clicking a non-current segment notifies onSelect with its key", () => {
  const seen = [];
  const { host } = render(["is", "oos"], "is", (k) => seen.push(k));
  const oos = segbtns(host).find((b) => b.dataset.seg === "oos");
  oos._listeners.click[0]();
  assert.deepEqual(seen, ["oos"]);
});

test("clicking the current segment does not notify (main.js buildSegToggle)", () => {
  const seen = [];
  const { host } = render(["is", "oos"], "is", (k) => seen.push(k));
  const is = segbtns(host).find((b) => b.dataset.seg === "is");
  is._listeners.click[0]();
  assert.deepEqual(seen, []);
});

// --- 4. setCurrent が .on を張り替える ------------------------------------------

test("setCurrent moves the .on marker to the new segment", () => {
  const { host, view } = render(["is", "oos"], "is");
  view.setCurrent("oos");
  const on = segbtns(host).filter((b) => isOn(b, "on")).map((b) => b.dataset.seg);
  assert.deepEqual(on, ["oos"]);
});

test("setCurrent is a no-op in the degenerate case (no buttons to move)", () => {
  const { view } = render(["single"], "single");
  assert.doesNotThrow(() => view.setCurrent("single"));
});

// --- 器の構成 -------------------------------------------------------------------

test("the toggle carries a seg-label and one button per key (移植元 index.html:16-20)", () => {
  const { host } = render(["is", "oos"], "is");
  const seg = findById(host, "segSel");
  assert.ok(seg.children.some((c) => hasClass(c, "seg-label")), "seg-label が無い");
  assert.deepEqual(segbtns(host).map((b) => b.dataset.seg), ["is", "oos"]);
});

test("the view uses the injected doc only (no global document)", () => {
  const { host } = render(["is", "oos"], "is");
  assert.ok(flatten(host).every((el) => typeof el.appendChild === "function"));
});
