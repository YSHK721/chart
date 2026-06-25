// report.js 純ロジック単体テスト（node:test・DOM 非依存）。
// 対象（パリティ点 8）: 数値整形（fmtReportVal）/ 値クラス（reportRowClass）/
//   章立てモデル（reportRowsModel・REPORT_GROUPS + 「その他」章）。
import { test } from "node:test";
import assert from "node:assert/strict";

import { fmtReportVal, reportRowClass, reportRowsModel } from "../report.js";

// --- fmtReportVal: 整数桁区切り / 小数3桁丸め / 非数値はそのまま --------------------

test("fmtReportVal formats integers with thousands separators", () => {
  assert.equal(fmtReportVal("11370"), "11,370");
  assert.equal(fmtReportVal("-4020"), "-4,020");
});

test("fmtReportVal clamps decimals to 3 places", () => {
  assert.equal(fmtReportVal("1.23456"), "1.235");
});

test("fmtReportVal passes through non-numeric strings", () => {
  assert.equal(fmtReportVal("56.47% (2950)"), "56.47% (2950)");
  assert.equal(fmtReportVal("StopEntryProbe_EA"), "StopEntryProbe_EA");
  assert.equal(fmtReportVal(null), "");
});

// --- reportRowClass: 負値=neg / Net|Gross Profit 正値=pos -------------------------

test("reportRowClass marks negatives neg", () => {
  assert.equal(reportRowClass("Gross Loss", "-73230"), "neg");
});

test("reportRowClass marks positive Net/Gross Profit pos", () => {
  assert.equal(reportRowClass("Total Net Profit", "11370"), "pos");
  assert.equal(reportRowClass("Gross Profit", "84600"), "pos");
});

test("reportRowClass leaves other positives unclassed", () => {
  assert.equal(reportRowClass("Profit Factor", "1.16"), "");
});

// --- reportRowsModel: 章立て＋その他章 -------------------------------------------

test("reportRowsModel groups present keys under REPORT_GROUPS chapters", () => {
  const groups = reportRowsModel({ "Total Net Profit": "11370", "Profit Factor": "1.16" });
  // 損益章のみ現れ、行は present キー分。
  assert.ok(groups.some((g) => /損益/.test(g.title)));
  const pl = groups.find((g) => /損益/.test(g.title));
  assert.ok(pl.rows.some((r) => r.key === "Total Net Profit"));
  assert.ok(pl.rows.some((r) => r.key === "Profit Factor"));
});

test("reportRowsModel skips chapters with no present keys", () => {
  const groups = reportRowsModel({ "Total Net Profit": "100" });
  const titles = groups.map((g) => g.title);
  assert.ok(!titles.some((t) => /ドローダウン|統計/.test(t)));
});

test("reportRowsModel routes unknown keys into a trailing その他 chapter", () => {
  const groups = reportRowsModel({ "Custom Metric XYZ": "42" });
  const last = groups[groups.length - 1];
  assert.equal(last.title, "その他");
  assert.ok(last.rows.some((r) => r.key === "Custom Metric XYZ"));
});

test("reportRowsModel carries Japanese label and value class per row", () => {
  const groups = reportRowsModel({ "Total Net Profit": "11370" });
  const row = groups.flatMap((g) => g.rows).find((r) => r.key === "Total Net Profit");
  assert.equal(row.labelJa, "総純損益");
  assert.equal(row.disp, "11,370");
  assert.equal(row.cls, "pos");
});
