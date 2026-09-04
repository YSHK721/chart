// graphs.js 純ロジック単体テスト（node:test・DOM 非依存）。
// 対象（アーキ指針 §2・SPEC#4）:
//   SPEC#4 インタラクティブグラフ（entries/pl 比較棒・相関散布・保有時間棒）の front 側純判定。
//   グラフ要素クリック→抽出は filterIdsBy*（純関数）で id Set を作り linkage.applyFilter へ渡す。
//   R-2 と単一規約: hour=entry の UTC hour / wday=(getUTCDay()+6)%7（Mon=0）/ hold=hold_sec バケット。
//   trades は data.segments[seg].trades を読む（フラット DATA.trades 参照は本番では使わない）。
import { test } from "node:test";
import assert from "node:assert/strict";

import {
  filterIdsByHour,
  filterIdsByWday,
  filterIdsByHold,
  scatterIdAt,
  scatterPairSources,
  holdPairSources,
} from "../graphs.js";

// 既知 UTC entry_time（back test_derive / heatmap.test.mjs と同一規約・同一定数）。
const TS_MON_H0 = 1776643200;   // 2026-04-20 00:00:00 UTC（Mon hour0）
const TS_MON_H23 = 1776726000;  // 2026-04-20 23:00:00 UTC（Mon hour23）
const TS_SUN_H23 = 1776639600;  // 2026-04-19 23:00:00 UTC（Sun hour23）

const _t = (id, entry_time, hold_sec) => ({ id, entry_time, hold_sec });

test("filterIdsByHour returns ids whose entry UTC hour matches", () => {
  const trades = [
    _t(1, TS_MON_H0, 30),
    _t(2, TS_MON_H23, 30),
    _t(3, TS_MON_H0, 30),
  ];
  assert.deepEqual([...filterIdsByHour(trades, 0)].sort((a, b) => a - b), [1, 3]);
  assert.deepEqual([...filterIdsByHour(trades, 23)], [2]);
});

test("filterIdsByHour on empty trades returns empty Set", () => {
  assert.equal(filterIdsByHour([], 0).size, 0);
  assert.equal(filterIdsByHour(undefined, 0).size, 0);
});

test("filterIdsByWday uses (getUTCDay()+6)%7 (Mon=0) convention", () => {
  const trades = [
    _t(1, TS_MON_H0, 30),    // Mon
    _t(2, TS_SUN_H23, 30),   // Sun（境界）
    _t(3, TS_MON_H23, 30),   // Mon
  ];
  assert.deepEqual([...filterIdsByWday(trades, "Mon")].sort((a, b) => a - b), [1, 3]);
  assert.deepEqual([...filterIdsByWday(trades, "Sun")], [2]);
});

test("filterIdsByHold buckets by hold_sec with [lo,hi) boundaries (HB)", () => {
  const trades = [
    _t(1, TS_MON_H0, 30),   // <1m
    _t(2, TS_MON_H0, 60),   // 1-2m（境界 60s は [60,120)）
    _t(3, TS_MON_H0, 90),   // 1-2m
    _t(4, TS_MON_H0, 200),  // 2-5m
  ];
  assert.deepEqual([...filterIdsByHold(trades, "<1m")], [1]);
  assert.deepEqual([...filterIdsByHold(trades, "1-2m")].sort((a, b) => a - b), [2, 3]);
  assert.deepEqual([...filterIdsByHold(trades, "2-5m")], [4]);
});

test("scatterIdAt picks IS array on datasetIndex 0", () => {
  // Arrange: 散布 2 系列（dataset0=IS / dataset1=OOS）。同一 index でも id が異なる。
  const isArr = [{ x: 1, y: 1, id: 11 }, { x: 2, y: 2, id: 12 }];
  const oosArr = [{ x: 3, y: 3, id: 91 }, { x: 4, y: 4, id: 92 }];
  // Act / Assert: datasetIndex=0 は IS 配列を参照する。
  assert.equal(scatterIdAt(isArr, oosArr, 0, 0), 11);
  assert.equal(scatterIdAt(isArr, oosArr, 0, 1), 12);
});

