// sim_display_view（表示層の器・F-2）の単体テスト（node:test・fake DOM）。
//
// 固定する不変条件:
//   1. 器の DOM は **View 自身が生成し所有する**（HTML ページを 1 枚も触らない・ISSUE-278 #16）。
//   2. id 体系は移植元 report_ui/web/index.html と同一（chart.js / table.js が引く id を
//      そのまま満たす: price-chart / paneBal / paneDD / chartBadge / tradeTable / hSel）。
//   3. style.css は **/sim/report-css/ の実体を link で読む**（見た目を写さない）。
//      link は mount 時に head へ挿し、unmount 時に外す（統合ページへ残さない）。
//   4. mount / unmount は冪等（モード往復で DOM が積み上がらない）。
//   5. 描画できないとき（ジョブ未指定・結果未生成）はメッセージだけを出す（部分描画しない）。
import { test } from "node:test";
import assert from "node:assert/strict";

import { fakeDoc, findById, flatten } from "./_fakes.js";
import { SIM_DISPLAY_IDS, createSimDisplayView } from "../js/adapter/front/sim_display_view.js";

function mounted() {
  const doc = fakeDoc();
  const host = doc.body;
  const view = createSimDisplayView({ doc });
  view.mount(host);
  return { doc, host, view };
}

// --- 1/2. 器の DOM と id 体系 ---------------------------------------------------

test("mount builds the container subtree under the given host", () => {
  const { host, view } = mounted();
  assert.equal(host.children.length, 1);
  assert.equal(host.children[0].id, SIM_DISPLAY_IDS.root);
  assert.equal(view.isMounted(), true);
});

test("the built DOM carries the report_ui id set (chart.js / table.js の引く id)", () => {
  const { host } = mounted();
  const root = host.children[0];
  for (const id of ["price-chart", "paneBal", "paneDD", "chartBadge", "tradeTable", "hSel"]) {
    assert.ok(findById(root, id), `#${id} が生成されていません`);
  }
});

// ヘッダの id は移植元と同じ `topbar` でなければならない。移植元 style.css:26-35 の
//   `#topbar { display:flex; padding:6px 12px; ... }` / `#topbar h1 { font-size:14px }` は
//   **id セレクタ**なので、別名（simTopbar）を付けると 1 つも当たらない。実測差分:
//   display block（flex でない）・h1 24px（14px でない）・hSel が 2 行目へ落ちる・ヘッダ高 66px（38px）。
//   見た目を合わせるために新しい CSS を書くのは複製（§11.4）なので、id を合わせる。
test("the header uses the report_ui id so #topbar rules apply", () => {
  const { host } = mounted();
  assert.equal(SIM_DISPLAY_IDS.topbar, "topbar");
  assert.ok(findById(host.children[0], "topbar"), "#topbar が生成されていません");
});

test("the header carries the h1 and the hSel label (移植元 index.html:13-24 と同じ構成)", () => {
  const { host } = mounted();
  const header = findById(host.children[0], "topbar");
  assert.deepEqual(header.children.map((c) => c.tagName), ["H1", "SPAN"]);
  assert.equal(header.children[1].id, SIM_DISPLAY_IDS.hSel);
});

test("SIM_DISPLAY_IDS matches the report_ui index.html id names", () => {
  assert.equal(SIM_DISPLAY_IDS.chart, "price-chart");
  assert.equal(SIM_DISPLAY_IDS.bal, "paneBal");
  assert.equal(SIM_DISPLAY_IDS.dd, "paneDD");
  assert.equal(SIM_DISPLAY_IDS.badge, "chartBadge");
  assert.equal(SIM_DISPLAY_IDS.table, "tradeTable");
  assert.equal(SIM_DISPLAY_IDS.hSel, "hSel");
});

test("the trade table ships with a thead and a tbody (table.js が querySelector する)", () => {
  const { host } = mounted();
  const table = findById(host.children[0], "tradeTable");
  assert.deepEqual(table.children.map((c) => c.tagName), ["THEAD", "TBODY"]);
});

