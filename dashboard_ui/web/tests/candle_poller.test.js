// candle_poller — ローソク再取得の発行判定（計算量テスト・絶対命令 §4.1）。
//
// なぜ状態検証と別に要るか: チャートの見た目は「毎 tick ローソクを取り直す」実装でも正しい
//   まま表示される（ISSUE-450 / ISSUE-257 と同型: 出力が正しい浪費は状態検証で原理的に
//   落ちない）。したがって Test Spy で**発行そのもの**を数える。時間は測らない。
//
// 固定する不変条件（回数そのものは焼き込まない・固定するのは**無駄の不在**）:
//   - 同じバー枠の内側では同じ時間足を再発行しない（発行 − 枠の進み = 0）
//   - 契機（tick 回数）を増やしても発行が増えない（2 点固定＝オーダーの表明）
//   - 表示する時間足を増やしても、1 時間足あたりの発行が増えない（2 点固定）
//   - 応答が返る前に同じ時間足の次を重ねない／stop() 後は 1 本も発行しない
//
// 構造は AAA。テスト名は「対象_条件_期待結果」。

import { test, describe } from 'node:test';
import assert from 'node:assert/strict';

import { createCandlePoller } from '../js/usecase/candle_poller.js';
import { DASHBOARD_TIMEFRAMES, TIMEFRAME_REFRESH_MS } from '../js/adapter/front/timeframes.js';

/** 発行の Test Spy（既定は即応答）。 */
function spyIssue(impl = () => Promise.resolve({ ok: true })) {
  const calls = [];
  const issue = (timeframe) => {
    calls.push(timeframe);
    return impl(timeframe);
  };
  return { issue, calls };
}

function harness({ timeframes = DASHBOARD_TIMEFRAMES, impl } = {}) {
  const spy = spyIssue(impl);
  let nowMs = 10 * 60_000;   // 0 起点だと「枠 0」への感度が消えるので途中の時刻から始める。
  const poller = createCandlePoller({
    issue: spy.issue,
    now: () => nowMs,
    timeframes,
    refreshMs: TIMEFRAME_REFRESH_MS,
  });
  return { spy, poller, advance: (ms) => { nowMs += ms; } };
}

describe('candle_poller — 発行判定と無駄の不在', () => {
  test('constructor_fails_closed_when_a_timeframe_has_no_period', () => {
    // 周期の引けない足を黙って毎 tick 発行へ倒すと、不変条件がその 1 本だけ無効になる。
    assert.throws(
      () => createCandlePoller({
        issue: () => {}, now: () => 0, timeframes: ['9x'], refreshMs: TIMEFRAME_REFRESH_MS,
      }),
      /9x/,
    );
  });

  test('the_first_tick_issues_every_timeframe_exactly_once', async () => {
    const h = harness();
    // Act
    await Promise.all(h.poller.tick());
    // Assert: 初回は全時間足を 1 回ずつ（重複なし・欠落なし）。
    assert.deepEqual([...h.spy.calls].sort(), [...DASHBOARD_TIMEFRAMES].sort());
  });

  // ---- 計算量テスト（絶対命令・§4.1）------------------------------------
  test('ticks_inside_the_same_bar_slot_issue_nothing', async () => {
    // 無駄の不在: 枠が進まない限り、応答は同じなので取り直しは丸ごと浪費。
    const h = harness();
    await Promise.all(h.poller.tick());
    const afterFirst = h.spy.calls.length;
    // Act: 1m の枠（60s）の内側で 5 回契機を与える。
    for (let i = 0; i < 5; i += 1) {
      h.advance(1_000);
      await Promise.all(h.poller.tick());
    }
    // Assert
    assert.equal(h.spy.calls.length, afterFirst);
  });

  test('issue_count_is_driven_by_slot_advance_not_by_tick_count', async () => {
    // オーダーの表明（2 点固定）: 同じ経過時間を細かく刻んでも粗く刻んでも発行数は同じ。
    const run = async (ticks) => {
      const h = harness();
      await Promise.all(h.poller.tick());   // 初回は同一時刻で発行し、以後の枠だけを比べる。
      const total = 10 * 60_000;   // 10 分（1m だけが 10 枠進む）。
      for (let i = 0; i < ticks; i += 1) {
        h.advance(total / ticks);
        await Promise.all(h.poller.tick());
      }
      return h.spy.calls.length;
    };
    // Act
    const coarse = await run(10);
    const fine = await run(600);
    // Assert: 契機を 60 倍にしても発行は増えない。
    assert.equal(fine, coarse);
  });

  test('per_timeframe_issue_count_does_not_grow_with_more_timeframes', async () => {
    // 表示の枚数を増やしても 1 時間足あたりの発行が増えない（ISSUE-452 の不変条件・2 点固定）。
    const run = async (timeframes) => {
      const h = harness({ timeframes });
      for (let i = 0; i < 30; i += 1) {
        h.advance(60_000);
        await Promise.all(h.poller.tick());
      }
      return h.spy.calls.filter((tf) => tf === '1m').length;
    };
    // Act
    const few = await run(['1m', '5m']);
    const many = await run([...DASHBOARD_TIMEFRAMES]);
    // Assert
    assert.ok(few > 0, '1m が 1 度も発行されていません（この検定は何も守れていない）');
    assert.equal(few, many);
  });

  test('a_slow_response_is_not_stacked_with_a_second_request', async () => {
    // ISSUE-257 と同型の積み上げ禁止: 応答が返る前に同じ時間足の次を重ねない。
    let release = () => {};
    const gate = new Promise((resolve) => { release = resolve; });
    const h = harness({ impl: () => gate });
    h.poller.tick();
    const inFlight = h.spy.calls.length;
    // Act: 応答が無いまま枠が進み、契機が来る。
    h.advance(120_000);
    h.poller.tick();
    // Assert: 発行中の時間足は重ねない。
    assert.equal(h.spy.calls.length, inFlight);
    release();
  });

  test('slot_advance_reissues_only_the_advanced_timeframes', async () => {
    // 発行 − 枠の進み = 0: 1m の枠だけ進んだら 1m だけ取り直す（他の 7 本は浪費になる）。
    const h = harness();
    await Promise.all(h.poller.tick());
    const before = h.spy.calls.length;
    // Act: 60 秒＝1m だけが次の枠へ入る（5m 以上は同じ枠のまま）。
    h.advance(60_000);
    await Promise.all(h.poller.tick());
    // Assert
    assert.deepEqual(h.spy.calls.slice(before), ['1m']);
  });

  test('no_request_is_issued_after_stop', async () => {
    const h = harness();
    await Promise.all(h.poller.tick());
    h.poller.stop();
    const after = h.spy.calls.length;
    // Act
    h.advance(3_600_000);
    h.poller.tick();
    // Assert
    assert.equal(h.spy.calls.length, after);
    assert.equal(h.poller.isRunning(), false);
  });
});
