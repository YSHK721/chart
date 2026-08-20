// 実行状態の掲示面（View・Phase 9 段階 3 S1 M6・§19.6）の単体テスト。
//
// 固定する不変条件:
//   1. `#simRunStatusPanel` を host の配下に組む。
//   2. **操作要素を 1 つも作らない**（INPUT / SELECT / BUTTON が 0 件）。この面は掲示専用で
//      あり、操作は M3（実行指示面）が持つ。ここに 1 つでも操作要素が生えると、画面契約の
//      検定（sim_form_mt5_contract）が「面の外の操作要素」として赤になる。
//   3. 掲示は 4 つの枠（phase / job / state / reason）に閉じ、更新は textContent だけで行う。
//   4. status（サーバ語彙）は**生値のまま**出す。front は日本語ラベル表を持たない。
//   5. 何度掲示しても DOM は増えない（枠は 1 組だけ）。
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { fakeDoc, findById, flatten } from "./_fakes.js";
import { createSimRunStatusView } from "../js/adapter/front/sim_run_status_view.js";

const HERE = dirname(fileURLToPath(import.meta.url));
const MODULE_PATH = join(HERE, "..", "js", "adapter", "front", "sim_run_status_view.js");

const CONTROL_TAGS = ["INPUT", "SELECT", "BUTTON"];

/** 掲示面を組んで返す。 */
function mounted() {
  const doc = fakeDoc();
  const view = createSimRunStatusView({ doc });
  view.mount(doc.body);
  return { doc, view, panel: findById(doc.body, "simRunStatusPanel") };
}

/** 掲示枠のテキストを class から引く（front が持つ唯一の掲示語彙）。 */
function textOf(panel, className) {
  const hit = flatten(panel).find((n) => String(n.className || "").split(/\s+/).includes(className));
  return hit ? String(hit.textContent || "") : null;
}

// --- 1. 器 ------------------------------------------------------------------------

test("mount builds the run status panel under the host", () => {
  // Arrange / Act
  const { doc, panel } = mounted();
  // Assert
  assert.ok(panel, "#simRunStatusPanel が生成されていない");
  assert.equal(panel.parentNode, doc.body, "host の直下に組まれていない");
});

test("mount appends the panel at the end of the host by default", () => {
  // Arrange: 先客が居る host（通常経路では 4 面の後ろに掲示面が並ぶ）
  const doc = fakeDoc();
  const first = doc.createElement("div");
  first.id = "alreadyThere";
  doc.body.appendChild(first);
  // Act
  createSimRunStatusView({ doc }).mount(doc.body);
  // Assert
  assert.deepEqual(doc.body.children.map((c) => c.id), ["alreadyThere", "simRunStatusPanel"]);
});

test("mount({atTop:true}) puts the panel first (§19.6 R2: mount 段の失敗時のみ最上部)", () => {
  // Arrange: 途中まで組めた面が既に居る host
  const doc = fakeDoc();
  const first = doc.createElement("div");
  first.id = "alreadyThere";
  doc.body.appendChild(first);
  // Act
  createSimRunStatusView({ doc }).mount(doc.body, { atTop: true });
  // Assert: 理由は画面の最上部に出す（途中まで組めた面に埋もれさせない）
  assert.deepEqual(doc.body.children.map((c) => c.id), ["simRunStatusPanel", "alreadyThere"]);
});

test("mount({atTop:true}) works on an empty host too (境界値: 先客 0 件)", () => {
  // Arrange / Act
  const doc = fakeDoc();
  createSimRunStatusView({ doc }).mount(doc.body, { atTop: true });
  // Assert
  assert.deepEqual(doc.body.children.map((c) => c.id), ["simRunStatusPanel"]);
});

// --- 2. 投入の掲示（投入中 → 受付）-------------------------------------------------

test("showSubmitting posts the in-flight phase", () => {
  // Arrange
  const { view, panel } = mounted();
  // Act
  view.showSubmitting();
  // Assert
  assert.match(String(textOf(panel, "run-status-phase")), /投入/);
});

test("showAccepted posts the job id and the server status verbatim", () => {
  // Arrange
  const { view, panel } = mounted();
  // Act
  view.showAccepted({ job_id: "j7", status: "received" });
  // Assert
  assert.equal(textOf(panel, "run-status-job"), "j7");
  assert.equal(textOf(panel, "run-status-state"), "received",
    "サーバ語彙を翻訳しています（front は状態のラベル表を持たない）");
});

// --- 3. 投入が拒まれた（400 の理由文をそのまま出す・ISSUE-423 の中核）-----------------

test("showRejected posts the server reason verbatim", () => {
  // Arrange
  const { view, panel } = mounted();
  // Act
  view.showRejected({ message: "settings.tester.Symbol と backtest.symbol が一致しません", status: 400 });
  // Assert
  assert.equal(textOf(panel, "run-status-reason"),
    "settings.tester.Symbol と backtest.symbol が一致しません",
    "サーバの理由文が画面に出ていない（ISSUE-423 の沈黙）");
  assert.equal(textOf(panel, "run-status-state"), "400", "HTTP 状態が出ていない");
});

test("showRejected posts a phase even when the reason is missing (無音にしない)", () => {
  // Arrange
  const { view, panel } = mounted();
  // Act
  view.showRejected({});
  // Assert
  assert.ok(String(textOf(panel, "run-status-phase")).length > 0,
    "理由が無いと画面が完全に無音になります");
});

// --- 4. 実行状態の掲示（終端判定の権威はサーバ・§19.6 R1）----------------------------
// 段階の文言は `terminal`（サーバが配る真偽値）だけで決める。status の値から段階を決めると
// front が終端集合を持つことになり、domain 規則の第 2 実装になる。

