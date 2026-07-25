// sw_rewrite.rewritePath の契約テスト（Red フェーズ）。
//
// 保証対象（基本設計書 §2 / §3）: Service Worker がアクティブモードに応じて
// root 相対 API fetch を `/live/*` `/replay/*` へリライトする純ロジック。
// 構造は AAA。テスト名は「対象_条件_期待結果」。
//
// Red: rewritePath は未実装で throw するため全ケース失敗。

import { describe, test, expect } from 'vitest';
import { rewritePath } from '../js/sw_rewrite.js';

describe('rewritePath', () => {
  // --- B1: live モードで API パスへ /live prefix 付与 ---
  test('live_mode_api_paths_get_live_prefix', () => {
    // Arrange / Act / Assert
    expect(rewritePath('live', '/compute')).toBe('/live/compute');
    expect(rewritePath('live', '/candles')).toBe('/live/candles');
  });

  test('live_mode_api_path_with_query_preserves_query', () => {
    expect(rewritePath('live', '/candles?tf=1D')).toBe('/live/candles?tf=1D');
  });

  // --- B2: replay モードで API パスへ /replay prefix 付与 ---
  test('replay_mode_api_paths_get_replay_prefix', () => {
    expect(rewritePath('replay', '/compute')).toBe('/replay/compute');
    expect(rewritePath('replay', '/intraday')).toBe('/replay/intraday');
  });

  // --- B2b: tf_period_profile はライブ core 専用（replay core 未実装）＝モードに関わらず常に /live ---
  test('tf_period_profile_always_routes_to_live_regardless_of_mode', () => {
    // replay モードでも /replay ではなく /live（replay core は 404 を返すため）。
    expect(rewritePath('replay', '/tf_period_profile?datasetRef=jp225_tick&timeframe=1D&from=1&to=2'))
      .toBe('/live/tf_period_profile?datasetRef=jp225_tick&timeframe=1D&from=1&to=2');
    // live モードでも当然 /live。
    expect(rewritePath('live', '/tf_period_profile?x=1')).toBe('/live/tf_period_profile?x=1');
  });

  // --- B3: 既に prefix 付きは不変（二重付与しない）---
  test('already_prefixed_live_path_is_unchanged', () => {
    expect(rewritePath('live', '/live/compute')).toBe('/live/compute');
  });

  test('already_prefixed_replay_path_is_unchanged', () => {
    expect(rewritePath('replay', '/replay/intraday')).toBe('/replay/intraday');
  });

  // --- B4: 非 API 静的資産は不変 ---
  test('non_api_static_paths_are_unchanged', () => {
    for (const p of ['/', '/index.html', '/js/unified_root.js', '/vendor/lib.js', '/sw.js']) {
      expect(rewritePath('live', p)).toBe(p);
    }
  });

  // --- B5: クロスオリジン絶対 URL は不変 ---
  test('cross_origin_absolute_url_is_unchanged', () => {
    const url = 'https://example.com/compute';
    expect(rewritePath('live', url)).toBe(url);
  });

  // --- 境界: 空文字パスは不変（異常系）---
  test('empty_path_is_unchanged', () => {
    expect(rewritePath('live', '')).toBe('');
  });
});
