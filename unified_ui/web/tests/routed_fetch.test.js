// createRoutedFetch の契約テスト（ISSUE-362）。
//
// 保証対象: ルーティング（モード prefix 付与）が **Service Worker の可用性に依存しない**こと。
//   SW が未登録・未制御・迂回（DevTools "Bypass for network"）でも、アプリが自分で prefix を
//   付けて正しい core へ届ける。規則は SW と同一の rewritePath を共有し、冪等ゆえ二重付与しない。
// 構造は AAA。テスト名は「対象_条件_期待結果」。

import { describe, test, expect } from 'vitest';
import { createRoutedFetch } from '../js/routed_fetch.js';
// モード集合・prefix の単一ソース（§3.5.6 の表駆動化）。テスト側も第 2 の定義を持たない。
import { MODE_IDS, prefixOf } from '../js/mode_table.js';

const ORIGIN = 'http://127.0.0.1:8000';

// 呼ばれた第 1 引数を記録するだけの baseFetch。
function spyFetch() {
  const calls = [];
  const fn = (input, init) => {
    calls.push(typeof input === 'string' ? input : (input && input.url) || input);
    return Promise.resolve({ ok: true, init });
  };
  fn.calls = calls;
  return fn;
}

function make(mode, base = spyFetch()) {
  return { base, fetch: createRoutedFetch({ baseFetch: base, getMode: () => mode, origin: ORIGIN }) };
}

describe('createRoutedFetch', () => {
  // --- 本題: SW 抜きで API へ prefix が付く ---
  test('live_mode_api_path_gets_live_prefix_without_service_worker', async () => {
    const { base, fetch } = make('live');
    await fetch('/candles?datasetRef=jp225_tick&timeframe=1m&limit=1500');
    expect(base.calls[0]).toBe('/live/candles?datasetRef=jp225_tick&timeframe=1m&limit=1500');
  });

  test('live_mode_catalog_gets_live_prefix', async () => {
    const { base, fetch } = make('live');
    await fetch('/catalog');
    expect(base.calls[0]).toBe('/live/catalog');
  });

  test('replay_mode_api_path_gets_replay_prefix', async () => {
    const { base, fetch } = make('replay');
    await fetch('/compute');
    expect(base.calls[0]).toBe('/replay/compute');
  });

  // --- ★最優先の不具合是正★ sim モードが live core へ無音で誤配される（§3.5.6 #9）---
  //
  // 旧 resolveMode は `m === 'replay' ? 'replay' : 'live'` で、**未知値をすべて live へ倒して**いた。
  //   'sim' はエラーにならず /live/* へ届き、ライブ core が**それらしい答えを返してしまう**。
  //   失敗が観測できない誤配（無音の間違い）であり、Phase 1 の通過条件に挙げられている。
  //   是正は「表の許可集合に含まれるモードだけ通し、含まれない値のみ既定へ倒す」。
  test('sim_mode_api_path_gets_sim_prefix_not_live', async () => {
    const { base, fetch } = make('sim');
    await fetch('/candles?datasetRef=jp225_tick&timeframe=1m&limit=1500');
    expect(base.calls[0]).toBe('/sim/candles?datasetRef=jp225_tick&timeframe=1m&limit=1500');
  });

  test('sim_mode_compute_post_goes_to_sim_core', async () => {
    const { base, fetch } = make('sim');
    await fetch('/compute', { method: 'POST', body: '{}' });
    expect(base.calls[0]).toBe('/sim/compute');
  });

  test('every_mode_in_the_table_routes_to_its_own_prefix', async () => {
    // 表に載っている全モードが自分の core へ回る（第 4 モード追加時も本ケースが自動で覆う）。
    for (const id of MODE_IDS) {
      const { base, fetch } = make(id);
      await fetch('/candles');
      expect(base.calls[0]).toBe(`${prefixOf(id)}/candles`);
    }
  });

  test('sim_mode_reads_mode_per_call_when_toggled_from_replay', async () => {
    const base = spyFetch();
    let mode = 'replay';
    const fetch = createRoutedFetch({ baseFetch: base, getMode: () => mode, origin: ORIGIN });
    await fetch('/candles');
    mode = 'sim';
    await fetch('/candles');
    expect(base.calls).toEqual(['/replay/candles', '/sim/candles']);
  });

  // --- 冪等: SW が生きている環境で二重付与しない ---
  test('already_prefixed_path_is_unchanged', async () => {
    const { base, fetch } = make('live');
    await fetch('/live/candles');
    expect(base.calls[0]).toBe('/live/candles');
  });

  // --- 非 API・静的資産は不変 ---
  test('static_asset_path_is_unchanged', async () => {
    const { base, fetch } = make('live');
    await fetch('/js/adapter/front/composition_root_front.js');
    expect(base.calls[0]).toBe('/js/adapter/front/composition_root_front.js');
  });

  // --- クロスオリジンは不変 ---
  test('cross_origin_absolute_url_is_unchanged', async () => {
    const { base, fetch } = make('live');
    await fetch('https://example.com/candles');
    expect(base.calls[0]).toBe('https://example.com/candles');
  });

  // --- 同一オリジンの絶対 URL も対象 ---
  test('same_origin_absolute_url_gets_prefix', async () => {
    const { base, fetch } = make('live');
    await fetch(`${ORIGIN}/candles?tf=1D`);
    expect(base.calls[0]).toBe('/live/candles?tf=1D');
  });

  // --- モードは呼び出しごとに読む（切替後も同一実体で正しく回る）---
  test('mode_is_read_per_call_so_toggle_takes_effect', async () => {
    const base = spyFetch();
    let mode = 'live';
    const fetch = createRoutedFetch({ baseFetch: base, getMode: () => mode, origin: ORIGIN });
    await fetch('/candles');
    mode = 'replay';
    await fetch('/candles');
    expect(base.calls).toEqual(['/live/candles', '/replay/candles']);
  });

  // --- init は素通し（method/headers/body を壊さない）---
  test('init_is_passed_through_unchanged', async () => {
    const base = spyFetch();
    const fetch = createRoutedFetch({ baseFetch: base, getMode: () => 'live', origin: ORIGIN });
    const init = { method: 'POST', body: '{}' };
    const res = await fetch('/compute', init);
    expect(res.init).toBe(init);
  });

  // --- 異常系: baseFetch 未注入は構築時に落とす（黙って global を掴まない）---
  test('missing_base_fetch_throws_at_construction', () => {
    expect(() => createRoutedFetch({ getMode: () => 'live' })).toThrow(TypeError);
  });

  // --- 未知モードは live へ倒す（全域性）---
  test('unknown_mode_falls_back_to_live', async () => {
    const { base, fetch } = make(undefined);
    await fetch('/candles');
    expect(base.calls[0]).toBe('/live/candles');
  });
});
