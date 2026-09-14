// linkage.js 純ロジック単体テスト（node:test・DOM 非依存）。
// 対象: 双方向ハイライト中枢の状態遷移（hoverTradeId / activeFilter）と購読通知。
// 設計（詳細設計 §11.1 linkage.js / アーキ指針 §3）: linkage は単一の hover 状態を保持し、
//   table / chart はこれを import（一方向）。DOM 副作用は購読者（main.js が登録）に委譲し、
//   linkage 自体は DOM 非依存の純状態機械として切り出す（テスト容易化）。
import { test } from "node:test";
import assert from "node:assert/strict";

import { createLinkage } from "../linkage.js";

test("setHover updates hoverTradeId to the given id", () => {
  // Arrange
  const lk = createLinkage();
  // Act
  lk.setHover(7, "table");
  // Assert
  assert.equal(lk.hoverTradeId, 7);
});

test("setHover(null) clears hoverTradeId back to null", () => {
  // Arrange
  const lk = createLinkage();
  lk.setHover(3, "chart");
  // Act
  lk.setHover(null, "chart");
  // Assert
  assert.equal(lk.hoverTradeId, null);
});

test("setHover is idempotent: same id does not re-notify subscribers", () => {
  // Arrange
  const lk = createLinkage();
  let calls = 0;
  lk.subscribe(() => { calls += 1; });
  // Act
  lk.setHover(5, "table");
  lk.setHover(5, "chart"); // 同一 id → 通知しない（試作 setHover の早期 return 踏襲）
  // Assert
  assert.equal(calls, 1);
});

test("setHover notifies subscribers with (id, source)", () => {
  // Arrange
  const lk = createLinkage();
  const seen = [];
  lk.subscribe((id, source) => seen.push([id, source]));
  // Act
  lk.setHover(9, "chart");
  // Assert
  assert.deepEqual(seen, [[9, "chart"]]);
});

test("applyFilter with a non-empty Set sets activeFilter to that Set", () => {
  // Arrange
  const lk = createLinkage();
  const ids = new Set([1, 2, 3]);
  // Act
  lk.applyFilter(ids, "hour 9:00");
  // Assert
  assert.equal(lk.activeFilter, ids);
});

test("applyFilter with null or empty Set resets activeFilter to null", () => {
  // Arrange
  const lk = createLinkage();
  lk.applyFilter(new Set([1]), "x");
  // Act
  lk.applyFilter(null, "");
  // Assert (試作 applyFilter: ids && ids.size ? ids : null)
  assert.equal(lk.activeFilter, null);
  // 空 Set も null 化
  lk.applyFilter(new Set([1]), "x");
  lk.applyFilter(new Set(), "empty");
  assert.equal(lk.activeFilter, null);
});

test("applyFilter notifies filter subscribers with (activeFilter, label)", () => {
  // Arrange
  const lk = createLinkage();
  const seen = [];
  lk.subscribeFilter((filter, label) => seen.push([filter, label]));
  const ids = new Set([2, 4]);
  // Act
  lk.applyFilter(ids, "wday Mon");
  // Assert
  assert.equal(seen.length, 1);
  assert.equal(seen[0][0], ids);
  assert.equal(seen[0][1], "wday Mon");
});
