// composition_root_front — dashboard 表示層の合成根（統合ページ側の入口）。
//
// 受け口契約（unified_ui 側は実装済み・commit b13fbae）:
//   unified_ui/web/js/unified_root.js:392-396
//     const dashboardHandle = await setupDashboardDisplay({
//       doc: document, host: bottomPane.host(), templates: readOnlyStorage(liveStorage),
//     });
//   unified_ui/web/tests/unified_root_dashboard_display_wiring.test.js が呼び出し側を固定している。
//
// 本スイートが固定するのはこちら側（受け取り側）の契約:
//   - `{enable, disable}` を返す（sim の `setupSimDisplay` と同形）
//   - host は **sim と共有する bottomPane の器**なので `disable()` で必ず unmount する
//   - `templates` は live スコープの**読み取り専用** storage（`setItem` 等は throw）
//   - 表示要素は View が生成し所有する（合成根は DOM を組み立てない）
//
// 計算量テスト（絶対命令・§4.1）: fetch を Test Spy にして
//   - 同一ボディの `/reach_sheet` を同一周期内に 2 回発行しない
//   - 表示行数（束の大きさ）を増やしても発行が増えない（2 点固定）
//   - `disable()` 後に 1 本も発行しない
//   を表明する。回数そのものは期待値に焼き込まない。
//
// 構造は AAA。テスト名は「対象_条件_期待結果」。

import { test, describe } from 'node:test';
import assert from 'node:assert/strict';

import { fakeDoc, fakeEl, flatten, sheetResponse, ladderRow, oscCell } from './_fake_dom.js';
import { fakeLwc } from './_fake_lwc.js';
import { TEMPLATE_STORAGE_KEYS } from '../js/adapter/front/template_binding_reader.js';
import { DASHBOARD_TIMEFRAMES } from '../js/adapter/front/timeframes.js';
import { setupDashboardDisplay } from '../js/adapter/front/composition_root_front.js';

/** 読み取り専用 storage（unified_ui の readOnlyStorage と同じ契約: 書けば throw）。 */
function readOnlyTemplates(instanceCount = 2) {
  const instances = Array.from({ length: instanceCount }, (_unused, i) => ({
    indicatorId: 'ma_marod', variant: 'default', params: { length: i + 1 }, visible: true, styles: null,
  }));
  const map = {
    [TEMPLATE_STORAGE_KEYS.templates]: JSON.stringify({ templates: [{ templateId: 'tpl#4', name: 'A', instances }] }),
    [TEMPLATE_STORAGE_KEYS.bindings]: JSON.stringify({
      bindings: { '1m': 'tpl#4', '5m': 'tpl#4', '15m': 'tpl#4', '1h': 'tpl#4', '4h': 'tpl#4', '1D': 'tpl#4', '1W': 'tpl#4', '1M': 'tpl#4' },
    }),
  };
  const refuse = (op) => () => { throw new TypeError(`readOnlyStorage: ${op} は許可されていない`); };
  return {
    getItem: (key) => (Object.prototype.hasOwnProperty.call(map, key) ? map[key] : null),
    key: () => null,
    get length() { return Object.keys(map).length; },
    setItem: refuse('setItem'), removeItem: refuse('removeItem'), clear: refuse('clear'),
  };
}

const PAYLOAD = sheetResponse({
  rows: [ladderRow()],
  current_index: 1,
  cells: [oscCell()],
});

/** fetch の Test Spy。/reach_sheet（POST）と /candles（GET）を別々に数える。 */
function spyFetch() {
  const calls = [];
  const candleCalls = [];
  const fetchFn = (url, init) => {
    if (init && init.body) {
      calls.push({ url, body: JSON.parse(init.body) });
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(PAYLOAD) });
    }
    candleCalls.push(url);
    return Promise.resolve({
      ok: true, status: 200,
      json: () => Promise.resolve({
        ok: true, candles: [{ time: 60, open: 1, high: 2, low: 0.5, close: 1.5 }],
      }),
    });
  };
  return { fetchFn, calls, candleCalls };
}

