// table.js 純ロジック単体テスト（node:test・DOM 非依存）。
// 対象: 試作 prototype_260623-02 の取引明細 12 列定義（COLS）と、列ソート比較純関数（compareTrades）。
// 設計（アーキ指針 §3 table.js）:
//   試作 12 列 = # / Open Time / Order / Type / Vol / Price / S/L / T/P / Time(close) /
//   Exit / State / Comment / Profit。Symbol 列は試作の取引明細に無い（銘柄はヘッダ表示）。
//   ソート比較は副作用のない純関数として切り出しテスト可能化する（試作 renderRows のソート式踏襲）。
import { test } from "node:test";
import assert from "node:assert/strict";

import { COLS, compareTrades, projectRow } from "../table.js";

// 試作 prototype_260623-02 index.html の COLS と同順・同キー（key の順序）。Symbol 列は無い。
const PROTO_KEYS = [
  "id", "entry_time", "order", "side", "volume",
  "entry_price", "sl", "tp", "exit_time", "exit_price", "comment", "profit",
];

test("COLS matches the prototype trade-detail 12 columns in order (no Symbol col)", () => {
  // Arrange / Act
  const keys = COLS.map((c) => c[0]);
  // Assert
  assert.equal(COLS.length, 12);
  assert.deepEqual(keys, PROTO_KEYS);
  assert.ok(!keys.includes("symbol"), "prototype trade table has no Symbol column");
});

test("time columns use kind 'time' (fmtT 整形対象) — Open Time と Time(close)", () => {
  const timeKeys = COLS.filter((c) => c[2] === "time").map((c) => c[0]);
  assert.deepEqual(timeKeys, ["entry_time", "exit_time"]);
});

test("Profit column is the last column with kind 'pl' (試作準拠の損益配色)", () => {
  const last = COLS[COLS.length - 1];
  assert.deepEqual(last, ["profit", "Profit", "pl"]);
});

test("COLS each column has a [key, label, kind] triple", () => {
  for (const c of COLS) {
    assert.ok(Array.isArray(c) && c.length === 3, `column ${JSON.stringify(c)} must be [key,label,kind]`);
    assert.equal(typeof c[0], "string");
    assert.equal(typeof c[1], "string");
    assert.equal(typeof c[2], "string");
  }
});

test("compareTrades sorts numeric ascending when dir=1", () => {
  // Arrange
  const a = { price: 100 };
  const b = { price: 200 };
  // Act / Assert
  assert.ok(compareTrades(a, b, "price", 1) < 0);
  assert.ok(compareTrades(b, a, "price", 1) > 0);
});

test("compareTrades sorts numeric descending when dir=-1", () => {
  // Arrange
  const a = { price: 100 };
  const b = { price: 200 };
  // Act / Assert
  assert.ok(compareTrades(a, b, "price", -1) > 0);
  assert.ok(compareTrades(b, a, "price", -1) < 0);
});

test("compareTrades sorts strings via localeCompare", () => {
  // Arrange
  const a = { type: "buy" };
  const b = { type: "sell" };
  // Act / Assert
  assert.ok(compareTrades(a, b, "type", 1) < 0); // "buy" < "sell"
  assert.ok(compareTrades(a, b, "type", -1) > 0);
});

test("compareTrades is null-safe: missing numeric key treated as 0", () => {
  // Arrange
  const a = {};            // price 欠落 → 0 扱い
  const b = { price: 50 };
  // Act / Assert
  assert.ok(compareTrades(a, b, "price", 1) < 0); // 0 < 50
});

test("compareTrades is null-safe: missing string key treated as empty string", () => {
  // Arrange
  const a = {};                 // comment 欠落 → '' 扱い
  const b = { comment: "tp" };
  // Act / Assert (空文字 < "tp")
  assert.ok(compareTrades(a, b, "comment", 1) < 0);
});

test("projectRow maps a trade to the prototype 12-col row (no Symbol col)", () => {
  // Arrange（trades[] → 試作 12 列キーへ射影）
  const t = {
    id: 7, side: "buy", entry_time: 1000, exit_time: 1060,
    entry_price: 100.5, exit_price: 105.0, profit: 50.0, volume: "0.1",
    sl: "98.5", tp: "105.5", order: 7, comment: "tp",
    balance: 10050, hold_sec: 60, mfe: 1.2, mae: 0.5,
  };
  // Act
  const row = projectRow(t);
  // Assert（試作 12 列の各キーが正しい値に射影される）
  assert.equal(row.entry_time, 1000);    // Open Time（fmtT 整形は描画側）
  assert.equal(row.order, 7);
  assert.equal(row.side, "buy");         // Type
  assert.equal(row.volume, "0.1");       // Vol
  assert.equal(row.entry_price, 100.5);  // Price
  assert.equal(row.sl, "98.5");
  assert.equal(row.tp, "105.5");
  assert.equal(row.exit_time, 1060);     // Time(close)
  assert.equal(row.exit_price, 105.0);   // Exit
  assert.equal(row.comment, "tp");       // State / Comment
  assert.equal(row.profit, 50.0);        // Profit 列（pl 配色用）
  assert.ok(!("symbol" in row), "no Symbol column in prototype trade table");
  // id を保持（hover/marker の単一 id 空間用）
  assert.equal(row.id, 7);
  // 12列 + id の各キーが射影されている
  for (const c of COLS) assert.ok(c[0] in row, `row missing key ${c[0]}`);
});
