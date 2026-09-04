// layout.js 純ロジック単体テスト（node:test・DOM 非依存）。
// 対象（パリティ点 6,10,11）: 最大化トグルの状態遷移（normal/chart/detail）。
import { test } from "node:test";
import assert from "node:assert/strict";

import { nextLayoutMode } from "../layout.js";

test("nextLayoutMode maximizes from normal", () => {
  assert.equal(nextLayoutMode("normal", "chart"), "chart");
  assert.equal(nextLayoutMode("normal", "detail"), "detail");
});

test("nextLayoutMode restores to normal when re-clicking the active mode", () => {
  assert.equal(nextLayoutMode("chart", "chart"), "normal");
  assert.equal(nextLayoutMode("detail", "detail"), "normal");
});

test("nextLayoutMode switches between maximized modes", () => {
  // chart 最大化中に detail ボタン → detail へ（normal を経由しない）。
  assert.equal(nextLayoutMode("chart", "detail"), "detail");
  assert.equal(nextLayoutMode("detail", "chart"), "chart");
});
