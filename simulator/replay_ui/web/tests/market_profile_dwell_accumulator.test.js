// market_profile_dwell_accumulator.js（domain・依存ゼロ純ロジック）の検証（replay_ui 移植版）。
//
// 参照実装（挙動の正解）＝ present-mode（indicator_ui/develop）の DwellAccumulator。本テストは
//   ① present 版と **数値一致**（同一入力で snapshot が deepEqual＝verbatim 移植の証明）、
//   ② mp_core.compute_dwell_profile 実測 golden（POC/VA/units/bins）との一致、
//   ③ dwell 原子・rollover・純性・valueArea 決定論タイブレーク・忠実 binning（wide/misaligned）
//   を固定する。DOM/chart/fetch 非依存。構造: Arrange-Act-Assert。
//
// ★この時点で simulator/replay_ui/web/js/domain/market_profile_dwell_accumulator.js は未実装（Red）。

import { test } from 'node:test';
import assert from 'node:assert/strict';

import {
  DwellAccumulator,
  activeSeconds,
  valueArea,
  VA_PCT,
} from '../js/domain/market_profile_dwell_accumulator.js';

// 参照実装（present-mode・indicator_ui）を直接 import して数値一致を実証する。
import {
  DwellAccumulator as RefDwellAccumulator,
  activeSeconds as refActiveSeconds,
  valueArea as refValueArea,
} from '../../../../indigators/indicator_ui/web/js/domain/market_profile_dwell_accumulator.js';

const DAY0 = 1704067200; // 2024-01-01 00:00 UTC（月曜・UTC 真夜中）。
const ALL_ACTIVE = Array.from({ length: 7 }, () => Array.from({ length: 24 }, () => true));

const TICKS = [
  [DAY0 + 7200, 1005],
  [DAY0 + 7500, 1015],
  [DAY0 + 7750, 1025],
  [DAY0 + 7900, 1005],
];

function makeCfg(baseFine, extra = {}) {
  return {
    baseFine: baseFine ?? new Array(10).fill(0),
    baseKmin: 100,
    activeTable: ALL_ACTIVE,
    priceMin: 1000,
    priceMax: 1100,
    nBins: 10,
    gridW: 10,
    formingStart: DAY0 + 7200,
    ...extra,
  };
}

function makeAcc(baseFine) {
  const acc = new DwellAccumulator();
  acc.init(makeCfg(baseFine));
  return acc;
}

// --- ① present 版との数値一致（verbatim 移植の証明・複数シナリオ deepEqual） ---
test('ported accumulator snapshot is byte-identical to the present (indicator_ui) reference for the same input', () => {
  // Arrange: 移植版・参照版を同一 cfg / 同一 tick 列で駆動する。
  const cfg = makeCfg();
  const ported = new DwellAccumulator();
  const ref = new RefDwellAccumulator();
  ported.init(cfg);
  ref.init(cfg);
  // Act
  for (const [sec, mid] of TICKS) { ported.addTick(sec, mid); ref.addTick(sec, mid); }
  // Assert: snapshot が完全一致（POC/VA/bins/units すべて）。
  assert.deepEqual(ported.snapshot(), ref.snapshot());
});

test('ported accumulator matches the present reference across wide/misaligned range and non-zero base', () => {
  // Arrange: wide/misaligned レンジ・非ゼロ base の別シナリオでも一致すること。
  const cfg = {
    baseFine: [3, 0, 0, 1, 0, 0, 2, 0, 0, 0, 0], baseKmin: 100, activeTable: ALL_ACTIVE,
    priceMin: 1005, priceMax: 1105, nBins: 6, gridW: 10, formingStart: DAY0 + 7200,
  };
  const wideTicks = [
    [DAY0 + 7200, 1006], [DAY0 + 7260, 1034], [DAY0 + 7300, 1039], [DAY0 + 7380, 1061],
    [DAY0 + 7500, 1006], [DAY0 + 7560, 1039], [DAY0 + 7700, 1072], [DAY0 + 7760, 1006],
  ];
  const ported = new DwellAccumulator();
  const ref = new RefDwellAccumulator();
  ported.init(cfg); ref.init(cfg);
  // Act
  for (const [sec, mid] of wideTicks) { ported.addTick(sec, mid); ref.addTick(sec, mid); }
  // Assert
  assert.deepEqual(ported.snapshot(), ref.snapshot());
});

test('ported activeSeconds / valueArea / VA_PCT equal the present reference exports', () => {
  // Arrange
  const table = Array.from({ length: 7 }, () => Array.from({ length: 24 }, () => true));
  table[0][3] = false;
  const a = DAY0 + 2 * 3600 + 1800;
  const b = DAY0 + 4 * 3600 + 600;
  const centers = [10, 20, 30, 40, 50];
  const tpo = [100, 50, 50, 50, 10];
  // Act / Assert
  assert.equal(activeSeconds(a, b, table), refActiveSeconds(a, b, table));
  assert.deepEqual(valueArea(centers, tpo, VA_PCT), refValueArea(centers, tpo, VA_PCT));
  assert.equal(VA_PCT, 0.70);
});

// --- ② mp_core 数値一致（POC/VA/units/形の golden） ---
test('snapshot reproduces mp_core POC/VA/units/bins for the reference tick sequence', () => {
  // Arrange
  const acc = makeAcc();
  // Act
  for (const [sec, mid] of TICKS) acc.addTick(sec, mid);
  const snap = acc.snapshot();
  // Assert（本番 compute_dwell_profile 実測 golden）
  assert.equal(snap.poc, 1005);
  assert.equal(snap.va_low, 1005);
  assert.equal(snap.va_high, 1015);
  assert.equal(snap.tpo_units, 700);
  assert.deepEqual(snap.bins.map((x) => x.tpo), [300, 250, 150, 0, 0, 0, 0, 0, 0, 0]);
  assert.deepEqual(snap.bins.map((x) => x.price), [1005, 1015, 1025, 1035, 1045, 1055, 1065, 1075, 1085, 1095]);
  assert.equal(snap.n_bins, 10);
  assert.equal(snap.price_min, 1000);
  assert.equal(snap.price_max, 1100);
});

