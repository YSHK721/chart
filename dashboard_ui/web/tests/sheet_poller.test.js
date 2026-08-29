// sheet_poller — 2 段の更新経路（設計書 §7）の**発行判定だけ**を持つ純ロジック。
//
//   段 1 = バー確定 → mode "full"（水準系列と観測値系列を突合し直す）
//   段 2 = ティック   → mode "tick"（観測値と到達状態だけを更新する）
//
// なぜ純ロジックとして分けるか: 「いつ発行するか」を DOM / HTTP と混ぜると、無駄な発行が
//   出ていても出力（表の見た目）は正しいままになり、状態検証では**原理的に落ちない**
//   （CLAUDE.md 絶対命令 §4.1・ISSUE-450 の欠陥と同型）。判定だけを取り出せば、発行回数を
//   直接数えられる。
//
// 計算量テストの表明（回数そのものは期待値に焼き込まない・固定するのは**無駄の不在**）:
//   - 同一周期内に同一ボディの発行を 2 回しない
//   - 表示行数・セル数を増やしても発行数が増えない（＝発行数は表示量に依存しない）
//
// 構造は AAA。テスト名は「対象_条件_期待結果」。

import { test, describe } from 'node:test';
import assert from 'node:assert/strict';

import { createSheetPoller } from '../js/usecase/sheet_poller.js';

/** 発行を数えるだけの Test Spy（1 呼出 = 1 発行）。 */
function spyIssue() {
  const calls = [];
  const issue = (request) => {
    calls.push(JSON.parse(JSON.stringify(request)));
    return Promise.resolve({ ok: true });
  };
  return { issue, calls };
}

/** 指定の時刻・バー時刻を返す時計（注入する＝実時間に依存させない・F.I.R.S.T Repeatable）。 */
function fakeClock(startMs) {
  let now = startMs;
  return { now: () => now, advance: (ms) => { now += ms; } };
}

const BODY = Object.freeze({ dataset_ref: 'jp225_tick', chart_timeframe: '1m', instances: [] });

