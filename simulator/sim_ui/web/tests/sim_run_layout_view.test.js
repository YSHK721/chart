// sim_run_layout_view（投入フォームの版面・ISSUE-441）の単体テスト（node:test・fake DOM）。
//
// 由来（依頼者指示 2026-08-22・実測 1990×約 260px の下部ペイン）: 4 面を縦へ積み、幅を
//   min(860px, 100%-24px) の中央寄せにしていたため、下部ペインを縮めると Inputs とスタートが
//   画面外へ出て操作できない。横は余っている（フォーム 860px に対し版面 1990px＝左右 57% が空白）。
//   設定と入力を横へ分けるための器がこの View である。
//
// 固定する不変条件:
//   1. 器（版面と 2 列）は View が生成し所有する（HTML も面も触らない）。
//   2. 列は 2 つ（設定 / 入力）で、順序は 設定 → 入力（左が設定）。
//   3. mount / unmount は冪等（往復で器が積み上がらない）。
//   4. 面の実装を 1 つも import しない（`import_source.test.js` が機械強制）。

import { test } from "node:test";
import assert from "node:assert/strict";

import { fakeDoc, findById } from "./_fakes.js";
import {
  SIM_RUN_FORM_ID,
  SIM_RUN_FORM_INPUTS_ID,
  SIM_RUN_FORM_SETTINGS_ID,
  createSimRunLayoutView,
} from "../js/adapter/front/sim_run_layout_view.js";

function mounted() {
  const doc = fakeDoc();
  const view = createSimRunLayoutView({ doc });
  view.mount(doc.body);
  return { doc, view };
}

test("mount は版面と 2 列を作り、設定列 → 入力列 の順に置く", () => {
  // Arrange / Act
  const { doc } = mounted();
  // Assert
  const root = findById(doc.body, SIM_RUN_FORM_ID);
  assert.ok(root, "版面が出ていない");
  assert.deepEqual(root.children.map((c) => c.id),
    [SIM_RUN_FORM_SETTINGS_ID, SIM_RUN_FORM_INPUTS_ID], "左が設定・右が入力");
});

test("面の挿し先は列そのもの（設定 / 入力）", () => {
  // Arrange / Act
  const { view } = mounted();
  // Assert
  assert.equal(view.settingsHost().id, SIM_RUN_FORM_SETTINGS_ID);
  assert.equal(view.inputsHost().id, SIM_RUN_FORM_INPUTS_ID);
});

test("未 mount では挿し先を返さない（黙って body へ落とさない）", () => {
  // Arrange
  const view = createSimRunLayoutView({ doc: fakeDoc() });
  // Assert: 呼出側が「器が無い」ことを判定できる（合成根は host へ縮退する）。
  assert.equal(view.settingsHost(), null);
  assert.equal(view.inputsHost(), null);
  assert.equal(view.isMounted(), false);
});

test("二重 mount で器は増えない", () => {
  // Arrange
  const { doc, view } = mounted();
  // Act
  view.mount(doc.body);
  // Assert
  assert.equal(doc.body.children.length, 1);
});

test("unmount は文書へ何も残さない（二重 unmount も安全）", () => {
  // Arrange
  const { doc, view } = mounted();
  // Act
  view.unmount();
  // Assert
  assert.equal(doc.body.children.length, 0);
  assert.equal(view.isMounted(), false);
  assert.doesNotThrow(() => view.unmount());
});
