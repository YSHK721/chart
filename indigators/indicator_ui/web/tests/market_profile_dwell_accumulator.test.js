// market_profile_dwell_accumulator.js（domain・依存ゼロ純ロジック）の検証。
//
// 設計入力: Phase2 設計 mp_ticklive_design.md「新規 domain DwellAccumulator」。
//   参照実装 prototype_260630-01/mp_core.py（_session_dwell/_active_seconds_cross/_value_area）と
//   数値一致（POC/VA/形）。golden 値は本番 market_profile_dwell.compute_dwell_profile（mp_core 等価）を
//   同一 tick 列・all-active table・range=[1000,1100]/nBins=10（binw=GRID_W=10 で整列）で実測した基準:
//     POC=1005, VA=[1005,1015], units=700, bins.tpo=[300,250,150,0,0,0,0,0,0,0]
//     activeSeconds(hr2:30→hr4:10, Mon hr3 休場)=2400
//   忠実 binning（Task A 是正）: base も forming も GRID_W 固定グリッド（fine grid）へ累積し snapshot で
//   表示 bin 再集計する＝mp_core.compute_profile と厳密一致。旧「forming を表示 bin 直接」方式は広レンジ
//   （binw≫gridW）や misaligned priceMin で POC/VA を最大 5bin ずらす（実 tick で実証済み）。下段の
//   「wide/misaligned」テストがこの是正を固定する（fine-grid golden vs 旧直接方式の乖離を検出）。
// DOM/chart/fetch 非依存。構造: Arrange-Act-Assert。

import { test } from 'node:test';
import assert from 'node:assert/strict';

import {
  DwellAccumulator,
  activeSeconds,
  valueArea,
} from '../js/domain/market_profile_dwell_accumulator.js';

const DAY0 = 1704067200; // 2024-01-01 00:00 UTC（月曜・UTC 真夜中）。
// 全 True の 7×24 活動テーブル（activeSeconds が全区間 gap = 秒差になる）。
const ALL_ACTIVE = Array.from({ length: 7 }, () => Array.from({ length: 24 }, () => true));

// 同一時内（hr2）・all-active の 4 tick。dwell: 300→bin0(1005), 250→bin1(1015), 150→bin2(1025), 末尾=0。
const TICKS = [
  [DAY0 + 7200, 1005],
  [DAY0 + 7500, 1015],
  [DAY0 + 7750, 1025],
  [DAY0 + 7900, 1005],
];

// base は GRID_W 固定グリッド（fine grid・kmin=100＝price 1000 起点）で受ける。整列レンジ
//   （binw==gridW==10・priceMin=1000=gridW 倍数）では fine bin i が表示 bin i へ 1:1 対応するため、
//   表示 bin 配列をそのまま baseFine として渡せる（golden 不変）。
function makeAcc(baseFine) {
  const acc = new DwellAccumulator();
  acc.init({
    baseFine: baseFine ?? new Array(10).fill(0),
    baseKmin: 100,
    activeTable: ALL_ACTIVE,
    priceMin: 1000,
    priceMax: 1100,
    nBins: 10,
    gridW: 10,
    formingStart: DAY0 + 7200,
  });
  return acc;
}

