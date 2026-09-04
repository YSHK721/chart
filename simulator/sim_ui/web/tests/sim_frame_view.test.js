// sim_frame_view（統合ページ側の器・F-2b）の単体テスト（node:test・fake DOM）。
//
// なぜ iframe か（実 UI 実測 2026-08-11 → 裁定 B）:
//   移植元 style.css は `body { background / color / font / height:100vh / display:flex }`
//   を持つ全画面レイアウトである。これを統合ページへ link すると **既存 UI の body 背景・
//   フォント・文字色が変わった**（実測: bodyBg 19,23,34 → 14,17,23／font "Helvetica Neue"
//   → Tahoma）。sim だけが読む CSS を統合ページへ載せない唯一の構造的手段が別文書
//   （iframe）である。Shadow DOM は :root カスタムプロパティの解決が未実証のため採らない。
//
// 固定する不変条件:
//   1. 器（#sim-display）と iframe は View が生成し所有する（HTML を 1 枚も触らない）。
//   2. iframe の src は `/sim/report_view.html?job=<id>`（job は URL で子へ渡す）。
//   3. **統合ページへ移植元 style.css を link しない**（波及遮断の要）。寸法だけを
//      sim 所有 CSS（/sim/css/sim_display.css）で与える。
//   4. mount / unmount は冪等（モード往復で器も link も積み上がらない）。
//   5. 子ページ（contentWindow）は同一オリジンで直参照できる形で公開する。
import { test } from "node:test";
import assert from "node:assert/strict";

import { fakeDoc } from "./_fakes.js";
import {
  SIM_FRAME_CSS,
  SIM_REPORT_VIEW_PATH,
  createSimFrameView,
  waitForContent,
} from "../js/adapter/front/sim_frame_view.js";


function mounted(jobId = "job-1") {
  const doc = fakeDoc();
  const view = createSimFrameView({ doc });
  view.mount(doc.body, jobId);
  return { doc, view, host: doc.body };
}

// --- 1. 器と iframe の所有 --------------------------------------------------------

test("mount builds the sim-display container under the given host", () => {
  const { host } = mounted();
  assert.equal(host.children.length, 1);
  assert.equal(host.children[0].id, "sim-display");
});

test("the container holds exactly one iframe", () => {
  const { host } = mounted();
  const frames = host.children[0].children.filter((c) => c.tagName === "IFRAME");
  assert.equal(frames.length, 1);
});

// --- 2. 子ページの URL（job は URL で渡す）----------------------------------------

test("the iframe points at the sim report view with the job id", () => {
  const { host } = mounted("abc123");
  const frame = host.children[0].children[0];
  assert.equal(frame.src, `${SIM_REPORT_VIEW_PATH}?job=abc123`);
});

test("the job id is percent-encoded into the query", () => {
  const { host } = mounted("a b&c");
  const frame = host.children[0].children[0];
  assert.equal(frame.src, `${SIM_REPORT_VIEW_PATH}?job=${encodeURIComponent("a b&c")}`);
});

test("no job id still loads the child (子が『ジョブ未指定』を掲示する)", () => {
  const doc = fakeDoc();
  const view = createSimFrameView({ doc });
  view.mount(doc.body, null);
  assert.equal(doc.body.children[0].children[0].src, SIM_REPORT_VIEW_PATH);
});

// --- 3. 統合ページへ style.css を持ち込まない（裁定 B の要）------------------------

test("mount links only the sim-owned frame stylesheet", () => {
  const { doc } = mounted();
  const links = doc.head.children.filter((c) => c.tagName === "LINK");
  assert.deepEqual(links.map((l) => l.href), [SIM_FRAME_CSS]);
});

test("the report_ui stylesheet is never linked into the host page", () => {
  const { doc } = mounted();
  for (const link of doc.head.children) {
    assert.ok(!String(link.href || "").includes("report-css"),
      "統合ページへ移植元 style.css を link している（波及遮断の破れ）");
  }
});

test("the sim-owned stylesheet is served from the sim origin", () => {
  assert.ok(SIM_FRAME_CSS.startsWith("/sim/"), SIM_FRAME_CSS);
  assert.ok(!SIM_FRAME_CSS.includes("report-css"), SIM_FRAME_CSS);
});

// --- 4. 冪等（モード往復で積み上がらない）----------------------------------------

test("mounting twice does not duplicate the container or the link", () => {
  const { doc, host, view } = mounted();
  view.mount(host, "job-1");
  assert.equal(host.children.length, 1);
  assert.equal(doc.head.children.length, 1);
});

