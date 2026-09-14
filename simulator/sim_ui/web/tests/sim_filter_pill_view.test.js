// sim_filter_pill_view（抽出フィルタのピル/件数・F-7 点18）の単体テスト。
//
// 移植元 main.js:151-162 の subscribeFilter の DOM 副作用（#clearFilter の表示切替・
// #detailCount の件数文言・✕ クリックで解除）を担う。main.js は boot entry で import
// 不可のため、この結線は v5 流の新規（複製ではない・doc §流用）。
//
// 固定する不変条件:
//   1. filter あり → ピル可視（inline-block）＋件数文言 ` · 抽出 N 件 (label)`。
//   2. filter なし（null）→ ピル非可視（none）＋件数文言は空。
//   3. ✕（#clearFilter）クリックで linkage.applyFilter(null, "")。
import { test } from "node:test";
import assert from "node:assert/strict";

import { fakeDoc } from "./_fakes.js";
import { createSimFilterPillView } from "../js/adapter/front/sim_filter_pill_view.js";

function wired() {
  const doc = fakeDoc();
  const clearFilter = doc.createElement("span");
  clearFilter.id = "clearFilter";
  const detailCount = doc.createElement("span");
  detailCount.id = "detailCount";
  const applied = [];
  const linkage = { applyFilter: (ids, label) => applied.push([ids, label]) };
  const view = createSimFilterPillView();
  view.wire({ clearFilter, detailCount, linkage });
  return { clearFilter, detailCount, linkage, applied, view };
}

// --- 1. filter あり --------------------------------------------------------------

test("an active filter shows the pill and the extraction count", () => {
  const { clearFilter, detailCount, view } = wired();
  view.reflect(new Set([1, 2, 3]), "Mon 9:00");
  assert.equal(clearFilter.style.display, "inline-block");
  assert.equal(detailCount.textContent, " · 抽出 3 件 (Mon 9:00)");
});

test("a missing label still renders the count (label 欠落耐性)", () => {
  const { detailCount, view } = wired();
  view.reflect(new Set([1]), undefined);
  assert.equal(detailCount.textContent, " · 抽出 1 件 ()");
});

// --- 2. filter なし --------------------------------------------------------------

test("clearing the filter hides the pill and empties the count", () => {
  const { clearFilter, detailCount, view } = wired();
  view.reflect(new Set([1]), "x");
  view.reflect(null, "");
  assert.equal(clearFilter.style.display, "none");
  assert.equal(detailCount.textContent, "");
});

// --- 3. ✕ クリックで解除 --------------------------------------------------------

test("clicking the pill clears the filter through the linkage", () => {
  const { clearFilter, applied } = wired();
  clearFilter.onclick();
  assert.deepEqual(applied, [[null, ""]]);
});

// --- 欠落耐性 -------------------------------------------------------------------

test("wire tolerates missing elements (呼び出し順・部分 DOM に依存しない)", () => {
  const view = createSimFilterPillView();
  assert.doesNotThrow(() => view.wire({ clearFilter: null, detailCount: null, linkage: null }));
  assert.doesNotThrow(() => view.reflect(new Set([1]), "x"));
  assert.doesNotThrow(() => view.reflect(null, ""));
});
