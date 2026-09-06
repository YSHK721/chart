// mp_fetch_context — テンプレートの MP 設定を、ライブと**同一の** fetch 文脈へ写す。
//
// 設計入力（依頼者承認 2026-09-06「MP 列＝ライブ MP の借用」・裁定 3）:
//   借用が意味を持つのは、ラダーが投げる文脈がライブチャートのそれと一致するときだけである。
//   ずれた瞬間にサーバ側メモの共有も仕様の同期も失われ、「似ているが別の MP」が版面に出る
//   ——出力はもっともらしいままなので、状態検証では原理的に落ちない。
//
// したがって写像は**自前で書かない**。ライブの `MpFetchParams`（mp_fetch_params.js:29）を
//   そのまま借り、その host 契約（`_sessions` / `_getCandles` / `_nowSec` /
//   `_replayScrub.isReplay()`・同ファイル 14-17 行の宣言）を dashboard 側で満たす。
//   受理キーの白表（ACCEPTED_KEYS）も、period 窓の条件も、dispbp→barw 写像も、
//   ライブが変われば自動で追随する。
//
// 本スイートは 2 種類の一致を固定する:
//   (a) **振る舞いの一致**: 実物の MpFetchParams を注入して、sessions / period / dispbp の
//       各 extra が実際に文脈へ載ることを観測する。
//   (b) **並びの一致**: ライブの refresh（market_profile_actor.js:473-476）の spread 順と、
//       本モジュールの組み立て順をソース走査で突き合わせる。spread は**後勝ち**なので、
//       順番が違えば同じ部品でも別の URL になる。値の検定では並びの入れ替わりを捕まえられない。
//
// 構造は AAA。テスト名は「対象_条件_期待結果」。

import { test, describe } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

import { createMpFetchContext } from '../js/adapter/front/mp_fetch_context.js';
import { createMpPoller } from '../js/usecase/mp_poller.js';
// ライブの実物（借用対象そのもの）を**公開面から**取る。adapter を直接名指すと、
//   live_public_api.js の再輸出を消しても検定が緑のままになり、借用面の保証が効かない。
import { MpFetchParams } from '../../../indigators/indicator_ui/web/js/public/live_public_api.js';
import { sessionDayStart } from '../../../indigators/indicator_ui/web/js/domain/session_day.js';

const DATASET_REF = 'jp225_tick';
const TIMEFRAME = '1m';

/** 2026-09-06 12:00:00 UTC 付近の 1m 足 1 本（`_getCandles` の供給に使う）。 */
const LATEST_CANDLE = Object.freeze({ time: 1_757_160_000, close: 40_000 });

function build({ candle = LATEST_CANDLE } = {}) {
  let latest = candle;
  const ctx = createMpFetchContext({
    MpFetchParams,
    datasetRef: DATASET_REF,
    timeframe: TIMEFRAME,
    getLatestCandle: () => latest,
    nowSec: () => LATEST_CANDLE.time,
  });
  return { ctx, setCandle: (c) => { latest = c; } };
}

