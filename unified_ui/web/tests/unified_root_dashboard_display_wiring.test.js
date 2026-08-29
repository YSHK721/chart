// 統合ルートが第 4 モード（dashboard）の表示層を **実際に結線している**ことの固定。
//
// なぜソースを読むのか（unified_root_toolbar_wiring.test.js と同じ理由）: `main()` は
//   document/window のあるブラウザでしか起動しないため node からは実行できない。一方で本件は
//   **受け口だけ作って呼び出し側が送っていない**という壊れ方が実際に起きる箇所である
//   （ISSUE-291 の実測: サーバ側に分岐を作っても front が送らなければ無言で死ぬ）。
//   createModeController 側の `layers` は単体検定で覆われているので、ここでは
//   「合成根がその口へ dashboard の層を実際に流し込んでいるか」だけを固定する。
//
// 参照実装は sim（`SIM_ROOT` → `setupSimDisplay({doc, host: bottomPane.host(), …})` →
//   ハンドルを controller へ注入）。dashboard はこれに厳密に倣う（新方式を作らない）。
//
// 構造は AAA。テスト名は「対象_条件_期待結果」。

import { describe, test, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

import { modeOf } from '../js/mode_table.js';

const SRC = readFileSync(fileURLToPath(new URL('../js/unified_root.js', import.meta.url)), 'utf8');
const SRC_NO_COMMENTS = SRC.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '');

describe('unified_root — dashboard 表示層の結線（T-4 拡張点の呼び出し側）', () => {
  test('dashboard_front_root_is_loaded_through_the_mode_prefix', () => {
    // Assert: 合成根の URL は当該モードの prefix 配下（router が dashboard core へ回す）。
    //   prefix は定義表から取る（テスト側にも第 2 の定義を持たない）。
    const prefix = modeOf('dashboard').prefix;
    expect(SRC_NO_COMMENTS).toContain(`${prefix}/js/adapter/front/composition_root_front.js`);
  });

  test('dashboard_display_setup_is_imported_and_called', () => {
    // Assert: sim の `setupSimDisplay` と同形の入口を import して呼んでいる。
    expect(SRC_NO_COMMENTS).toMatch(/\{\s*setupDashboardDisplay\s*\}\s*=\s*await import\(/);
    expect(SRC_NO_COMMENTS).toMatch(/await setupDashboardDisplay\(\{/);
  });

  test('dashboard_display_is_hosted_by_the_bottom_pane_like_sim', () => {
    // Assert: 置き場所を決めるのは統合層（器の所有者）。dashboard 側は渡された host へ挿すだけ。
    const call = SRC_NO_COMMENTS.match(/await setupDashboardDisplay\(\{[\s\S]*?\n\s*\}\)/);
    expect(call).not.toBeNull();
    expect(call[0]).toMatch(/host:\s*bottomPane\.host\(\)/);
    expect(call[0]).toMatch(/doc:\s*document/);
  });

  test('dashboard_display_receives_a_read_only_live_scoped_storage', () => {
    // Assert: テンプレート束は live スコープの storage を**読み取り専用**で渡す（T-2）。
    //   書ける口を渡すと第 4 モードの不具合が live の資産を壊す経路になる。
    const call = SRC_NO_COMMENTS.match(/await setupDashboardDisplay\(\{[\s\S]*?\n\s*\}\)/);
    expect(call[0]).toMatch(/readOnlyStorage\(/);
    // 読み取り専用ラッパは葉モジュールから import する（合成根が自前で書かない）。
    expect(SRC_NO_COMMENTS).toMatch(/import\s*\{\s*readOnlyStorage\s*\}\s*from\s*'\.\/readonly_storage\.js'/);
    // スコープを選ぶのは合成根（View ではない）。live スコープであることを明示している。
    expect(SRC_NO_COMMENTS).toMatch(/scopedStorage\([^)]*MODE\.LIVE\)/);
  });

  test('dashboard_layer_is_registered_into_the_layers_map_of_the_controller', () => {
    // Assert: 端から端まで結線（受け口だけ作って渡さない状態を検出する）。
    // 定義側（`export function createModeController({…})`）ではなく**呼び出し側**を見る。
    const call = SRC_NO_COMMENTS.match(/modeController = createModeController\(\{[\s\S]*?\n\s*\}\);/);
    expect(call).not.toBeNull();
    expect(call[0]).toMatch(/layers:/);
    expect(call[0]).toContain('dashboardHandle');
    // sim の層も同じ口から渡す（層の受け取り方を 2 通りに分けない）。
    expect(call[0]).toContain('simHandle');
  });

  test('the_root_never_builds_the_dashboard_dom_itself', () => {
    // Assert: 表示要素は View が生成し所有する（ISSUE-452 禁止事項・overlay_host.js 規約）。
    //   合成根が DOM を作り始めると、中央 factory へ育って OCP 違反になる。
    expect(SRC_NO_COMMENTS).not.toMatch(/document\.createElement/);
    expect(SRC_NO_COMMENTS).not.toMatch(/innerHTML/);
  });

  test('mode_ids_are_not_hardcoded_as_string_literals_in_the_root', () => {
    // Assert: モード名は定義表由来（MODE.* / 表の走査）。生の 'dashboard' を書かない。
    expect(SRC_NO_COMMENTS).not.toContain("'dashboard'");
    expect(SRC_NO_COMMENTS).not.toContain('"dashboard"');
  });
});
