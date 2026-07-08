// chart.js 純ロジック単体テスト（node:test・DOM/vendor 非依存）。
// 対象（パリティ点 1,2,3,4）: balance_curve の前方補完（balanceForwardFill）/ 重複排除
//   （dedupeCurve）/ アンダーウォーター DD 系列 / time→value 索引（byTimeResolve・クロスヘア入力）。
import { test } from "node:test";
import assert from "node:assert/strict";

import { dedupeCurve, balanceForwardFill, byTimeResolve } from "../chart.js";

// --- dedupeCurve: time 重複排除＋昇順ソート ------------------------------------

test("dedupeCurve sorts by time ascending and keeps the last value per time", () => {
  const out = dedupeCurve([
    { time: 30, value: 3 }, { time: 10, value: 1 }, { time: 10, value: 99 },
  ]);
  assert.deepEqual(out, [{ time: 10, value: 99 }, { time: 30, value: 3 }]);
});

test("dedupeCurve returns [] for null/empty input", () => {
  assert.deepEqual(dedupeCurve(null), []);
  assert.deepEqual(dedupeCurve([]), []);
});

// --- balanceForwardFill: バー時刻への前方補完＋アンダーウォーター DD ---------------

test("balanceForwardFill forward-fills balance onto each bar time", () => {
  const barTimes = [100, 200, 300, 400];
  const curve = [{ time: 200, value: 10500 }, { time: 400, value: 10200 }];
  const { balData } = balanceForwardFill(barTimes, curve, 10000);
  // t=100: 未約定→init 10000 / t=200: 10500 / t=300: 10500 保持 / t=400: 10200。
  assert.deepEqual(balData.map((p) => p.value), [10000, 10500, 10500, 10200]);
  assert.deepEqual(balData.map((p) => p.time), barTimes);
});

test("balanceForwardFill drawdown is peak-relative and never positive", () => {
  const barTimes = [100, 200, 300, 400];
  const curve = [{ time: 200, value: 10500 }, { time: 400, value: 10200 }];
  const { ddData } = balanceForwardFill(barTimes, curve, 10000);
  // peak 推移: 10000→10500→10500→10500。dd = cur-peak。
  assert.deepEqual(ddData.map((p) => p.value), [0, 0, 0, -300]);
  assert.ok(ddData.every((p) => p.value <= 0), "DD は常に ≤0（アンダーウォーター）");
});

test("balanceForwardFill uses default deposit when none given", () => {
  const { balData } = balanceForwardFill([50], [], undefined);
  assert.equal(balData[0].value, 10000);
});

// --- byTimeResolve: time→value 索引（クロスヘア同期の他窓値引き） -------------------

test("byTimeResolve builds a time->value Map", () => {
  const m = byTimeResolve([{ time: 10, value: 1 }, { time: 20, value: 2 }]);
  assert.equal(m.get(10), 1);
  assert.equal(m.get(20), 2);
  assert.equal(m.get(99), undefined);
});
