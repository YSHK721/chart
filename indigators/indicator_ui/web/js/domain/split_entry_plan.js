// split_entry_plan.js（domain）— Step 3「分割エントリー — f をロットに変換」の
//   **権威の鏡**（ISSUE-368 スライス 2）。
//
// 権威は Python: simulator/usecase/split_entry_plan.py（参照実装
//   integrated_position_sizing_calculator.html Step 3 の本実装化・TBD-1 で建値は価格の単一ソース）。
//   写しを 1 つだけ置き、golden fixture（simulator/tests/fixtures/split_entry/js_golden_cases.json）
//   との一致を tests/py_parity_split_entry.test.js が拘束する（.doc/LAYERING_CONVENTIONS.md:28-30）。
//
// 【設計書からの乖離・要裁定】設計 §8 は新規 domain を「import 0」と記しているが、本モジュールは
//   同一パッケージ内の ./account_margin_core.js を import する（import 1）。理由:
//   必要証拠金・ロスカット価格をここに書き下すと**証拠金式の第 2 実装**が生まれ、
//   絶対規律である複製禁止（sizing_ports.py:42-52・§12.3-3）に正面から違反する。
//   Python 権威も同じ理由で account_engine.official_* を「呼ぶ」形にしている。
//   層規約（.doc/LAYERING_CONVENTIONS / 設計 §9「domain: 自パッケージ内のみ」）は
//   domain 内 import を許しており、既存 domain にも同種の内部 import が 3 本ある
//   （domain_models→color_roles・tickvol_bands→tf_meta/session_day）。
//   「import 0」と「複製禁止」が両立しないため、**複製禁止を優先**した。
//
// 演算の順序・反復回数・上限は Python 権威と 1:1 に保つ（IEEE754 は結合則を満たさないため、
//   順序を変えると最終桁がずれて golden 検定が落ちる）。
//
// 依存: 同一 domain パッケージのみ（DOM・fetch・lwc を触らない）。Worker からも読める。

import { losscutPrice, requiredMargin } from './account_margin_core.js';

export const LONG = 'long';
export const SHORT = 'short';

// 参照実装 :484 の K の下限・上限
export const MIN_SPLITS = 1;
export const MAX_SPLITS = 10;

export const WEIGHT_PATTERNS = ['equal', 'linear', 'double', 'custom'];
export const LOT_MODES = ['int', 'dec'];
export const CAP_BASES = ['margin', 'lc'];

// cap_lot の二分探索（Python 権威と同一の上限・反復回数）
const BISECT_ITERS = 200;
const BISECT_UPPER = 1e18;

// :1029 marginBinds の判定に使う許容（参照実装そのまま）
const BINDS_EPS = 1e-9;

/**
 * :880-888 genWeights(K,pattern)。equal=1 / linear=i+1 / double=2^i / custom。
 * @param {number} splits 分割本数 K
 * @param {string} pattern
 * @param {number[]|null} [customWeights]
 * @returns {number[]}
 */
export function generateWeights(splits, pattern, customWeights = null) {
  if (!WEIGHT_PATTERNS.includes(pattern)) {
    throw new Error(`未知の weight_pattern です: ${pattern}（既知: ${WEIGHT_PATTERNS.join(', ')}）`);
  }
  if (pattern === 'custom') {
    if (customWeights === null || customWeights === undefined) {
      throw new Error("weight_pattern='custom' には custom_weights が必要です");
    }
    if (customWeights.length < splits) {
      throw new Error(`custom_weights の長さが分割数に足りません: ${customWeights.length} < ${splits}`);
    }
    return customWeights.slice(0, splits);
  }
  const out = [];
  for (let i = 0; i < splits; i += 1) {
    if (pattern === 'equal') {
      out.push(1);
    } else if (pattern === 'double') {
      out.push(2 ** i);
    } else {
      out.push(i + 1);
    }
  }
  return out;
}

/**
 * ロスカットが target より手前に来ない最大の合計ロット（:1017-1023 の権威版）。
 * 閉形式を書き下さず、単調性を使った二分探索で account_margin_core.losscutPrice に判定させる。
 */