/** 手回しの時計（自動の timer を使わない＝検定が実時間を待たない）。 */
function harness({ instanceCount = 2, tickIntervalMs = 1_000, lwc = null } = {}) {
  const doc = fakeDoc();
  const host = fakeEl('div');
  const spy = spyFetch();
  let nowMs = 0;
  let barCloseTime = 100;
  const setup = setupDashboardDisplay({
    doc,
    host,
    templates: readOnlyTemplates(instanceCount),
    fetch: spy.fetchFn,
    apiPrefix: '/dashboard',
    now: () => nowMs,
    // 時計を注入し、勝手に setInterval を張らせない（監視が検定の後も走り続けない）。
    schedule: () => () => {},
    barCloseTimeOf: () => barCloseTime,
    // 既定 null: 既存の検定はチャート無し環境（掲示のみ）で回る。チャートの検定は fakeLwc を渡す。
    lwc,
  });
  return {
    doc, host, spy, setup,
    advance: (ms) => { nowMs += ms; },
    closeBar: () => { barCloseTime += 60; },
  };
}

describe('composition_root_front — setupDashboardDisplay の受け取り側契約', () => {
  test('setup_returns_the_enable_disable_handle_the_unified_root_expects', async () => {
    // Arrange / Act
    const handle = await harness().setup;
    // Assert: sim の setupSimDisplay と同形（層は 1 枚の口で受け渡される・arch-spec T-4）。
    assert.equal(typeof handle.enable, 'function');
    assert.equal(typeof handle.disable, 'function');
  });

  test('nothing_is_mounted_before_enable_is_called', async () => {
    // モードへ入るまで版面を出さない（他モードの版面へ割り込まない）。
    const h = harness();
    await h.setup;
    assert.equal(h.host.children.length, 0);
  });

  test('enable_mounts_the_sheets_the_view_owns', async () => {
    const h = harness();
    const handle = await h.setup;
    // Act
    await handle.enable();
    // Assert: 第 1 表・第 2 表・チャート一覧（ISSUE-452 内容 2）がすべて器の中に在る。
    assert.ok(flatten(h.host).some((el) => el.classList.contains('dash-ladder')));
    assert.ok(flatten(h.host).some((el) => el.classList.contains('dash-osc')));
    assert.ok(flatten(h.host).some((el) => el.classList.contains('dash-charts')));
  });

  test('enable_with_a_chart_library_fetches_candles_once_per_timeframe', async () => {
    // チャート一覧のローソクは live の /candles から時間足ごとに 1 本（初回）。
    const h = harness({ lwc: fakeLwc().lwc });
    const handle = await h.setup;
    // Act
    await handle.enable();
    // Assert: 発行先は live の配信面・全時間足を 1 回ずつ（重複なし・欠落なし）。
    const fetched = h.spy.candleCalls.map((url) => new URLSearchParams(url.split('?')[1]).get('timeframe'));
    assert.deepEqual([...fetched].sort(), [...DASHBOARD_TIMEFRAMES].sort());
    assert.ok(h.spy.candleCalls.every((url) => url.startsWith('/live/candles?')));
  });

  test('no_candles_are_fetched_when_the_chart_library_is_absent', async () => {
    // 描けない環境で取得だけするのは「作ってから捨てる」型の浪費（絶対命令 §4.1）。
    const h = harness({ lwc: null });
    const handle = await h.setup;
    // Act
    await handle.enable();
    h.advance(3_600_000);
    await handle.refresh();
    // Assert
    assert.equal(h.spy.candleCalls.length, 0);
  });

  test('candle_fetches_do_not_grow_while_no_bar_slot_advances', async () => {
    // 無駄の不在（結線ぶんの表明）: 周期の内側の契機ではローソクを取り直さない。
    const h = harness({ lwc: fakeLwc().lwc });
    const handle = await h.setup;
    await handle.enable();
    const afterEnable = h.spy.candleCalls.length;
    // Act: 1m の枠（60s）の内側で 5 回契機を与える。
    for (let i = 0; i < 5; i += 1) {
      h.advance(1_000);
      await handle.refresh();
    }
    // Assert
    assert.equal(h.spy.candleCalls.length, afterEnable);
  });

  test('no_candle_fetch_is_issued_after_disable', async () => {
    const h = harness({ lwc: fakeLwc().lwc });
    const handle = await h.setup;
    await handle.enable();
    await handle.disable();
    const after = h.spy.candleCalls.length;
    // Act
    h.advance(3_600_000);
    await handle.refresh();
    // Assert
    assert.equal(h.spy.candleCalls.length, after);
  });

  test('disable_unmounts_everything_because_the_host_is_shared_with_sim', async () => {
    // unified_root.js:392-396 の host は bottomPane.host()＝sim と共有の器。
    const h = harness();
    const handle = await h.setup;
    await handle.enable();
    // Act
    await handle.disable();
    // Assert
    assert.equal(h.host.children.length, 0);
  });

  test('enable_after_disable_mounts_again_without_duplicating', async () => {
    // モードの出入りは何度も起きる。再入で器が二重にならないこと。
    const h = harness();
    const handle = await h.setup;
    await handle.enable();
    await handle.disable();
    // Act
    await handle.enable();
    // Assert
    assert.equal(h.host.children.length, 1);
  });

  test('the_root_never_writes_to_the_read_only_template_storage', async () => {
    // T-2: 書ける口を渡さない設計なので、書けば TypeError で落ちる。落ちなければ読むだけ。
    const h = harness();
    const handle = await h.setup;
    await assert.doesNotReject(async () => { await handle.enable(); });
  });

  test('a_missing_binding_is_shown_as_a_reason_instead_of_an_empty_sheet', async () => {
    // 無言縮退の禁止（§5.2 と同じ規約）。束が組めないことを掲示する。
    const doc = fakeDoc();
    const host = fakeEl('div');
    const empty = {
      getItem: () => null, key: () => null, length: 0,
      setItem: () => { throw new TypeError('ro'); },
      removeItem: () => { throw new TypeError('ro'); },
      clear: () => { throw new TypeError('ro'); },
    };
    const handle = await setupDashboardDisplay({
      doc, host, templates: empty, fetch: spyFetch().fetchFn, apiPrefix: '/dashboard',
      now: () => 0, schedule: () => () => {}, barCloseTimeOf: () => 100,
    });
    // Act
    await handle.enable();
    // Assert
    assert.match(host.textContent, /紐付/);
  });

  test('a_missing_host_fails_closed_rather_than_drawing_nothing_silently', async () => {
    // overlay_host.js 規約: アンカーが無ければ例外（無言 no-op にしない）。
    const handle = await setupDashboardDisplay({
      doc: fakeDoc(), host: null, templates: readOnlyTemplates(),
      fetch: spyFetch().fetchFn, apiPrefix: '/dashboard',
      now: () => 0, schedule: () => () => {}, barCloseTimeOf: () => 100,
    });
    await assert.rejects(() => handle.enable(), /アンカー|ホスト/);
  });

  // ---- 計算量テスト（絶対命令・§4.1）------------------------------------
  test('the_same_body_is_not_posted_twice_within_the_same_cycle', async () => {
    // 無駄の不在: 同一周期内の同一ボディで往復が増えない。回数は焼き込まない。
    const h = harness({ tickIntervalMs: 1_000 });
    const handle = await h.setup;
    await handle.enable();
    const afterEnable = h.spy.calls.length;
    assert.ok(afterEnable > 0, 'enable で 1 度も発行していません');
    // Act: 周期の内側で 5 回 契機を与える。
    for (let i = 0; i < 5; i += 1) {
      h.advance(100);
      await handle.refresh();
    }
    // Assert
    assert.equal(h.spy.calls.length, afterEnable);
  });

  test('post_count_does_not_grow_when_the_sheet_shows_more_rows', async () => {
    // オーダーの表明（2 点固定）: 束＝表示行数の源を倍にしても発行数が変わらない。
    const runWith = async (instanceCount) => {
      const h = harness({ instanceCount });
      const handle = await h.setup;
      await handle.enable();
      for (let step = 0; step < 12; step += 1) {
        h.advance(1_000);
        if (step % 4 === 3) h.closeBar();
        await handle.refresh();
      }
      return { posts: h.spy.calls.length, rows: h.spy.calls[0].body.instances.length };
    };
    // Act
    const few = await runWith(2);
    const many = await runWith(4);
    // Assert: 束は実際に増えているのに、発行数は変わらない。
    assert.ok(many.rows > few.rows, '束が増えていません（この検定は何も守れていない）');
    assert.ok(few.posts > 1, '発行が起きていません');
    assert.equal(few.posts, many.posts);
  });

  test('a_bar_close_switches_the_stage_without_adding_an_extra_round_trip', async () => {
    // §7 の 2 段は**同じ 1 本の往復**の mode 違いであって、段が増えても往復は増えない。
    const h = harness();
    const handle = await h.setup;
    await handle.enable();
    const afterEnable = h.spy.calls.length;
    // Act
    h.advance(1_000);
    h.closeBar();
    await handle.refresh();
    // Assert
    assert.equal(h.spy.calls.length, afterEnable + 1);
    assert.equal(h.spy.calls[h.spy.calls.length - 1].body.mode, 'full');
  });

  // ---- 省リソース段階 1+2（依頼者承認 2026-08-30） --------------------------
  test('an_identical_response_does_not_rebuild_the_sheet_dom', async () => {
    // 段階 1: 内容が不変なら第 1 表・第 2 表の DOM を 1 要素も作り直さない
    //   （毎秒の全再構築は内容不変時にはまるごと浪費）。
    const h = harness();
    const handle = await h.setup;
    await handle.enable();
    const rowBefore = flatten(h.host).find((el) => el.classList.contains('dash-ladder-row'));
    assert.ok(rowBefore, '初回描画が行を建てていません');
    // Act: 同一内容の応答で契機を 3 回通す（周期をまたいで発行させる）。
    for (let i = 0; i < 3; i += 1) {
      h.advance(1_100);
      await handle.refresh();
    }
    // Assert: 行が同一オブジェクトのまま＝作り直していない。
    const rowAfter = flatten(h.host).find((el) => el.classList.contains('dash-ladder-row'));
    assert.equal(rowAfter, rowBefore);
  });

  test('a_known_state_token_travels_back_and_an_unchanged_reply_touches_nothing', async () => {
    // 段階 2: 完全応答の state を次要求の known_state に載せ、unchanged の極小応答では
    //   版面へ一切触らない（内容はサーバが「不変」と言った直前の完全応答のまま）。
    const doc = fakeDoc();
    const host = fakeEl('div');
    const bodies = [];
    let fullServed = 0;
    const fetchFn = (url, init) => {
      const body = JSON.parse(init.body);
      bodies.push(body);
      if (body.known_state === 's1') {
        return Promise.resolve({
          ok: true, status: 200,
          json: () => Promise.resolve({ ok: true, unchanged: true, state: 's1' }),
        });
      }
      fullServed += 1;
      return Promise.resolve({
        ok: true, status: 200,
        json: () => Promise.resolve({ ...PAYLOAD, state: 's1' }),
      });
    };
    let nowMs = 0;
    const handle = await setupDashboardDisplay({
      doc, host, templates: readOnlyTemplates(2), fetch: fetchFn, apiPrefix: '/dashboard',
      now: () => nowMs, schedule: () => () => {}, barCloseTimeOf: () => 100,
    });
    await handle.enable();
    assert.equal(bodies[0].known_state, null);   // 初回は完全応答を要求する。
    const rowBefore = flatten(host).find((el) => el.classList.contains('dash-ladder-row'));
    // Act: 以後の契機はトークン付きで発行され、unchanged が返る。
    for (let i = 0; i < 3; i += 1) {
      nowMs += 1_100;
      await handle.refresh();
    }
    // Assert
    assert.ok(bodies.length >= 3);
    assert.ok(bodies.slice(1).every((body) => body.known_state === 's1'));
    assert.equal(fullServed, 1, 'unchanged が効いていません（完全応答が繰り返されています）');
    const rowAfter = flatten(host).find((el) => el.classList.contains('dash-ladder-row'));
    assert.equal(rowAfter, rowBefore);   // 版面は初回のまま（1 要素も作り直していない）。
    // Act: 出入りの後は必ず完全応答を要求し直す（unchanged では空の版面が残るため）。
    await handle.disable();
    await handle.enable();
    assert.equal(bodies[bodies.length - 1].known_state, null);
    assert.equal(fullServed, 2);
  });

  test('an_unchanged_reply_keeps_the_direction_ground_without_rebuilding', async () => {
    // 地色は状態（依頼者指示 2026-08-31: 上＝緑・下＝赤・中間色なし）。unchanged が続いても
    //   色は保たれ、かつ版面は作り直さない（段階 2 の節約を崩さない）。
    const doc = fakeDoc();
    const host = fakeEl('div');
    let phase = 'first';
    const fetchFn = (url, init) => {
      const responses = {
        first: { ...sheetResponse({ rows: [ladderRow()], current_index: 1, cells: [oscCell()], current_price: 65756.0 }), state: 's1' },
        moved: { ...sheetResponse({ rows: [ladderRow()], current_index: 1, cells: [oscCell()], current_price: 65758.0 }), state: 's2' },
        idle: { ok: true, unchanged: true, state: 's2' },
      };
      const body = responses[phase];
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(body) });
    };
    let nowMs = 0;
    const handle = await setupDashboardDisplay({
      doc, host, templates: readOnlyTemplates(2), fetch: fetchFn, apiPrefix: '/dashboard',
      now: () => nowMs, schedule: () => () => {}, barCloseTimeOf: () => 100,
    });
    await handle.enable();
    // Act: 価格が動く → 効果が付く。
    phase = 'moved';
    nowMs += 1_100;
    await handle.refresh();
    const currentOf = () => flatten(host).find((el) => el.classList.contains('dash-ladder-current'));
    assert.equal(currentOf().classList.contains('dash-ladder-current-up'), true);
    // Act: 以後は unchanged。次の契機で効果は落ちる（凍結しない）。
    phase = 'idle';
    nowMs += 1_100;
    await handle.refresh();
    assert.equal(currentOf().classList.contains('dash-ladder-current-up'), true);
    // さらに unchanged が続いても版面は作り直されず、色も保たれる（段階 2 の節約は保つ）。
    const row = currentOf();
    nowMs += 1_100;
    await handle.refresh();
    assert.equal(currentOf(), row);
    assert.equal(row.classList.contains('dash-ladder-current-up'), true);
  });

  test('the_live_tick_player_is_borrowed_started_and_drives_only_the_current_row', async () => {
    // なめらか tick 再生（依頼者指示 2026-08-31: ライブチャート仕様）。再生機構は live の
    //   LiveTickPlayer を実行時 import で借り、renderer には現在値行のその場書き換えだけを
    //   結線する。tick の適用は /reach_sheet の発行を 1 本も生まない（発行 − 使用 = 0）。
    class FakePlayer {
      constructor(opts) {
        FakePlayer.last = this;
        this.opts = opts;
        this.started = 0;
        this.stopped = 0;
      }

      start() { this.started += 1; }

      stop() { this.stopped += 1; }
    }
    const doc = fakeDoc();
    const host = fakeEl('div');
    const spy = spyFetch();
    const handle = await setupDashboardDisplay({
      doc, host, templates: readOnlyTemplates(2), fetch: spy.fetchFn, apiPrefix: '/dashboard',
      now: () => 0, schedule: () => () => {}, barCloseTimeOf: () => 100,
      loadLiveTickPlayer: () => Promise.resolve({ LiveTickPlayer: FakePlayer }),
    });
    await handle.enable();
    await Promise.resolve();   // import の then を流す。
    const player = FakePlayer.last;
    assert.ok(player, 'player が組み立てられていません');
    assert.equal(player.started, 1);
    assert.equal(player.opts.datasetRef, 'jp225_tick');
    assert.equal(typeof player.opts.fetchLiveTicks, 'function');
    assert.equal(typeof player.opts.loadFormingBar, 'function');
    // Act: tick を大量に適用しても /reach_sheet の発行は増えない（計算量の表明）。
    const issuedBefore = spy.calls.length;
    for (let i = 0; i < 50; i += 1) {
      player.opts.renderer.updateLastCandle({ time: 60, open: 1, high: 2, low: 0.5, close: 65000 + i });
    }
    assert.equal(spy.calls.length, issuedBefore);
    const current = flatten(host).find((el) => el.classList.contains('dash-ladder-current'));
    assert.match(current.textContent, /65,049\.0/);   // 最後の tick が現在値表示へ。

    // ---- 両表のなめらか再生（依頼者指示 2026-08-31: ライブチャートと同じ更新粒度） ----
    // 申告（specs）は**表になった instance だけ**（PAYLOAD の oscCell 1 件＋ladderRow 1 件）。
    //   instance_key から組み直される（params_key は JSON 復元・計算足は params.timeframe）。
    const specs = player.opts.getComputeSpecs();
    assert.equal(specs.length, 2);
    const oscSpec = specs.find((s) => s.indicatorId === 'ma_marod');
    const rowSpec = specs.find((s) => s.indicatorId === 'cvfe');
    assert.ok(oscSpec && rowSpec);
    assert.equal(oscSpec.variant, 'default');
    assert.deepEqual(oscSpec.params, { length: 50, timeframe: '1m' });
    assert.deepEqual(rowSpec.params, { timeframe: '5m' });
    assert.equal(typeof player.opts.getLimit(), 'number');
    // tails（サーバ計算の末尾値）が第 2 表の現在値と第 1 表の価格・距離・差へ流れる。
    //   発行は 1 本も増えない。
    const beforeTails = spy.calls.length;
    player.opts.applyFormingTails({
      [oscSpec.instanceId]: { ma_marod: 2.57 },
      [rowSpec.instanceId]: { cvfe_u2: 65900.5 },
    }, 60);
    assert.equal(spy.calls.length, beforeTails);
    const oscCellEl = flatten(host).find(
      (el) => el.dataset.indicator === 'ma_marod' && el.dataset.timeframe === '1m'
        && el.classList.contains('dash-osc-cell'),
    );
    assert.match(oscCellEl.textContent, /2\.57/);
    const ladderRowEl = flatten(host).find(
      (el) => el.classList.contains('dash-ladder-row') && !el.classList.contains('dash-ladder-current'),
    );
    assert.match(ladderRowEl.textContent, /65,900\.5/);   // 水準価格が tick 粒度で追随。

    // Act: disable で止まる。
    await handle.disable();
    assert.equal(player.stopped, 1);
  });

  test('no_request_is_issued_after_disable', async () => {
    // モードを出た後も dashboard core を叩き続けると、live のプールを奪う。
    const h = harness();
    const handle = await h.setup;
    await handle.enable();
    await handle.disable();
    const after = h.spy.calls.length;
    // Act
    h.advance(10_000);
    h.closeBar();
    await handle.refresh();
    // Assert
    assert.equal(h.spy.calls.length, after);
  });

  test('a_response_that_lands_after_disable_is_dropped_without_rejecting', async () => {
    // モードの切り替えは発行中に起きる。応答が届いたときには View は既に unmount されており、
    //   そこへ描こうとすると View が throw する。その throw は `issue` の Promise の中で
    //   起きるため誰も catch せず、**unhandled rejection** としてページ側に現れる
    //   （周期実行の `() => { refresh(); }` は戻り値を捨てている）。
    let release = () => {};
    const gate = new Promise((resolve) => { release = resolve; });
    const doc = fakeDoc();
    const host = fakeEl('div');
    const handle = await setupDashboardDisplay({
      doc,
      host,
      templates: readOnlyTemplates(2),
      fetch: () => gate.then(() => ({
        ok: true, status: 200, json: () => Promise.resolve(PAYLOAD),
      })),
      apiPrefix: '/dashboard',
      now: () => 0,
      schedule: () => () => {},
      barCloseTimeOf: () => 100,
    });

    // Act: 発行中にモードを出て、その後で応答が着弾する。
    const inFlight = handle.enable();
    await handle.disable();
    release();

    // Assert: 例外にならず、器へも 1 要素も戻らない。
    await inFlight;
    assert.equal(host.children.length, 0);
  });

  test('the_root_builds_no_dom_of_its_own', async () => {
    // 表示要素は View が生成し所有する（ISSUE-452 禁止事項・overlay_host.js 規約）。
    //   合成根が DOM を作り始めると中央 factory へ育って OCP 違反になる。
    const { readFileSync } = await import('node:fs');
    const { fileURLToPath } = await import('node:url');
    const src = readFileSync(fileURLToPath(new URL('../js/adapter/front/composition_root_front.js', import.meta.url)), 'utf8')
      .replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '');
    assert.equal(/createElement|innerHTML/.test(src), false);
  });
});
