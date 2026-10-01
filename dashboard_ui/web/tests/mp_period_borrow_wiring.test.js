// MP の期間水準（設計書 §3.5）— 合成根での借用・当期の列・mp_levels の送信・掲示・計算量。
//
// 設計入力（依頼者指示 2026-09-29「「日」「週」「月」のMFの「POT」「VAH」「VAL」を追加しろ」・
//   §3.5.2〜§3.5.4 の裁定）:
//   - 値は live core の `/tf_period_profile` を借りる（URL はライブの buildTfPeriodUrl・
//     src / va は MP 列と同じ instance の設定を mpTfPeriodSrc で写したもの）。
//   - 当期＝その足の現在バー（live_tick_players の onBar）の time と列の time が一致する列。
//     取得窓は [time, time + 1)。
//   - front は 9 値を `/reach_sheet` の要求の欄 `mp_levels` に載せる（合流はサーバ）。
//   - 現在バーが未着・当期の列が無い足は行を出さず、理由を掲示欄に出す。
//
// 計算量テスト（CLAUDE.md 絶対命令 §4.1・設計書 §3.5.3「取得の回数: 時間足ごとに 1 回」）:
//   観測の境界は合成根が注入で受け取る `fetch`（宣言済みの口）だけ。内部名は差し替えない。
//   - 発行 − 使用 = 0: 借りた応答はどれも、その後の `/reach_sheet` の mp_levels に載る。
//   - 契機（足ごとの現在バー・1m バー枠・設定）1 つにつき、各足の発行は 1 本以下。
//   - オーダー（2 点ずつ）: ラダーの行数・応答の列の本数・ティック数を変えても発行は変わらない。
//   回数そのものは期待値に焼き込まない。
//
// 構造は AAA。テスト名は「対象_条件_期待結果」。

import { test, describe } from 'node:test';
import assert from 'node:assert/strict';

import { fakeDoc, fakeEl, flatten, sheetResponse, ladderRow } from './_fake_dom.js';
import { TEMPLATE_STORAGE_KEYS } from '../js/adapter/front/template_binding_reader.js';
import { setupDashboardDisplay } from '../js/adapter/front/composition_root_front.js';
import { MP_PERIOD_TIMEFRAMES } from '../js/domain/mp_period_levels.js';
// live core の公開面（合成根が実行時 import するものと同じ面）。
import * as livePublicApi from '../../../indigators/indicator_ui/web/js/public/live_public_api.js';

const BAR_MS = 60_000;
/** 足ごとの現在バーの time（1W / 1M は期間ラベル＝現在時刻より後になりうる・§3.5.3）。 */
const BAR_TIMES = Object.freeze({ '1m': 1_790_690_400, '1D': 1_790_640_000, '1W': 1_790_985_600, '1M': 1_791_158_400 });

function readOnlyTemplates(mpParams = { src: 'zp', va: 0.7 }) {
  const templates = [{
    templateId: 'tpl#chart',
    name: 'tpl#chart',
    instances: [
      { indicatorId: 'market_profile', variant: 'default', params: mpParams, visible: true, styles: null },
    ],
  }];
  const bindings = Object.fromEntries(
    ['1m', '5m', '15m', '1h', '4h', '1D', '1W', '1M'].map((tf) => [tf, 'tpl#chart']),
  );
  const map = {
    [TEMPLATE_STORAGE_KEYS.templates]: JSON.stringify({ templates }),
    [TEMPLATE_STORAGE_KEYS.bindings]: JSON.stringify({ bindings }),
  };
  const refuse = (op) => () => { throw new TypeError(`readOnlyStorage: ${op} は許可されていない`); };
  return {
    getItem: (key) => (Object.prototype.hasOwnProperty.call(map, key) ? map[key] : null),
    key: () => null,
    get length() { return Object.keys(map).length; },
    setItem: refuse('setItem'), removeItem: refuse('removeItem'), clear: refuse('clear'),
  };
}

