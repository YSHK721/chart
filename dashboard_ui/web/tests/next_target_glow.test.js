// next_target_glow —「次のターゲット」印の移動 → 行の残光の予定表（domain・純ロジック）。
//
// なぜ単体で検定するのか（ISSUE-502 段階 4C・F-2）:
//   分割前この予定表は reach_sheet_view.js の中にあり、「一度だけ点く」「賞味期限で捨てる」
//   「窓の外の移動は記録だけ保つ」という**時間の規則**を、表を組んで DOM のクラスを読む
//   ことでしか確かめられなかった。時間の規則と版面は変更要求元が別なので、規則だけを固定する。
//
// 構造は AAA。テスト名は「対象_条件_期待結果」。

import { test, describe } from 'node:test';
import assert from 'node:assert/strict';

import {
  createNextTargetGlow,
  markOwnersOf,
  NEXT_MOVE_FADE_SECONDS,
} from '../js/domain/next_target_glow.js';

/** 行 1 本（印の持ち主判定に要るものだけ）。 */
function row({ timeframe = '1m', label = 'a', distance = 10, marks = [] } = {}) {
  return { timeframe, label, distance, horizon_marks: marks };
}

describe('next_target_glow — 印の持ち主', () => {
  test('a_mark_is_owned_per_horizon_and_side_because_the_sign_defines_the_side', () => {
    // Arrange: 同じ地平の印が上下に 1 つずつ（距離の符号で側が決まる）。
    const rows = [
      row({ label: 'above', distance: 5, marks: ['short'] }),
      row({ label: 'below', distance: -5, marks: ['short'] }),
    ];
    // Act
    const owners = markOwnersOf(rows);
    // Assert
    assert.equal(owners.get('short:up'), '1m|above');
    assert.equal(owners.get('short:down'), '1m|below');
  });

  test('a_row_without_marks_owns_nothing', () => {
    assert.equal(markOwnersOf([row({ marks: [] })]).size, 0);
  });

  test('the_same_label_on_another_timeframe_is_another_row', () => {
    // 識別子に時間足が入っていないと、別の足の同名水準が「移動」と誤認される。
    const owners = markOwnersOf([
      row({ timeframe: '1m', label: 'ma', distance: 5, marks: ['short'] }),
      row({ timeframe: '1h', label: 'ma', distance: 5, marks: ['medium'] }),
    ]);
    assert.equal(owners.get('short:up'), '1m|ma');
    assert.equal(owners.get('medium:up'), '1h|ma');
  });
});