test("showJobState posts the raw status while the job is not terminal", () => {
  // Arrange
  const { view, panel } = mounted();
  // Act
  view.showJobState({ status: "running", failure_reason: null, terminal: false });
  // Assert
  assert.equal(textOf(panel, "run-status-state"), "running");
  assert.equal(textOf(panel, "run-status-reason"), "", "理由が無いのに何か出ています");
  assert.match(String(textOf(panel, "run-status-phase")), /実行中/);
});

test("showJobState posts the failure reason once the job is terminal", () => {
  // Arrange
  const { view, panel } = mounted();
  // Act
  view.showJobState({ status: "failed", failure_reason: "N-05: 非対象トークン", terminal: true });
  // Assert
  assert.equal(textOf(panel, "run-status-state"), "failed");
  assert.equal(textOf(panel, "run-status-reason"), "N-05: 非対象トークン");
  assert.match(String(textOf(panel, "run-status-phase")), /終了/);
});

test("showJobState treats a missing terminal flag as not terminal (境界値)", () => {
  // Arrange
  const { view, panel } = mounted();
  // Act: `terminal` を配らない（古い応答）サーバでも段階は「実行中」側に倒す
  view.showJobState({ status: "received" });
  // Assert
  assert.match(String(textOf(panel, "run-status-phase")), /実行中/);
  assert.equal(textOf(panel, "run-status-state"), "received");
});

test("showJobState keeps the accepted job id on screen (どの run の状態かが読めること)", () => {
  // Arrange
  const { view, panel } = mounted();
  view.showAccepted({ job_id: "j9", status: "received" });
  // Act
  view.showJobState({ status: "running", terminal: false });
  // Assert
  assert.equal(textOf(panel, "run-status-job"), "j9",
    "状態更新で job_id が消えています（どの run の状態か分からなくなる）");
});

// --- 4b. 監視を諦めた（ジョブは終わっていないが、状態の取得を止めた）-------------------
// 「実行中…」のまま止まると、利用者は更新を待ち続ける。止まったのは**監視**であって
// ジョブではない——この 2 つを画面で区別する（終端語彙は持ち込まない）。

test("showWatchAbandoned distinguishes a stopped watch from a running job", () => {
  // Arrange
  const { view, panel } = mounted();
  view.showAccepted({ job_id: "j5", status: "received" });
  view.showJobState({ status: "running", terminal: false });
  // Act
  view.showWatchAbandoned({ status: "running", failure_reason: "状態を取得できません (HTTP 502)" });
  // Assert: 直近の状態と諦めた理由は残しつつ、段階は「実行中」でも「終了」でもない
  assert.equal(textOf(panel, "run-status-state"), "running");
  assert.equal(textOf(panel, "run-status-reason"), "状態を取得できません (HTTP 502)");
  assert.equal(textOf(panel, "run-status-job"), "j5");
  const phase = String(textOf(panel, "run-status-phase"));
  assert.ok(phase.length > 0, "段階が空です");
  assert.doesNotMatch(phase, /実行中/, "監視を止めたのに「実行中」のままです（更新を待ち続けます）");
  assert.doesNotMatch(phase, /終了/, "ジョブが終わったと読める文言です（終端はサーバが決める）");
});

// --- 5. 器そのものを組めなかった場合（mount 段の失敗・B4）----------------------------

test("showFatal posts the failure message", () => {
  // Arrange
  const { view, panel } = mounted();
  // Act
  view.showFatal("Tester パネルを構築できません: boom");
  // Assert
  assert.equal(textOf(panel, "run-status-reason"), "Tester パネルを構築できません: boom");
  assert.ok(String(textOf(panel, "run-status-phase")).length > 0);
});

// --- 6. 掲示専用であることの機械強制 ------------------------------------------------

test("the status panel ships no control element at all (画面契約: 操作は M3 が持つ)", () => {
  // Arrange
  const { view, panel } = mounted();
  // Act: 全メソッドを一巡させても操作要素は増えない
  view.showSubmitting();
  view.showAccepted({ job_id: "j1", status: "received" });
  view.showRejected({ message: "x", status: 400 });
  view.showJobState({ status: "running", terminal: false });
  view.showFatal("y");
  // Assert
  const controls = flatten(panel).filter((n) => CONTROL_TAGS.includes(n.tagName));
  assert.deepEqual(controls.map((n) => n.tagName), [],
    "掲示面に操作要素があります（面の外の操作要素として画面契約検定が赤になります）");
});

test("repeated posts do not grow the DOM (枠は 1 組だけ)", () => {
  // Arrange
  const { view, panel } = mounted();
  view.showSubmitting();
  const before = flatten(panel).length;
  // Act
  for (let i = 0; i < 5; i += 1) view.showJobState({ status: "running", terminal: false });
  // Assert
  assert.equal(flatten(panel).length, before, "掲示のたびに要素が増えています");
});

test("posting before mount is a no-op instead of a crash", () => {
  // Arrange
  const view = createSimRunStatusView({ doc: fakeDoc() });
  // Act / Assert: mount 前の掲示で落ちない（合成根の防御が二重の例外を作らない）
  assert.doesNotThrow(() => view.showFatal("boom"));
});

test("the status view writes no terminal status vocabulary of its own (§19.6 R1)", () => {
  // Arrange: 実行されるソースを読む（掲示の語彙が front 側に無いことの機械強制）
  const src = readFileSync(MODULE_PATH, "utf8");
  // Assert
  for (const token of ["completed", "cancelled", "\"failed\"", "'failed'"]) {
    assert.ok(!src.includes(token),
      `${token} を front が書いています（終端集合の第 2 実装＝サーバ語彙の複製）`);
  }
});
