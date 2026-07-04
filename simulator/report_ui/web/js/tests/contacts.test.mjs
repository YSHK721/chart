// 接点マーカー純ロジック単体テスト（node:test・DOM/vendor 非依存）。
// 対象（追加機能）: agg.contacts[{time,price,dir}] → lwc マーカー配列変換
//   （up→arrowUp/belowBar・down→arrowDown/aboveBar）/ time 昇順 sort / MARKER_CAP 間引き /
//   トグル state（visible=false で非表示）。売買マーカーとは別系列・別配色で分離する。
import { test } from "node:test";
import assert from "node:assert/strict";

import {
  contactToMarker, contactsToMarkers, contactsInRange,
  CONTACT_UP_COLOR, CONTACT_DOWN_COLOR, CONTACT_MARKER_CAP,
} from "../chart.js";
import { contactsOf } from "../data.js";

// --- contactToMarker: 1 接点 → 1 マーカー（up/down で shape/position/color 分離） -----

test("contactToMarker maps an up-contact to arrowUp/belowBar with up color", () => {
  const m = contactToMarker({ time: 100, price: 39400.0, dir: "up" }, 0);
  assert.equal(m.time, 100);
  assert.equal(m.position, "belowBar");
  assert.equal(m.shape, "arrowUp");
  assert.equal(m.color, CONTACT_UP_COLOR);
  assert.equal(m.id, "c0");
});

test("contactToMarker maps a down-contact to arrowDown/aboveBar with down color", () => {
  const m = contactToMarker({ time: 200, price: 39500.0, dir: "down" }, 3);
  assert.equal(m.position, "aboveBar");
  assert.equal(m.shape, "arrowDown");
  assert.equal(m.color, CONTACT_DOWN_COLOR);
  assert.equal(m.id, "c3");
});

test("contact colors differ from trade marker colors (green/red)", () => {
  // 売買マーカー配色（#26a69a 買/ #ef5350 売）と区別できること
  assert.notEqual(CONTACT_UP_COLOR, "#26a69a");
  assert.notEqual(CONTACT_DOWN_COLOR, "#ef5350");
  assert.notEqual(CONTACT_UP_COLOR, CONTACT_DOWN_COLOR);
});

// --- contactsToMarkers: 配列変換＋time 昇順 sort＋cap＋トグル ----------------------

test("contactsToMarkers sorts by time ascending and assigns ids in time order", () => {
  const out = contactsToMarkers([
    { time: 300, price: 3, dir: "up" },
    { time: 100, price: 1, dir: "down" },
    { time: 200, price: 2, dir: "up" },
  ], { visible: true });
  assert.deepEqual(out.map((m) => m.time), [100, 200, 300]);
  assert.deepEqual(out.map((m) => m.id), ["c0", "c1", "c2"]);
});

test("contactsToMarkers returns [] when not visible (toggle off)", () => {
  const out = contactsToMarkers([{ time: 1, price: 1, dir: "up" }], { visible: false });
  assert.deepEqual(out, []);
});

test("contactsToMarkers defaults to visible when opts omitted", () => {
  const out = contactsToMarkers([{ time: 1, price: 1, dir: "up" }]);
  assert.equal(out.length, 1);
});

test("contactsToMarkers thins to [] when over cap (mirrors trade MARKER_CAP)", () => {
  const many = Array.from({ length: CONTACT_MARKER_CAP + 1 },
    (_, i) => ({ time: i, price: 1, dir: "up" }));
  assert.deepEqual(contactsToMarkers(many, { visible: true }), []);
});

test("contactsToMarkers keeps markers exactly at cap boundary", () => {
  const atCap = Array.from({ length: CONTACT_MARKER_CAP },
    (_, i) => ({ time: i, price: 1, dir: "down" }));
  assert.equal(contactsToMarkers(atCap, { visible: true }).length, CONTACT_MARKER_CAP);
});

test("contactsToMarkers returns [] for null/empty input", () => {
  assert.deepEqual(contactsToMarkers(null), []);
  assert.deepEqual(contactsToMarkers([]), []);
});

// --- contactsInRange: 可視レンジ絞り込み（cap 前・全件 cap 超過でも表示できるように） ----

test("contactsInRange keeps only contacts whose time is within [from,to]", () => {
  const cs = [
    { time: 50, price: 1, dir: "up" },
    { time: 150, price: 2, dir: "down" },
    { time: 250, price: 3, dir: "up" },
  ];
  const out = contactsInRange(cs, { from: 100, to: 200 });
  assert.deepEqual(out.map((c) => c.time), [150]);
});

test("contactsInRange includes contacts exactly on the boundaries", () => {
  const cs = [{ time: 100, price: 1, dir: "up" }, { time: 200, price: 2, dir: "down" }];
  const out = contactsInRange(cs, { from: 100, to: 200 });
  assert.deepEqual(out.map((c) => c.time), [100, 200]);
});

test("contactsInRange returns all when range is null (defensive)", () => {
  const cs = [{ time: 1, price: 1, dir: "up" }];
  assert.deepEqual(contactsInRange(cs, null), cs);
  assert.deepEqual(contactsInRange(null, null), []);
});

test("range-filter then cap lets a large set display when zoomed in", () => {
  // 全件 cap 超過でも、可視レンジ絞り込み後に cap 以下なら表示される（本番の恒常非表示回避）
  const total = Array.from({ length: CONTACT_MARKER_CAP + 500 },
    (_, i) => ({ time: i, price: 1, dir: "up" }));
  const zoomed = contactsInRange(total, { from: 0, to: 10 }); // 11 件
  assert.equal(contactsToMarkers(zoomed, { visible: true }).length, 11);
});

// --- contactsOf(data, seg): agg.contacts の防御的取得（R-4 防御） ------------------

test("contactsOf returns agg.contacts for an existing segment", () => {
  const contacts = [{ time: 10, price: 1, dir: "up" }];
  const data = { segments: { is: { agg: { contacts } } } };
  assert.deepEqual(contactsOf(data, "is"), contacts);
});

test("contactsOf returns [] when contacts missing / agg missing / segment missing", () => {
  assert.deepEqual(contactsOf({ segments: { is: { agg: {} } } }, "is"), []);
  assert.deepEqual(contactsOf({ segments: { is: {} } }, "is"), []);
  assert.deepEqual(contactsOf({ segments: {} }, "is"), []);
  assert.deepEqual(contactsOf(undefined, "is"), []);
});
