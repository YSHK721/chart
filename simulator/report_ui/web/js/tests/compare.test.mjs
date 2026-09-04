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
  underwaterCurve,
  metricRetention,
  metricRetentionAll,
  degradationBars,
  radarClamp,
  RADAR_METRICS,
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

// --- 点12 cmpDD: アンダーウォーター（残高ベース DD）系列 --------------------------

test("underwaterCurve starts at zero (init-1 anchor) and is never positive", () => {
  const bc = [{ time: 100, value: 10500 }, { time: 200, value: 9800 }, { time: 300, value: 10100 }];
  const uw = underwaterCurve(bc, 10000);
  assert.equal(uw.points[0].x, 99);     // bc[0].time - 1
  assert.equal(uw.points[0].y, 0);      // 起点 0
  assert.ok(uw.points.every((p) => p.y <= 0), "DD は常に ≤0");
});

test("underwaterCurve maxDrawdown is the deepest peak-relative drop", () => {
  // peak: 10000→10500→10500→10500。最深は t=200 で 9800-10500 = -700。
  const bc = [{ time: 100, value: 10500 }, { time: 200, value: 9800 }, { time: 300, value: 10100 }];
  const uw = underwaterCurve(bc, 10000);
  assert.equal(uw.maxDrawdown, -700);
  assert.ok(uw.maxDdPct < 0);
});

test("underwaterCurve handles empty curve", () => {
  const uw = underwaterCurve([], 10000);
  assert.deepEqual(uw.points, []);
  assert.equal(uw.maxDrawdown, 0);
});

// --- 点13 cmpRadar: 維持率（hi 軸=OOS/IS・低DD 軸=絶対値の逆数・符号非依存） ---------

test("metricRetention computes oos/is for higher-is-better axes", () => {
  const m = { k: "profit_factor", l: "PF", hi: true };
  assert.equal(metricRetention(m, { profit_factor: 1.16 }, { profit_factor: 0.58 }), 0.5);
});

test("metricRetention low-DD axis uses |is|/|oos| and is sign-independent", () => {
  // summary.max_dd_pct は負値（-11.52 等）。符号非依存で IS=10/OOS=20 → 維持率 0.5。
  const m = { k: "max_dd_pct", l: "低DD", hi: false };
  assert.equal(metricRetention(m, { max_dd_pct: -10 }, { max_dd_pct: -20 }), 0.5);
  assert.equal(metricRetention(m, { max_dd_pct: 10 }, { max_dd_pct: 20 }), 0.5);
});

test("metricRetention returns 0 when IS metric is zero (hi axis)", () => {
  const m = { k: "return_pct", l: "リターン", hi: true };
  assert.equal(metricRetention(m, { return_pct: 0 }, { return_pct: 50 }), 0);
});

test("metricRetentionAll returns one value per radar axis", () => {
  const isS = { profit_factor: 1, win_rate: 50, payoff: 2, expectancy: 2, return_pct: 100, max_dd_pct: -10 };
  const oosS = { profit_factor: 1, win_rate: 50, payoff: 2, expectancy: 2, return_pct: 100, max_dd_pct: -10 };
  const out = metricRetentionAll(isS, oosS);
  assert.equal(out.length, RADAR_METRICS.length);
  assert.ok(out.every((v) => Math.abs(v - 1) < 1e-9), "同値なら全軸 1.0 維持");
});

test("radarClamp bounds values to [0, 1.3]", () => {
  assert.equal(radarClamp(-5), 0);
  assert.equal(radarClamp(0.7), 0.7);
  assert.equal(radarClamp(9), 1.3);
});

// --- 点14 cmpDeg: 維持率バーの色しきい値（>=0.95 緑 / >=0.7 黄 / 他 赤） --------------

test("degradationBars colors by retention thresholds", () => {
  // PF 維持1.0(緑) / 勝率0.8(黄) / ペイオフ0.5(赤) … 残りは同値=1.0(緑)。
  const isS = { profit_factor: 1, win_rate: 50, payoff: 2, expectancy: 2, return_pct: 100, max_dd_pct: -10 };
  const oosS = { profit_factor: 1, win_rate: 40, payoff: 1, expectancy: 2, return_pct: 100, max_dd_pct: -10 };
  const db = degradationBars(isS, oosS);
  assert.equal(db.labels.length, RADAR_METRICS.length);
  assert.equal(db.colors[0], "#26a69a"); // PF 1.0 → 緑
  assert.equal(db.colors[1], "#e3b341"); // 勝率 0.8 → 黄
  assert.equal(db.colors[2], "#ef5350"); // ペイオフ 0.5 → 赤
});
