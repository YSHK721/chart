// 統合ルートが第 4 モード（dashboard）の表示層を **実際に結線している**ことの固定。
//
// 壊れ方の想定（ISSUE-291 の実測）: **受け口だけ作って呼び出し側が送っていない**。サーバ側に
//   分岐を作っても front が送らなければ無言で死ぬ。createModeController 側の `layers` は
//   単体検定で覆われているので、ここでは「合成根がその口へ dashboard の層を実際に流し込んで
//   いるか」だけを固定する。
//
// 検定の立て方の変更（ISSUE-479 Wave2b J-5・assert 差し替えの記録）:
//   旧検定は `main()` が node から実行できないことを理由に、**ソース文字列の一致**で結線を
//   見ていた（`'/dashboard/js/adapter/front/composition_root_front.js' を含む`・
//   `await setupDashboardDisplay({` を含む・`host: dashboardArea` を含む・
//   `layers:` の中に `dashboardHandle` という識別子が在る、等）。これは 2 つの意味で弱い:
//     1. 綴りが合っていても**実際に呼ばれるか**は分からない（文字列は実行されない）。
//     2. dashboard という 1 モードの手書き行を固定するので、第 5 モードを足したときに
//        「足し忘れ」を検出できない（検定自体が OCP 違反を保護していた）。
//   J-5 で読み込み・据付・登録は `loadDisplayLayers`（MODES の走査）に閉じたので、本検定は
//   **その関数を実際に走らせて**、表の行から導いた入口・器・引数・登録が成立することを見る。
//   固定していた性質は 1 つも落とさず、いずれも「dashboard 固有の手書き」から
//   「表の行から導かれる形」へ移してある（＝第 5 モードにも同じ検査が効く）。
//
// 構造は AAA。テスト名は「対象_条件_期待結果」。

import { describe, test, expect, vi } from 'vitest';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

import { modeOf, MODES } from '../js/mode_table.js';
import { loadDisplayLayers } from '../js/unified_root.js';

const SRC = readFileSync(fileURLToPath(new URL('../js/unified_root.js', import.meta.url)), 'utf8');
const SRC_NO_COMMENTS = SRC.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '');

const DASHBOARD = modeOf('dashboard');

/** 表が宣言した入口 URL に応じた偽 module を返す spy（実際の読込を差し替える）。 */
function makeHarness() {
  const setups = new Map();
  const importModule = vi.fn(async (url) => {
    const row = MODES.find((m) => m.displayLayerPath === url);
    const setup = vi.fn(async (args) => ({ args, enable: vi.fn(), disable: vi.fn() }));
    setups.set(row.id, setup);
    return { [row.displayLayerExport]: setup };
  });
  const bottomPane = {
    isUserSized: () => false, setHeightPx: vi.fn(), host: () => 'BOTTOM_PANE_HOST',
  };
  const context = {
    doc: 'DOC',
    hosts: { bottomPane: 'BOTTOM_PANE_HOST', fullArea: 'FULL_AREA_HOST' },
    lwc: 'LWC',
    bottomPane,
    liveStorage: { getItem: (k) => `stored:${k}`, key: () => null, length: 0 },
  };
  return { importModule, setups, context };
}