// --- mp_core 数値一致（POC/VA/形） ---
test('snapshot reproduces mp_core POC/VA/units/bins for the reference tick sequence', () => {
  // Arrange
  const acc = makeAcc();
  // Act
  for (const [sec, mid] of TICKS) acc.addTick(sec, mid);
  const snap = acc.snapshot();
  // Assert: 本番 compute_dwell_profile（mp_core 等価）実測 golden と一致。
  //   MP-03 golden 導出根拠（mp_core.compute_profile src="dwell" / _session_dwell の式・all-active table）:
  //     TICKS=4本（全て hr2・all-active）。dwell[i] = 隣接 gap（次 tick までの活発秒）を tick i の価格 bin へ。
  //       tick0(1005): gap=7500-7200=300 → bin floor(1005/10)=100(fine0=display0) に +300
  //       tick1(1015): gap=7750-7500=250 → display1 に +250
  //       tick2(1025): gap=7900-7750=150 → display2 に +150
  //       tick3(1005): 末尾＝次 tick 未着で dwell=0
  //     ∴ bins.tpo=[300,250,150,0..]、units=Σdwell=700。
  //     POC=argmax=bin0(300)→center=1005。VA（_value_area・va_pct=0.70）: total=700, threshold=490。
  //       降順採用 {bin0:300, bin1:250} で cum=550≥490 → chosen={0,1} → center min/max=[1005,1015]。
  assert.equal(snap.poc, 1005);
  assert.equal(snap.va_low, 1005);
  assert.equal(snap.va_high, 1015);
  assert.equal(snap.tpo_units, 700);
  assert.deepEqual(snap.bins.map((b) => b.tpo), [300, 250, 150, 0, 0, 0, 0, 0, 0, 0]);
  assert.deepEqual(snap.bins.map((b) => b.price), [1005, 1015, 1025, 1035, 1045, 1055, 1065, 1075, 1085, 1095]);
  assert.equal(snap.n_bins, 10);
  assert.equal(snap.price_min, 1000);
  assert.equal(snap.price_max, 1100);
});

// --- 最新 tick dwell=0（次 tick 未着まで滞在ゼロ） ---
test('the latest tick contributes dwell=0 until the next tick arrives', () => {
  // Arrange
  const acc = makeAcc();
  // Act: 最初の 1 tick のみ（後続なし）→ forming は全ゼロ
  acc.addTick(TICKS[0][0], TICKS[0][1]);
  const afterOne = acc.snapshot();
  // 2 tick 目到着で 1 tick 目の gap(300) が確定
  acc.addTick(TICKS[1][0], TICKS[1][1]);
  const afterTwo = acc.snapshot();
  // Assert
  assert.equal(afterOne.tpo_units, 0, '最新 tick は次 tick 未着まで dwell=0');
  assert.equal(afterTwo.tpo_units, 300, '2 tick 目到着で 1 tick 目 gap=300 が確定');
  assert.equal(afterTwo.bins[0].tpo, 300);
});

// --- rollover reset（init 再実行で forming クリア） ---
test('init resets the forming accumulation (rollover)', () => {
  // Arrange
  const acc = makeAcc();
  for (const [sec, mid] of TICKS) acc.addTick(sec, mid);
  assert.equal(acc.snapshot().tpo_units, 700);
  // Act: 新しい足へ rollover = init 再実行
  acc.init({
    baseFine: new Array(10).fill(0),
    baseKmin: 100,
    activeTable: ALL_ACTIVE,
    priceMin: 1000, priceMax: 1100, nBins: 10, gridW: 10, formingStart: DAY0 + 10800,
  });
  const snap = acc.snapshot();
  // Assert: forming が全リセット（base のみ＝ゼロ）
  assert.equal(snap.tpo_units, 0);
  assert.deepEqual(snap.bins.map((b) => b.tpo), new Array(10).fill(0));
});

// --- 空 tick = base のみ ---
test('with no ticks the snapshot equals the base bins only', () => {
  // Arrange: 非ゼロ base
  const base = [100, 50, 0, 0, 0, 0, 0, 0, 0, 0];
  const acc = makeAcc(base);
  // Act
  const snap = acc.snapshot();
  // Assert
  assert.deepEqual(snap.bins.map((b) => b.tpo), base);
  assert.equal(snap.tpo_units, 150);
  assert.equal(snap.poc, 1005);
});

// --- combined = base + forming（binning 線形性） ---
test('snapshot combines base and forming linearly per display bin', () => {
  // Arrange: base bin0=1000
  const base = [1000, 0, 0, 0, 0, 0, 0, 0, 0, 0];
  const acc = makeAcc(base);
  // Act: forming で bin0 に +300、bin1 に +250、bin2 に +150
  for (const [sec, mid] of TICKS) acc.addTick(sec, mid);
  const snap = acc.snapshot();
  // Assert: 表示 bin ごとに base + forming
  assert.equal(snap.bins[0].tpo, 1300); // 1000 + 300
  assert.equal(snap.bins[1].tpo, 250);
  assert.equal(snap.bins[2].tpo, 150);
  assert.equal(snap.tpo_units, 1700);
});

