// compare.js 純ロジック単体テスト（node:test・DOM 非依存）。
// 対象（⑤ R-2/R-4・詳細設計 §11・試作 index.html:1045-1104 buildCompare）:
//   比較・判定タブの純判定: 判定バナー文言マッピング・劣化比較表の比/差算出
//   （数値項目のみ・ratio=OOS/IS・delta=OOS−IS・null/inf 規約）・REPORT_GROUPS 章立て。
//   Chart.js 実描画・DOM 構築は e2e（verify_compare.py）で被覆。本ファイルは純関数のみ。
import { test } from "node:test";
import assert from "node:assert/strict";

import {
  verdictLabel,
  parseReportNum,
  compareCell,
  buildCompareRows,
} from "../compare.js";

// --- 判定バナー文言マッピング（R-2: 過剰最適化/要注意/合格） ---------------------

test("verdictLabel maps fail/warn/pass to Japanese banner words", () => {
  assert.equal(verdictLabel("fail"), "過剰最適化");
  assert.equal(verdictLabel("warn"), "要注意");
  assert.equal(verdictLabel("pass"), "合格");
});

test("verdictLabel returns empty string for unknown result", () => {
  assert.equal(verdictLabel(""), "");
  assert.equal(verdictLabel("other"), "");
});

// --- report 値の数値抽出（数値項目のみ比/差を出すための判定） --------------------

test("parseReportNum extracts plain numbers", () => {
  assert.equal(parseReportNum("11370"), 11370);
  assert.equal(parseReportNum("-4020"), -4020);
  assert.equal(parseReportNum("1.16"), 1.16);
});

test("parseReportNum strips parenthetical and percent suffixes", () => {
  // "56.47% (2950)" → 56.47（先頭数値・% と (...) を除去）。
  assert.equal(parseReportNum("56.47% (2950)"), 56.47);
  assert.equal(parseReportNum("2400 (10.50%)"), 2400);
});

test("parseReportNum returns null for non-numeric strings", () => {
  assert.equal(parseReportNum("StopEntryProbe_EA"), null);
  assert.equal(parseReportNum("2026.04.01-04.14"), null);
  assert.equal(parseReportNum("inf"), null);
  assert.equal(parseReportNum(null), null);
  assert.equal(parseReportNum(undefined), null);
});

// --- 劣化比較セル: ratio=OOS/IS・delta=OOS−IS（数値項目のみ） --------------------

test("compareCell computes ratio=oos/is and delta=oos-is for numerics", () => {
  const c = compareCell("100", "50");
  assert.equal(c.is, 100);
  assert.equal(c.oos, 50);
  assert.equal(c.ratio, 0.5);
  assert.equal(c.delta, -50);
});

test("compareCell ratio is null when IS is zero (divide guard)", () => {
  const c = compareCell("0", "50");
  assert.equal(c.ratio, null);
  assert.equal(c.delta, 50);
});

test("compareCell returns nulls for non-numeric items (no ratio/delta)", () => {
  // 文字列項目（Expert/Period 等）は比/差を算出しない。
  const c = compareCell("StopEntryProbe_EA", "StopEntryProbe_EA");
  assert.equal(c.is, null);
  assert.equal(c.oos, null);
  assert.equal(c.ratio, null);
  assert.equal(c.delta, null);
});

test("compareCell deltaClass reflects sign of delta", () => {
  assert.equal(compareCell("10", "20").deltaClass, "pos");
  assert.equal(compareCell("20", "10").deltaClass, "neg");
  assert.equal(compareCell("10", "10").deltaClass, "");
});

// --- 劣化比較表モデル: REPORT_GROUPS 章立て・IS|OOS|比|差 ------------------------

test("buildCompareRows yields group headers followed by metric rows", () => {
  const isR = {
    "Total Net Profit": "11370", "Profit Factor": "1.16", "Z-Score": "-0.09",
  };
  const oosR = {
    "Total Net Profit": "-4020", "Profit Factor": "0.89", "Z-Score": "-0.34",
  };
  const rows = buildCompareRows(isR, oosR);
  // グループ見出し行が存在する（type === "group"）。
  const groups = rows.filter((r) => r.type === "group");
  assert.ok(groups.length > 0);
  // 指標行が存在し IS/OOS 値を保持する。
  const net = rows.find((r) => r.key === "Total Net Profit");
  assert.ok(net);
  assert.equal(net.type, "metric");
  assert.equal(net.isRaw, "11370");
  assert.equal(net.oosRaw, "-4020");
  assert.equal(net.ratio, parseReportNum("-4020") / parseReportNum("11370"));
  assert.equal(net.delta, parseReportNum("-4020") - parseReportNum("11370"));
});

test("buildCompareRows skips groups with no present keys", () => {
  // どの REPORT_GROUPS 章にも該当キーが無い → 当該章見出しは出さない（試作 present.length 判定）。
  const isR = { "Total Net Profit": "100" };
  const oosR = { "Total Net Profit": "50" };
  const rows = buildCompareRows(isR, oosR);
  const titles = rows.filter((r) => r.type === "group").map((r) => r.title);
  // P/L 章だけが現れる（DD 章・統計章は該当キー無しで非表示）。
  assert.ok(titles.some((t) => /損益/.test(t)));
  assert.ok(!titles.some((t) => /ドローダウン|統計/.test(t)));
});

test("buildCompareRows includes the Japanese label for each metric row", () => {
  const isR = { "Profit Factor": "1.16" };
  const oosR = { "Profit Factor": "0.89" };
  const rows = buildCompareRows(isR, oosR);
  const pf = rows.find((r) => r.key === "Profit Factor");
  assert.equal(pf.labelJa, "プロフィットファクター");
});

test("buildCompareRows keeps string metrics with null ratio/delta", () => {
  const isR = { "Expert": "StopEntryProbe_EA", "Symbol": "JP225" };
  const oosR = { "Expert": "StopEntryProbe_EA", "Symbol": "JP225" };
  const rows = buildCompareRows(isR, oosR);
  const exp = rows.find((r) => r.key === "Expert");
  assert.ok(exp);
  assert.equal(exp.ratio, null);
  assert.equal(exp.delta, null);
});
