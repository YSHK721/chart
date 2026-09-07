// ladder_scope — 第 1 表の「どの行を出すか」の状態機械（domain・純ロジック）。
//
// なぜ単体で検定するのか（ISSUE-502 段階 4C・F-2）:
//   分割前この遷移は reach_sheet_view.js のクリックハンドラの中にあり、「期間ボタンと
//   時間足ピルが同じ集合を操作する」「全期間は全選択へ戻す」という**操作仕様**を、
//   ボタンの DOM を組んで押さないと確かめられなかった。操作仕様（依頼者）と版面の構造
//   （列・セル）は変更要求元が別なので、遷移だけを直接固定する。
//
// 構造は AAA。テスト名は「対象_条件_期待結果」。

import { test, describe } from 'node:test';
import assert from 'node:assert/strict';

import { createLadderScope, buildGroups } from '../js/domain/ladder_scope.js';
import { DASHBOARD_TIMEFRAMES } from '../js/domain/dashboard_timeframes_generated.js';

/** 行 1 本（絞り込みと現在値位置の判定に要るものだけ）。 */
function row(timeframe, distance) {
  return { timeframe, distance };
}

describe('ladder_scope — 期間グループ', () => {
  test('the_groups_partition_the_timeframes_without_overlap_or_gap', () => {
    // 複数選択の区分として意味を持つには、3 群が互いに素かつ全体を覆っていなければならない
    //   （重なると 1 本の足が 2 つのボタンに属し、押下状態が矛盾する）。
    const groups = buildGroups(DASHBOARD_TIMEFRAMES);
    const flat = groups.flatMap((g) => g.tfs);
    assert.deepEqual(flat, [...DASHBOARD_TIMEFRAMES]);
    assert.equal(new Set(flat).size, flat.length);
  });

  test('the_boundaries_are_the_design_thresholds_of_one_hour_and_one_day', () => {
    // §4.3 の閾値（1h・1D）。ここがずれると「中期」に日足が混ざる。
    const groups = buildGroups(DASHBOARD_TIMEFRAMES);
    assert.equal(groups.find((g) => g.key === 'medium').tfs[0], '1h');
    assert.equal(groups.find((g) => g.key === 'long').tfs[0], '1D');
  });
});

describe('ladder_scope — 選択の遷移', () => {
  test('everything_is_selected_at_first_so_no_row_is_hidden_silently', () => {
    const scope = createLadderScope();
    for (const tf of DASHBOARD_TIMEFRAMES) assert.equal(scope.isOn(tf), true);
    assert.equal(scope.isNarrowed(), false);
    assert.equal(scope.isWindowless(), false);
  });

  test('toggling_a_group_that_is_fully_on_turns_all_of_its_timeframes_off', () => {
    // Arrange
    const scope = createLadderScope();
    const long = scope.groups.find((g) => g.key === 'long');
    // Act
    scope.toggleGroup('long');
    // Assert
    for (const tf of long.tfs) assert.equal(scope.isOn(tf), false);
    assert.equal(scope.isGroupActive('long'), false);
    assert.equal(scope.isNarrowed(), true);
  });

  test('toggling_a_partly_on_group_turns_all_of_its_timeframes_on', () => {
    // Arrange: 群の 1 本だけを外した状態。
    const scope = createLadderScope();
    const long = scope.groups.find((g) => g.key === 'long');
    scope.toggleTimeframe(long.tfs[0]);
    // Act
    scope.toggleGroup('long');
    // Assert: 半端な状態からは「全部入れる」へ倒れる（押すたびに歯抜けが増えない）。
    for (const tf of long.tfs) assert.equal(scope.isOn(tf), true);
  });

  test('the_pills_and_the_group_buttons_drive_the_same_selection_set', () => {
    // フィルタ軸を 2 本にしないことの固定: ピルで外した足は群ボタンの押下状態にも出る。
    // Arrange
    const scope = createLadderScope();
    const short = scope.groups.find((g) => g.key === 'short');
    // Act
    scope.toggleTimeframe(short.tfs[0]);
    // Assert
    assert.equal(scope.isGroupActive('short'), false);
  });

  test('windowless_restores_the_full_selection_and_is_released_by_any_pick', () => {
    // Arrange: 絞り込んだ状態から全期間へ。
    const scope = createLadderScope();
    scope.toggleGroup('short');
    // Act
    scope.toggleWindowless();
    // Assert: 全選択へ戻り、窓なしになる（絞り込みは通さない）。
    assert.equal(scope.isWindowless(), true);
    assert.equal(scope.isNarrowed(), false);
    for (const tf of DASHBOARD_TIMEFRAMES) assert.equal(scope.isOn(tf), true);
    // Act: もう一度押すと窓ありへ戻る（選択は全選択のまま）。
    scope.toggleWindowless();
    assert.equal(scope.isWindowless(), false);
    // Act: 何かを選び直すと窓なしは解除される。
    scope.toggleWindowless();
    scope.toggleTimeframe('1m');
    assert.equal(scope.isWindowless(), false);
  });

  test('an_unknown_group_key_changes_nothing_instead_of_throwing', () => {
    const scope = createLadderScope();
    scope.toggleGroup('nope');
    assert.equal(scope.isNarrowed(), false);
    assert.equal(scope.isGroupActive('nope'), false);
  });
});