// 実 UI 実測（2026-08-11・統合 UI :8000）で判明した壊れ方の回帰の壁:
//   `.mv-pane` は移植元 style.css:86 で `position:absolute; inset:0` である。効かせるには
//   移植元 index.html:56 の `.mv-body { position: relative }`（＝位置指定された祖先）が要る。
//   sim の器にはタブが無く `.mv-body` も無いので、絶対配置はビューポート基準へ落ち、
//   **統合ページ全面を覆って既存ツールバーのクリックを飲み込んだ**（elementFromPoint が
//   `#enter-sim` ではなく明細の `th` を返す＝モードから出られない）。
//   sim はタブ機構を使わない（YAGNI）。よってタブ用の class を借りない。
test("the detail container does not borrow the tab-pane class (実 UI で全面を覆った)", () => {
  const { host } = mounted();
  for (const el of flatten(host.children[0])) {
    assert.ok(!String(el.className || "").split(/\s+/).includes("mv-pane"),
      "mv-pane は .mv-body（position:relative）が無いと全面を覆う");
  }
});

test("elements exposes the live element references for the renderer", () => {
  const { host, view } = mounted();
  const root = host.children[0];
  assert.equal(view.elements.chart, findById(root, "price-chart"));
  assert.equal(view.elements.bal, findById(root, "paneBal"));
  assert.equal(view.elements.dd, findById(root, "paneDD"));
  assert.equal(view.elements.badge, findById(root, "chartBadge"));
  assert.equal(view.elements.table, findById(root, "tradeTable"));
  assert.equal(view.elements.hSel, findById(root, "hSel"));
});

test("the view creates every element through the injected doc (no global document)", () => {
  const { host } = mounted();
  // fake doc の createElement 由来の要素だけで構成されている＝グローバル document 非依存。
  assert.ok(flatten(host.children[0]).every((el) => typeof el.appendChild === "function"));
});

// --- 3. CSS は View の責務ではない（裁定 B）--------------------------------------
// 移植元 style.css の link は**子文書 report_view.html** が持つ。View が読み込む文書へ
// 勝手に link を挿すと、その文書が統合ページだったときに既存 UI の見た目を変えてしまう
// （実測: body 背景 19,23,34 → 14,17,23／font "Helvetica Neue",Arial → Tahoma）。

test("the view never touches the document head (CSS は子文書が持つ)", () => {
  const { doc, view } = mounted();
  assert.deepEqual(doc.head.children, []);
  view.unmount();
  assert.deepEqual(doc.head.children, []);
});

// --- 4. mount / unmount の冪等（モード往復で積み上がらない）-----------------------

test("mounting twice does not duplicate the container", () => {
  const { host, view } = mounted();
  view.mount(host);
  assert.equal(host.children.length, 1);
});

test("unmount detaches the container and is idempotent", () => {
  const { host, view } = mounted();
  view.unmount();
  view.unmount();
  assert.deepEqual(host.children, []);
  assert.equal(view.isMounted(), false);
});

test("mount after unmount rebuilds the container (往復しても壊れない)", () => {
  const { host, view } = mounted();
  view.unmount();
  view.mount(host);
  assert.equal(host.children.length, 1);
  assert.ok(findById(host.children[0], "price-chart"));
});

// --- 5. 描画できないときはメッセージだけ（部分描画しない）------------------------

test("showMessage writes the text into the message element", () => {
  const { host, view } = mounted();
  view.showMessage("ジョブ未指定");
  const el = findById(host.children[0], SIM_DISPLAY_IDS.message);
  assert.equal(el.textContent, "ジョブ未指定");
  assert.equal(el.classList.contains("hidden"), false);
});

test("the message element starts hidden and empty", () => {
  const { host } = mounted();
  const el = findById(host.children[0], SIM_DISPLAY_IDS.message);
  assert.equal(el.textContent, "");
  assert.equal(el.classList.contains("hidden"), true);
});

test("clearMessage hides the message again", () => {
  const { host, view } = mounted();
  view.showMessage("結果未生成");
  view.clearMessage();
  const el = findById(host.children[0], SIM_DISPLAY_IDS.message);
  assert.equal(el.textContent, "");
  assert.equal(el.classList.contains("hidden"), true);
});

test("showMessage before mount does not throw (呼び出し順に依存しない)", () => {
  const doc = fakeDoc();
  const view = createSimDisplayView({ doc });
  assert.doesNotThrow(() => view.showMessage("ジョブ未指定"));
});