describe('unified_root — dashboard 表示層の結線（T-4 拡張点の呼び出し側）', () => {
  test('dashboard_layer_is_loaded_from_the_entry_point_declared_by_its_own_table_row', async () => {
    // Arrange: 旧 assert（ソースに `<prefix>/js/adapter/front/composition_root_front.js` が
    //   在る）が固定していた「合成根の URL は当該モードの prefix 配下」は、表の行が持つ規則へ
    //   移した（mode_table の `a_declared_display_layer_path_names_only_the_public_facade_of_its_own_core`）。
    //   ここではさらに強く、**その URL が実際に読み込まれる**ことを見る。
    const { importModule, context } = makeHarness();

    // Act
    await loadDisplayLayers({ importModule, context });

    // Assert
    expect(importModule).toHaveBeenCalledWith(DASHBOARD.displayLayerPath);
    expect(DASHBOARD.displayLayerPath.startsWith(`${DASHBOARD.prefix}/`)).toBe(true);
    // 統合層は入口 URL を自前の定数で持たない（表が唯一源＝置き換え忘れを作らない）。
    expect(SRC_NO_COMMENTS).not.toContain(DASHBOARD.displayLayerPath);
  });

  test('the_setup_published_under_the_declared_export_name_is_actually_invoked_once', async () => {
    // Arrange: 旧 assert（`{ setupDashboardDisplay } = await import(` と
    //   `await setupDashboardDisplay({` がソースに在る）が固定していた「入口を import して
    //   呼んでいる」は、**実際に 1 回呼ばれる**ことの観測へ移した。
    const { importModule, setups, context } = makeHarness();

    // Act
    await loadDisplayLayers({ importModule, context });

    // Assert
    expect(setups.get(DASHBOARD.id)).toHaveBeenCalledTimes(1);
    // モードごとの個別 import 文・個別呼出は本体から消えている（表の走査 1 本に閉じた）。
    expect(SRC_NO_COMMENTS).not.toMatch(/setupDashboardDisplay/);
    expect(SRC_NO_COMMENTS).not.toMatch(/setupSimDisplay/);
  });

  test('dashboard_display_is_hosted_by_its_own_full_area_not_the_bottom_pane', async () => {
    // Arrange: 旧 assert（`host: dashboardArea` を含み `host: bottomPane.host()` を含まない）が
    //   固定していた「置き場所は専用の全面ホストであって sim と同じ下部ペインではない」は、
    //   **据付関数が実際に受け取った host** の観測へ移した（ISSUE-460 の是正内容は不変）。
    const { importModule, setups, context } = makeHarness();

    // Act
    await loadDisplayLayers({ importModule, context });
    const [args] = setups.get(DASHBOARD.id).mock.calls[0];

    // Assert
    expect(args.host).toBe(context.hosts.fullArea);
    expect(args.host).not.toBe(context.hosts.bottomPane);
    expect(args.doc).toBe(context.doc);
    // 器の生成は専用 View（dashboard_area_view.js）が所有する（合成根は DOM を作らない規約）。
    expect(SRC_NO_COMMENTS).toMatch(/mountDashboardArea\(/);
  });

  test('dashboard_display_receives_a_read_only_live_scoped_storage', async () => {
    // Arrange: 旧 assert（呼出テキストに `readOnlyStorage(` が在る）が固定していた
    //   「テンプレート束は読み取り専用」は、**渡された束が実際に書き込みを拒む**ことの
    //   観測へ移した（T-2）。書ける口を渡すと第 4 モードの不具合が live の資産を壊す経路になる。
    const { importModule, setups, context } = makeHarness();

    // Act
    await loadDisplayLayers({ importModule, context });
    const [args] = setups.get(DASHBOARD.id).mock.calls[0];

    // Assert
    expect(args.templates.getItem('tpl')).toBe('stored:tpl');
    expect(() => args.templates.setItem('tpl', 'x')).toThrow(TypeError);
    expect(() => args.templates.removeItem('tpl')).toThrow(TypeError);
    // 読み取り専用ラッパは葉モジュールから import する（合成根が自前で書かない）。
    expect(SRC_NO_COMMENTS).toMatch(/import\s*\{\s*readOnlyStorage\s*\}\s*from\s*'\.\/readonly_storage\.js'/);
    // スコープを選ぶのは合成根（View ではない）。live スコープであることを明示している。
    expect(SRC_NO_COMMENTS).toMatch(/scopedStorage\([^)]*MODE\.LIVE\)/);
  });

  test('every_declaring_mode_is_registered_into_the_layers_map_handed_to_the_controller', async () => {
    // Arrange: 旧 assert（createModeController 呼出のテキストに `dashboardHandle` と
    //   `simHandle` という識別子が在る）が固定していた「端から端まで結線されている」は、
    //   **宣言した全モードが登録される**ことの観測＋「その Map がそのまま渡る」ことの
    //   構造固定へ移した。旧形は 2 モードの名前を数え上げていたので、第 5 モードの
    //   足し忘れを検出できなかった（検定が OCP 違反を保護していた）。
    const { importModule, context } = makeHarness();

    // Act
    const layers = await loadDisplayLayers({ importModule, context });

    // Assert: 登録は表の宣言と 1:1（欠けも余りも無い）。
    expect([...layers.keys()])
      .toEqual(MODES.filter((m) => m.displayLayerPath).map((m) => m.id));
    expect(layers.has(DASHBOARD.id)).toBe(true);
    // 呼び出し側は「作った Map をそのまま渡す」形である（受け口だけ作って渡さない状態の検出）。
    const built = SRC_NO_COMMENTS.match(/(\w+)\s*=\s*await loadDisplayLayers\(/);
    expect(built, 'loadDisplayLayers の戻り値を束縛していない').not.toBeNull();
    expect(SRC_NO_COMMENTS).toMatch(new RegExp(`(?:const|let)\\s+${built[1]}\\b`));
    const call = SRC_NO_COMMENTS.match(/modeController = createModeController\(\{[\s\S]*?\n\s*\}\);/);
    expect(call).not.toBeNull();
    expect(call[0]).toMatch(new RegExp(`layers(:\\s*${built[1]})?,`));
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
