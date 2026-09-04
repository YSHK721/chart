// sw_rewrite.rewritePath の契約テスト（Red フェーズ）。
//
// 保証対象（基本設計書 §2 / §3）: Service Worker がアクティブモードに応じて
// root 相対 API fetch を `/live/*` `/replay/*` へリライトする純ロジック。
// 構造は AAA。テスト名は「対象_条件_期待結果」。
//
// Red: rewritePath は未実装で throw するため全ケース失敗。

import { describe, test, expect } from 'vitest';
import { rewritePath, LIVE_ONLY_SEGMENTS } from '../js/sw_rewrite.js';
// モード集合・prefix の単一ソース（§3.5.6 の表駆動化）。テスト側も第 2 の定義を持たない。
import { MODE_PREFIXES } from '../js/mode_table.js';

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

  // --- B2s: 第 3 モード sim の契約（基本設計書 §3.5.6 #7・§11.2）------------------
  //
  // 注記（TDD の誠実性）: 以下 4 件は**表駆動化の前でも通る**。旧実装の付与は
  //   `` `/${mode}${path}` `` というテンプレート補間で、モード名を検証せずそのまま prefix に
  //   していたためである（= 'sim' でも偶然正しい文字列になる）。よってこれらは Red ではなく
  //   **sim 契約の回帰固定**として置く。旧実装が実際に壊れているのは直後の全域性
  //   （未知モード）であり、そちらが本 Cycle の Red である。
  test('sim_mode_api_paths_get_sim_prefix', () => {
    expect(rewritePath('sim', '/compute')).toBe('/sim/compute');
    expect(rewritePath('sim', '/candles')).toBe('/sim/candles');
  });

  test('sim_mode_api_path_with_query_preserves_query', () => {
    expect(rewritePath('sim', '/candles?tf=1D')).toBe('/sim/candles?tf=1D');
  });

  test('every_mode_prefix_in_the_table_is_treated_as_already_prefixed', () => {
    // 二重付与しない（front 付与 → SW 素通しの冪等性）。判定は表由来なので、第 4 モードを
    //   表へ足した時点で本ケースも自動的にその prefix を覆う（列挙の取り残しが起きない）。
    for (const prefix of MODE_PREFIXES) {
      expect(rewritePath('sim', `${prefix}/compute`)).toBe(`${prefix}/compute`);
      expect(rewritePath('sim', prefix)).toBe(prefix);
    }
  });

  test('live_only_segments_route_to_live_even_in_sim_mode', () => {
    // LIVE_ONLY_SEGMENTS の既存挙動は sim でも不変（replay と同じ扱い）。
    for (const seg of LIVE_ONLY_SEGMENTS) {
      expect(rewritePath('sim', `/${seg}`)).toBe(`/live/${seg}`);
      expect(rewritePath('sim', `/${seg}?x=1`)).toBe(`/live/${seg}?x=1`);
    }
  });

  // --- B2t: ★Red★ 表に無いモードは既定モードの prefix へ倒す（全域性）--------------
  //
  // 旧実装は `` `/${mode}${path}` `` でモード名をそのまま prefix にしていた。表に無い値
  //   （タイプミス・将来値・壊れた SW メッセージ）を渡すと `/nope/compute` という**どの core
  //   にも存在しない**パスを生成し、ルータで 404 になる。routed_fetch.js:69 の誤配（§3.5.6 #9）と
  //   同じ「無音で間違った先へ行く」欠陥である。prefix は表から引く＝表に無ければ既定へ倒す。
  test('unknown_mode_falls_back_to_the_default_mode_prefix', () => {
    expect(rewritePath('nope', '/compute')).toBe('/live/compute');
    expect(rewritePath(undefined, '/candles?tf=1D')).toBe('/live/candles?tf=1D');
    expect(rewritePath('SIM', '/candles')).toBe('/live/candles');   // 大文字は表に無い
  });

  // --- B2a: tickvol_profile（取引密度帯）は両 core 実装済み＝アクティブモードの core へ回す ---
  test('tickvol_profile_routes_to_the_active_mode_core', () => {
    // ライブ core・リプレイ core の双方が同一実装を持ち応答が byte 一致するため、live 固定にしない。
    expect(rewritePath('live', '/tickvol_profile?datasetRef=jp225_tick&sessions=20'))
      .toBe('/live/tickvol_profile?datasetRef=jp225_tick&sessions=20');
    expect(rewritePath('replay', '/tickvol_profile?datasetRef=jp225_tick&until=1785528000'))
      .toBe('/replay/tickvol_profile?datasetRef=jp225_tick&until=1785528000');
    expect(LIVE_ONLY_SEGMENTS.has('tickvol_profile')).toBe(false);
  });

  // --- B2b: tf_period_profile はライブ core 専用（replay core 未実装）＝モードに関わらず常に /live ---
  test('tf_period_profile_always_routes_to_live_regardless_of_mode', () => {
    // replay モードでも /replay ではなく /live（replay core は 404 を返すため）。
    expect(rewritePath('replay', '/tf_period_profile?datasetRef=jp225_tick&timeframe=1D&from=1&to=2'))
      .toBe('/live/tf_period_profile?datasetRef=jp225_tick&timeframe=1D&from=1&to=2');
    // live モードでも当然 /live。
    expect(rewritePath('live', '/tf_period_profile?x=1')).toBe('/live/tf_period_profile?x=1');
  });

  // --- B2c: OCP 是正の表駆動回帰 — LIVE_ONLY_SEGMENTS の各セグメントはモード非依存で常に /live ---
  test('all_live_only_segments_route_to_live_in_both_modes', () => {
    expect(LIVE_ONLY_SEGMENTS.size).toBeGreaterThan(0);
    for (const seg of LIVE_ONLY_SEGMENTS) {
      for (const mode of ['live', 'replay']) {
        expect(rewritePath(mode, `/${seg}`)).toBe(`/live/${seg}`);
        expect(rewritePath(mode, `/${seg}?x=1`)).toBe(`/live/${seg}?x=1`);
      }
    }
  });

  test('live_only_table_matches_legacy_hardcode', () => {
    // 従来 rewritePath 本体にハードコードしていた特例集合と表が完全一致＝振る舞い不変の回帰壁。
    expect([...LIVE_ONLY_SEGMENTS].sort()).toEqual(['tf_period_profile']);
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