/** live の LiveTickPlayer の代わり（足ごとの形成中バーを手で流す）。 */
function fakePlayers() {
  const all = [];
  class FakePlayer {
    constructor(opts) { this.opts = opts; all.push(this); }

    start() {}

    stop() {}
  }
  return {
    load: () => Promise.resolve({ LiveTickPlayer: FakePlayer }),
    /** その足の形成中バーを 1 本流す（同じ time なら足内のティック）。 */
    deliver(timeframe, time, close = 65_000) {
      const player = all.find((p) => p.opts.getTimeframe() === timeframe);
      player.opts.renderer.updateLastCandle({ time, open: close, high: close, low: close, close });
    },
  };
}

function queryOf(url) {
  const params = new URL(url, 'http://x').searchParams;
  return {
    timeframe: params.get('timeframe'),
    from: Number(params.get('from')),
    to: Number(params.get('to')),
    src: params.get('src'),
    va: params.get('va'),
  };
}

/**
 * 合成根を手回しの時計で回す試験台。
 *
 * `/tf_period_profile` の応答は発行ごとに異なる価格を持つ（どの応答が mp_levels へ使われたかを
 * 一意に辿れるようにする）。`extraColumns` は当期以外の列の本数（列の本数の 2 点用）。
 */
function harness({
  rows = 1, extraColumns = 0, templates = readOnlyTemplates(), columnFor = null,
  loadLiveMpApi = () => Promise.resolve(livePublicApi),
} = {}) {
  const doc = fakeDoc();
  const host = fakeEl('div');
  const players = fakePlayers();
  const issued = [];   // [{url, query, slot, prices}]
  const sheetBodies = [];
  let nowMs = BAR_TIMES['1m'] * 1000;

  const fetchFn = (url, init) => {
    if (init && init.body) {
      sheetBodies.push(JSON.parse(init.body));
      const payload = sheetResponse({
        rows: Array.from({ length: rows }, (_u, i) => ladderRow({ price: 65_000 + i, label: `row${i}` })),
        current_index: rows,
      });
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(payload) });
    }
    const text = String(url);
    if (text.includes('/tf_period_profile')) {
      const query = queryOf(text);
      const base = 60_000 + issued.length * 10;
      const prices = { poc: base + 1, va_high: base + 2, va_low: base };
      issued.push({ url: text, query, slot: Math.floor(nowMs / BAR_MS), prices });
      const current = columnFor ? columnFor(query, prices) : { time: query.from, levels: [], ...prices };
      const columns = [
        ...Array.from({ length: extraColumns }, (_u, i) => ({
          time: query.from - (i + 1) * 86_400, levels: [], poc: 1, va_high: 2, va_low: 0,
        })),
        ...(current ? [current] : []),
      ];
      return Promise.resolve({
        ok: true, status: 200, json: () => Promise.resolve({ ok: true, tf: query.timeframe, unit: 1, columns }),
      });
    }
    if (text.includes('/market_profile')) {
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({ ok: false, error: { message: 'x' } }) });
    }
    return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({ ok: true, candles: [] }) });
  };

  const setup = setupDashboardDisplay({
    doc,
    host,
    templates,
    fetch: fetchFn,
    apiPrefix: '/dashboard',
    now: () => nowMs,
    schedule: () => () => {},
    barCloseTimeOf: () => Math.floor(nowMs / BAR_MS),
    loadLiveMpApi,
    loadLiveTickPlayer: players.load,
    loadPeriodPresets: () => Promise.reject(new Error('none')),
    lwc: null,
  });

  const flush = async () => { for (let i = 0; i < 16; i += 1) await Promise.resolve(); };
  return {
    host, issued, sheetBodies, players, setup, flush,
    advance: (ms) => { nowMs += ms; },
    /** 1m バー枠の番号と枠内の位置（0..1）で時計を合わせる（枠の進みを操作列に依存させない）。 */
    setNow: (minute, fraction) => {
      nowMs = BAR_TIMES['1m'] * 1000 + minute * BAR_MS + Math.floor(fraction * BAR_MS);
    },
    /** 全足の形成中バーを流す（times で上書きできる）。 */
    deliverAll(times = BAR_TIMES, close = 65_000) {
      for (const [timeframe, time] of Object.entries(times)) {
        players.deliver(timeframe, time, close);
      }
    },
  };
}