test("scatterIdAt picks OOS array on datasetIndex 1 (OOS click returns OOS trade id)", () => {
  // Arrange: 🟡-2 回帰核心。OOS 点クリック（datasetIndex=1）で IS の id を返してはならない。
  const isArr = [{ x: 1, y: 1, id: 11 }, { x: 2, y: 2, id: 12 }];
  const oosArr = [{ x: 3, y: 3, id: 91 }, { x: 4, y: 4, id: 92 }];
  // Act / Assert: datasetIndex=1 は OOS 配列を参照し、正しい OOS trade id を返す。
  assert.equal(scatterIdAt(isArr, oosArr, 1, 0), 91);
  assert.equal(scatterIdAt(isArr, oosArr, 1, 1), 92);
});

test("scatterIdAt returns null for out-of-range index or missing point", () => {
  const isArr = [{ x: 1, y: 1, id: 11 }];
  assert.equal(scatterIdAt(isArr, [], 1, 0), null);   // OOS 空配列
  assert.equal(scatterIdAt(isArr, [], 0, 5), null);   // index 範囲外
});

test("scatterPairSources returns IS as dataset0 and OOS as dataset1 (seg-independent)", () => {
  // Arrange: IS/OOS で別 scatter を持つ data。
  const data = {
    segments: {
      is: { agg: { scatter_mfe: [{ x: 1, y: 1, id: 11 }], scatter_mae: [{ x: 1, y: 1, id: 11 }] } },
      oos: { agg: { scatter_mfe: [{ x: 9, y: 9, id: 91 }], scatter_mae: [{ x: 9, y: 9, id: 91 }] } },
    },
  };
  // Act: 🟡-1 — seg に依らず dataset0=IS / dataset1=OOS。
  const mfe = scatterPairSources(data, "mfe");
  const mae = scatterPairSources(data, "mae");
  // Assert: dataset0 は IS(id11)・dataset1 は OOS(id91)。二重表示（cur 依存）にならない。
  assert.deepEqual(mfe.a.map((p) => p.id), [11]);
  assert.deepEqual(mfe.b.map((p) => p.id), [91]);
  assert.deepEqual(mae.a.map((p) => p.id), [11]);
  assert.deepEqual(mae.b.map((p) => p.id), [91]);
});

test("holdPairSources returns IS hold_pl as dataset0 and OOS as dataset1 (seg-independent)", () => {
  // Arrange: IS/OOS で別 hold_pl。
  const data = {
    segments: {
      is: { agg: { hold_pl: { "<1m": 5.0, "1-2m": 3.0 } } },
      oos: { agg: { hold_pl: { "<1m": -2.0, "1-2m": 1.0 } } },
    },
  };
  // Act: seg に依らず dataset0=IS / dataset1=OOS。
  const r = holdPairSources(data);
  // Assert: ラベル順は IS 基準・a=IS 値 / b=OOS 値（同ラベルで並置・二重表示なし）。
  assert.deepEqual(r.labels, ["<1m", "1-2m"]);
  assert.deepEqual(r.a, [5.0, 3.0]);
  assert.deepEqual(r.b, [-2.0, 1.0]);
});

test("filter pure functions read segments[seg].trades (no flat DATA.trades)", () => {
  // data.segments[seg].trades を渡せば抽出できる（フラット参照に依存しない設計の固定）。
  const data = {
    segments: {
      is: { trades: [_t(1, TS_MON_H0, 30), _t(2, TS_MON_H23, 30)] },
      oos: { trades: [_t(3, TS_MON_H0, 30)] },
    },
  };
  assert.deepEqual([...filterIdsByHour(data.segments.is.trades, 0)], [1]);
  assert.deepEqual([...filterIdsByHour(data.segments.oos.trades, 0)], [3]);
});