describe('sheet_poller — 2 段の発行判定', () => {
  test('first_call_issues_a_full_request_because_nothing_has_been_built_yet', () => {
    // Arrange
    const spy = spyIssue();
    const clock = fakeClock(1_000);
    const poller = createSheetPoller({ issue: spy.issue, now: clock.now });
    // Act
    poller.tick({ body: BODY, barCloseTime: 100 });
    // Assert: 最初は必ず段 1（水準系列を持っていない）。
    assert.equal(spy.calls.length, 1);
    assert.equal(spy.calls[0].mode, 'full');
  });

  test('a_tick_without_a_new_bar_close_issues_the_tick_stage_not_the_full_stage', async () => {
    const spy = spyIssue();
    const clock = fakeClock(1_000);
    const poller = createSheetPoller({ issue: spy.issue, now: clock.now });
    await poller.tick({ body: BODY, barCloseTime: 100 });
    // Act: バーは確定していない（barCloseTime が同じ）。
    clock.advance(1_000);
    await poller.tick({ body: BODY, barCloseTime: 100 });
    // Assert: 段 2 のみ（段 1 を撃ち直さない＝§7 の表そのもの）。
    assert.equal(spy.calls.length, 2);
    assert.equal(spy.calls[1].mode, 'tick');
  });

  test('a_new_bar_close_time_issues_the_full_stage_again', async () => {
    const spy = spyIssue();
    const clock = fakeClock(1_000);
    const poller = createSheetPoller({ issue: spy.issue, now: clock.now });
    await poller.tick({ body: BODY, barCloseTime: 100 });
    // Act
    clock.advance(60_000);
    await poller.tick({ body: BODY, barCloseTime: 160 });
    // Assert
    assert.equal(spy.calls[1].mode, 'full');
  });

  // ---- 計算量テスト（絶対命令・§4.1）------------------------------------
  test('the_same_body_is_not_issued_twice_within_the_same_cycle', async () => {
    // Arrange: 同一周期（tickIntervalMs 未満）で何度呼ばれても、発行は 1 回に畳まれる。
    const spy = spyIssue();
    const clock = fakeClock(0);
    const poller = createSheetPoller({
      issue: spy.issue, now: clock.now, tickIntervalMs: 1_000,
    });
    await poller.tick({ body: BODY, barCloseTime: 100 });
    const afterFirst = spy.calls.length;
    // Act: 周期の内側で 5 回叩く。
    for (let i = 0; i < 5; i += 1) {
      clock.advance(100);
      await poller.tick({ body: BODY, barCloseTime: 100 });
    }
    // Assert: 発行は増えない（無駄の不在。回数そのものは期待値に焼き込まない）。
    assert.equal(spy.calls.length, afterFirst);
  });

  test('the_cycle_boundary_lets_exactly_one_more_issue_through', async () => {
    const spy = spyIssue();
    const clock = fakeClock(0);
    const poller = createSheetPoller({
      issue: spy.issue, now: clock.now, tickIntervalMs: 1_000,
    });
    await poller.tick({ body: BODY, barCloseTime: 100 });
    const afterFirst = spy.calls.length;
    // Act: 周期をまたぐ。
    clock.advance(1_000);
    await poller.tick({ body: BODY, barCloseTime: 100 });
    clock.advance(10);
    await poller.tick({ body: BODY, barCloseTime: 100 });
    // Assert: またいだ 1 回だけ通る（次の周期は開いていない）。
    assert.equal(spy.calls.length, afterFirst + 1);
  });

  test('issue_count_does_not_grow_when_the_sheet_shows_more_rows', async () => {
    // オーダーの表明: 表示量（instance 束の大きさ＝行数・セル数の源）を変えた 2 点で、
    //   同じ操作列に対する発行数が一致すること。回数そのものは焼き込まない。
    const runWith = async (instanceCount) => {
      const spy = spyIssue();
      const clock = fakeClock(0);
      const poller = createSheetPoller({
        issue: spy.issue, now: clock.now, tickIntervalMs: 1_000,
      });
      const body = {
        ...BODY,
        instances: Array.from({ length: instanceCount }, (_unused, i) => ({
          instance_id: `i#${i}`, indicator_id: 'ma_marod', variant: 'default',
          params: { length: 5 }, timeframe: '1m',
        })),
      };
      let barCloseTime = 100;
      for (let step = 0; step < 12; step += 1) {
        clock.advance(1_000);
        if (step % 4 === 3) barCloseTime += 60;
        await poller.tick({ body, barCloseTime });
      }
      return spy.calls.length;
    };
    // Act
    const few = await runWith(11);
    const many = await runWith(23);
    // Assert: 2 点（少ない束 / 倍の束）で一致する。発行が起きていること自体も確かめる
    //   （両方 0 や両方 1 で「一致」してしまうと、この検定は何も守らない）。
    assert.ok(few > 1, `発行が起きていません（few=${few}）`);
    assert.equal(few, many);
  });

  test('a_changed_body_is_issued_even_inside_the_same_cycle_because_it_is_not_the_same_request', async () => {
    // 「同一ボディを 2 回発行しない」は**同一性**の表明であって、周期で全部止める意味ではない。
    //   束が変われば内容が変わるので、周期の内側でも 1 回だけ通る（無言の欠落を作らない）。
    const spy = spyIssue();
    const clock = fakeClock(0);
    const poller = createSheetPoller({
      issue: spy.issue, now: clock.now, tickIntervalMs: 1_000,
    });
    await poller.tick({ body: BODY, barCloseTime: 100 });
    const afterFirst = spy.calls.length;
    // Act
    clock.advance(10);
    await poller.tick({ body: { ...BODY, chart_timeframe: '5m' }, barCloseTime: 100 });
    // Assert
    assert.equal(spy.calls.length, afterFirst + 1);
  });

  test('stop_prevents_any_further_issue', async () => {
    // disable() 後に発行が続くと、モードを出た後も dashboard core を叩き続ける。
    const spy = spyIssue();
    const clock = fakeClock(0);
    const poller = createSheetPoller({ issue: spy.issue, now: clock.now, tickIntervalMs: 0 });
    await poller.tick({ body: BODY, barCloseTime: 100 });
    const before = spy.calls.length;
    // Act
    poller.stop();
    clock.advance(10_000);
    await poller.tick({ body: BODY, barCloseTime: 200 });
    // Assert
    assert.equal(spy.calls.length, before);
  });

  test('an_in_flight_request_is_not_overlapped_by_the_next_one', () => {
    // 応答が返る前に次を撃つと、遅い段 1 の最中に段 1 が積み上がる（ISSUE-257 と同型の
    //   「捨てられる計算」）。在庫が 1 本を超えないことを固定する。
    const calls = [];
    let release;
    const issue = (request) => {
      calls.push(request);
      return new Promise((resolve) => { release = () => resolve({ ok: true }); });
    };
    const clock = fakeClock(0);
    const poller = createSheetPoller({ issue, now: clock.now, tickIntervalMs: 0 });
    poller.tick({ body: BODY, barCloseTime: 100 });
    // Act: 応答が返らないまま次の契機が来る。
    clock.advance(5_000);
    poller.tick({ body: BODY, barCloseTime: 160 });
    // Assert
    assert.equal(calls.length, 1);
    assert.equal(typeof release, 'function');
  });
});
