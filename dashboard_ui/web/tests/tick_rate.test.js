// tick_rate — 更新**頻度**から効果の濃度を出す規則（domain・純ロジック）。
//
// なぜ単体で検定するのか（ISSUE-502 段階 4C・F-2）:
//   分割前この規則は reach_sheet_view.js の中にあり、「窓の外の更新が濃度に効かない」ことを
//   確かめるには表を組んで DOM の style を読む必要があった。頻度の規則と版面は変更要求元が
//   別なので、規則だけを直接固定する。
//
// 構造は AAA。テスト名は「対象_条件_期待結果」。

import { test, describe } from 'node:test';
import assert from 'node:assert/strict';

import {
  createTickRateMeter,
  TICK_FULL_RATE,
  TICK_MIN_STRENGTH,
  TICK_RATE_WINDOW_SECONDS,
} from '../js/domain/tick_rate.js';

describe('tick_rate — 更新頻度 → 濃度', () => {
  test('a_single_update_takes_the_floor_because_movement_must_be_visible', () => {
    // Arrange
    const meter = createTickRateMeter();
    // Act
    const strength = meter.register(1_000);
    // Assert: 1 回/2 秒 = 0.5 回/秒 → 10% だが、下限で持ち上がる（動いたことが見える）。
    assert.equal(strength, TICK_MIN_STRENGTH);
    assert.equal(meter.strength(), TICK_MIN_STRENGTH);
  });

  test('a_faster_stream_is_denser_than_a_slower_one', () => {
    // Arrange: 同じ 1 秒間に 2 回 と 6 回。
    const slow = createTickRateMeter();
    const fast = createTickRateMeter();
    // Act
    for (const t of [1_000, 1_000.5]) slow.register(t);
    for (const t of [1_000, 1_000.1, 1_000.2, 1_000.3, 1_000.4, 1_000.5]) fast.register(t);
    // Assert
    assert.ok(fast.strength() > slow.strength(),
      `速い方が濃くありません: fast=${fast.strength()} slow=${slow.strength()}`);
  });

  test('the_full_rate_saturates_at_a_hundred_instead_of_overflowing', () => {
    // Arrange: 窓いっぱいに FULL_RATE を超える頻度を入れる。
    const meter = createTickRateMeter();
    const count = TICK_FULL_RATE * TICK_RATE_WINDOW_SECONDS * 4;
    // Act
    for (let i = 0; i < count; i += 1) {
      meter.register(1_000 + (i * TICK_RATE_WINDOW_SECONDS) / count);
    }
    // Assert: 上限で頭打ち（100 を超える濃度を発明しない）。
    assert.equal(meter.strength(), 100);
  });

  test('updates_older_than_the_window_do_not_count_toward_the_rate', () => {
    // 窓の意味の固定: 古い更新が残ると「もう止まっている」相場が濃いままになる。
    // Arrange
    const meter = createTickRateMeter();
    for (let i = 0; i < 20; i += 1) meter.register(1_000 + i * 0.05);
    const dense = meter.strength();
    // Act: 窓を跨いだ先で 1 回だけ更新する。
    const later = meter.register(1_000 + TICK_RATE_WINDOW_SECONDS * 10);
    // Assert
    assert.ok(dense > TICK_MIN_STRENGTH, '前提が崩れています（密な区間が濃くなっていない）');
    assert.equal(later, TICK_MIN_STRENGTH);
  });

  test('a_missing_clock_takes_the_floor_instead_of_inventing_a_rate', () => {
    // 時計が無い環境で頻度を名乗ると、観測していない量を発明することになる。
    const meter = createTickRateMeter();
    assert.equal(meter.register(null), TICK_MIN_STRENGTH);
  });

  test('fade_clears_the_effect_but_keeps_the_observation_window', () => {
    // Arrange: 濃い状態を作る。
    const meter = createTickRateMeter();
    for (let i = 0; i < 10; i += 1) meter.register(1_000 + i * 0.05);
    // Act
    meter.fade();
    const afterFade = meter.strength();
    const resumed = meter.register(1_000 + 0.55);
    // Assert: 効果は 0 になるが、窓は残っているので再開時は密なまま（履歴を捨てていない）。
    assert.equal(afterFade, 0);
    assert.ok(resumed > TICK_MIN_STRENGTH, `窓が捨てられています: ${resumed}`);
  });

  test('reset_drops_the_history_so_the_next_update_starts_from_the_floor', () => {
    // Arrange
    const meter = createTickRateMeter();
    for (let i = 0; i < 10; i += 1) meter.register(1_000 + i * 0.05);
    // Act
    meter.reset();
    // Assert
    assert.equal(meter.strength(), 0);
    assert.equal(meter.register(1_000 + 0.55), TICK_MIN_STRENGTH);
  });
});

describe('tick_rate — 計算量（絶対命令 §4.1）', () => {
  test('the_retained_observations_do_not_grow_with_total_history', () => {
    // 「作ってから捨てる」の不在: 窓の外の観測を保ち続けると、長時間の稼働でメモリと
    //   1 回あたりの走査が単調に増える。**入力を増やしても保持が増えない**ことを 2 点で見る。
    //   観測は register の戻り値（＝窓内の件数から決まる濃度）で行う。
    const measure = (totalTicks) => {
      const meter = createTickRateMeter();
      // 窓よりはるかに長い時間へ均等に散らす（窓内には常に 1 件しか入らない配置）。
      for (let i = 0; i < totalTicks; i += 1) {
        meter.register(1_000 + i * TICK_RATE_WINDOW_SECONDS * 2);
      }
      return meter.strength();
    };
    // 履歴 20 件と 400 件で同じ濃度＝窓の外は 1 件も勘定に残っていない。
    assert.equal(measure(20), measure(400));
    assert.equal(measure(400), TICK_MIN_STRENGTH);
  });
});
