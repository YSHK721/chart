// table.js 純ロジック単体テスト（node:test・DOM 非依存）。
// 対象: SPEC §2.2.2 の 11 列定義（COLS）と、列ソートの比較純関数（compareTrades）。
// 設計（アーキ指針 §3 table.js / 詳細設計 §4.4 orders 11キー）:
//   SPEC 11列 = Open(Time), Order, Symbol, Type, Volume, Price, S/L, T/P, Time(close), State, Comment。
//   明細は trades[] を一次ソースに描画し、Symbol 列は meta.symbol を射影する。
//   ソート比較は副作用のない純関数として切り出しテスト可能化する（試作 renderRows のソート式踏襲）。
import { test } from "node:test";
import assert from "node:assert/strict";

import { COLS, compareTrades, projectRow } from "../table.js";

// SPEC §2.2.2 の 11 列（key の順序）。Symbol は meta 射影列。
const SPEC_KEYS = [
  "open_time", "order", "symbol", "type", "volume",
  "price", "sl", "tp", "exit_time", "state", "comment",
];

test("COLS defines exactly the SPEC 2.2.2 11 columns in order", () => {
  // Arrange / Act
  const keys = COLS.map((c) => c[0]);
  // Assert
  assert.equal(COLS.length, 11);
  assert.deepEqual(keys, SPEC_KEYS);
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

test("projectRow maps a 16-key trade to the SPEC 11-col row (with meta.symbol)", () => {
  // Arrange（trades[] 16キー → SPEC 11列キーへ射影。詳細設計 §4.4）
  const t = {
    id: 7, side: "buy", entry_time: 1000, exit_time: 1060,
    entry_price: 100.5, exit_price: 105.0, profit: 50.0, volume: "0.1",
    sl: "98.5", tp: "105.5", order: 7, comment: "tp",
    balance: 10050, hold_sec: 60, mfe: 1.2, mae: 0.5,
  };
  // Act
  const row = projectRow(t, "JP225");
  // Assert（SPEC 11列の各キーが正しい値に射影される）
  assert.equal(row.open_time, 1000);     // entry_time
  assert.equal(row.order, 7);
  assert.equal(row.symbol, "JP225");     // meta.symbol 射影
  assert.equal(row.type, "buy");         // side
  assert.equal(row.volume, "0.1");
  assert.equal(row.price, 100.5);        // entry_price
  assert.equal(row.sl, "98.5");
  assert.equal(row.tp, "105.5");
  assert.equal(row.exit_time, 1060);
  assert.equal(row.state, "tp");         // comment 写像
  assert.equal(row.comment, "tp");
  // id を保持（hover/marker の単一 id 空間用）
  assert.equal(row.id, 7);
  // SPEC 11列 + id の合計キー数
  for (const c of COLS) assert.ok(c[0] in row, `row missing SPEC key ${c[0]}`);
});
