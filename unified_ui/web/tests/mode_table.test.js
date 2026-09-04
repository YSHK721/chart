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
  BOTTOM_PANE_HOST_KIND,
} from '../js/mode_table.js';

describe('mode_table — モード定義表', () => {
  // --- T1: 表の内容（3 値化の到達点）---
  test('table_lists_live_replay_sim_dashboard_in_that_order', () => {
    // Assert: 既定モード（live）が先頭。巡回既定はこの順に従う。
    //   第 4 モード dashboard は末尾（ISSUE-452 / 設計書 §4.6・追加は表の 1 行）。
    expect(MODE_IDS).toEqual(['live', 'replay', 'sim', 'dashboard']);
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

  // --- ISSUE-452 / 設計書 §4.6: 第 4 モード dashboard（価格ラダーの置き場所）---------------
  //
  // ラダーはチャート画面へ置けない（価格軸整列 2.4px/行・ページ級タブ不在・併置でチャートが
  //   320px 狭くなる＝いずれも実測で却下）。`/live` `/replay` `/sim` と並ぶ 4 つ目のモードとして
  //   追加する。追加は**本表への 1 行**で完結し、prefix 判定・body クラス・SW・routed_fetch の
  //   本体は 1 行も変わらない（それを覆うのが表駆動の各検定）。
  test('dashboard_row_declares_prefix_body_class_and_toggle_like_the_other_modes', () => {
    // Assert: 1 行が既存 3 モードと同じ 6 属性を持つ（欠けた属性は無音の失敗になる）。
    expect(modeOf('dashboard')).toMatchObject({
      id: 'dashboard',
      prefix: '/dashboard',
      bodyClass: 'um-mode-dashboard',
      toggleId: 'enter-dashboard',
      label: 'ダッシュボード',
      buttonTitle: 'ダッシュボード表示のオン・オフ',
    });
  });

  test('dashboard_core_declares_that_it_has_no_chart_api', () => {
    // Assert: dashboard core は `/candles` `/compute` を持たない（専用プロセス・arch-spec §3）。
    //   true と誤宣言すると、時間足・指標の操作が 404 になるだけで画面に何も起きない（無音の失敗）。
    expect(modeOf('dashboard').chartApi).toBe(false);
    expect(hasChartApi('dashboard')).toBe(false);
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

  // --- ISSUE-479 Wave2 J-5: 表示層の入口も表が持つ（unified_root の表駆動化）------------
  //
  // なぜ表へ載せるか: 従来 unified_root は sim / dashboard の合成根 URL を**自分の定数**で持ち、
  //   `import` 文・destructuring・setup 呼出・layers の 4 箇所へモードごとの行を書いていた。
  //   モードを 1 つ足すたびに 4 箇所を同時に直す義務が生まれ、1 箇所でも取り残すと
  //   **無症状で誤動作する**（押しても器が出ない）。入口も表の属性にすれば、第 5 モードの追加は
  //   表の 1 行で完結し、統合層の本体は 1 行も変わらない（OCP）。
  //
  // 属性 3 つ:
  //   displayLayerPath   … 表示層の入口 module の URL（自 core の公開面のみ・持たないモードは null）
  //   displayLayerExport … その module が公開する据付関数の名前
  //   hostKind           … 統合層が渡す器の種別（器の所有者は統合層＝core は id を知らない）
  test('each_row_declares_a_display_layer_entry_point_or_declares_it_has_none', () => {
    // Assert: 属性が「在るか無いか」ではなく**全行に在る**こと（欠けた行は無音の失敗になる）。
    for (const row of MODES) {
      expect(Object.keys(row)).toEqual(expect.arrayContaining([
        'displayLayerPath', 'displayLayerExport', 'hostKind',
      ]));
    }
    // 単一 chart の上で働く core（chartApi あり）は自前の表示層を読み込まない。
    //   live はチャートそのもの、replay の層は live 合成根が boot.replayHandle として返す。
    for (const row of MODES.filter((m) => m.chartApi)) {
      expect(row.displayLayerPath).toBe(null);
      expect(row.displayLayerExport).toBe(null);
      expect(row.hostKind).toBe(null);
    }
  });

  test('a_core_without_the_chart_api_declares_its_own_display_layer', () => {
    // Assert: 自前の器を出す層（chartApi なし）は入口を必ず宣言する。宣言が無ければ統合層は
    //   その層を読み込めず、モードへ入っても何も出ない（ISSUE-291 と同型の「無言の死」）。
    const hosted = MODES.filter((m) => !m.chartApi);
    expect(hosted.length).toBeGreaterThan(0);
    for (const row of hosted) {
      expect(typeof row.displayLayerPath).toBe('string');
      expect(typeof row.displayLayerExport).toBe('string');
      expect(row.displayLayerExport.length).toBeGreaterThan(0);
      expect(typeof row.hostKind).toBe('string');
      expect(row.hostKind.length).toBeGreaterThan(0);
    }
  });

  test('a_declared_display_layer_path_names_only_the_public_facade_of_its_own_core', () => {
    // Assert: 入口は **自 core の公開面**（`/<prefix>/js/public/*.js`）だけを名指す。内部階層
    //   （adapter/front/…）を名指すと core 側の配置換えで統合層が無言の 404 になる。これらは
    //   識別子渡しの動的 import で読まれるため import 走査には原理的に現れない（J-4 の実測）。
    const declared = MODES.filter((m) => m.displayLayerPath);
    expect(declared.length).toBeGreaterThan(0); // 空振り（走査 0 件で緑）を塞ぐ。
    for (const row of declared) {
      expect(row.displayLayerPath).toMatch(
        new RegExp(`^${row.prefix}/js/public/[^/]+\\.js$`),
      );
    }
  });

  test('host_kind_agrees_with_the_bottom_pane_attribute_of_the_same_row', () => {
    // Assert: 「どの器へ挿すか」と「body へ um-bottom-pane-mode を付けるか」は同じ事実である。
    //   別々に宣言している以上、片方だけ直された日に静かにずれる。突合を検定で塞ぐ
    //   （導出化＝bottomPane 属性の撤去は承認事項として別途提案する）。
    for (const row of MODES) {
      expect(row.bottomPane).toBe(row.hostKind === BOTTOM_PANE_HOST_KIND);
    }
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
    expect(MODE_PREFIXES).toEqual(['/live', '/replay', '/sim', '/dashboard']);
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
    expect(nextMode('sim')).toBe('dashboard');
    expect(nextMode('dashboard')).toBe('live');
  });

  test('next_mode_of_unknown_returns_default_mode', () => {
    expect(nextMode('nope')).toBe(DEFAULT_MODE);
  });

  // --- T5: ツールバーのボタン構成（app_chrome_view へ注入する定義配列）---
  test('toggle_buttons_exclude_default_mode_and_carry_id_label_title', () => {
    // 既定モード（live）はトグルの「オフ」状態＝専用ボタンを持たない。
    expect(MODE_TOGGLE_BUTTONS.map((b) => b.id)).toEqual(['enter-replay', 'enter-sim', 'enter-dashboard']);
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
