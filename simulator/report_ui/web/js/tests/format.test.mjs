// format.js 純ロジック単体テスト（node:test・DOM 非依存）。
// 対象: fmtT（UNIX 秒・UTC → "YYYY.MM.DD hh:mm:ss"・試作 fmtT 準拠・点15/16/12 で共用）。
import { test } from "node:test";
import assert from "node:assert/strict";

import { fmtT, fmtMoney, cfmt, cfmtLocale, signClass } from "../format.js";

test("fmtT formats a UNIX seconds timestamp in UTC with zero-padding", () => {
  // 2026-04-15 03:07:09 UTC.
  const ts = Date.parse("2026-04-15T03:07:09Z") / 1000;
  assert.equal(fmtT(ts), "2026.04.15 03:07:09");
});

test("fmtT zero-pads single-digit month/day/time fields", () => {
  const ts = Date.parse("2026-01-02T00:00:05Z") / 1000;
  assert.equal(fmtT(ts), "2026.01.02 00:00:05");
});

test("fmtT returns empty string for null/non-finite input", () => {
  assert.equal(fmtT(null), "");
  assert.equal(fmtT(undefined), "");
  assert.equal(fmtT(NaN), "");
});

// 既存ヘルパの回帰確認（fmtT 追加で他関数を壊していない）。
test("existing format helpers still behave", () => {
  assert.equal(fmtMoney(11370), "11,370");
  assert.equal(cfmt(1.2345, 2), "1.23");
  assert.equal(cfmtLocale(11370, 0), "11,370");
  assert.equal(signClass(-1), "neg");
});