function maxLotWithLosscutBeyondTarget(direction, avgPrice, target, balance, marginRate, pointValue) {
  const long = direction === LONG;
  const safe = (totalLot) => {
    const price = losscutPrice(direction, [{ price: avgPrice, units: totalLot }],
      balance, marginRate, pointValue);
    if (price === null) {
      return true;
    }
    return long ? price <= target : price >= target;
  };
  if (safe(BISECT_UPPER)) {
    return Infinity;
  }
  let lo = 0;
  let hi = BISECT_UPPER;
  for (let i = 0; i < BISECT_ITERS; i += 1) {
    const mid = (lo + hi) / 2;
    if (mid <= lo || mid >= hi) {
      break;
    }
    if (safe(mid)) {
      lo = mid;
    } else {
      hi = mid;
    }
  }
  return lo;
}

/**
 * :956-1031 build() の計算部（DOM 直読み num() を除いた純関数部）。
 *
 * @param {{direction:string, entry_prices:number[], stop_price:number, take_price:(number|null),
 *          fraction:number, balance:number, point_value:number, margin_rate:number,
 *          win_rate:number, weight_pattern:string, custom_weights:(number[]|null),
 *          lot_mode:string, cap_basis:string}} spec
 *   キー名は golden fixture（Python 権威の snake_case）に合わせる。
 * @returns {object} 権威 SplitEntryPlan の写し（キーは fixture と同じ snake_case）
 */