// --- ③ dwell 原子: 最新 tick は次 tick 未着まで dwell=0 ---
test('the latest tick contributes dwell=0 until the next tick arrives', () => {
  const acc = makeAcc();
  acc.addTick(TICKS[0][0], TICKS[0][1]);
  const afterOne = acc.snapshot();
  acc.addTick(TICKS[1][0], TICKS[1][1]);
  const afterTwo = acc.snapshot();
  assert.equal(afterOne.tpo_units, 0);
  assert.equal(afterTwo.tpo_units, 300);
  assert.equal(afterTwo.bins[0].tpo, 300);
});

// --- ③ rollover reset（init 再実行で forming クリア） ---
test('init resets the forming accumulation (rollover)', () => {
  const acc = makeAcc();
  for (const [sec, mid] of TICKS) acc.addTick(sec, mid);
  assert.equal(acc.snapshot().tpo_units, 700);
  acc.init(makeCfg(new Array(10).fill(0), { formingStart: DAY0 + 10800 }));
  const snap = acc.snapshot();
  assert.equal(snap.tpo_units, 0);
  assert.deepEqual(snap.bins.map((x) => x.tpo), new Array(10).fill(0));
});

// --- ③ 空 tick = base のみ ---
test('with no ticks the snapshot equals the base bins only', () => {
  const base = [100, 50, 0, 0, 0, 0, 0, 0, 0, 0];
  const acc = makeAcc(base);
  const snap = acc.snapshot();
  assert.deepEqual(snap.bins.map((x) => x.tpo), base);
  assert.equal(snap.tpo_units, 150);
  assert.equal(snap.poc, 1005);
});

// --- ③ combined = base + forming（binning 線形性） ---
test('snapshot combines base and forming linearly per display bin', () => {
  const base = [1000, 0, 0, 0, 0, 0, 0, 0, 0, 0];
  const acc = makeAcc(base);
  for (const [sec, mid] of TICKS) acc.addTick(sec, mid);
  const snap = acc.snapshot();
  assert.equal(snap.bins[0].tpo, 1300);
  assert.equal(snap.bins[1].tpo, 250);
  assert.equal(snap.bins[2].tpo, 150);
  assert.equal(snap.tpo_units, 1700);
});

// --- ③ base 不変（入力配列を破壊しない） ---
test('init does not mutate the caller-supplied baseFine array', () => {
  const base = [5, 0, 0, 0, 0, 0, 0, 0, 0, 0];
  const acc = makeAcc(base);
  for (const [sec, mid] of TICKS) acc.addTick(sec, mid);
  acc.snapshot();
  assert.deepEqual(base, [5, 0, 0, 0, 0, 0, 0, 0, 0, 0]);
});

// --- ③ snapshot は純 ---
test('snapshot is pure: repeated calls return equal results without mutating state', () => {
  const acc = makeAcc();
  for (const [sec, mid] of TICKS) acc.addTick(sec, mid);
  assert.deepEqual(acc.snapshot(), acc.snapshot());
});

// --- ③ activeSeconds 境界積分 ---
test('activeSeconds integrates active seconds and excludes an inactive hour boundary', () => {
  const table = Array.from({ length: 7 }, () => Array.from({ length: 24 }, () => true));
  table[0][3] = false;
  const a = DAY0 + 2 * 3600 + 1800;
  const b = DAY0 + 4 * 3600 + 600;
  assert.equal(activeSeconds(a, b, table), 2400);
});

// --- ③ valueArea 決定論タイブレーク（同値群が閾値を跨ぐ） ---
test('valueArea fixes the deterministic (ascending-index) tie-break when equal bins straddle the va_pct threshold', () => {
  const centers = [10, 20, 30, 40, 50];
  const tpo = [100, 50, 50, 50, 10];
  const [lo, hi] = valueArea(centers, tpo, 0.70);
  assert.equal(lo, 10);
  assert.equal(hi, 30);
});

// --- ③ 忠実 binning: wide/misaligned で fine-grid が mp_core と一致 ---
test('snapshot matches mp_core fine-grid golden for a wide, misaligned range (not display-direct)', () => {
  const acc = new DwellAccumulator();
  acc.init({
    baseFine: new Array(11).fill(0), baseKmin: 100, activeTable: ALL_ACTIVE,
    priceMin: 1005, priceMax: 1105, nBins: 6, gridW: 10, formingStart: DAY0 + 7200,
  });
  const wideTicks = [
    [DAY0 + 7200, 1006], [DAY0 + 7260, 1034], [DAY0 + 7300, 1039], [DAY0 + 7380, 1061],
    [DAY0 + 7500, 1006], [DAY0 + 7560, 1039], [DAY0 + 7700, 1072], [DAY0 + 7760, 1006],
  ];
  for (const [sec, mid] of wideTicks) acc.addTick(sec, mid);
  const snap = acc.snapshot();
  assert.deepEqual(snap.bins.map((x) => x.tpo), [120, 260, 0, 120, 60, 0]);
  assert.equal(snap.poc, 1030);
  assert.equal(snap.va_low, 1013.33);
  assert.equal(snap.va_high, 1063.33);
  assert.equal(snap.tpo_units, 560);
});
