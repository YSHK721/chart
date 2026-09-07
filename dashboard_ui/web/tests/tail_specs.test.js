// tail_specs — なめらか再生の申告と流し先の台帳（usecase・純ロジック）。
//
// なぜ単体で検定するのか（ISSUE-502 段階 4C・F-3）:
//   分割前この契約の知識は composition_root_front.js の中にあり、「表に出ない instance へ
//   tails を計算させない」「同じ instance を 2 回申告しない」を確かめるには合成根を起動して
//   fetch を数える必要があった。これは `/live_ticks` の契約が変わったときに書き換わる場所で、
//   結線（誰と誰を繋ぐか）とは変更要求元が別である。
//
// 構造は AAA。テスト名は「対象_条件_期待結果」。

import { test, describe } from 'node:test';
import assert from 'node:assert/strict';

import {
  createTailSpecLedger,
  tailInstanceIdOf,
  tailSpecOf,
} from '../js/usecase/tail_specs.js';

const KEY_A = ['ma_marod', 'default', '{"length":20}', '1m'];
const KEY_B = ['ma_marod', 'default', '{"length":20}', '1h'];

describe('tail_specs — 申告 ID', () => {
  test('the_id_is_composed_once_so_the_declaration_and_the_lookup_cannot_drift', () => {
    // 申告と引き当てが同じ関数を通ることが唯一の保証。spec の instanceId が
    //   tailInstanceIdOf と一致しないと、tails が届いても誰にも配られない。
    assert.equal(tailSpecOf(KEY_A).instanceId, tailInstanceIdOf(KEY_A));
  });

  test('two_instances_differing_only_in_timeframe_get_different_ids', () => {
    assert.notEqual(tailInstanceIdOf(KEY_A), tailInstanceIdOf(KEY_B));
  });

  test('a_separator_inside_an_element_cannot_collapse_two_instances', () => {
    // 区切りが可視文字だと、要素側に同じ文字が来たとき別の instance が同じ ID へ潰れる。
    //   潰れても値は出るので、出力の検査では原理的に落ちない種類のずれである。
    const a = tailInstanceIdOf(['x|y', 'v', '{}', '1m']);
    const b = tailInstanceIdOf(['x', 'y|v', '{}', '1m']);
    assert.notEqual(a, b);
  });
});

describe('tail_specs — spec の組み立て', () => {
  test('the_calculation_timeframe_rides_on_params_as_the_mtf_convention_requires', () => {
    // ISSUE-274 の MTF override と同じ規約（params.timeframe に載せる）。
    const spec = tailSpecOf(KEY_B);
    assert.equal(spec.indicatorId, 'ma_marod');
    assert.equal(spec.variant, 'default');
    assert.deepEqual(spec.params, { length: 20, timeframe: '1h' });
  });

  test('a_malformed_key_yields_nothing_instead_of_a_half_built_spec', () => {
    // 半端な spec を申告すると、サーバは計算して返し、こちらは配れない＝丸ごと浪費になる。
    assert.equal(tailSpecOf(null), null);
    assert.equal(tailSpecOf(['a', 'b', '{}']), null);
    assert.equal(tailSpecOf(['a', 'b', 'not json', '1m']), null);
    assert.equal(tailSpecOf(['a', 'b', '"text"', '1m']), null);
  });
});

describe('tail_specs — 台帳', () => {
  test('a_cell_with_a_value_series_declares_its_instance_and_its_target', () => {
    // Arrange
    const ledger = createTailSpecLedger();
    // Act
    ledger.rebuild({
      cells: [{
        instance_key: KEY_A, indicator_id: 'ma_marod', timeframe: '1m', value_series: 'ma',
      }],
    });
    // Assert
    assert.equal(ledger.specs().length, 1);
    assert.deepEqual(ledger.targetOf(tailInstanceIdOf(KEY_A)), {
      indicatorId: 'ma_marod', timeframe: '1m', valueSeries: 'ma',
    });
  });

  test('a_cell_without_a_value_series_is_not_declared_because_nothing_could_use_it', () => {
    // 宣言の無いセル（旧応答・積算セル）は流さない＝従来の 1s 表示のまま。
    const ledger = createTailSpecLedger();
    ledger.rebuild({ cells: [{ instance_key: KEY_A, indicator_id: 'x', timeframe: '1m' }] });
    assert.deepEqual(ledger.specs(), []);
  });

  test('a_row_declares_its_instance_but_no_second_table_target', () => {
    // 第 1 表の流し先の解決は View 側（instance_key × series → 行）なので、ここは申告のみ。
    const ledger = createTailSpecLedger();
    ledger.rebuild({ rows: [{ instance_key: KEY_A, series: 'ma' }] });
    assert.equal(ledger.specs().length, 1);
    assert.equal(ledger.targetOf(tailInstanceIdOf(KEY_A)), undefined);
  });

  test('a_rebuild_replaces_the_declaration_instead_of_accumulating', () => {
    // 積み上がると、表から消えた instance の末尾値を永久に計算させ続けることになる。
    const ledger = createTailSpecLedger();
    ledger.rebuild({ rows: [{ instance_key: KEY_A, series: 'ma' }] });
    ledger.rebuild({ rows: [{ instance_key: KEY_B, series: 'ma' }] });
    assert.equal(ledger.specs().length, 1);
    assert.equal(ledger.specs()[0].params.timeframe, '1h');
  });

  test('clear_drops_everything_so_a_reopened_host_declares_nothing_stale', () => {
    const ledger = createTailSpecLedger();
    ledger.rebuild({ rows: [{ instance_key: KEY_A, series: 'ma' }] });
    ledger.clear();
    assert.deepEqual(ledger.specs(), []);
    assert.equal(ledger.targetOf(tailInstanceIdOf(KEY_A)), undefined);
  });
});

describe('tail_specs — 計算量（絶対命令 §4.1）', () => {
  test('the_same_instance_on_both_tables_is_declared_once', () => {
    // 発行 − 使用 = 0: 同じ末尾値を 2 回計算させるのは、片方を必ず捨てるということである。
    // Arrange
    const ledger = createTailSpecLedger();
    // Act: 第 2 表のセルと第 1 表の行が同じ instance を指す。
    ledger.rebuild({
      cells: [{
        instance_key: KEY_A, indicator_id: 'ma_marod', timeframe: '1m', value_series: 'ma',
      }],
      rows: [{ instance_key: KEY_A, series: 'level' }],
    });
    // Assert
    assert.equal(ledger.specs().length, 1);
  });

  test('the_declarations_stay_one_per_distinct_instance_as_the_sheet_grows', () => {
    // オーダーの表明（2 点）: 行数を増やしても申告は**相異なる instance の数**で決まる。
    //   行数に比例して申告する実装は、同じ末尾値を何度も計算させている。
    const measure = (rowCount) => {
      const rows = Array.from({ length: rowCount }, () => ({
        instance_key: KEY_A, series: 'level',
      }));
      const ledger = createTailSpecLedger();
      ledger.rebuild({ rows });
      return ledger.specs().length;
    };
    assert.equal(measure(10), 1);
    assert.equal(measure(200), 1);
  });
});
