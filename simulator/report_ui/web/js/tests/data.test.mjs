// data.js 純アクセサ単体テスト（node:test・DOM 非依存）。
// 対象: aggOf(data, seg) — segments[seg].agg を防御的に取得する共通アクセサ。
//   graphs.js / heatmap.js に重複していた (data.segments[seg].agg || {}) パターンを集約した
//   read-only アクセサ（R-4 防御: agg 欠落時に {} を返し参照例外を防ぐ）。
import { test } from "node:test";
import assert from "node:assert/strict";

import { aggOf } from "../data.js";

test("aggOf returns the agg object for an existing segment", () => {
  const agg = { entries_hour: { 0: 3 }, heat: [{ wday: "Mon", hour: 0 }] };
  const data = { segments: { is: { agg }, oos: { agg: {} } } };
  assert.equal(aggOf(data, "is"), agg);
});

test("aggOf returns {} when segment.agg is missing (R-4 defense)", () => {
  const data = { segments: { is: { trades: [] } } };
  assert.deepEqual(aggOf(data, "is"), {});
});

test("aggOf returns {} when the segment itself is missing (R-4 defense)", () => {
  const data = { segments: {} };
  assert.deepEqual(aggOf(data, "is"), {});
});

test("aggOf returns {} when data has no segments (R-4 defense)", () => {
  assert.deepEqual(aggOf({}, "is"), {});
  assert.deepEqual(aggOf(undefined, "is"), {});
});
