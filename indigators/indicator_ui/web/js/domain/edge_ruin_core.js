// edge_ruin_core.js（domain）— Step 1「エッジと破産確率」の**権威の鏡**（ISSUE-368 スライス 2）。
//
// 権威は Python: simulator/usecase/edge_ruin.py（参照実装
//   integrated_position_sizing_calculator.html Step 1 の本実装化）。言語が違うため共有できないので
//   写しを 1 つだけ置き、golden fixture（simulator/tests/fixtures/edge_ruin/js_golden_cases.json）
//   との一致を tests/py_parity_edge_ruin.test.js が拘束する（.doc/LAYERING_CONVENTIONS.md:28-30）。
//
// 権威との対応（Python 側の行対応表 edge_ruin.py:11-23 を経由して参照実装 HTML へ辿れる）:
//   kelly_fraction             → kellyFraction          （HTML :582）
//   growth_rate                → growthRate             （HTML :583）
//   Mulberry32                 → Mulberry32             （HTML :686）
//   simulate_ruin_probability  → simulateRuinProbability（HTML :599-605）
//   solve_edge_ruin            → solveEdgeRuin          （HTML :624-644）
//
// 決定性: 参照実装 HTML はシード無しの Math.random を使うが、権威は「同一入力→同一出力」の
//   ため PRNG を Mulberry32＋シードに固定している。**本モジュールも Math.random を使わない**
//   （使うと golden 検定が統計比較に落ち、bit 単位の照合ができなくなる）。
//
// 乱数の消費順は権威と 1:1 に保つ（grid 昇順に sims 回ずつ → 最後にフルケリー点で sims*2 回）。
//   順序を変えると同一シードでも系列がずれる。
//
// 依存ゼロ（DOM・fetch・lwc を触らない）。Worker からも読める純関数のみ。

// HTML :598 const SIMS=4000
export const SIMS = 4000;
// HTML :628 steps=60
const STEPS = 60;
// mulberry32 の定数（HTML :686 そのまま）
const MULBERRY_INC = 0x6D2B79F5;

/**
 * 参照実装 HTML :686 の mulberry32（32bit PRNG）。Python 側 Mulberry32 と同一のビット列を出す。
 */
export class Mulberry32 {
  constructor(seed) {
    this._a = seed >>> 0;
  }

  /** [0,1) の一様乱数。 */
  random() {
    this._a = (this._a + MULBERRY_INC) >>> 0;
    let t = this._a;
    t = Math.imul(t ^ (t >>> 15), 1 | t);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  }
}

/** HTML :582 kelly(p,R) = (R·p − q)/R。EV≤0 のとき負値を返す（=賭けない）。 */
export function kellyFraction(p, payoffRatio) {
  const q = 1 - p;
  return (payoffRatio * p - q) / payoffRatio;
}

/**
 * HTML :583 幾何成長率 g(f) = p·ln(1+R·f) + q·ln(1−f)。
 * f<=0 は 0、f>=1 は −Infinity（参照実装の条件をそのまま保つ）。
 */
export function growthRate(f, p, payoffRatio) {
  if (f <= 0) {
    return 0;
  }
  if (f >= 1) {
    return -Infinity;
  }
  const q = 1 - p;
  return p * Math.log(1 + payoffRatio * f) + q * Math.log(1 - f);
}

/**
 * HTML :599-605 simRoR。対数資産が破産水準を割った試行の比率を返す。
 * rng は呼び出し側が保持する（参照実装が単一ストリームを grid の順に消費するのと同じ順序にするため）。
 */
export function simulateRuinProbability(f, p, payoffRatio, ruinLevel, horizon, sims, rng) {
  if (f <= 0) {
    return 0;
  }
  const logWin = Math.log(1 + payoffRatio * f);
  const lose = 1 - f;
  const logLose = lose > 0 ? Math.log(lose) : -Infinity;
  const logRuin = Math.log(ruinLevel);
  let ruined = 0;
  for (let s = 0; s < sims; s += 1) {
    let le = 0;
    for (let t = 0; t < horizon; t += 1) {
      le += rng.random() < p ? logWin : logLose;
      if (le <= logRuin) {
        ruined += 1;
        break;
      }
    }
  }
  return ruined / sims;
}

/**
 * HTML :624-644 runMC の計算部（DOM 描画を除く）。
 *
 * @param {{win_rate:number, payoff_ratio:number, ruin_level:number, alpha:number,
 *          horizon:number, split_count:number, seed:number, sims:number}} spec
 *   キー名は golden fixture（Python 権威の snake_case）に合わせる。
 * @returns {object} 権威 EdgeRuinResult の写し（JS 側は camelCase）
 */
export function solveEdgeRuin(spec) {
  const p = spec.win_rate;
  const payoffRatio = spec.payoff_ratio;
  const sims = spec.sims === undefined ? SIMS : spec.sims;
  const seed = spec.seed === undefined ? 1 : spec.seed;
  const q = 1 - p;
  const rng = new Mulberry32(seed);

  // :628 fk / fmax
  const fk = kellyFraction(p, payoffRatio);
  const fMax = Math.min(0.9, Math.max(0.35, (fk > 0 ? fk : 0.1) * 2.4));

  // :629 grid（i=1..steps）
  const grid = [];
  for (let i = 1; i <= STEPS; i += 1) {
    grid.push((fMax * i) / STEPS);
  }
  // :630 gPts
  const growthCurve = grid.map((f) => [f, growthRate(f, p, payoffRatio)]);
  // :631-633 rorPts（grid 昇順に消費）
  const rorCurve = grid.map((f) => [
    f, simulateRuinProbability(f, p, payoffRatio, spec.ruin_level, spec.horizon, sims, rng),
  ]);

  // :636-637 先頭から連続して RoR≤α である最後の格子点
  let fSafe = 0;
  let rorAtSafe = 0;
  for (const [f, ror] of rorCurve) {
    if (ror <= spec.alpha) {
      fSafe = f;
      rorAtSafe = ror;
    } else {
      break;
    }
  }
  // :638 最初の跨ぎ区間で線形補間（跨ぎが無ければ上の走査結果のまま）
  for (let i = 1; i < rorCurve.length; i += 1) {
    const [aF, aRor] = rorCurve[i - 1];
    const [bF, bRor] = rorCurve[i];
    if (aRor <= spec.alpha && spec.alpha < bRor) {
      const t = (spec.alpha - aRor) / (bRor - aRor);
      fSafe = aF + (bF - aF) * t;
      rorAtSafe = spec.alpha;
      break;
    }
  }

  // :639 フルケリー点の RoR は 2 倍の試行数で測る
  const rorAtKelly = fk > 0
    ? simulateRuinProbability(fk, p, payoffRatio, spec.ruin_level, spec.horizon, sims * 2, rng)
    : 0;
  // :640
  const gKelly = growthRate(Math.max(fk, 0), p, payoffRatio);
  const gSafe = growthRate(fSafe, p, payoffRatio);
  const refRuin = p > q ? (q / p) ** spec.split_count : 1;

  return {
    lossRate: q,
    expectedValue: payoffRatio * p - q,
    kellyFraction: fk,
    halfKellyFraction: Math.max(fk, 0) / 2,   // :643 S.fHalf
    constrainedFraction: fSafe,
    rorAtConstrained: rorAtSafe,
    rorAtKelly,
    growthAtKelly: gKelly,
    growthAtConstrained: gSafe,
    equalBetRuinReference: refRuin,
    fMax,
    rorCurve,
    growthCurve,
  };
}