function messageOf(host) {
  const el = flatten(host).find((e) => e.classList && e.classList.contains('dash-sheet-message'));
  return el ? String(el.textContent) : '';
}

function lastLevels(h) {
  const body = h.sheetBodies[h.sheetBodies.length - 1];
  return body ? body.mp_levels : undefined;
}

/** 有効化 → 全足の現在バー → 借用の着弾 → シート要求 1 本、まで進める。 */
async function started(options) {
  const h = harness(options);
  const handle = await h.setup;
  await handle.enable();
  await h.flush();
  h.deliverAll();
  h.advance(1_100);
  await handle.refresh();
  await h.flush();
  h.advance(1_100);
  await handle.refresh();
  await h.flush();
  return { h, handle };
}

describe('MP の期間水準 — 借用と送信', () => {
  test('the_nine_borrowed_levels_ride_on_the_reach_sheet_request', async () => {
    // Act
    const { h } = await started();

    // Assert: 足ごとに当期の列の poc / va_high / va_low がそのまま載る。
    const expected = [];
    for (const timeframe of MP_PERIOD_TIMEFRAMES) {
      const hit = h.issued.find((entry) => entry.query.timeframe === timeframe);
      assert.ok(hit, `${timeframe} を借りていません`);
      expected.push(
        { timeframe, level: 'POC*', price: hit.prices.poc },
        { timeframe, level: 'VAH', price: hit.prices.va_high },
        { timeframe, level: 'VAL', price: hit.prices.va_low },
      );
    }
    assert.deepEqual(lastLevels(h), expected);
  });

  test('the_window_is_the_current_bar_time_and_the_query_is_the_live_one', async () => {
    const { h } = await started({ templates: readOnlyTemplates({ src: 'zp', va: 0.62 }) });

    for (const timeframe of MP_PERIOD_TIMEFRAMES) {
      const hit = h.issued.find((entry) => entry.query.timeframe === timeframe);
      const query = {
        datasetRef: livePublicApi.DEFAULT_DATASET_REF, timeframe,
        from: BAR_TIMES[timeframe], to: BAR_TIMES[timeframe] + 1,
        src: livePublicApi.mpTfPeriodSrc('zp'), va: 0.62,
      };
      // URL はライブの唯一源そのもの（prefix は live core）。
      assert.equal(hit.url, `/live${livePublicApi.buildTfPeriodUrl(query)}`);
    }
  });

  test('a_line_poc_source_names_the_row_poc_and_carries_no_src', async () => {
    const { h } = await started({ templates: readOnlyTemplates({ src: 'dwell', va: 0.7 }) });

    assert.ok(h.issued.length > 0);
    assert.ok(h.issued.every((entry) => entry.query.src === null), 'dwell に src を付けています');
    const names = new Set(lastLevels(h).map((level) => level.level));
    assert.deepEqual([...names].sort(), ['POC', 'VAH', 'VAL']);
  });

  test('a_timeframe_whose_bar_has_not_arrived_is_not_fetched_and_is_posted', async () => {
    // Arrange: 1M の現在バーだけ届かない。
    const h = harness();
    const handle = await h.setup;
    await handle.enable();
    await h.flush();
    const { '1M': _skip, ...others } = BAR_TIMES;
    h.deliverAll(others);

    // Act
    h.advance(1_100);
    await handle.refresh();
    await h.flush();
    h.advance(1_100);
    await handle.refresh();
    await h.flush();

    // Assert
    assert.ok(h.issued.every((entry) => entry.query.timeframe !== '1M'), '現在バーの無い足を借りています');
    assert.ok(lastLevels(h).every((level) => level.timeframe !== '1M'));
    assert.match(messageOf(h.host), /MP の期間水準（1M）/);
  });

  test('a_timeframe_without_the_current_column_sends_no_rows_and_is_posted', async () => {
    const { h } = await started({
      columnFor: (query, prices) => (query.timeframe === '1W' ? null : { time: query.from, levels: [], ...prices }),
    });

    assert.ok(lastLevels(h).every((level) => level.timeframe !== '1W'));
    assert.match(messageOf(h.host), /MP の期間水準（1W）/);
  });

  test('levels_of_a_closed_period_are_not_sent_after_the_bar_moves_on', async () => {
    // Arrange
    const { h, handle } = await started();
    // Act: 1D の現在バーが翌日へ進んだ直後（借用の着弾前）にシートを撃つ。
    h.players.deliver('1D', BAR_TIMES['1D'] + 86_400);
    h.advance(1_100);
    await handle.refresh();
    // Assert: 確定済みの期間の値は使わない（§3.5.3）。
    const sent = h.sheetBodies[h.sheetBodies.length - 1].mp_levels;
    assert.ok(sent.every((level) => level.timeframe !== '1D'), '前日の水準を送っています');
  });

  test('a_live_face_without_the_tf_period_names_posts_and_issues_nothing', async () => {
    const { buildTfPeriodUrl: _a, mpTfPeriodSrc: _b, mpSourceCapability: _c, ...without } = livePublicApi;
    const { h } = await started({ loadLiveMpApi: () => Promise.resolve(without) });

    assert.equal(h.issued.length, 0);
    assert.deepEqual(lastLevels(h), []);
    assert.match(messageOf(h.host), /MP の期間水準/);
  });
});