test("unmount removes the container and the stylesheet link", () => {
  const { doc, host, view } = mounted();
  view.unmount();
  assert.deepEqual(host.children, []);
  assert.deepEqual(doc.head.children, []);
  assert.equal(view.isMounted(), false);
});

test("unmount is idempotent", () => {
  const { host, view } = mounted();
  view.unmount();
  assert.doesNotThrow(() => view.unmount());
  assert.deepEqual(host.children, []);
});

test("mount after unmount rebuilds the frame (往復しても壊れない)", () => {
  const { doc, host, view } = mounted();
  view.unmount();
  view.mount(host, "job-2");
  assert.equal(host.children.length, 1);
  assert.equal(host.children[0].children[0].src, `${SIM_REPORT_VIEW_PATH}?job=job-2`);
  assert.equal(doc.head.children.length, 1);
});

// --- 5. 子ページの直参照（同一オリジン）------------------------------------------

test("childWindow exposes the iframe contentWindow", () => {
  const { host, view } = mounted();
  const frame = host.children[0].children[0];
  frame.contentWindow = { marker: "child" };
  assert.deepEqual(view.childWindow(), { marker: "child" });
});

test("childWindow is null before mount and after unmount", () => {
  const doc = fakeDoc();
  const view = createSimFrameView({ doc });
  assert.equal(view.childWindow(), null);
  view.mount(doc.body, "j");
  view.unmount();
  assert.equal(view.childWindow(), null);
});

// --- ISSUE-442: 中身の高さを宿主へ伝える（下部ペインの既定高さの入力）-----------------
// 何を測るか＝投入フォームの版面（#simRunForm）だけ。結果ビューアでは測らない（広い方が
// 読みやすく「収まる高さ」という概念が当てはまらない＝狭める既定を勝手に決めない）。
// いつ測るか＝子が組み立て完了を表明（`__simReportViewReady`）してから。load 直後は
// 組み立て途中の高さになる（実測 2026-08-22: 109px → ペインが 123px で開いた）。

test("contentHeightPx は子文書の投入フォームの高さを返す", () => {
  // Arrange
  const doc = fakeDoc();
  const view = createSimFrameView({ doc });
  view.mount(doc.body, null);
  const form = { scrollHeight: 287 };
  view.frameElement().contentDocument = { getElementById: (id) => (id === "simRunForm" ? form : null) };
  // Act / Assert
  assert.equal(view.contentHeightPx(), 287);
});

test("contentHeightPx はフォームが無ければ null（結果ビューア・未読込）", () => {
  // Arrange
  const doc = fakeDoc();
  const view = createSimFrameView({ doc });
  view.mount(doc.body, "job-1");
  // Assert: contentDocument 未設定（未読込相当）
  assert.equal(view.contentHeightPx(), null);
  // Assert: 読み込めてもフォームが無い（結果ビューア）
  view.frameElement().contentDocument = { getElementById: () => null };
  assert.equal(view.contentHeightPx(), null);
});

test("waitForContent は子の組み立て完了を待ってから 1 回だけ知らせる", () => {
  // Arrange: 3 フレーム目で完了を表明する子。
  const frames = [];
  const raf = (fn) => frames.push(fn);
  const child = { __simReportViewReady: false };
  const seen = [];
  const frame = { childWindow: () => child, contentHeightPx: () => 287 };
  // Act
  waitForContent(frame, raf, (h) => seen.push(h));
  const run = () => { const q = frames.splice(0); q.forEach((fn) => fn()); };
  run(); run();                      // まだ未完了＝知らせない
  assert.deepEqual(seen, []);
  child.__simReportViewReady = true;
  run();                             // 完了を検出 → もう 1 フレーム置く
  assert.deepEqual(seen, []);
  run();                             // ここで測って知らせる
  // Assert
  assert.deepEqual(seen, [287]);
  run();
  assert.deepEqual(seen, [287], "知らせるのは 1 回だけ");
});

test("waitForContent は待ち続けない（表明が来なければ諦める）", () => {
  // Arrange: 一生 ready にならない子。
  const frames = [];
  const raf = (fn) => frames.push(fn);
  const seen = [];
  waitForContent({ childWindow: () => ({}), contentHeightPx: () => 287 }, raf, (h) => seen.push(h));
  // Act: 予約が尽きるまで回す（上限が無ければ無限に回り続ける）。
  let ticks = 0;
  while (frames.length && ticks < 1000) { frames.splice(0).forEach((fn) => fn()); ticks += 1; }
  // Assert
  assert.equal(seen.length, 0);
  assert.ok(ticks < 1000, `予約が尽きない（上限が効いていない）: ${ticks}`);
});
