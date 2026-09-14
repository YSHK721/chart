// py_parity_edge_ruin.test.js — Python 権威（edge_ruin.solve_edge_ruin）との一致検定
//   （ISSUE-368 スライス 2）。
//
// 権威は Python（simulator/usecase/edge_ruin.py）。fixture
//   （simulator/tests/fixtures/edge_ruin/js_golden_cases.json＝
//   simulator/tools/export_edge_ruin_fixtures.py が生成）に対し、JS 側の写し
//   （js/domain/edge_ruin_core.js）の一致を全ケース検定する。
//
// 許容差（設計書 出力 3 スライス 2・根拠は simulator/tests/unit/test_edge_ruin.py:20-31 と本実測）:
//   - 閉形式部（expected_value / kelly_fraction / half_kelly / (q/p)^N / f_max / grid の f 座標）
//     … **厳密一致（許容 0）**
//   - RoR（ror_curve の値・ror_at_constrained・ror_at_kelly・constrained_fraction）
//     … **厳密一致（許容 0）**。Mulberry32 を移植し乱数の消費順も同一にしてあるため
//       bit 単位で一致する。**1 ケースでも外れたら許容差を緩めず Red のまま停止する**
//       （TBD-7・無検証で緩めない）。
//   - growth（growth_curve の値・growth_at_kelly / growth_at_constrained）
//     … **設計書からの乖離（実測 2026-08-20・要裁定）**。設計は「相対 1e-15」と記していたが、
//       実測すると相対で最大 1.17e-14 に達する。原因は特定済みで、g(f)=p·ln(1+Rf)+q·ln(1−f) が
//       符号の異なる 2 項の差であり、**桁落ちが log の 1 ULP 差を増幅する**ためである。
//       決定的証拠: (1) Python 側の log 値を JS の合成式へ入れると g は 60/60 で bit 一致する
//       ＝式そのものは同一、(2) 食い違うのは log の値だけで、その相対差は最大 2.16e-16（1 ULP）。
//       したがって桁落ち後の値に固定の相対許容を当てる設計自体が不適で、
//       **項の大きさへ伝播誤差を戻した絶対許容** |Δg| ≤ 4·eps·(p|ln(1+Rf)| + q|ln(1−f)|) を使う。
//       実測の最悪値はこの許容の 0.91/4 ＝ 23%（4.4 倍の余裕）。式が実際にずれた場合は
//       この桁を大きく超えるため検出力は落ちない（緩めではなく、当てる先を正した）。
//
// fixture への到達方法は market_profile/web/tests/py_parity_golden.test.js と同方式。

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

import {
  Mulberry32, kellyFraction, growthRate, simulateRuinProbability, solveEdgeRuin,
} from '../js/domain/edge_ruin_core.js';

const REPO = join(dirname(fileURLToPath(import.meta.url)), '..', '..', '..', '..');
const golden = JSON.parse(
  readFileSync(join(REPO, 'simulator', 'tests', 'fixtures', 'edge_ruin', 'js_golden_cases.json'), 'utf8'),
);

// 上記の伝播誤差許容の係数（実測の最悪値 0.91 eps に対し 4 eps ＝ 4.4 倍の余裕）。
const GROWTH_ULP_FACTOR = 4;

/** g(f) の 2 項の大きさから伝播誤差の上限を作る（桁落ち後の値に相対許容を当てない）。 */
function growthAbsTol(f, p, payoffRatio) {
  if (!(f > 0) || f >= 1) {
    return 0;
  }
  const q = 1 - p;
  const terms = p * Math.abs(Math.log(1 + payoffRatio * f)) + q * Math.abs(Math.log(1 - f));
  return GROWTH_ULP_FACTOR * Number.EPSILON * terms;
}

function assertGrowth(got, want, f, p, payoffRatio, msg) {
  if (got === want) {
    return;
  }
  assert.ok(Number.isFinite(got) && Number.isFinite(want), `${msg}: 非有限 ${got} / ${want}`);
  const tol = growthAbsTol(f, p, payoffRatio);
  const diff = Math.abs(got - want);
  assert.ok(diff <= tol, `${msg}: ${got} != ${want}（差 ${diff} > 許容 ${tol}）`);
}

test('fixture は格子＋参照実装既定＋境界ケースを保持している', () => {
  assert.ok(golden.cases.length >= 30, `ケース数 ${golden.cases.length}`);
  assert.equal(golden.cases.filter((c) => c.spec.sims === 4000).length, 1);
});