describe('ladder_scope — 絞り込みと現在値の位置', () => {
  const ROWS = [
    row('1m', 30), row('1h', 20), row('1D', 10),
    row('1m', -10), row('1h', -20), row('1D', -30),
  ];

  test('a_full_selection_passes_every_row_through_including_unknown_timeframes', () => {
    // 全選択のときフィルタを通さない＝サーバが新しい足を出しても版面から消えない。
    const scope = createLadderScope();
    const rows = [...ROWS, row('3m', 5)];
    assert.equal(scope.filter(rows).length, rows.length);
  });

  test('a_narrowed_selection_keeps_the_server_order_of_the_remaining_rows', () => {
    // Arrange
    const scope = createLadderScope();
    scope.toggleGroup('short');   // 1m/5m/15m を外す
    // Act
    const kept = scope.filter(ROWS);
    // Assert: 並びはサーバのまま（順序を再計算しない）。
    assert.deepEqual(kept.map((r) => r.timeframe), ['1h', '1D', '1h', '1D']);
  });

  test('the_current_index_comes_from_the_server_when_nothing_is_narrowed', () => {
    const scope = createLadderScope();
    assert.equal(scope.currentIndexOf(ROWS, 3), 3);
  });

  test('an_out_of_range_server_index_falls_to_the_nearest_edge', () => {
    const scope = createLadderScope();
    assert.equal(scope.currentIndexOf(ROWS, 99), ROWS.length);
    assert.equal(scope.currentIndexOf(ROWS, -5), 0);
    assert.equal(scope.currentIndexOf(ROWS, undefined), 0);
  });

  test('a_narrowed_range_recounts_the_position_from_the_same_definition', () => {
    // 絞ると行が抜けるので、サーバの添字はもう位置を指さない。定義（現在値より上＝距離が
    //   正の行数）で数え直す＝数値の再計算ではない。
    // Arrange
    const scope = createLadderScope();
    scope.toggleGroup('short');
    // Act
    const kept = scope.filter(ROWS);
    // Assert: 残った 4 行のうち距離が正なのは 2 行。
    assert.equal(scope.currentIndexOf(kept, 3), 2);
  });
});

describe('ladder_scope — 計算量（絶対命令 §4.1）', () => {
  test('filtering_reads_each_row_a_constant_number_of_times', () => {
    // オーダーの表明（2 点）: 行数を 20 倍にしても 1 行あたりの読み取り回数は変わらない
    //   ——行数に対して超線形に読む実装（毎行で全行を走る等）を落とす。
    const measure = (rowCount) => {
      let reads = 0;
      const rows = Array.from({ length: rowCount }, (_unused, i) => ({
        get timeframe() { reads += 1; return i % 2 === 0 ? '1m' : '1D'; },
        distance: i,
      }));
      const scope = createLadderScope();
      scope.toggleGroup('short');
      scope.filter(rows);
      return reads / rowCount;
    };
    assert.equal(measure(10), measure(200));
  });
});
