// mp_bin — 借用した MP プロファイルの「価格 → bin の密度」写像（domain・純関数）。
//
// 設計入力（依頼者承認 2026-09-06「MP 列＝ライブ MP の借用」・裁定 5/6）:
//   ラダーの MP 列は live core の `/market_profile` 応答をそのまま引く。応答は bin の**配列**を
//   運ぶので、行の価格がどの bin に入るかを版面側で決める必要がある。
//
// なぜ zp 式でなければならないか（実測 2026-09-06）:
//   借用する既定ソースは zp（`MP_DEFAULT_SOURCE`）であり、その bin 帰属の参照実装は
//   `market_profile_api/compute/market_profile_zp.py:400,441,458`:
//       binw  = (price_max - price_min) / n_bins
//       index = clip(int((price - price_min) / binw), 0, n_bins - 1)
//   一方 dashboard のサーバ側 `_norm_at`（market_profile_gateway.py:239）は
//       index = min(n_bins - 1, int((price - price_min) / span * n_bins))
//   を使う。これは **candle 版 MP core** に合わせた式で、zp とは丸めが 1 回ずれる。
//   代数的には同値だが浮動小数では別物で、price_min=38000 / price_max=42000 / n_bins=60 の
//   bin 境界 61 点のうち **6 点**（39000 / 39800 / 40000 / 41400 / 41600 / 41800）で
//   別の bin へ落ちる（本ファイル冒頭の golden がその 6 点そのもの）。
//   ずれても「隣の bin の濃さ」が出るだけで版面は正しく見えるため、状態検証では原理的に
//   落ちない種類のずれである。だから式の選択そのものを検定で固定する。
//
// 構造は AAA。テスト名は「対象_条件_期待結果」。

import { test, describe } from 'node:test';
import assert from 'node:assert/strict';

import { mpNormAt } from '../js/domain/mp_bin.js';

const PRICE_MIN = 38_000;
const PRICE_MAX = 42_000;
const N_BINS = 60;

/** 各 bin の norm を互いに異なる値にして、引かれた **添字** を観測できるようにする。 */
function fixtureProfile(overrides = {}) {
  return {
    price_min: PRICE_MIN,
    price_max: PRICE_MAX,
    n_bins: N_BINS,
    bins: Array.from({ length: N_BINS }, (_unused, i) => ({
      price: PRICE_MIN + ((i + 0.5) * (PRICE_MAX - PRICE_MIN)) / N_BINS,
      norm: i / (N_BINS - 1),
    })),
    ...overrides,
  };
}

const normOfIndex = (i) => i / (N_BINS - 1);

/**
 * zp と candle で帰属が食い違う bin 境界（実測 2026-09-06・61 点中 6 点）。
 * `[価格, zp 式の添字, candle(span) 式の添字]`。
 */
const DIVERGENT_BOUNDARIES = Object.freeze([
  [39_000, 14, 15],
  [39_800, 26, 27],
  [40_000, 29, 30],
  [41_400, 50, 51],
  [41_600, 53, 54],
  [41_800, 56, 57],
]);

