// モード定義表（mode_table.js）の契約テスト。
//
// 保証対象（基本設計書 §3.5.6 / §11.2）: モード集合・URL prefix・body クラス・トグルボタン id・
//   ボタンラベルの**単一ソース**。統合層のフロント各モジュールと sw.js はこの表だけを参照し、
//   モード名・prefix・クラス名を自前で書かない（第 4 モード追加＝表 1 行の追加で本体不変）。
// 構造は AAA。テスト名は「対象_条件_期待結果」。

import { describe, test, expect } from 'vitest';
import {
  MODES,
  MODE_IDS,
  DEFAULT_MODE,
  MODE_PREFIXES,
  MODE_TOGGLE_BUTTONS,
  isKnownMode,
  modeOf,
  prefixOf,
  bodyClassOf,
  nextMode,
  hasChartApi,
  CHART_API_BODY_CLASS,
} from '../js/mode_table.js';

describe('mode_table — モード定義表', () => {
  // --- T1: 表の内容（3 値化の到達点）---
  test('table_lists_live_replay_sim_in_that_order', () => {
    // Assert: 既定モード（live）が先頭。巡回既定はこの順に従う。
    expect(MODE_IDS).toEqual(['live', 'replay', 'sim']);
    expect(DEFAULT_MODE).toBe('live');
  });

  test('each_row_declares_prefix_body_class_and_toggle', () => {
    // Assert: 1 行が「モード名・URL prefix・body クラス・トグル id・ラベル」を全部持つ。
    expect(modeOf('live')).toMatchObject({
      id: 'live', prefix: '/live', bodyClass: 'um-mode-live', toggleId: null, label: null,
    });
    expect(modeOf('replay')).toMatchObject({
      id: 'replay', prefix: '/replay', bodyClass: 'um-mode-replay', toggleId: 'enter-replay', label: 'リプレイ',
    });
    expect(modeOf('sim')).toMatchObject({
      id: 'sim', prefix: '/sim', bodyClass: 'um-mode-sim', toggleId: 'enter-sim', label: 'シミュレーション',
    });
  });

  // --- 🟡-5: core がチャート API を持つか（時間足切替・指標計算の可否）------------------
  //
  // sim core は Phase 1 で静的配信しか持たない（`simulator/sim_ui/framework/serve_sim.py`）。
  //   sim モード中にツールバーの時間足・指標を操作すると `/sim/candles` `/sim/compute` が飛び、
  //   **404 になるだけで画面には何も出ない**（無音の失敗）。どの core がチャート API を持つかは
  //   モードの属性なので、表に持たせて UI 側が参照できるようにする。
  test('each_row_declares_whether_its_core_has_the_chart_api', () => {
    // Assert: live / replay は持つ。sim は Phase 1 では持たない。
    expect(modeOf('live').chartApi).toBe(true);
    expect(modeOf('replay').chartApi).toBe(true);
    expect(modeOf('sim').chartApi).toBe(false);
  });

  test('has_chart_api_reports_the_declared_value_and_defaults_false_for_unknown', () => {
    expect(hasChartApi('live')).toBe(true);
    expect(hasChartApi('sim')).toBe(false);
    // 未知モードは「持たない」側へ倒す（持つと誤認して操作させる方が有害）。
    expect(hasChartApi('nope')).toBe(false);
  });

  test('table_and_rows_are_frozen', () => {
    // Assert: 実行時に書き換えられない（単一ソースの保証）。
    expect(Object.isFrozen(MODES)).toBe(true);
    for (const row of MODES) {
      expect(Object.isFrozen(row)).toBe(true);
    }
  });

  // --- T2: 許可集合（routed_fetch / sw.js の誤配遮断の土台）---
  test('is_known_mode_accepts_all_three_and_rejects_others', () => {
    for (const id of MODE_IDS) {
      expect(isKnownMode(id)).toBe(true);
    }
    for (const bad of [undefined, null, '', 'LIVE', 'simulation', 'foo', 0, {}]) {
      expect(isKnownMode(bad)).toBe(false);
    }
  });

  // --- T3: 派生表（prefix / body クラス）---
  test('prefixes_are_derived_from_the_table', () => {
    expect(MODE_PREFIXES).toEqual(['/live', '/replay', '/sim']);
  });

  test('prefix_of_unknown_mode_falls_back_to_default_mode_prefix', () => {
    expect(prefixOf('sim')).toBe('/sim');
    expect(prefixOf('nope')).toBe('/live');
  });

  test('body_class_of_returns_the_declared_class', () => {
    expect(bodyClassOf('sim')).toBe('um-mode-sim');
    expect(bodyClassOf('nope')).toBe(null);
  });

  // --- T4: 巡回（toggle の既定算出が表由来であること）---
  test('next_mode_walks_the_table_and_wraps_around', () => {
    expect(nextMode('live')).toBe('replay');
    expect(nextMode('replay')).toBe('sim');
    expect(nextMode('sim')).toBe('live');
  });

  test('next_mode_of_unknown_returns_default_mode', () => {
    expect(nextMode('nope')).toBe(DEFAULT_MODE);
  });

  // --- T5: ツールバーのボタン構成（app_chrome_view へ注入する定義配列）---
  test('toggle_buttons_exclude_default_mode_and_carry_id_label_title', () => {
    // 既定モード（live）はトグルの「オフ」状態＝専用ボタンを持たない。
    expect(MODE_TOGGLE_BUTTONS.map((b) => b.id)).toEqual(['enter-replay', 'enter-sim']);
    for (const b of MODE_TOGGLE_BUTTONS) {
      expect(typeof b.label).toBe('string');
      expect(b.label.length).toBeGreaterThan(0);
      expect(typeof b.title).toBe('string');
      expect(typeof b.mode).toBe('string');
    }
  });

  test('toggle_button_for_replay_keeps_the_existing_markup_contract', () => {
    // 既存 markup（app_chrome_view の enterReplay 分岐）と byte 等価にするための固定値。
    const replay = MODE_TOGGLE_BUTTONS.find((b) => b.mode === 'replay');
    expect(replay).toMatchObject({
      id: 'enter-replay', label: 'リプレイ', title: 'リプレイ表示のオン・オフ',
    });
  });
});
