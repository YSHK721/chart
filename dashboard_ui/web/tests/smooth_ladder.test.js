// smooth_ladder — なめらか再生の水準台帳と距離・差の式（domain・純ロジック）。
//
// なぜ単体で検定するのか（ISSUE-502 段階 4C・F-2）:
//   分割前この式は reach_sheet_view.js の refreshSmoothNumbers の中にあり、「差の隣接は
//   **サーバの全行順**で決まる（絞り込み・窓と無関係）」という最も壊れやすい性質を、
//   表を組んで可視行の文字を読むことでしか確かめられなかった。可視行だけで隣を取る実装は
//   数字を出し続けるので、出力の検査では原理的に落ちない。
//
// 式はサーバの参照定義（dashboard_ui/domain/price_ladder.py）そのもの:
//   距離 = 水準価格 − 現在値 / 差 = 直前行（サーバ全行順）の水準価格 − 自行の水準価格
//
// 構造は AAA。テスト名は「対象_条件_期待結果」。

import { test, describe } from 'node:test';
import assert from 'node:assert/strict';

import { createSmoothLedger } from '../js/domain/smooth_ladder.js';

/** 行 1 本（台帳が読む欄だけ）。 */
function row({ timeframe = '1m', label = 'a', price = 100, instanceKey = null, series = null } = {}) {
  return {
    timeframe, label, price, instance_key: instanceKey, series,
  };
}

const ROWS = [
  row({ label: 'top', price: 120, instanceKey: ['ma', 'v', '{}', '1m'], series: 'ma' }),
  row({ label: 'mid', price: 110, instanceKey: ['ma', 'v', '{}', '1h'], series: 'ma' }),
  row({ label: 'low', price: 100 }),
];

describe('smooth_ladder — 台帳', () => {
  test('every_row_is_indexed_by_its_logical_key_in_server_order', () => {
    const ledger = createSmoothLedger();
    ledger.reset(ROWS);
    assert.equal(ledger.size(), 3);
    assert.equal(ledger.indexOf('1m|top'), 0);
    assert.equal(ledger.indexOf('1m|mid'), 1);
    assert.equal(ledger.indexOf('1m|low'), 2);
    assert.equal(ledger.indexOf('1m|nope'), undefined);
  });

  test('a_rebuild_replaces_the_previous_ledger_instead_of_appending', () => {
    const ledger = createSmoothLedger();
    ledger.reset(ROWS);
    ledger.reset([row({ label: 'only', price: 1 })]);
    assert.equal(ledger.size(), 1);
    assert.equal(ledger.indexOf('1m|top'), undefined);
  });

  test('clear_empties_the_ledger', () => {
    const ledger = createSmoothLedger();
    ledger.reset(ROWS);
    ledger.clear();
    assert.equal(ledger.size(), 0);
    assert.equal(ledger.numbersAt(0, 100), null);
  });
});

describe('smooth_ladder — 距離と差の式', () => {
  test('the_distance_is_the_level_price_minus_the_current_price', () => {
    const ledger = createSmoothLedger();
    ledger.reset(ROWS);
    assert.equal(ledger.numbersAt(0, 105).distance, 15);
    assert.equal(ledger.numbersAt(2, 105).distance, -5);
  });

  test('the_gap_is_measured_against_the_previous_row_in_server_order', () => {
    const ledger = createSmoothLedger();
    ledger.reset(ROWS);
    assert.equal(ledger.numbersAt(1, 105).gap, 10);
    assert.equal(ledger.numbersAt(2, 105).gap, 10);
  });

  test('the_first_row_has_no_gap_because_there_is_no_row_before_it', () => {
    // 0 を書くと「差が無い」と「差が 0」が版面で区別できなくなる。
    const ledger = createSmoothLedger();
    ledger.reset(ROWS);
    assert.equal(ledger.numbersAt(0, 105).gap, null);
  });

  test('a_non_finite_level_price_yields_nothing_so_the_cell_is_left_alone', () => {
    const ledger = createSmoothLedger();
    ledger.reset([row({ label: 'bad', price: 'x' }), row({ label: 'ok', price: 10 })]);
    assert.equal(ledger.numbersAt(0, 5), null);
    // 直前行が非有限なら差だけが空になる（価格と距離は出る）。
    const second = ledger.numbersAt(1, 5);
    assert.equal(second.distance, 5);
    assert.equal(second.gap, null);
  });

  test('an_index_outside_the_ledger_yields_nothing', () => {
    const ledger = createSmoothLedger();
    ledger.reset(ROWS);
    assert.equal(ledger.numbersAt(9, 100), null);
  });

  test('the_gap_survives_a_filtered_view_because_adjacency_is_the_full_order', () => {
    // 絞り込みで真ん中の行が版面から消えても、台帳の隣接は変わらない＝差の意味が変わらない。
    //   可視行だけで隣を取る実装はここで別の値を出す。
    const ledger = createSmoothLedger();
    ledger.reset(ROWS);
    // 版面には 0 と 2 だけが出ているつもりで、行 2 の差を読む。
    assert.equal(ledger.numbersAt(2, 105).gap, 10);   // 110 − 100（消えた行が隣のまま）
    assert.notEqual(ledger.numbersAt(2, 105).gap, 20); // 120 − 100 ではない
  });
});

describe('smooth_ladder — tails の流し込み', () => {
  test('a_tail_value_overrides_the_server_price_for_that_row', () => {
    // Arrange
    const ledger = createSmoothLedger();
    ledger.reset(ROWS);
    // Act
    ledger.applyTails((key, series) => (key[3] === '1m' && series === 'ma' ? 130 : undefined));
    // Assert
    assert.equal(ledger.numbersAt(0, 100).price, 130);
    assert.equal(ledger.numbersAt(1, 100).price, 110);   // 触られていない行は種のまま
  });

  test('a_non_numeric_tail_is_ignored_instead_of_poisoning_the_ledger', () => {
    const ledger = createSmoothLedger();
    ledger.reset(ROWS);
    ledger.applyTails(() => 'NaN-ish');
    assert.equal(ledger.numbersAt(0, 100).price, 120);
    ledger.applyTails(() => Number.NaN);
    assert.equal(ledger.numbersAt(0, 100).price, 120);
  });
});

describe('smooth_ladder — 計算量（絶対命令 §4.1）', () => {
  test('no_lookup_is_issued_for_a_row_that_could_not_use_it', () => {
    // 発行 − 使用 = 0: 申告（instance_key × series）を持たない行に末尾値の引き当てを
    //   発行するのは、返ってきても使えない計算を頼むことである。
    // Arrange
    const ledger = createSmoothLedger();
    ledger.reset(ROWS);
    let issued = 0;
    // Act
    ledger.applyTails(() => { issued += 1; return undefined; });
    // Assert: 申告を持つ 2 行ぶんだけ（3 行目は series も key も無い）。
    assert.equal(issued, 2);
  });

  test('the_lookups_stay_one_per_declared_row_as_the_sheet_grows', () => {
    // オーダーの表明（2 点）: 行数を増やしても 1 行 1 回のまま（行ごとに全行を走らない）。
    const measure = (count) => {
      const rows = Array.from({ length: count }, (_unused, i) => row({
        label: `r${i}`, price: i, instanceKey: ['ma', 'v', '{}', '1m'], series: 'ma',
      }));
      const ledger = createSmoothLedger();
      ledger.reset(rows);
      let issued = 0;
      ledger.applyTails(() => { issued += 1; return undefined; });
      return issued / count;
    };
    assert.equal(measure(10), 1);
    assert.equal(measure(200), 1);
  });
});