// --- base 不変（入力配列を破壊しない） ---
test('init does not mutate the caller-supplied baseFine array', () => {
  // Arrange
  const base = [5, 0, 0, 0, 0, 0, 0, 0, 0, 0];
  const acc = makeAcc(base);
  // Act
  for (const [sec, mid] of TICKS) acc.addTick(sec, mid);
  acc.snapshot();
  // Assert: 呼び出し側の base 配列は不変
  assert.deepEqual(base, [5, 0, 0, 0, 0, 0, 0, 0, 0, 0]);
});

// --- snapshot は純（再呼び出しで同値・状態を破壊しない） ---
test('snapshot is pure: repeated calls return equal results without mutating state', () => {
  // Arrange
  const acc = makeAcc();
  for (const [sec, mid] of TICKS) acc.addTick(sec, mid);
  // Act
  const a = acc.snapshot();
  const b = acc.snapshot();
  // Assert
  assert.deepEqual(a, b);
});

// --- activeSeconds: 時間境界を跨ぎ休場時を除外（_active_seconds_cross 相当） ---
test('activeSeconds integrates active seconds and excludes an inactive hour boundary', () => {
  // Arrange: Mon hr3 のみ休場、hr2:30 → hr4:10 を積分
  const table = Array.from({ length: 7 }, () => Array.from({ length: 24 }, () => true));
  table[0][3] = false; // Mon（wd=0）hr3 休場
  const a = DAY0 + 2 * 3600 + 1800; // hr2:30
  const b = DAY0 + 4 * 3600 + 600;  // hr4:10
  // Act
  const s = activeSeconds(a, b, table);
  // Assert: hr2 内 1800s + hr3 休場 0 + hr4 内 600s = 2400（本番 _active_seconds_cross golden）
  assert.equal(s, 2400);
});

test('activeSeconds within a single active hour equals the raw gap', () => {
  // Arrange
  const a = DAY0 + 7200;
  const b = DAY0 + 7260;
  // Act / Assert
  assert.equal(activeSeconds(a, b, ALL_ACTIVE), 60);
});

// --- valueArea: mp_core _value_area 相当（降順・同値は index 昇順・min/max center） ---
test('valueArea selects bins by descending tpo (ties by ascending index) to reach va_pct', () => {
  // Arrange: centers 0..3, tpo [300,250,150,0]、total=700、threshold=490
  const centers = [1005, 1015, 1025, 1035];
  const tpo = [300, 250, 150, 0];
  // Act
  const [lo, hi] = valueArea(centers, tpo, 0.70);
  // Assert: bin0(300)+bin1(250)=550≥490 → {0,1} → [1005,1015]
  assert.equal(lo, 1005);
  assert.equal(hi, 1015);
});

// --- MP-02 境界 tie golden: 同値 bin 群が VA 70% 閾値を跨ぐ配置で決定論タイブレークを固定する ---
//   参照 mp_core._value_area は np.argsort[::-1]（unstable）で同値 bin の順序が非決定論＝厳密一致は元来
//   不能。本実装は「降順・同値は index 昇順」を**意図的な決定論方針**として採用する（accumulator の
//   valueArea コメント参照）。本テストはその方針を回帰的に固定する。
//   設計: centers=[10,20,30,40,50] tpo=[100,50,50,50,10]、total=260、threshold=0.70*260=182。
//     降順・同値 index 昇順の採用順 = [i0(100), i1(50), i2(50), i3(50), i4(10)]。
//     累積 100→150→200(≥182 で break) ＝ 採用 bin {i0,i1,i2}。同値群 {i1,i2,i3} は閾値を跨ぎ i3 だけ
//     除外される。ここで採用 center = {10,20,30} → va_low=10 / va_high=30。
//   非決定論（index 降順）だったなら {i0,i3,i2} が採られ va_high=40 になる＝分岐しうる。決定論方針が
//   va_high=30 に固定することを golden として明示する。
test('valueArea fixes the deterministic (ascending-index) tie-break when equal bins straddle the va_pct threshold', () => {
  // Arrange
  const centers = [10, 20, 30, 40, 50];
  const tpo = [100, 50, 50, 50, 10];
  // Act
  const [lo, hi] = valueArea(centers, tpo, 0.70);
  // Assert: 決定論タイブレーク（index 昇順）で採用 {i0,i1,i2} → [10, 30]。index 降順なら hi=40 に分岐。
  assert.equal(lo, 10);
  assert.equal(hi, 30, '同値群が閾値を跨ぐとき決定論タイブレークが va_high=30 に固定する（非決定論なら 40）');
});

