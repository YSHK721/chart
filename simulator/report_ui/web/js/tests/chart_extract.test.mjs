// chart.js から抽出した純関数・公開定数の単体テスト（node:test・DOM/vendor 非依存）。
// 抽出の目的（Phase 4 D-1/D-2）: sim 表示層（v5.2.0 アダプタ）が同じ**構築規則**を
//   使えるようにする。規則を sim 側へ書き写すと、片方だけ直る／片方だけ腐る形で必ず
//   食い違う（パリティ点 S3/S4/S5/S6・点7 が静かにドリフトする）。
// 挙動等価: 抽出元（renderMarkers / renderChart / dimCandlesForTrade / _visibleTrades）は
//   本関数群を呼ぶ形へ置き換える。既存 12 本の被覆する挙動は不変。
import { test } from "node:test";
import assert from "node:assert/strict";

import {
  DIM_ALPHA, MARKER_CAP, EXIT_COLOR, DEFAULT_DEPOSIT,
  _withAlpha, _bisectLeft,
  buildTradeMarkers, buildDimBars, mergeDimBarsForTrade,
  visibleTradesInRange, chartBadgeText,
} from "../chart.js";

// --- D-1 公開定数（試作の実値を単一ソース化） ---------------------------------

test("exported constants keep the prototype values", () => {
  assert.equal(DIM_ALPHA, 0.15);       // 点 S3/S4 の減光アルファ
  assert.equal(MARKER_CAP, 700);       // 点 S6 マーカー上限
  assert.equal(EXIT_COLOR, "#6b7785"); // 決済マーカー色
  assert.equal(DEFAULT_DEPOSIT, 10000);
});

test("_withAlpha converts #rrggbb to rgba with the given alpha", () => {
  assert.equal(_withAlpha("#26a69a", DIM_ALPHA), "rgba(38,166,154,0.15)");
  assert.equal(_withAlpha("#ef5350", DIM_ALPHA), "rgba(239,83,80,0.15)");
});

test("_withAlpha passes through non #rrggbb input unchanged", () => {
  assert.equal(_withAlpha("rgba(0,0,0,0)", 0.5), "rgba(0,0,0,0)");
  assert.equal(_withAlpha(null, 0.5), null);
});

test("_bisectLeft returns the leftmost insertion point", () => {
  const a = [10, 20, 20, 30];
  assert.equal(_bisectLeft(a, 5), 0);
  assert.equal(_bisectLeft(a, 20), 1);   // 等値は左端
  assert.equal(_bisectLeft(a, 31), 4);   // 全要素より大きい
  assert.equal(_bisectLeft([], 1), 0);
});

// --- D-2 売買マーカー配列生成（点 S5 / S3） -----------------------------------

const BUY = { id: 1, side: "buy", profit: 100, entry_time: 200, exit_time: 400 };
const SELL = { id: 2, side: "sell", profit: -50, entry_time: 100, exit_time: 300 };

test("buildTradeMarkers emits an entry and an exit marker per trade", () => {
  const mk = buildTradeMarkers([BUY], null);
  assert.equal(mk.length, 2);
  assert.deepEqual(mk.map((m) => m.id), ["e1", "x1"]);
});

test("buildTradeMarkers ids are 'e'+id (entry) and 'x'+id (exit) — 点 S5", () => {
  const ids = buildTradeMarkers([BUY, SELL], null).map((m) => m.id).sort();
  assert.deepEqual(ids, ["e1", "e2", "x1", "x2"]);
});

test("buildTradeMarkers places buy entry below the bar with an up arrow", () => {
  const [entry] = buildTradeMarkers([BUY], null);
  assert.equal(entry.position, "belowBar");
  assert.equal(entry.shape, "arrowUp");
  assert.equal(entry.time, 200);
});

test("buildTradeMarkers places sell entry above the bar with a down arrow", () => {
  const entry = buildTradeMarkers([SELL], null).find((m) => m.id === "e2");
  assert.equal(entry.position, "aboveBar");
  assert.equal(entry.shape, "arrowDown");
});

test("buildTradeMarkers colors entry green when profitable and red otherwise", () => {
  assert.equal(buildTradeMarkers([BUY], null)[0].color, "#26a69a");
  assert.equal(buildTradeMarkers([SELL], null).find((m) => m.id === "e2").color, "#ef5350");
});

test("buildTradeMarkers draws the exit as a small circle in EXIT_COLOR", () => {
  const exit = buildTradeMarkers([BUY], null).find((m) => m.id === "x1");
  assert.equal(exit.shape, "circle");
  assert.equal(exit.color, EXIT_COLOR);
  assert.equal(exit.size, 0.6);
  assert.equal(exit.time, 400);
  assert.equal(exit.position, "aboveBar"); // buy の決済は上
});

test("buildTradeMarkers enlarges the hovered pair and labels it '#id' — 点 S3", () => {
  const mk = buildTradeMarkers([BUY, SELL], 1);
  const entry = mk.find((m) => m.id === "e1");
  const exit = mk.find((m) => m.id === "x1");
  assert.equal(entry.size, 1.4);
  assert.equal(entry.text, "#1");
  assert.equal(exit.size, 1.4);
});

test("buildTradeMarkers dims the non-hovered pairs to DIM_ALPHA — 点 S3", () => {
  const mk = buildTradeMarkers([BUY, SELL], 1);
  const other = mk.find((m) => m.id === "e2");
  assert.equal(other.color, _withAlpha("#ef5350", DIM_ALPHA));
  assert.equal(mk.find((m) => m.id === "x2").color, _withAlpha(EXIT_COLOR, DIM_ALPHA));
});

