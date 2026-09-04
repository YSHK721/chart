// 初期区間の選択規則（承認 G の回帰ゲート 7）。
//
// report.json の segments は供給元で 2 通りある:
//   - report_ui の 2 区間払い出し: {"is", "oos"}（挿入順で "is" が先）
//   - sim ジョブの単一 run:        {"single"}（IS/OOS 比較は未実施）
// 表示層はどちらでも**先頭の区間**を描く。キー名を条件に書くと、供給元が増えるたびに
// front を直す義務が生まれる（sim の "single" を足したときに実際そうなる）。
//
// 非整数キーのオブジェクトはプロパティの挿入順が保たれる（ECMAScript の
// OrdinaryOwnPropertyKeys: 整数索引キーだけが昇順へ並べ替えられる）。よって
// Object.keys(segments)[0] は決定的である。**数値キーには適用しない**。
import { test } from "node:test";
import assert from "node:assert/strict";

import { firstSegment } from "../js/adapter/front/report_source_client.js";

test("a two-segment payload starts on the IS segment", () => {
  const payload = { segments: { is: { label: "IS" }, oos: { label: "OOS" } } };
  assert.equal(firstSegment(payload).label, "IS");
});

test("a single-run payload starts on the single segment", () => {
  const payload = { segments: { single: { label: "" } } };
  assert.equal(firstSegment(payload), payload.segments.single);
});

test("the selection does not require the key to be named 'is'", () => {
  const payload = { segments: { run: { label: "RUN" } } };
  assert.equal(firstSegment(payload).label, "RUN");
});

test("insertion order decides when the IS key is absent", () => {
  const payload = { segments: { single: { label: "S" }, extra: { label: "E" } } };
  assert.equal(firstSegment(payload).label, "S");
});

test("an empty or missing segments map selects nothing (部分描画しない)", () => {
  assert.equal(firstSegment({ segments: {} }), null);
  assert.equal(firstSegment({}), null);
  assert.equal(firstSegment(null), null);
});

test("Object.keys keeps insertion order for non-integer keys (前提の実証)", () => {
  // 本テストが依拠する言語仕様そのものを固定する（前提を推測で置かない）。
  const segments = { single: 1, is: 2, oos: 3 };
  assert.deepEqual(Object.keys(segments), ["single", "is", "oos"]);
});