// --- Task A 忠実 binning: wide/misaligned レンジで fine-grid が mp_core と厳密一致（旧直接方式と乖離） ---
//   range=[1005,1105]/nBins=6（binw=16.67≫gridW=10・priceMin=1005 は gridW 倍数でない）。fine grid
//   kw0=100/size=11。golden は mp_core.compute_profile（＝fine grid→表示 bin 再集計）の実測:
//     bins.tpo=[120,260,0,120,60,0] POC=1030 VA=[1013.33,1063.33] units=560。
//   旧「forming を表示 bin 直接」方式は bins.tpo=[120,40,220,120,60,0]/POC=1046.67 となり POC が 1bin ずれる。
test('snapshot matches mp_core fine-grid golden for a wide, misaligned range (not display-direct)', () => {
  // Arrange: base 空（forming のみ）で fine-grid 忠実性を直接検証する。
  const acc = new DwellAccumulator();
  acc.init({
    baseFine: new Array(11).fill(0), // fine grid size = floor(1105/10)-floor(1005/10)+1 = 11
    baseKmin: 100,
    activeTable: ALL_ACTIVE,
    priceMin: 1005, priceMax: 1105, nBins: 6, gridW: 10, formingStart: DAY0 + 7200,
  });
  // 同一活発時内(hr2)の 8 tick。dwell=gap（末尾=0）。価格は fine/display で帰属 bin が食い違う配置。
  const wideTicks = [
    [DAY0 + 7200, 1006], [DAY0 + 7260, 1034], [DAY0 + 7300, 1039], [DAY0 + 7380, 1061],
    [DAY0 + 7500, 1006], [DAY0 + 7560, 1039], [DAY0 + 7700, 1072], [DAY0 + 7760, 1006],
  ];
  // Act
  for (const [sec, mid] of wideTicks) acc.addTick(sec, mid);
  const snap = acc.snapshot();
  // Assert: fine-grid（mp_core 忠実）golden。旧直接方式の [120,40,220,120,60,0]/POC=1046.67 とは非一致。
  //   MP-03 golden 導出根拠（mp_core.compute_profile: fine grid 累積 → 表示 bin 再集計・binw=100/6=16.667）:
  //     fine grid kw0=floor(1005/10)=100, size=floor(1105/10)-100+1=11。dwell を tick 価格の fine bin へ:
  //       off0(price 100x): t0(1006)60 + t4(1006)60 = 120
  //       off3(103x):       t1(1034)40 + t2(1039)80 + t5(1039)140 = 260
  //       off6(106x):       t3(1061)120         off7(107x): t6(1072)60   （末尾 t7 は dwell=0）
  //     fine→表示 bin: center_fine=(100+i+0.5)*10=1005+10i、disp=clip(floor((center_fine-1005)/16.667),0,5):
  //       off0→disp0(+120), off3(center1035)→disp1(+260), off6(center1065)→disp3(+120), off7(center1075)→disp4(+60)
  //     ∴ tpo=[120,260,0,120,60,0]、units=560。POC=argmax=disp1→center=1005+1.5*16.667=1030。
  //     VA（threshold=0.70*560=392）: 降順採用 {disp1:260, disp0:120, disp3:120}（同値 disp0/disp3 は index 昇順）
  //       で cum=500≥392 → center min=disp0(1013.33)/max=disp3(1063.33)。
  assert.deepEqual(snap.bins.map((b) => b.tpo), [120, 260, 0, 120, 60, 0]);
  assert.equal(snap.poc, 1030);
  assert.equal(snap.va_low, 1013.33);
  assert.equal(snap.va_high, 1063.33);
  assert.equal(snap.tpo_units, 560);
});
