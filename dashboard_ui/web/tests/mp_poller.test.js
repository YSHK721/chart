// mp_poller — 借用 MP の**発行判定だけ**を持つ純ロジック（usecase）。
//
// 設計入力（依頼者裁定 2026-09-06 案 a「更新契機はライブと完全同期」）:
//   ライブの MP 再計算の入口（recomputeAllApplied → onLiveTick）を実際に呼ぶのは
//   **バー確定**（update_scheduler.requestFull）と時間足変更だけである。足内ティック駆動は
//   ISSUE-250 で廃止済み。したがってラダー側も「チャート足（1m）のバー枠が進んだとき」に
//   畳む——枠の内側で取り直しても zp の応答は同じで、丸ごと浪費になる。
//   これに「モード有効化の初回」と「テンプレートの MP 設定が変わったとき」を足す。
//
// なぜ DOM / HTTP から切り離すか（CLAUDE.md 絶対命令 §4.1・candle_poller.js と同じ理由）:
//   「作ってから捨てる」欠陥は出力が正しいまま残るので、MP 列の見た目を検査しても原理的に
//   落ちない。発行判定を純ロジックへ出せば Test Spy で発行そのものを数えられる。
//
// 本スイートが固定する不変条件（**回数そのものは焼き込まない**。固定するのは無駄の不在）:
//   - 同じバー枠の内側では 2 回発行しない
//   - 契機（tick 呼び出し）を増やしても発行が増えない（発行は枠の進みと設定の変化だけで決まる）
//   - 応答が返る前に次を重ねない（ISSUE-257 と同型の積み上げ禁止）
//   - `stop()` 後は 1 本も発行しない
//   - 発行したものは 1 本残らず呼び出し側へ渡る（発行 − 使用 = 0）
//
// 構造は AAA。テスト名は「対象_条件_期待結果」。

import { test, describe } from 'node:test';
import assert from 'node:assert/strict';

import { createMpPoller } from '../js/usecase/mp_poller.js';

/** 1m 足のバー周期（判定はチャート足の枠＝candle_poller と同じ floor(now/周期)）。 */
const BAR_MS = 60_000;

/** 発行の Test Spy と手回しの時計。 */
function harness({ resolveNow = true } = {}) {
  const issued = [];
  let release = null;
  const poller = createMpPoller({
    issue: (context) => {
      issued.push(context);
      return resolveNow
        ? Promise.resolve({ ok: true })
        : new Promise((resolve) => { release = resolve; });
    },
    now: () => nowMs,
    barMs: BAR_MS,
  });
  let nowMs = 0;
  return {
    poller,
    issued,
    advance: (ms) => { nowMs += ms; },
    settle: async () => { if (release) { release({ ok: true }); release = null; } await Promise.resolve(); },
  };
}

