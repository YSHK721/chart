// compare.js の不足指標導出（augmentReport 一式）単体テスト（node:test・DOM 非依存）。
import { test } from "node:test";
import assert from "node:assert/strict";

import {
  ghprFromCurve, pearson, lrStats, fmtHoldTime, augmentReport,
} from "../compare.js";

test("ghprFromCurve: 一定成長は HPR の幾何平均を返す", () => {
  // init 100 → 110 → 121（毎回 ×1.1）。幾何平均 HPR = 1.1。
  const g = ghprFromCurve([{ time: 1, value: 110 }, { time: 2, value: 121 }], 100);
  assert.ok(Math.abs(g - 1.1) < 1e-9, `ghpr=${g}`);
  assert.equal(ghprFromCurve([], 100), null); // 空は null
});

test("pearson: 完全正相関=1 / 完全負相関=-1 / 定数=0", () => {
  assert.ok(Math.abs(pearson([1, 2, 3], [2, 4, 6]) - 1) < 1e-9);
  assert.ok(Math.abs(pearson([1, 2, 3], [6, 4, 2]) + 1) < 1e-9);
  assert.equal(pearson([1, 2, 3], [5, 5, 5]), 0); // y 分散0
  assert.equal(pearson([1], [1]), null);          // n<2
});

test("lrStats: 直線上の値は相関=1・標準誤差=0", () => {
  const { correlation, stdError } = lrStats([0, 2, 4, 6]); // y=2x
  assert.ok(Math.abs(correlation - 1) < 1e-9);
  assert.ok(stdError < 1e-9);
});

test("fmtHoldTime: 秒→『H時間MM分SS秒』", () => {
  assert.equal(fmtHoldTime(0), "0時間00分00秒");
  assert.equal(fmtHoldTime(134), "0時間02分14秒");   // 2分14秒
  assert.equal(fmtHoldTime(3661), "1時間01分01秒");
});

test("augmentReport: 既存キーは上書きせず不足指標のみ補完する", () => {
  const seg = {
    report: { "Total Net Profit": "100", "AHPR": "1.0050" },
    meta: { bars: 1234 },
    trades: [
      { profit: 10, mfe: 5, mae: 2, hold_sec: 60 },
      { profit: -5, mfe: 1, mae: 8, hold_sec: 120 },
      { profit: 20, mfe: 9, mae: 1, hold_sec: 30 },
    ],
    agg: { balance_curve: [{ time: 1, value: 10010 }, { time: 2, value: 10005 }, { time: 3, value: 10025 }] },
  };
  const r = augmentReport(seg, { initial_deposit: 10000 });
  // 既存キーは不変
  assert.equal(r["Total Net Profit"], "100");
  assert.equal(r["AHPR"], "1.0050");
  // 補完された不足指標
  assert.equal(r["Total Deals"], "6");            // 3 trades × 2
  assert.equal(r["Bars"], "1234");
  assert.equal(r["Symbols"], "1");
  assert.equal(r["History Quality"], "100%");
  assert.equal(r["Leverage"], "1:10");
  assert.equal(r["OnTester result"], "0");
  assert.equal(r["Minimal position holding time"], "0時間00分30秒");
  assert.equal(r["Maximal position holding time"], "0時間02分00秒");
  assert.ok("GHPR" in r && "LR Correlation" in r && "LR Standard Error" in r);
  assert.ok("Correlation (Profits,MFE)" in r && "Correlation (Profits,MAE)" in r && "Correlation (MFE,MAE)" in r);
});

test("augmentReport: equity 系（Ticks/Margin Level/Equity DD Relative）は補完しない（要 equity_curve）", () => {
  const r = augmentReport({ report: {}, trades: [], agg: {}, meta: {} }, {});
  assert.ok(!("Ticks" in r), "Ticks は frontend 導出不能のため非補完");
  assert.ok(!("Margin Level" in r));
  assert.ok(!("Equity Drawdown Relative" in r));
});