export function buildSplitEntryPlan(spec) {
  const direction = spec.direction;
  if (direction !== LONG && direction !== SHORT) {
    throw new Error(`direction は '${LONG}' / '${SHORT}' です: ${direction}`);
  }
  const prices = spec.entry_prices.map(Number);
  const k = prices.length;
  if (k < MIN_SPLITS || k > MAX_SPLITS) {
    throw new Error(`entry_prices は ${MIN_SPLITS}〜${MAX_SPLITS} 本です: ${k}`);
  }
  if (!LOT_MODES.includes(spec.lot_mode)) {
    throw new Error(`lot_mode は ${LOT_MODES.join(', ')} です: ${spec.lot_mode}`);
  }
  if (!CAP_BASES.includes(spec.cap_basis)) {
    throw new Error(`cap_basis は ${CAP_BASES.join(', ')} です: ${spec.cap_basis}`);
  }
  const long = direction === LONG;
  const balance = spec.balance;
  const pointValue = spec.point_value;
  const marginRate = spec.margin_rate;
  const stop = spec.stop_price;

  // :965 損切り側に最も近い建玉
  const nearest = long ? Math.min(...prices) : Math.max(...prices);
  // :966-972 損切りは価格指定系統のみ移植。D は nearest から逆算する。
  let stopInvalid = !(long ? nearest - stop > 0 : stop - nearest > 0);

  // :974 各建玉→損切りの向き付き距離
  const distances = [];
  for (const price of prices) {
    const di = long ? price - stop : stop - price;
    distances.push(di);
    if (!(di > 0)) {
      stopInvalid = true;
    }
  }

  // :975-979 重み・基準ロット・各建玉のロット
  const weights = generateWeights(k, spec.weight_pattern, spec.custom_weights ?? null);
  let swd = 0;
  for (let i = 0; i < k; i += 1) {
    swd += weights[i] * distances[i];
  }
  const baseLot = swd > 0 ? (spec.fraction * balance) / (pointValue * swd) : 0;
  const lotsRaw = weights.map((w) => baseLot * w);
  // :979 OANDA は整数・切り捨て（保守側）
  const lots = spec.lot_mode === 'int' ? lotsRaw.map((x) => Math.floor(x)) : lotsRaw.slice();

  // :980-985 合計・リスク配分・加重平均建値
  let totalLot = 0;
  let sumLd = 0;
  let sumLp = 0;
  for (let i = 0; i < k; i += 1) {
    totalLot += lots[i];
    sumLd += lots[i] * distances[i];
    sumLp += lots[i] * prices[i];
  }
  const riskShares = [];
  for (let i = 0; i < k; i += 1) {
    riskShares.push(sumLd > 0 ? (lots[i] * distances[i]) / sumLd : 0);
  }
  const totalRisk = pointValue * sumLd;
  const avgPrice = totalLot > 0 ? sumLp / totalLot : 0;
  const roundZeroed = spec.lot_mode === 'int'
    && lots.some((li, i) => li === 0 && lotsRaw[i] > 0);

  // :986-995 利確ブロック。TP は「第1建値 P₀ からの値幅」で、TBD-1 で P₀ 入力が消えた後は
  //   entry_prices[0] がその役割を負う（:359 のラベル「第1建値 P₀」＝:931 の direct 既定シード）。
  let rr = null;
  let profitYen = null;
  let profitRate = null;
  let breakeven = null;
  let excess = null;
  let evYen = null;
  if (spec.take_price !== null && spec.take_price !== undefined) {
    const first = prices[0];
    const takeDistance = long ? spec.take_price - first : first - spec.take_price;
    if (takeDistance > 0 && totalLot > 0) {
      const target = long ? first + takeDistance : first - takeDistance;
      let profit = 0;
      for (let i = 0; i < k; i += 1) {
        profit += lots[i] * Math.abs(target - prices[i]);
      }
      rr = profit / (totalRisk / pointValue);
      profitYen = profit * pointValue;
      profitRate = balance > 0 ? profitYen / balance : 0;
      breakeven = 1 / (1 + rr);
      excess = spec.win_rate - breakeven;
      evYen = spec.win_rate * profitYen - (1 - spec.win_rate) * totalRisk;
    }
  }

  // :996 損失率
  const lossRate = balance > 0 ? totalRisk / balance : 0;

  // :998-1001 必要証拠金・使用率（権威の鏡へ委譲）
  const entries = [];
  for (let i = 0; i < k; i += 1) {
    entries.push({ price: prices[i], units: lots[i] });
  }
  const reqMargin = requiredMargin(entries, marginRate, pointValue);
  const marginUse = balance > 0 ? reqMargin / balance : 0;

  // :1009-1011 ロスカット（権威の鏡へ委譲。距離は加重平均建値からの向き付き差として派生）
  let lcPrice;
  let lcDistance;
  if (totalLot > 0) {
    lcPrice = losscutPrice(direction, entries, balance, marginRate, pointValue);
    lcDistance = long ? avgPrice - lcPrice : lcPrice - avgPrice;
  } else {
    // :1009 U=0 のとき参照実装は逆行距離 0・価格は avgP（=0）とする
    lcDistance = 0;
    lcPrice = long ? avgPrice - lcDistance : avgPrice + lcDistance;
  }
  const immediateLc = marginUse >= 1;

  // :1012-1013
  const stopDistance = Math.abs(avgPrice - stop);
  const lcBeforeStop = immediateLc || lcDistance < stopDistance;

  // :1015-1024 建て制約
  const marginCapLot = marginUse > 0 ? totalLot / marginUse : Infinity;
  let capTarget = null;
  let capLot;
  if (spec.cap_basis === 'lc') {
    capTarget = stop;             // :1018 ロスカット目標＝損切り価格
    capLot = maxLotWithLosscutBeyondTarget(direction, avgPrice, capTarget,
      balance, marginRate, pointValue);
  } else {
    capLot = marginCapLot;
  }

  // :1025-1029
  const scale = totalLot > 0 ? Math.min(1, capLot / totalLot) : 1;
  let buildableLot = totalLot * scale;
  if (spec.lot_mode === 'int') {
    buildableLot = Math.floor(buildableLot);
  }
  const effectiveRisk = totalLot > 0 ? totalRisk * (buildableLot / totalLot) : 0;
  const marginBinds = capLot < totalLot - BINDS_EPS;

  return {
    distances,
    entry_prices: prices,
    weights,
    weighted_distance_sum: swd,
    base_lot: baseLot,
    lots_raw: lotsRaw,
    lots,
    total_lot: totalLot,
    avg_price: avgPrice,
    risk_shares: riskShares,
    total_risk: totalRisk,
    loss_rate: lossRate,
    stop_price: stop,
    stop_invalid: stopInvalid,
    round_zeroed: roundZeroed,
    rr,
    profit_yen: profitYen,
    profit_rate: profitRate,
    breakeven,
    excess,
    ev_yen: evYen,
    win_rate: spec.win_rate,
    required_margin: reqMargin,
    margin_use: marginUse,
    losscut_price: lcPrice,
    losscut_distance: lcDistance,
    stop_distance: stopDistance,
    lc_before_stop: lcBeforeStop,
    immediate_lc: immediateLc,
    cap_target: capTarget,
    cap_lot: capLot,
    scale,
    buildable_lot: buildableLot,
    effective_risk: effectiveRisk,
    margin_binds: marginBinds,
  };
}