test("buildTradeMarkers keeps full color when nothing is hovered", () => {
  const mk = buildTradeMarkers([BUY, SELL], null);
  assert.equal(mk.find((m) => m.id === "e2").color, "#ef5350");
  assert.equal(mk.find((m) => m.id === "x1").size, 0.6);
  assert.equal(mk.find((m) => m.id === "e1").text, "");
});

test("buildTradeMarkers returns markers sorted by time", () => {
  const times = buildTradeMarkers([BUY, SELL], null).map((m) => m.time);
  assert.deepEqual(times, [...times].sort((a, b) => a - b));
  assert.deepEqual(times, [100, 200, 300, 400]);
});

test("buildTradeMarkers returns [] for empty/nullish input", () => {
  assert.deepEqual(buildTradeMarkers([], null), []);
  assert.deepEqual(buildTradeMarkers(null, null), []);
});

// --- D-2 減光バー配列生成（点 S4） ---------------------------------------------

const BARS = [
  { time: 100, open: 10, high: 12, low: 9, close: 11 },  // up
  { time: 200, open: 11, high: 11, low: 8, close: 9 },   // down
  { time: 300, open: 9, high: 10, low: 8, close: 10 },   // up
  { time: 400, open: 10, high: 11, low: 9, close: 10 },  // up（close===open）
];

test("buildDimBars keeps OHLC and adds DIM_ALPHA colors per direction", () => {
  const dim = buildDimBars(BARS);
  assert.equal(dim.length, 4);
  assert.equal(dim[0].color, _withAlpha("#26a69a", DIM_ALPHA));
  assert.equal(dim[1].color, _withAlpha("#ef5350", DIM_ALPHA));
  assert.equal(dim[3].color, _withAlpha("#26a69a", DIM_ALPHA)); // close===open は up 扱い
  assert.equal(dim[0].open, 10);
  assert.equal(dim[0].close, 11);
  assert.equal(dim[0].time, 100);
});

test("buildDimBars paints wick and border with the same dimmed color", () => {
  const [b] = buildDimBars(BARS);
  assert.equal(b.wickColor, b.color);
  assert.equal(b.borderColor, b.color);
});

test("buildDimBars returns [] for empty/nullish input", () => {
  assert.deepEqual(buildDimBars(null), []);
  assert.deepEqual(buildDimBars([]), []);
});

// --- D-2 ペア区間だけ明色に戻す（点 S4: [entry_time, exit_time]） ----------------

test("mergeDimBarsForTrade restores normal bars inside [entry_time, exit_time]", () => {
  const times = BARS.map((b) => b.time);
  const normal = BARS.map((b) => ({ ...b }));
  const dim = buildDimBars(BARS);
  const merged = mergeDimBarsForTrade(times, normal, dim, { entry_time: 200, exit_time: 300 });
  assert.equal(merged[0], dim[0]);      // 区間外は減光のまま
  assert.equal(merged[1], normal[1]);   // 区間内は通常色
  assert.equal(merged[2], normal[2]);   // hi = bisectLeft(times, exit_time+1) → exit 足を含む
  assert.equal(merged[3], dim[3]);
});

test("mergeDimBarsForTrade includes the exit bar itself (hi = bisectLeft(t, exit+1))", () => {
  const times = BARS.map((b) => b.time);
  const normal = BARS.map((b) => ({ ...b }));
  const dim = buildDimBars(BARS);
  const merged = mergeDimBarsForTrade(times, normal, dim, { entry_time: 400, exit_time: 400 });
  assert.equal(merged[3], normal[3]);
  assert.equal(merged[2], dim[2]);
});

test("mergeDimBarsForTrade does not mutate the source arrays", () => {
  const times = BARS.map((b) => b.time);
  const normal = BARS.map((b) => ({ ...b }));
  const dim = buildDimBars(BARS);
  const before = dim.slice();
  mergeDimBarsForTrade(times, normal, dim, { entry_time: 100, exit_time: 400 });
  assert.deepEqual(dim, before);
});

// --- D-2 可視取引の絞り（点 7 の母集合） ----------------------------------------

test("visibleTradesInRange keeps trades overlapping the visible range", () => {
  const rows = [BUY, SELL];
  assert.deepEqual(visibleTradesInRange(rows, { from: 250, to: 260 }), [BUY, SELL]);
  assert.deepEqual(visibleTradesInRange(rows, { from: 350, to: 500 }), [BUY]);
  assert.deepEqual(visibleTradesInRange(rows, { from: 0, to: 50 }), []);
});

test("visibleTradesInRange returns all rows when the range is unknown", () => {
  const rows = [BUY, SELL];
  assert.deepEqual(visibleTradesInRange(rows, null), rows);
  assert.deepEqual(visibleTradesInRange(null, null), []);
});

// --- D-2 chartBadge の文言（点 7） ----------------------------------------------

test("chartBadgeText reports the visible trade count — 点 7", () => {
  assert.equal(chartBadgeText(0), "0 trades in view");
  assert.equal(chartBadgeText(12), "12 trades in view");
});

test("chartBadgeText asks to zoom in when the count exceeds the cap — 点 S6", () => {
  assert.equal(
    chartBadgeText(MARKER_CAP + 1),
    `${MARKER_CAP + 1} trades in view — ズームインでマーカー表示 (cap ${MARKER_CAP})`,
  );
  // 上限ちょうどは通常表示（> 判定であることを固定する）。
  assert.equal(chartBadgeText(MARKER_CAP), `${MARKER_CAP} trades in view`);
});