describe('mp_fetch_context — ライブと同一の取得文脈', () => {
  test('the_context_always_names_the_dataset_and_the_ladder_timeframe', () => {
    // Arrange
    const { ctx } = build();
    // Act
    const context = ctx.context();
    // Assert
    assert.equal(context.datasetRef, DATASET_REF);
    assert.equal(context.timeframe, TIMEFRAME);
  });

  test('the_accepted_settings_pass_through_to_the_context', () => {
    // 白表は MpFetchParams が唯一源（bins/va/src/range/resmode/period/dispbp）。
    const { ctx } = build();
    ctx.setParams({ src: 'zp', va: 0.7, bins: '60' });
    const context = ctx.context();
    assert.equal(context.src, 'zp');
    assert.equal(context.va, 0.7);
    assert.equal(context.bins, '60');
  });

  test('a_setting_outside_the_white_list_never_reaches_the_context', () => {
    // `mode` は表示モードであって取得パラメータではない（下の sessions 検定が担当する）。
    const { ctx } = build();
    ctx.setParams({ src: 'zp', mode: 'sessions', styles: { color: 'red' } });
    const context = ctx.context();
    assert.equal('mode' in context, false);
    assert.equal('styles' in context, false);
  });

  test('the_sessions_mode_is_declared_through_the_host_instead_of_a_query_key', () => {
    // 日別モードは host の `_sessions` から sessionsExtra() が導く（mp_fetch_params.js:82-84）。
    const { ctx } = build();
    ctx.setParams({ src: 'zp', mode: 'normal' });
    assert.equal('sessions' in ctx.context(), false);

    ctx.setParams({ src: 'zp', mode: 'sessions' });
    assert.equal(ctx.context().sessions, true);
  });

  test('the_day_window_resolves_from_the_latest_candle_like_the_live_chart', () => {
    // period='day' × zp × 通常モードのときだけ from=セッション日始端（mp_fetch_params.js:92-111）。
    const { ctx } = build();
    ctx.setParams({ src: 'zp', period: 'day' });
    assert.equal(ctx.context().from, sessionDayStart(LATEST_CANDLE.time));
  });

  test('the_day_window_stays_off_for_all_period_and_for_sources_without_one', () => {
    const { ctx } = build();
    ctx.setParams({ src: 'zp', period: 'all' });
    assert.equal('from' in ctx.context(), false);
    // dwell は期間窓を持たない（mp_source_capability.js の記述子 hasPeriodWindow=false）。
    ctx.setParams({ src: 'dwell', period: 'day' });
    assert.equal('from' in ctx.context(), false);
  });

  test('the_day_window_degrades_to_the_whole_period_when_no_candle_has_arrived', () => {
    // 境界値: ローソク未取得。窓を成さないので全期間へ縮退する（非破壊・ライブと同じ）。
    const { ctx, setCandle } = build();
    ctx.setParams({ src: 'zp', period: 'day' });
    setCandle(null);
    assert.equal('from' in ctx.context(), false);
  });

  test('the_display_width_in_bp_maps_onto_the_range_path_from_the_latest_close', () => {
    // dispbp → barw = close × bp/1e4 → resmode='range' + range（mp_fetch_params.js:119-132）。
    const { ctx } = build();
    ctx.setParams({ src: 'zp', dispbp: 3 });
    const context = ctx.context();
    assert.equal(context.resmode, 'range');
    assert.equal(Number(context.range), (LATEST_CANDLE.close * 3) / 1e4);
  });

  test('the_context_never_declares_a_replay_cursor_because_the_dashboard_is_always_live', () => {
    // 単一時計（ISSUE-129）: ライブ present は clockExtra が空＝`to` を送らない。
    //   `to` を送るとサーバの壁時計とずれ、ライブと byte 非等価な MP になる。
    const { ctx } = build();
    ctx.setParams({ src: 'zp', period: 'day', dispbp: 3 });
    assert.equal('to' in ctx.context(), false);
  });

  test('the_spread_order_matches_the_live_refresh_so_the_last_writer_is_the_same', async () => {
    // 並びの一致（本ファイル冒頭 (b)）。spread は後勝ちなので、同じ部品でも順番が違えば
    //   別の URL になる。ライブが並びを変えたらここが赤くなる＝再同期の合図である。
    const spreadsOf = (source, from, to) => {
      const region = source.slice(source.indexOf(from), source.indexOf(to, source.indexOf(from)));
      assert.ok(region.length > 0, `走査域が見つかりません: ${from}`);
      return [...region.matchAll(/\.\.\.\s*([A-Za-z_$][\w.$]*)/g)]
        .map((m) => m[1].split('.').pop().replace(/^_/, '').toLowerCase());
    };

    const live = readFileSync(
      fileURLToPath(new URL(
        '../../../indigators/indicator_ui/web/js/adapter/front/market_profile_actor.js',
        import.meta.url,
      )), 'utf8',
    );
    const mine = readFileSync(
      fileURLToPath(new URL('../js/adapter/front/mp_fetch_context.js', import.meta.url)), 'utf8',
    );

    // ライブ側は refresh() の中の取得だけを見る（forming 経路の別の組み立てと混ぜない）。
    const liveOrder = spreadsOf(live, 'getContext（datasetRef', '});');
    const myOrder = spreadsOf(mine, 'MP_FETCH_CONTEXT_SPREAD_ORDER', '};');

    // ライブの並び（market_profile_actor.js:473-476）。ここが変わったら借用側も直す。
    assert.deepEqual(liveOrder, [
      'getcontext', 'params', 'sessionsextra', 'periodextra', 'dispextra', 'clockextra',
    ]);
    // 借用側: getContext 相当（datasetRef/timeframe）→ params → 同じ 3 つの extra。
    //   clockExtra はライブ present で常に空（market_profile_actor.js:380-382）なので持たない。
    assert.deepEqual(myOrder, [
      'base', 'values', 'sessionsextra', 'periodextra', 'dispextra',
    ]);
    assert.deepEqual(
      myOrder.slice(2), liveOrder.slice(2, -1),
      'extra の並びがライブの refresh とずれています',
    );
  });

  test('the_trigger_key_ignores_price_movement_so_ticks_alone_never_refetch', () => {
    // 契機の鍵は「ユーザーが所有する設定の同一性」でなければならない。
    //   カタログの dispbp 既定は 3.0（catalog_entry.js:97）なので、実運用の params には
    //   ほぼ常に dispbp が載る。dispExtra は barw を**最新 close から**導く（価格由来）ため、
    //   文脈そのものを鍵にすると値動きのたびに鍵が変わる＝ティックのたびに再取得になる。
    //   参照実装ではdispExtra は refresh の**内側**で採取されるだけで契機に関与しない
    //   （market_profile_actor.js:471-476。契機は update_scheduler のバー確定）。
    const { ctx, setCandle } = build();
    ctx.setParams({ src: 'zp', dispbp: 3.0, mode: 'normal' });

    const before = ctx.settingsKey();
    setCandle({ time: LATEST_CANDLE.time, close: LATEST_CANDLE.close + 12.5 });

    assert.equal(ctx.settingsKey(), before, '値動きだけで契機の鍵が変わっています');
  });

  test('the_trigger_key_changes_when_the_user_changes_a_setting', () => {
    // 鍵が価格を無視するようにした結果、**設定変更に反応しなくなる**退行を塞ぐ
    //   （無視のしすぎ＝ユーザーがチャートで変えてもラダーが追従しない）。
    const { ctx } = build();
    ctx.setParams({ src: 'zp', va: 0.7, dispbp: 3.0 });
    const before = ctx.settingsKey();

    ctx.setParams({ src: 'zp', va: 0.62, dispbp: 3.0 });

    assert.notEqual(ctx.settingsKey(), before);
  });

  test('the_trigger_key_changes_when_the_display_mode_changes', () => {
    // mode は白表の外（params.values() に出ない）が、sessions は URL を変える。
    //   鍵を params だけで作ると日別モードへの切り替えが契機にならない。
    const { ctx } = build();
    ctx.setParams({ src: 'zp', mode: 'normal' });
    const before = ctx.settingsKey();

    ctx.setParams({ src: 'zp', mode: 'sessions' });

    assert.notEqual(ctx.settingsKey(), before);
  });

  test('the_fetch_context_still_follows_the_price_even_though_the_key_does_not', () => {
    // 鍵から価格を外しても、**発行時の URL** は従来どおり価格に追従する（借用の同一性は
    //   ここで担保される）。鍵と URL の役割を混ぜないことの表明。
    const { ctx, setCandle } = build();
    ctx.setParams({ src: 'zp', dispbp: 3.0 });
    const before = ctx.context().range;

    setCandle({ time: LATEST_CANDLE.time, close: LATEST_CANDLE.close * 2 });

    assert.notEqual(ctx.context().range, before, '発行時の URL が価格に追従していません');
  });

  test('price_movement_alone_does_not_increase_the_number_of_borrows', async () => {
    // 計算量（絶対命令 §4.1）: 実 MpFetchParams ＋ 実 mp_poller ＋ 実勢の値動きで、
    //   1 バー枠の内側の発行が契機の数で決まらないことを ≥2 点で固定する。
    //   回数そのものは焼き込まない——比べるのは「ティック密度を変えても等しい」ことだけ。
    //
    // **tick() のたびに microtask を回すこと**（実配置と同じ形にする）。mp_poller は
    //   in-flight 中の再入を捨てる設計なので、同期ループで回すと `inFlight` が一度も
    //   解けず、鍵の中身と無関係に発行が常に 1 になる＝検定が完全に空虚になる。
    //   実測（2026-09-06）: 欠陥版の鍵（context() 由来）を入れても
    //   同期ループでは 1 vs 1 で**緑**、await を挟むと 60 vs 600 で**赤**。
    const borrowsAt = async (ticksPerBar) => {
      let candle = { time: LATEST_CANDLE.time, close: LATEST_CANDLE.close };
      const ctx = createMpFetchContext({
        MpFetchParams,
        datasetRef: DATASET_REF,
        timeframe: TIMEFRAME,
        getLatestCandle: () => candle,
        nowSec: () => LATEST_CANDLE.time,
      });
      ctx.setParams({ src: 'zp', dispbp: 3.0, mode: 'normal' });
      let nowMs = 0;
      let issued = 0;
      const poller = createMpPoller({
        issue: () => { issued += 1; return Promise.resolve({ ok: true }); },
        now: () => nowMs,
        barMs: 60_000,
      });
      for (let i = 0; i < ticksPerBar; i += 1) {
        // 値動きのみ（設定は不変）。刻みは再生粒度 100ms 相当の実勢。
        candle = { time: LATEST_CANDLE.time, close: LATEST_CANDLE.close + (i % 40) * 0.5 };
        poller.tick({ paramsKey: ctx.settingsKey() });
        // 発行の解決を待つ（in-flight を解く）。実配置では応答が返るたびに解ける。
        await Promise.resolve();
        await Promise.resolve();
        nowMs += Math.floor(60_000 / ticksPerBar);
      }
      return issued;
    };

    assert.equal(await borrowsAt(60), await borrowsAt(600), '値動きの数だけ借用が増えています');
  });

  test('reading_the_context_twice_issues_no_extra_candle_lookups_per_part', () => {
    // 計算量（絶対命令 §4.1）: 文脈 1 本の組み立てでローソクを読む回数は、
    //   それを必要とする部分（periodExtra / dispExtra）の数だけで決まる。
    //   回数そのものは焼き込まない——固定するのは「1 回の組み立てが 2 回ぶんに増えない」こと。
    let reads = 0;
    const ctx = createMpFetchContext({
      MpFetchParams,
      datasetRef: DATASET_REF,
      timeframe: TIMEFRAME,
      getLatestCandle: () => { reads += 1; return LATEST_CANDLE; },
      nowSec: () => LATEST_CANDLE.time,
    });
    ctx.setParams({ src: 'zp', period: 'day', dispbp: 3 });

    ctx.context();
    const first = reads;
    ctx.context();

    assert.ok(first > 0, 'ローソクが 1 度も読まれていません（写像が効いていない）');
    assert.equal(reads - first, first, '同じ組み立てで読取回数が増えています');
  });
});