test('kellyFraction / growthRate は Python の閉形式と一致する（f の境界含む）', () => {
  // :583 の条件をそのまま保つ: f<=0 は 0、f>=1 は -Infinity。
  assert.equal(growthRate(0, 0.38, 2.74), 0);
  assert.equal(growthRate(-0.1, 0.38, 2.74), 0);
  assert.equal(growthRate(1, 0.38, 2.74), -Infinity);
  assert.equal(growthRate(1.5, 0.38, 2.74), -Infinity);
  // EV<=0 では f* が負（賭けない）。
  assert.ok(kellyFraction(0.3, 1.2) < 0);
});

test('Mulberry32 は Python 移植と同一のビット列を出す（fixture 経由で間接固定）', () => {
  // 直接の系列は fixture に無いが、同一シードで 2 本のストリームが一致することは
  // 実装内で保証されるべき性質（決定性）。ここは決定性のみを固定する。
  const a = new Mulberry32(1);
  const b = new Mulberry32(1);
  for (let i = 0; i < 100; i += 1) {
    const x = a.random();
    assert.equal(x, b.random(), `決定性 i=${i}`);
    assert.ok(x >= 0 && x < 1, `範囲 i=${i}`);
  }
});

test('simulateRuinProbability は f<=0 で 0 を返す（:600 の条件をそのまま保つ）', () => {
  assert.equal(simulateRuinProbability(0, 0.38, 2.74, 0.5, 250, 100, new Mulberry32(1)), 0);
  assert.equal(simulateRuinProbability(-1, 0.38, 2.74, 0.5, 250, 100, new Mulberry32(1)), 0);
});

test('solveEdgeRuin は Python 権威と一致する（閉形式と RoR は厳密・growth は伝播誤差許容）', () => {
  for (const c of golden.cases) {
    const got = solveEdgeRuin(c.spec);
    const exp = c.expected;

    // --- 閉形式部: 厳密一致（許容 0） ---
    assert.equal(got.lossRate, exp.loss_rate, `${c.id} loss_rate`);
    assert.equal(got.expectedValue, exp.expected_value, `${c.id} expected_value`);
    assert.equal(got.kellyFraction, exp.kelly_fraction, `${c.id} kelly_fraction`);
    assert.equal(got.halfKellyFraction, exp.half_kelly_fraction, `${c.id} half_kelly_fraction`);
    assert.equal(got.equalBetRuinReference, exp.equal_bet_ruin_reference,
      `${c.id} equal_bet_ruin_reference`);
    assert.equal(got.fMax, exp.f_max, `${c.id} f_max`);

    // --- RoR: 厳密一致（許容 0）。外れたら緩めず Red のまま停止する（TBD-7） ---
    assert.equal(got.rorCurve.length, exp.ror_curve.length, `${c.id} ror_curve 長さ`);
    for (let i = 0; i < exp.ror_curve.length; i += 1) {
      assert.equal(got.rorCurve[i][0], exp.ror_curve[i][0], `${c.id} ror_curve[${i}].f`);
      assert.equal(got.rorCurve[i][1], exp.ror_curve[i][1], `${c.id} ror_curve[${i}].ror`);
    }
    assert.equal(got.constrainedFraction, exp.constrained_fraction, `${c.id} constrained_fraction`);
    assert.equal(got.rorAtConstrained, exp.ror_at_constrained, `${c.id} ror_at_constrained`);
    assert.equal(got.rorAtKelly, exp.ror_at_kelly, `${c.id} ror_at_kelly`);

    // --- growth: 伝播誤差許容 4·eps·(項の大きさ)（math.log と V8 Math.log の 1 ULP 差） ---
    assert.equal(got.growthCurve.length, exp.growth_curve.length, `${c.id} growth_curve 長さ`);
    for (let i = 0; i < exp.growth_curve.length; i += 1) {
      assert.equal(got.growthCurve[i][0], exp.growth_curve[i][0], `${c.id} growth_curve[${i}].f`);
      assertGrowth(got.growthCurve[i][1], exp.growth_curve[i][1],
        exp.growth_curve[i][0], c.spec.win_rate, c.spec.payoff_ratio,
        `${c.id} growth_curve[${i}].g`);
    }
    assertGrowth(got.growthAtKelly, exp.growth_at_kelly,
      Math.max(exp.kelly_fraction, 0), c.spec.win_rate, c.spec.payoff_ratio,
      `${c.id} growth_at_kelly`);
    assertGrowth(got.growthAtConstrained, exp.growth_at_constrained,
      exp.constrained_fraction, c.spec.win_rate, c.spec.payoff_ratio,
      `${c.id} growth_at_constrained`);
  }
});