describe('mp_bin — 価格 → bin 密度の写像（zp 式）', () => {
  test('a_price_inside_the_range_reads_the_density_of_its_own_bin', () => {
    // Arrange: bin 0 の内側（下端から半 bin）。
    const profile = fixtureProfile();
    // Act
    const norm = mpNormAt(profile, PRICE_MIN + 33);
    // Assert
    assert.equal(norm, normOfIndex(0));
  });

  test('the_lower_edge_belongs_to_the_first_bin', () => {
    const profile = fixtureProfile();
    assert.equal(mpNormAt(profile, PRICE_MIN), normOfIndex(0));
  });

  test('the_upper_edge_belongs_to_the_last_bin_instead_of_falling_out', () => {
    // 境界値: price === price_max。閉じ側を落とすと最上位 bin が永久に無色になる。
    const profile = fixtureProfile();
    assert.equal(mpNormAt(profile, PRICE_MAX), normOfIndex(N_BINS - 1));
  });

  test('a_price_below_the_range_reads_no_density_instead_of_the_edge_bin', () => {
    // zp の compute は clip して端の bin へ丸めるが、それは**集計側**の規則である。
    //   版面で丸めると「プロファイルの外の水準」が端の濃さで塗られ、外だと分からなくなる。
    const profile = fixtureProfile();
    assert.equal(mpNormAt(profile, PRICE_MIN - 0.01), null);
  });

  test('a_price_above_the_range_reads_no_density_instead_of_the_edge_bin', () => {
    const profile = fixtureProfile();
    assert.equal(mpNormAt(profile, PRICE_MAX + 0.01), null);
  });

  test('the_bin_attribution_follows_the_zp_reference_and_not_the_candle_one', () => {
    // 式の選択そのものの固定（本ファイル冒頭の理由）。zp 式の添字に**一致**し、
    //   candle(span) 式の添字とは**異なる**ことを両方向で表明する——片方だけだと、
    //   両式が一致する 55 点を見ているだけで通ってしまう。
    const profile = fixtureProfile();
    for (const [price, zpIndex, spanIndex] of DIVERGENT_BOUNDARIES) {
      assert.notEqual(zpIndex, spanIndex, `golden が壊れています: ${price}`);
      assert.equal(
        mpNormAt(profile, price), normOfIndex(zpIndex),
        `境界 ${price} が zp 式の bin ${zpIndex} に落ちていません`,
      );
      assert.notEqual(
        mpNormAt(profile, price), normOfIndex(spanIndex),
        `境界 ${price} が candle(span) 式の bin ${spanIndex} に落ちています`,
      );
    }
  });

  test('every_bin_boundary_agrees_with_the_zp_formula_across_the_whole_range', () => {
    // 全 61 境界点で参照式（zp）と一致する（6 点だけを見て済ませない）。
    const profile = fixtureProfile();
    const binw = (PRICE_MAX - PRICE_MIN) / N_BINS;
    for (let i = 0; i <= N_BINS; i += 1) {
      const price = PRICE_MIN + i * binw;
      const expected = Math.min(N_BINS - 1, Math.trunc((price - PRICE_MIN) / binw));
      assert.equal(mpNormAt(profile, price), normOfIndex(expected), `境界 ${price}`);
    }
  });

  test('a_missing_or_empty_profile_reads_no_density_instead_of_throwing', () => {
    assert.equal(mpNormAt(null, 40_000), null);
    assert.equal(mpNormAt(undefined, 40_000), null);
    assert.equal(mpNormAt(fixtureProfile({ bins: [] }), 40_000), null);
    assert.equal(mpNormAt(fixtureProfile({ bins: undefined }), 40_000), null);
    assert.equal(mpNormAt(fixtureProfile({ n_bins: 0 }), 40_000), null);
  });

  test('a_degenerate_range_reads_no_density_instead_of_dividing_by_zero', () => {
    // 境界値: price_max === price_min（素材 1 点）。幅 0 で割ると Infinity → 添字が壊れる。
    assert.equal(mpNormAt(fixtureProfile({ price_max: PRICE_MIN }), PRICE_MIN), null);
  });

  test('a_non_finite_price_reads_no_density', () => {
    const profile = fixtureProfile();
    for (const price of [NaN, Infinity, -Infinity, null, undefined, 'x']) {
      assert.equal(mpNormAt(profile, price), null, `price=${String(price)}`);
    }
  });

  test('a_bin_carrying_a_non_finite_norm_reads_no_density_instead_of_nan', () => {
    // サーバが null / NaN を運んだとき、版面へ NaN 幅のバーを出さない。
    const broken = fixtureProfile();
    broken.bins[0] = { price: PRICE_MIN, norm: null };
    assert.equal(mpNormAt(broken, PRICE_MIN), null);
  });
});