describe('MP の期間水準 — 計算量（発行は時間足ごとに 1 回）', () => {
  /**
   * 決まった操作列を流す: 3 分ぶん、各分に ticks 回のティック（同じ現在バーの値動き）と
   * シートの契機を通し、途中で 1D の現在バーを 1 度進める。
   */
  async function run({ ticks = 5, rows = 1, extraColumns = 0 } = {}) {
    const h = harness({ rows, extraColumns });
    const handle = await h.setup;
    await handle.enable();
    await h.flush();
    const times = { ...BAR_TIMES };
    for (let minute = 0; minute < 3; minute += 1) {
      if (minute === 2) {
        times['1D'] += 86_400;
      }
      for (let k = 0; k < ticks; k += 1) {
        h.deliverAll(times, 65_000 + k);
        h.setNow(minute, (k + 1) / (ticks + 2));
        await handle.refresh();
        await h.flush();
      }
    }
    // 最後の借用を送り切る契機（同じ枠の中）。
    h.setNow(2, (ticks + 1.5) / (ticks + 2));
    await handle.refresh();
    await h.flush();
    return h;
  }

  test('every_borrowed_response_is_sent_on_a_later_sheet_request', async () => {
    // Act
    const h = await run();

    // Assert: 発行 − 使用 = 0。
    assert.ok(h.issued.length > 0);
    const sent = new Set(h.sheetBodies.flatMap((body) => (body.mp_levels ?? []).map(
      (level) => `${level.timeframe}|${level.price}`,
    )));
    const unused = h.issued.filter((entry) => !sent.has(`${entry.query.timeframe}|${entry.prices.poc}`));
    assert.deepEqual(unused.map((entry) => entry.url), []);
  });

  test('each_timeframe_is_fetched_at_most_once_per_trigger', async () => {
    const h = await run({ ticks: 20 });

    // 契機＝（足・1m バー枠・現在バー・設定）。同じ契機で同じ足を 2 回借りない。
    const triggers = h.issued.map((entry) => JSON.stringify([
      entry.query.timeframe, entry.slot, entry.query.from, entry.query.src, entry.query.va,
    ]));
    assert.equal(new Set(triggers).size, triggers.length);
  });

  test('fetches_do_not_grow_with_ticks', async () => {
    const few = await run({ ticks: 3 });
    const many = await run({ ticks: 30 });
    assert.equal(many.issued.length, few.issued.length);
  });

  test('fetches_do_not_grow_with_ladder_rows', async () => {
    const one = await run({ rows: 1 });
    const many = await run({ rows: 25 });
    assert.equal(many.issued.length, one.issued.length);
  });

  test('fetches_do_not_grow_with_columns_in_the_response', async () => {
    const one = await run({ extraColumns: 0 });
    const many = await run({ extraColumns: 30 });
    assert.equal(many.issued.length, one.issued.length);
  });
});