describe('mp_poller — 借用 MP の発行判定', () => {
  test('createMpPoller_without_its_injections_refuses_instead_of_issuing_every_tick', () => {
    assert.throws(() => createMpPoller({}), TypeError);
    assert.throws(() => createMpPoller({ issue: () => {} }), TypeError);
    assert.throws(() => createMpPoller({ issue: () => {}, now: () => 0 }), TypeError);
    // 周期が引けないまま毎 tick 発行へ倒すと、本スイートの守る不変条件が無効になる。
    assert.throws(() => createMpPoller({ issue: () => {}, now: () => 0, barMs: 0 }), TypeError);
  });

  test('the_first_trigger_after_enabling_issues_exactly_one_fetch', () => {
    // モード有効化の初回（依頼者裁定 a の 3 契機のうち 1 つ）。
    const h = harness();
    const first = h.poller.tick({ paramsKey: 'zp' });
    assert.equal(h.issued.length, 1);
    assert.equal(first.length, 1, '発行したものが呼び出し側へ渡っていません');
  });

  test('further_triggers_inside_the_same_bar_issue_nothing', async () => {
    // ティックなし＝発行 0（≥2 点で観測する）。
    const h = harness();
    h.poller.tick({ paramsKey: 'zp' });
    await Promise.resolve();
    const before = h.issued.length;

    h.advance(1_000);
    assert.deepEqual(h.poller.tick({ paramsKey: 'zp' }), []);
    h.advance(30_000);
    assert.deepEqual(h.poller.tick({ paramsKey: 'zp' }), []);

    assert.equal(h.issued.length, before, '枠の内側で発行が増えています');
  });

  test('crossing_a_bar_boundary_issues_the_next_one_and_only_one', async () => {
    const h = harness();
    h.poller.tick({ paramsKey: 'zp' });
    await Promise.resolve();
    const before = h.issued.length;

    h.advance(BAR_MS);
    h.poller.tick({ paramsKey: 'zp' });
    await Promise.resolve();
    assert.equal(h.issued.length, before + 1, '枠が進んだのに発行されていません');

    // 進んだ先の枠の内側では、何度叩いても増えない。
    for (let i = 0; i < 20; i += 1) {
      h.advance(1_000);
      h.poller.tick({ paramsKey: 'zp' });
    }
    assert.equal(h.issued.length, before + 1, '同じ枠で 2 回目が出ています');
  });

  test('the_number_of_triggers_does_not_change_the_number_of_fetches', async () => {
    // オーダーの表明（2 点固定）: 契機を 10 倍にしても発行は増えない
    //   ——発行は枠の進みだけで決まる。
    const run = async (ticksPerBar) => {
      const h = harness();
      for (let bar = 0; bar < 3; bar += 1) {
        for (let i = 0; i < ticksPerBar; i += 1) {
          h.poller.tick({ paramsKey: 'zp' });
          await Promise.resolve();
          h.advance(Math.floor(BAR_MS / ticksPerBar));
        }
      }
      return h.issued.length;
    };
    assert.equal(await run(3), await run(30));
  });

  test('skipping_many_bars_at_once_still_issues_only_one', async () => {
    // 境界値: 長い停止のあとの再開。取りこぼした枠のぶんを溜めて撃たない
    //   （応答は最新の 1 本で足りる＝過去ぶんは作って捨てる計算になる）。
    const h = harness();
    h.poller.tick({ paramsKey: 'zp' });
    await Promise.resolve();
    const before = h.issued.length;

    h.advance(BAR_MS * 100);
    h.poller.tick({ paramsKey: 'zp' });
    await Promise.resolve();

    assert.equal(h.issued.length, before + 1);
  });

  test('changing_the_mp_settings_issues_a_refetch_without_waiting_for_the_next_bar', async () => {
    // 3 つ目の契機。ユーザーがチャートで MP 設定を変えたらラダーも追従する
    //   （次のバーまで最大 1 分古い MP を出し続けない）。
    const h = harness();
    h.poller.tick({ paramsKey: 'src=zp' });
    await Promise.resolve();
    const before = h.issued.length;

    h.advance(1_000);
    h.poller.tick({ paramsKey: 'src=dwell' });
    await Promise.resolve();

    assert.equal(h.issued.length, before + 1);
  });

  test('the_same_settings_never_issue_a_refetch_on_their_own', async () => {
    // 設定の**同一性**で判定する（毎 tick で組み直した同値のキーを別物と読まない）。
    const h = harness();
    h.poller.tick({ paramsKey: 'src=zp&va=0.7' });
    await Promise.resolve();
    const before = h.issued.length;

    for (let i = 0; i < 10; i += 1) {
      h.advance(1_000);
      h.poller.tick({ paramsKey: 'src=zp&va=0.7' });
    }

    assert.equal(h.issued.length, before);
  });

  test('a_fetch_still_in_flight_is_not_stacked_on', () => {
    // ISSUE-257 と同型の積み上げ禁止。応答が返る前に次を重ねない。
    const h = harness({ resolveNow: false });
    h.poller.tick({ paramsKey: 'zp' });
    const before = h.issued.length;

    h.advance(BAR_MS * 5);
    h.poller.tick({ paramsKey: 'zp' });
    h.advance(BAR_MS * 5);
    h.poller.tick({ paramsKey: 'other' });

    assert.equal(h.issued.length, before, '応答待ちのまま次を重ねています');
  });

  test('the_poller_resumes_after_the_flight_settles', async () => {
    // 上の抑止が**恒久的な沈黙**になっていないこと（在庫を抱えたまま死なない）。
    const h = harness({ resolveNow: false });
    h.poller.tick({ paramsKey: 'zp' });
    const before = h.issued.length;
    await h.settle();

    h.advance(BAR_MS);
    h.poller.tick({ paramsKey: 'zp' });
    await Promise.resolve();

    assert.equal(h.issued.length, before + 1);
  });

  test('a_failed_fetch_does_not_wedge_the_poller_shut', async () => {
    // 異常系: 発行が reject しても in-flight の札を戻す（1 度の失敗で永久に止まらない）。
    let calls = 0;
    let nowMs = 0;
    const poller = createMpPoller({
      issue: () => { calls += 1; return Promise.reject(new Error('切断')); },
      now: () => nowMs,
      barMs: BAR_MS,
    });
    const swallow = (promises) => promises.forEach((p) => p.catch(() => {}));

    swallow(poller.tick({ paramsKey: 'zp' }));
    await Promise.resolve();
    await Promise.resolve();
    nowMs += BAR_MS;
    swallow(poller.tick({ paramsKey: 'zp' }));
    await Promise.resolve();

    assert.equal(calls, 2, '1 度の失敗で発行が止まっています');
  });

  test('stop_silences_every_further_trigger', async () => {
    // モードを出た後に叩き続けない。
    const h = harness();
    h.poller.tick({ paramsKey: 'zp' });
    await Promise.resolve();
    const before = h.issued.length;

    h.poller.stop();
    h.advance(BAR_MS * 3);
    assert.deepEqual(h.poller.tick({ paramsKey: 'zp' }), []);
    assert.deepEqual(h.poller.tick({ paramsKey: 'changed' }), []);

    assert.equal(h.issued.length, before);
    assert.equal(h.poller.isRunning(), false);
  });

  test('every_issued_fetch_is_handed_back_to_the_caller', async () => {
    // 発行 − 使用 = 0（この層での表明）。呼び出し側が受け取れない発行を作らない
    //   ——受け取れなければ結果は捨てられ、まさに「作って捨てる」計算になる。
    const h = harness();
    let handed = 0;
    for (let bar = 0; bar < 5; bar += 1) {
      handed += h.poller.tick({ paramsKey: 'zp' }).length;
      await Promise.resolve();
      h.advance(BAR_MS);
    }
    assert.equal(handed, h.issued.length);
    assert.ok(handed > 0, '1 本も発行されていません（検定が空振り）');
  });
});
