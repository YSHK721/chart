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
