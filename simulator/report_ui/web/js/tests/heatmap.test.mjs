// heatmap.js 純ロジック単体テスト（node:test・DOM 非依存）。
// 対象（アーキ指針 §2/§4・詳細設計 §11.1 heatmap.js）:
//   F-3 最小単位ヒートマップ（wday×hour セル）の front 側純判定。
//   R-2（最重要）: back（derive.heat_cells の weekday() Mon=0・UTC）と front の (wday,hour) 判定を
//   単一規約 `(getUTCDay()+6)%7`（Mon=0）＋UTC に固定し、同一 trade を選ぶことを検証する。
//   セルクリック→抽出は collectCellIds（純関数）で id Set を作り linkage.applyFilter へ渡す。
import { test } from "node:test";
import assert from "node:assert/strict";

import { WEEKORDER, wdayHourOf, tradeMatchesCell, collectCellIds } from "../heatmap.js";

// 既知 UTC entry_time（back test_derive と同一規約・同一定数）。
const TS_MON_H0 = 1776643200;   // 2026-04-20 00:00:00 UTC（Mon hour0）
const TS_MON_H23 = 1776726000;  // 2026-04-20 23:00:00 UTC（Mon hour23）
const TS_SUN_H23 = 1776639600;  // 2026-04-19 23:00:00 UTC（Sun hour23）

test("WEEKORDER is Mon..Sun (Mon=0 convention, matches back derive.WEEK)", () => {
  assert.deepEqual(WEEKORDER, ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]);
});

test("wdayHourOf maps Monday hour0 to {wday:'Mon', hour:0} (UTC)", () => {
  // R-2: front (getUTCDay()+6)%7 が back weekday()=0→'Mon' と一致
  assert.deepEqual(wdayHourOf(TS_MON_H0), { wday: "Mon", hour: 0 });
});

test("wdayHourOf maps Sunday hour23 to {wday:'Sun', hour:23} (boundary)", () => {
  // 境界（日曜・hour=23）: back weekday()=6→'Sun' と一致
  assert.deepEqual(wdayHourOf(TS_SUN_H23), { wday: "Sun", hour: 23 });
});

test("wdayHourOf maps Monday hour23 to {wday:'Mon', hour:23} (boundary)", () => {
  assert.deepEqual(wdayHourOf(TS_MON_H23), { wday: "Mon", hour: 23 });
});

test("tradeMatchesCell selects a trade whose entry falls in the (wday,hour) cell", () => {
  // R-2 同一 trade 選択: back が (Mon,0) セルに分類する entry を front も (Mon,0) で選ぶ
  const t = { id: 7, entry_time: TS_MON_H0 };
  assert.equal(tradeMatchesCell(t, "Mon", 0), true);
  assert.equal(tradeMatchesCell(t, "Mon", 23), false); // hour 不一致
  assert.equal(tradeMatchesCell(t, "Sun", 0), false);  // wday 不一致
});

test("collectCellIds returns the Set of ids whose entries match the cell", () => {
  // セルクリック抽出: 該当 (wday,hour) の trade id のみ Set 化（linkage.applyFilter 入力）
  const trades = [
    { id: 1, entry_time: TS_MON_H0 },   // (Mon,0)
    { id: 2, entry_time: TS_MON_H23 },  // (Mon,23)
    { id: 3, entry_time: TS_MON_H0 },   // (Mon,0)
    { id: 4, entry_time: TS_SUN_H23 },  // (Sun,23)
  ];
  const ids = collectCellIds(trades, "Mon", 0);
  assert.ok(ids instanceof Set);
  assert.deepEqual([...ids].sort((a, b) => a - b), [1, 3]);
});

test("collectCellIds for a boundary Sunday cell selects only that trade", () => {
  // 境界（日曜）: back の (Sun,23) セルと front 抽出が同一 trade を選ぶ
  const trades = [
    { id: 1, entry_time: TS_MON_H0 },
    { id: 2, entry_time: TS_SUN_H23 },
  ];
  assert.deepEqual([...collectCellIds(trades, "Sun", 23)], [2]);
});

// --- R-2 最重要回帰: back heat 分類 ⇄ front フィルタ判定が同一 trade を選ぶ ----
// back（derive.heat_cells）が各 entry_time を割り当てる (wday,hour) を「正解の分配」とし、
// front の wdayHourOf/collectCellIds が全 trade を back と同一セルへ分配することを検証する。
// back 正解は test_derive と同一の検証済み定数（py weekday() と js (getUTCDay()+6)%7 が
// 同一 ts で一致することを実機で実証済 — §upstream-input-validation）。
// 境界（日曜・hour0・hour23）を含む全 trade で「back セル割当 == front セル割当」。
test("R-2: front cell assignment matches back heat_cells partition for every trade (incl. boundaries)", () => {
  // back（derive.heat_cells）が割り当てる正解 (wday,hour)。test_derive の検証済みベクトル。
  const BACK_GROUND_TRUTH = [
    { entry_time: TS_MON_H0, wday: "Mon", hour: 0 },    // Mon hour0
    { entry_time: TS_MON_H23, wday: "Mon", hour: 23 },  // Mon hour23
    { entry_time: TS_SUN_H23, wday: "Sun", hour: 23 },  // 境界: 日曜 hour23
  ];
  const trades = BACK_GROUND_TRUTH.map((g, i) => ({ id: i + 1, entry_time: g.entry_time }));

  // (a) front 単位判定が back 正解と 1 件ずつ一致
  for (let i = 0; i < BACK_GROUND_TRUTH.length; i++) {
    const g = BACK_GROUND_TRUTH[i];
    assert.deepEqual(wdayHourOf(g.entry_time), { wday: g.wday, hour: g.hour },
      `trade ${i + 1} front cell != back cell`);
  }

  // (b) front の collectCellIds が back の各セル分配と同一 id 集合を返す（同一 trade を選ぶ）
  const backPartition = {};
  BACK_GROUND_TRUTH.forEach((g, i) => {
    (backPartition[`${g.wday}|${g.hour}`] ||= []).push(i + 1);
  });
  for (const key of Object.keys(backPartition)) {
    const [w, h] = [key.split("|")[0], +key.split("|")[1]];
    const frontIds = [...collectCellIds(trades, w, h)].sort((a, b) => a - b);
    assert.deepEqual(frontIds, backPartition[key].sort((a, b) => a - b),
      `cell ${key}: front ids != back partition`);
  }
});
