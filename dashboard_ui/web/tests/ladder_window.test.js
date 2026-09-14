// ladder_window — 現在値を中心とした表示窓の幾何（domain・純関数）。
//
// なぜ単体で検定するのか（ISSUE-502 段階 4C・F-2）:
//   分割前この算術は reach_sheet_view.js の fitWindow の中にあり、境界（溢れていない・
//   測れない・1 本しか入らない・拡大方向）を確かめるには器の高さを持つ DOM ダブルを
//   組む必要があった。測定は DOM の仕事だが、測った数から決める規則は純粋な算術である。
//
// 構造は AAA。テスト名は「対象_条件_期待結果」。

import { test, describe } from 'node:test';
import assert from 'node:assert/strict';

import { WINDOW_RADIUS, fitRadius, sliceWindow } from '../js/domain/ladder_window.js';

describe('ladder_window — 建てる範囲', () => {
  test('the_window_is_centred_on_the_current_row', () => {
    assert.deepEqual(
      sliceWindow({ total: 100, at: 50, radius: 5, windowless: false }),
      { start: 45, end: 55 },
    );
  });

  test('the_window_is_clamped_at_both_ends_instead_of_running_past_them', () => {
    assert.deepEqual(
      sliceWindow({ total: 8, at: 1, radius: 5, windowless: false }),
      { start: 0, end: 6 },
    );
    assert.deepEqual(
      sliceWindow({ total: 8, at: 7, radius: 5, windowless: false }),
      { start: 2, end: 8 },
    );
  });

  test('windowless_takes_everything_because_it_is_the_full_view_option', () => {
    assert.deepEqual(
      sliceWindow({ total: 100, at: 50, radius: 5, windowless: true }),
      { start: 0, end: 100 },
    );
  });

  test('an_empty_sheet_yields_an_empty_range', () => {
    assert.deepEqual(
      sliceWindow({ total: 0, at: 0, radius: 5, windowless: false }),
      { start: 0, end: 0 },
    );
  });
});

describe('ladder_window — 器への適合', () => {
  const BASE = { rowHeight: 20, rowCount: 10, currentRadius: WINDOW_RADIUS };

  test('content_that_fits_needs_no_shrinking', () => {
    // 溢れていない＝縮める理由が無い（毎描画の作り直しを生まない）。
    assert.equal(fitRadius({ ...BASE, boxHeight: 400, contentHeight: 300 }), null);
  });

  test('an_unmeasurable_box_changes_nothing_so_the_check_stays_deterministic', () => {
    // 実高を測れない環境（テストダブル）は上限のまま。数値を発明しない。
    assert.equal(fitRadius({ ...BASE, boxHeight: undefined, contentHeight: 300 }), null);
    assert.equal(fitRadius({ ...BASE, boxHeight: 0, contentHeight: 300 }), null);
    assert.equal(fitRadius({ ...BASE, boxHeight: 100, contentHeight: '300' }), null);
    assert.equal(fitRadius({ ...BASE, boxHeight: 100, contentHeight: 300, rowHeight: 0 }), null);
  });

  test('an_overflowing_box_shrinks_to_the_rows_that_actually_fit', () => {
    // Arrange: 行 20px が 10 本 + 見出し 40px = 240px を、160px の器へ入れる。
    //   収まるのは (160 - 40) / 20 = 6 本。現在値行 1 本を除いた 5 本を上下へ折半 → 2。
    // Act
    const next = fitRadius({
      boxHeight: 160, contentHeight: 240, rowHeight: 20, rowCount: 10, currentRadius: WINDOW_RADIUS,
    });
    // Assert
    assert.equal(next, 2);
  });

  test('the_radius_never_grows_back_so_the_fit_does_not_oscillate', () => {
    // 拡大方向にも動くと、縮める → 溢れなくなる → 広げる → また溢れる、で毎描画作り直しになる。
    assert.equal(fitRadius({
      boxHeight: 400, contentHeight: 500, rowHeight: 20, rowCount: 10, currentRadius: 2,
    }), null);
  });

  test('a_box_that_fits_almost_nothing_still_keeps_one_row_on_each_side', () => {
    // 0 本にすると版面から水準が消える。読めない版面より「1 本＋掲示」を選ぶ。
    const next = fitRadius({
      boxHeight: 45, contentHeight: 240, rowHeight: 20, rowCount: 10, currentRadius: WINDOW_RADIUS,
    });
    assert.equal(next, 1);
  });
});

describe('ladder_window — 計算量（絶対命令 §4.1）', () => {
  test('the_built_range_stays_bounded_by_the_radius_as_the_sheet_grows', () => {
    // 「作ってから捨てる」の不在をオーダーで表明する（2 点）: 全行数が 10 倍になっても
    //   建てる本数は半径で決まる。行数に比例して建ててから隠す実装を落とす。
    const width = (total) => {
      const { start, end } = sliceWindow({
        total, at: Math.floor(total / 2), radius: WINDOW_RADIUS, windowless: false,
      });
      return end - start;
    };
    assert.equal(width(100), WINDOW_RADIUS * 2);
    assert.equal(width(1_000), WINDOW_RADIUS * 2);
  });
});