describe('next_target_glow — 予定表', () => {
  test('the_first_response_schedules_nothing_because_there_is_no_before', () => {
    // 初回に光らせると、起動しただけで全部の印が動いたように見える。
    const glow = createNextTargetGlow();
    glow.track([row({ label: 'a', marks: ['short'] })], 1_000);
    assert.equal(glow.size(), 0);
  });

  test('a_mark_changing_owner_schedules_the_new_owner', () => {
    // Arrange
    const glow = createNextTargetGlow();
    glow.track([row({ label: 'a', marks: ['short'] })], 1_000);
    // Act
    glow.track([row({ label: 'b', marks: ['short'] })], 1_001);
    // Assert
    const due = glow.due(1_001);
    assert.deepEqual(due.map((d) => d.owner), ['1m|b']);
    assert.equal(due[0].elapsed, 0);
  });

  test('a_mark_staying_put_schedules_nothing', () => {
    const glow = createNextTargetGlow();
    glow.track([row({ label: 'a', marks: ['short'] })], 1_000);
    glow.track([row({ label: 'a', marks: ['short'] })], 1_001);
    assert.equal(glow.size(), 0);
  });

  test('an_applied_entry_is_not_handed_out_again_between_renders', () => {
    // 発行 − 使用 = 0 の骨格: 1 回の移動で点灯は 1 回。呼ばれた回数ではなく移動の回数で決まる。
    // Arrange
    const glow = createNextTargetGlow();
    glow.track([row({ label: 'a', marks: ['short'] })], 1_000);
    glow.track([row({ label: 'b', marks: ['short'] })], 1_001);
    // Act
    const first = glow.due(1_001);
    glow.markApplied(first[0].mark);
    // Assert: 以後いくら聞かれても同じ予約は返さない。
    assert.equal(glow.due(1_001).length, 0);
    assert.equal(glow.due(1_002).length, 0);
    assert.equal(glow.due(1_003).length, 0);
  });

  test('a_rebuilt_table_gets_the_effect_back_because_the_classes_were_removed', () => {
    // 表を作り直すと発光のクラスも消える。予約は「未適用」へ戻り、経過ぶんだけ進んだ状態で
    //   もう一度渡される（負の delay で残り時間から続く＝再点滅にはならない）。
    // Arrange
    const glow = createNextTargetGlow();
    glow.track([row({ label: 'a', marks: ['short'] })], 1_000);
    glow.track([row({ label: 'b', marks: ['short'] })], 1_001);
    glow.markApplied(glow.due(1_001)[0].mark);
    // Act: 次の描画（内容は変わらない）。
    glow.track([row({ label: 'b', marks: ['short'] })], 1_003);
    const again = glow.due(1_003);
    // Assert
    assert.equal(again.length, 1);
    assert.equal(again[0].owner, '1m|b');
    assert.equal(again[0].elapsed, 2);
  });

  test('an_expired_entry_is_dropped_instead_of_relighting_forever', () => {
    // Arrange
    const glow = createNextTargetGlow();
    glow.track([row({ label: 'a', marks: ['short'] })], 1_000);
    glow.track([row({ label: 'b', marks: ['short'] })], 1_001);
    // Act
    const due = glow.due(1_001 + NEXT_MOVE_FADE_SECONDS);
    // Assert: 賞味期限に達した予約は返さず、記録ごと捨てる。
    assert.deepEqual(due, []);
    assert.equal(glow.size(), 0);
  });

  test('a_missing_clock_drops_the_effect_instead_of_inventing_a_permanent_glow', () => {
    // 経過を測れないまま光らせると、消えない残光を発明することになる。
    // Arrange
    const glow = createNextTargetGlow();
    glow.track([row({ label: 'a', marks: ['short'] })], 1_000);
    glow.track([row({ label: 'b', marks: ['short'] })], 1_001);
    assert.equal(glow.size(), 1);
    // Act
    glow.track([row({ label: 'c', marks: ['short'] })], null);
    // Assert
    assert.equal(glow.size(), 0);
  });

  test('clear_forgets_the_previous_owners_so_the_next_response_is_a_first_one', () => {
    // 版面を畳んで開き直したとき、畳む前との差を「移動」と読むと全行が一斉に光る。
    // Arrange
    const glow = createNextTargetGlow();
    glow.track([row({ label: 'a', marks: ['short'] })], 1_000);
    // Act
    glow.clear();
    glow.track([row({ label: 'b', marks: ['short'] })], 1_001);
    // Assert
    assert.equal(glow.size(), 0);
  });
});

describe('next_target_glow — 計算量（絶対命令 §4.1）', () => {
  test('the_number_of_scheduled_glows_follows_moves_not_rows', () => {
    // 「作ってから捨てる」の不在をオーダーで表明する: 行数を倍にしても、動いた印が 1 つなら
    //   予約は 1 つ。行数に比例して予約が増える実装は、使われない発光を作って捨てている。
    const measure = (rowCount) => {
      const before = [];
      const after = [];
      for (let i = 0; i < rowCount; i += 1) {
        before.push(row({ label: `r${i}`, marks: i === 0 ? ['short'] : [] }));
        after.push(row({ label: `r${i}`, marks: i === 1 ? ['short'] : [] }));
      }
      const glow = createNextTargetGlow();
      glow.track(before, 1_000);
      glow.track(after, 1_001);
      return glow.size();
    };
    assert.equal(measure(10), 1);
    assert.equal(measure(200), 1);
  });
});
