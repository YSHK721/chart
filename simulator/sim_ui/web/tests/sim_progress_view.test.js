// 結果待ちの進み具合（sim_progress_view）の検定（2026-09-27 依頼者指示）。
//
// 固定する不変条件:
//   1. 値（0〜100）はバーの長さと％の文字の両方に出る。
//   2. null は「準備中」で、長さを名乗らない（0% を捏造しない）。
//   3. 100 は「結果を書き出し中」（足はすべて処理済み・終端はサーバが決める）。
//   4. 掲示面（M6）は終端でバーを隠し、実行中は応答の progress をそのまま出す。
import { test } from "node:test";
import assert from "node:assert/strict";

import { createSimProgressView, progressLabel, PROGRESS_TEXT } from "../js/adapter/front/sim_progress_view.js";
import { createSimRunStatusView } from "../js/adapter/front/sim_run_status_view.js";
import { showRunningAndWaitForCompletion } from "../js/adapter/front/job_completion_wait.js";

function fakeDoc() {
  const make = (tag) => ({
    tagName: tag, className: "", textContent: "", style: {}, children: [], firstChild: null,
    appendChild(c) { this.children.push(c); this.firstChild = this.children[0]; return c; },
    insertBefore(c) { this.children.unshift(c); this.firstChild = c; return c; },
  });
  return { createElement: make };
}

test("progressLabel spells the value, the preparing state and the writing state", () => {
  assert.equal(progressLabel(null), PROGRESS_TEXT.preparing);
  assert.equal(progressLabel(0), "0%");
  assert.equal(progressLabel(42), "42%");
  assert.match(progressLabel(100), /^100%（結果を書き出し中/);
});

test("show puts the value into both the bar width and the percent text", () => {
  const doc = fakeDoc();
  const view = createSimProgressView({ doc });
  view.mount(doc.createElement("div"));
  view.show(37);
  assert.equal(view.elements.fill.style.width, "37%");
  assert.equal(view.elements.label.textContent, "37%");
  assert.equal(view.elements.root.ariaValueNow, "37");
  assert.ok(!/hidden|indeterminate/.test(view.elements.root.className));
});

test("null shows the preparing state without claiming a length", () => {
  const doc = fakeDoc();
  const view = createSimProgressView({ doc });
  view.mount(doc.createElement("div"));
  view.show(null);
  assert.match(view.elements.root.className, /sim-progress--indeterminate/);
  assert.equal(view.elements.fill.style.width, "");
  assert.equal(view.elements.label.textContent, PROGRESS_TEXT.preparing);
  assert.equal(view.elements.root.ariaValueNow, null);
});

test("hide hides the bar", () => {
  const doc = fakeDoc();
  const view = createSimProgressView({ doc });
  view.mount(doc.createElement("div"));
  view.show(10);
  view.hide();
  assert.match(view.elements.root.className, /sim-progress--hidden/);
});

test("the run status panel shows the server progress while running and hides it at the end", () => {
  const doc = fakeDoc();
  const status = createSimRunStatusView({ doc });
  status.mount(doc.createElement("div"));
  const bar = status.elements.progressNode;
  status.showJobState({ status: "running", terminal: false, progress: 64 });
  assert.equal(bar.children[1].textContent, "64%");
  status.showJobState({ status: "completed", terminal: true, progress: null });
  assert.match(bar.className, /sim-progress--hidden/);
});

test("the completion wait feeds each update's progress to the bar", () => {
  const shown = [];
  const progress = { show: (p) => shown.push(p), hide: () => shown.push("hide") };
  let notify = null;
  showRunningAndWaitForCompletion({
    view: { showMessage() {} },
    statusClient: { watch: (_id, fn) => { notify = fn; return () => {}; } },
    jobId: "j", reload() {}, progress,
  });
  notify({ status: "running", terminal: false, progress: null });
  notify({ status: "running", terminal: false, progress: 12 });
  notify({ status: "completed", terminal: true, progress: null });
  assert.deepEqual(shown, [null, null, 12, "hide"]);
});
