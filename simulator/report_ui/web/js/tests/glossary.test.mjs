// glossary.js 静的辞書単体テスト（node:test・DOM 非依存・依存0）。
// 対象（⑤ R-2/R-3・詳細設計 §11・試作 index.html:851-889）:
//   REPORT_GROUPS（章立て [title, keys[]] 配列）と LABELS_JA（英ラベル→日本語呼称 dict）の
//   静的辞書。compare.js / 将来の glossary 表示が消費する依存0のリーフモジュール。
import { test } from "node:test";
import assert from "node:assert/strict";

import { REPORT_GROUPS, LABELS_JA } from "../glossary.js";

test("REPORT_GROUPS is a non-empty array of [title, keys[]] pairs", () => {
  assert.ok(Array.isArray(REPORT_GROUPS));
  assert.ok(REPORT_GROUPS.length > 0);
  for (const grp of REPORT_GROUPS) {
    assert.equal(grp.length, 2);
    assert.equal(typeof grp[0], "string");
    assert.ok(Array.isArray(grp[1]));
  }
});

test("REPORT_GROUPS groups the core P/L metrics under a single section", () => {
  // 「損益と資金効率」章に Total Net Profit / Profit Factor が属する（試作章立て準拠）。
  const pl = REPORT_GROUPS.find((g) => g[1].includes("Total Net Profit"));
  assert.ok(pl, "Total Net Profit must belong to a group");
  assert.ok(pl[1].includes("Profit Factor"));
  assert.ok(pl[1].includes("Gross Profit"));
});

test("REPORT_GROUPS includes drawdown and statistical sections", () => {
  const allKeys = REPORT_GROUPS.flatMap((g) => g[1]);
  assert.ok(allKeys.includes("Balance Drawdown Maximal"));
  assert.ok(allKeys.includes("Z-Score"));
});

test("LABELS_JA maps known English labels to Japanese names", () => {
  assert.equal(LABELS_JA["Total Net Profit"], "総純損益");
  assert.equal(LABELS_JA["Profit Factor"], "プロフィットファクター");
  assert.equal(LABELS_JA["Z-Score"], "Zスコア");
});

test("LABELS_JA covers every key referenced by REPORT_GROUPS", () => {
  // 章立てに載るキーは全て日本語呼称を持つ（未登録キーは表示で英語フォールバックするが、
  // 章立て対象キーは辞書整合を固定する）。
  for (const [, keys] of REPORT_GROUPS) {
    for (const k of keys) {
      assert.ok(k in LABELS_JA, `LABELS_JA must contain ${k}`);
    }
  }
});
