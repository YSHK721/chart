// 表示層の読み込みが**モード定義表の走査 1 本**で組まれることの固定（ISSUE-479 Wave2b J-5）。
//
// なぜ在るか（arch-spec §0 T-4 の残り半分）:
//   `createModeController` の側は既に表駆動である（`layers` Map ＋ MODES 走査）。しかし
//   **層を作って渡す側**（`main()`）はモードごとに 4 箇所へ手で書かれていた——URL 定数・
//   `import` の destructuring・`setup*Display(...)` の呼出・`layers` Map のエントリ。
//   モードを 1 つ足すたびに 4 箇所を同時に直す義務が生まれ、1 箇所でも取り残すと
//   **無症状で誤動作する**（ボタンは点くのに器が出ない）。実際 J-4 の実測では、これらの URL は
//   識別子渡しの動的 import で読まれるため import 走査にも型にも現れず、綴り違いは実 UI で
//   初めて 404 として現れる。
//
//   よって層の読み込み・据付・登録は `loadDisplayLayers` 1 本に閉じ、モードごとの差は
//   **表の行**（displayLayerPath / displayLayerExport / hostKind）と、統合層が所有する
//   `LAYER_EXTRAS`（モード固有の追加注入）だけに置く。第 5 モードの追加は表の 1 行で完結する。
//
// 検定の立て方: `main()` は document/window のあるブラウザでしか起動しないため node からは
//   実行できない。そこで**読み込みそのものを関数として切り出し**、動的 import を注入して
//   実際に走らせる（ソース文字列の一致ではなく、振る舞いで固定する）。
//
// OCP の実証（R2）: 第 5 モードは `mode_table.js` へ 1 行足すだけで扱えなければならない。
//   本ファイルは production コードを 1 行も変えずに表だけを差し替え（vi.doMock）、層の
//   読込・登録・遷移が成立することを実測する。
//
// 構造は AAA。テスト名は「対象_条件_期待結果」。

import { describe, test, expect, vi, beforeEach } from 'vitest';

import { MODES, FULL_AREA_HOST_KIND } from '../js/mode_table.js';

//: 第 5 モードの「1 行」。既存 4 行と同じ属性しか持たない（特別扱いをしない）。
const FIFTH_ROW = Object.freeze({
  id: 'fake5',
  prefix: '/fake5',
  bodyClass: 'um-mode-fake5',
  toggleId: 'enter-fake5',
  label: '第 5 モード',
  buttonTitle: '第 5 モードのオン・オフ',
  chartApi: false,
  displayLayerPath: '/fake5/js/public/fake5_public_api.js',
  displayLayerExport: 'setupFake5Display',
  hostKind: FULL_AREA_HOST_KIND,
});

/**
 * unified_root を（必要なら表を差し替えて）読み直す。
 * production コードには手を触れず、差し替えるのは定義表だけ＝OCP の実証条件そのもの。
 */
async function loadRoot({ extraRows = [] } = {}) {
  vi.resetModules();
  if (extraRows.length > 0) {
    vi.doMock('../js/mode_table.js', async (importOriginal) => {
      const actual = await importOriginal();
      return { ...actual, MODES: Object.freeze([...actual.MODES, ...extraRows]) };
    });
  } else {
    vi.doUnmock('../js/mode_table.js');
  }
  return import('../js/unified_root.js');
}

/** 表が宣言した公開面 URL → その据付関数を持つ偽 module（発行回数を数える spy つき）。 */
function makeImportSpy(rows) {
  const setups = new Map();
  const importModule = vi.fn(async (url) => {
    const row = rows.find((r) => r.displayLayerPath === url);
    if (!row) {
      throw new Error(`未知の URL を読み込もうとした: ${url}`);
    }
    const setup = vi.fn(async (args) => ({
      id: row.id, args, enable: vi.fn(), disable: vi.fn(),
    }));
    setups.set(row.id, setup);
    return { [row.displayLayerExport]: setup };
  });
  return { importModule, setups };
}

function makeContext() {
  const bottomPane = {
    isUserSized: vi.fn(() => false),
    setHeightPx: vi.fn(),
    host: () => 'BOTTOM_PANE_HOST',
  };
  return {
    doc: 'DOC',
    hosts: { bottomPane: 'BOTTOM_PANE_HOST', fullArea: 'FULL_AREA_HOST' },
    lwc: 'LWC',
    bottomPane,
    liveStorage: { getItem: vi.fn(() => 'stored'), key: vi.fn(() => 'k'), length: 1 },
  };
}

describe('unified_root — 表示層の読み込み（MODES 走査）', () => {
  beforeEach(() => {
    vi.resetModules();
  });

  // --- R1: 層はモードごとの手書きではなく表から来る -----------------------------------
  test('one_layer_is_loaded_per_declaring_row_and_registered_under_its_mode_id', async () => {
    // Arrange
    const { loadDisplayLayers } = await loadRoot();
    const declaring = MODES.filter((m) => m.displayLayerPath);
    const { importModule } = makeImportSpy(MODES);

    // Act
    const layers = await loadDisplayLayers({ importModule, context: makeContext() });

    // Assert: 登録の鍵はモード id（controller の `layers` がそのまま引ける形）。
    expect([...layers.keys()]).toEqual(declaring.map((m) => m.id));
    // 宣言していない行（chartApi を持つ core）の層は作らない。
    for (const row of MODES.filter((m) => !m.displayLayerPath)) {
      expect(layers.has(row.id)).toBe(false);
    }
  });

  test('each_row_is_loaded_from_the_url_declared_in_the_table_not_from_a_root_local_constant', async () => {
    // Arrange
    const { loadDisplayLayers } = await loadRoot();
    const { importModule } = makeImportSpy(MODES);

    // Act
    await loadDisplayLayers({ importModule, context: makeContext() });

    // Assert: 読み込んだ URL の集合が、表が宣言した URL の集合と一致する
    //   （統合層が自前の定数を別に持っていれば、どちらかが余る／欠ける）。
    expect(importModule.mock.calls.map(([url]) => url))
      .toEqual(MODES.filter((m) => m.displayLayerPath).map((m) => m.displayLayerPath));
  });

  test('a_facade_that_does_not_publish_the_declared_export_fails_loudly', async () => {
    // Arrange: 公開面が名前を変えた（＝統合層が無言で 404 相当になる）状況。
    const { loadDisplayLayers } = await loadRoot();
    const importModule = vi.fn(async () => ({ somethingElse: () => {} }));

    // Act / Assert: 黙って層無しで起動しない（無音の失敗を作らない）。
    await expect(loadDisplayLayers({ importModule, context: makeContext() }))
      .rejects.toThrow(/setupSimDisplay/);
  });

  // --- 引数の合成（器の種別 ＋ モード固有の追加注入）----------------------------------
  test('each_layer_receives_the_host_of_its_declared_kind_and_the_shared_document', async () => {
    // Arrange
    const { loadDisplayLayers } = await loadRoot();
    const { importModule, setups } = makeImportSpy(MODES);
    const context = makeContext();

    // Act
    await loadDisplayLayers({ importModule, context });

    // Assert: 器を決めるのは統合層。core は渡された host へ挿すだけ（core は統合ページの id を
    //   知らない＝DIP）。どの器かは表の hostKind が引く。
    for (const row of MODES.filter((m) => m.displayLayerPath)) {
      const [args] = setups.get(row.id).mock.calls[0];
      expect(args.host).toBe(context.hosts[row.hostKind]);
      expect(args.doc).toBe(context.doc);
    }
    // 置き場所が 2 種類あること自体（下部ペインと専用の全面ホスト）を固定する。
    //   dashboard を sim と同じ下部ペインへ出す誤りは実際に起きた（ISSUE-460）。
    const hostKinds = MODES.filter((m) => m.displayLayerPath).map((m) => m.hostKind);
    expect(new Set(hostKinds).size).toBe(2);
  });

  test('the_sim_layer_receives_the_vendor_and_a_height_hook_that_defers_to_a_user_sized_pane', async () => {
    // Arrange: sim 固有の追加注入は統合層（ペインの所有者）が持つ。
    const { loadDisplayLayers } = await loadRoot();
    const { importModule, setups } = makeImportSpy(MODES);
    const context = makeContext();

    // Act
    await loadDisplayLayers({ importModule, context });
    const [args] = setups.get('sim').mock.calls[0];

    // Assert: vendor はそのまま渡る。
    expect(args.lwc).toBe(context.lwc);
    // 高さの決定権はペインの所有者（統合層）にある。sim は測って渡すだけ。
    args.onContentHeight(100);
    expect(context.bottomPane.setHeightPx).toHaveBeenCalledTimes(1);
    expect(context.bottomPane.setHeightPx.mock.calls[0][0]).toBeGreaterThan(100);
    // 利用者が一度でも分割線を掴んでいたら**触らない**（ビュー自動介入の禁止・ISSUE-164）。
    context.bottomPane.isUserSized.mockReturnValue(true);
    args.onContentHeight(200);
    expect(context.bottomPane.setHeightPx).toHaveBeenCalledTimes(1);
  });

  test('the_dashboard_layer_receives_the_live_scoped_templates_as_read_only', async () => {
    // Arrange
    const { loadDisplayLayers } = await loadRoot();
    const { importModule, setups } = makeImportSpy(MODES);
    const context = makeContext();

    // Act
    await loadDisplayLayers({ importModule, context });
    const [args] = setups.get('dashboard').mock.calls[0];

    // Assert: 読めるが書けない（第 4 モードの不具合が live の資産を壊す経路を作らない・T-2）。
    expect(args.templates.getItem('tpl')).toBe('stored');
    expect(context.liveStorage.getItem).toHaveBeenCalledWith('tpl');
    expect(() => args.templates.setItem('tpl', 'x')).toThrow(TypeError);
    expect(() => args.templates.removeItem('tpl')).toThrow(TypeError);
    expect(() => args.templates.clear()).toThrow(TypeError);
  });

  test('a_row_without_mode_specific_extras_is_set_up_with_the_shared_arguments_only', async () => {
    // Arrange: 追加注入を持たない第 5 モード（＝表 1 行だけで足りる場合）。
    const rows = [...MODES, FIFTH_ROW];
    const { loadDisplayLayers } = await loadRoot({ extraRows: [FIFTH_ROW] });
    const { importModule, setups } = makeImportSpy(rows);

    // Act
    await loadDisplayLayers({ importModule, context: makeContext() });
    const [args] = setups.get(FIFTH_ROW.id).mock.calls[0];

    // Assert: 共通引数だけが渡り、他モードの固有引数は混ざらない。
    expect(Object.keys(args).sort()).toEqual(['doc', 'host']);
    expect(args.host).toBe('FULL_AREA_HOST');
  });

  // --- R2: OCP の実証（表 1 行の追加だけで層が読まれ・登録され・遷移できる）-------------
  test('a_fifth_mode_added_as_one_table_row_is_loaded_registered_and_reachable_by_transition', async () => {
    // Arrange: production コードは 1 行も変えていない。差し替えたのは定義表だけ。
    const rows = [...MODES, FIFTH_ROW];
    const { loadDisplayLayers, createModeController } = await loadRoot({ extraRows: [FIFTH_ROW] });
    const { importModule } = makeImportSpy(rows);

    // Act: 層を組み、そのまま controller へ渡す（main() と同じ渡し方）。
    const layers = await loadDisplayLayers({ importModule, context: makeContext() });
    const calls = [];
    const mc = createModeController({
      controller: { clearRevealCache: () => calls.push('clearRevealCache') },
      layers,
      pollers: [{ start: () => calls.push('poller.start'), stop: () => calls.push('poller.stop') }],
      setSwMode: (mode) => { calls.push(`sw:${mode}`); return Promise.resolve(true); },
      applyMode: (mode) => calls.push(`ui:${mode}`),
      initialMode: 'live',
    });
    await mc.toggle(FIFTH_ROW.id);

    // Assert: 読み込まれ、登録され、遷移で enable される。
    expect(importModule).toHaveBeenCalledWith(FIFTH_ROW.displayLayerPath);
    expect(layers.has(FIFTH_ROW.id)).toBe(true);
    expect(mc.getMode()).toBe(FIFTH_ROW.id);
    expect(layers.get(FIFTH_ROW.id).enable).toHaveBeenCalledTimes(1);
    // 器を持つ層（chartApi なし）の遷移手順は sim / dashboard と同形（特別扱いが無い）。
    expect(calls).toEqual([
      'poller.stop', 'clearRevealCache', 'sw:live', `sw:${FIFTH_ROW.id}`, `ui:${FIFTH_ROW.id}`,
    ]);
  });

  test('leaving_the_fifth_mode_folds_its_layer_like_any_other', async () => {
    // Arrange
    const rows = [...MODES, FIFTH_ROW];
    const { loadDisplayLayers, createModeController } = await loadRoot({ extraRows: [FIFTH_ROW] });
    const { importModule } = makeImportSpy(rows);
    const layers = await loadDisplayLayers({ importModule, context: makeContext() });
    const mc = createModeController({
      controller: {}, layers, setSwMode: () => Promise.resolve(true), initialMode: 'live',
    });

    // Act
    await mc.toggle(FIFTH_ROW.id);
    await mc.toggle('live');

    // Assert: 器を統合ページへ残さない（第 5 モードにも同じ規律が効く）。
    expect(layers.get(FIFTH_ROW.id).disable).toHaveBeenCalledTimes(1);
    expect(mc.getMode()).toBe('live');
  });

  // --- 計算量（無駄の不在）------------------------------------------------------------
  // 固定するのは「発行した読込 − 使った層 = 0」であって、回数そのものではない。
  // 回数を期待値へ焼き込むと、浪費が仕様へ昇格する（ISSUE-450）。
  test('C1_boot_issues_no_module_load_beyond_the_layers_it_registers', async () => {
    // Arrange
    const { loadDisplayLayers } = await loadRoot();
    const { importModule } = makeImportSpy(MODES);

    // Act
    const layers = await loadDisplayLayers({ importModule, context: makeContext() });

    // Assert: 発行 − 登録 = 0（作って捨てる読込が 1 件も無い）。
    expect(importModule.mock.calls.length - layers.size).toBe(0);
    // 同じ URL を二度読まない（読み直しは同じ module を返すだけの浪費）。
    const urls = importModule.mock.calls.map(([u]) => u);
    expect(new Set(urls).size).toBe(urls.length);
  });

  test('C3_switching_modes_issues_no_module_load_at_all', async () => {
    // Arrange: 層は起動時の 1 回だけ作る。切替は enable/disable であって作り直しではない
    //   （単一 mount の要）。切替のたびに読み直す形は出力が正しいまま浪費だけが増えるので、
    //   状態検証では原理的に落ちない——発行回数でしか捕まえられない。
    const { loadDisplayLayers, createModeController } = await loadRoot();
    const { importModule } = makeImportSpy(MODES);
    const layers = await loadDisplayLayers({ importModule, context: makeContext() });
    const atBoot = importModule.mock.calls.length;
    const mc = createModeController({
      controller: {}, layers, setSwMode: () => Promise.resolve(true), initialMode: 'live',
    });

    // Act / Assert: 1 回の切替で 0 回。
    await mc.toggle('sim');
    expect(importModule.mock.calls.length - atBoot).toBe(0);

    // Act / Assert: 8 回の切替でも 0 回（入力を増やしても発行が増えない＝オーダーの表明）。
    const tour = ['dashboard', 'live', 'replay', 'sim', 'dashboard', 'live', 'sim', 'live'];
    for (const id of tour) {
      await mc.toggle(id);
    }
    expect(importModule.mock.calls.length - atBoot).toBe(0);
  });

  test('C2_declaring_one_more_row_issues_exactly_one_more_module_load', async () => {
    // Arrange: 入力（表の行数）を 2 点で変え、発行が**出力量だけで決まる**ことを表明する。
    const four = await loadRoot();
    const spy4 = makeImportSpy(MODES);
    const layers4 = await four.loadDisplayLayers({
      importModule: spy4.importModule, context: makeContext(),
    });

    const five = await loadRoot({ extraRows: [FIFTH_ROW] });
    const spy5 = makeImportSpy([...MODES, FIFTH_ROW]);
    const layers5 = await five.loadDisplayLayers({
      importModule: spy5.importModule, context: makeContext(),
    });

    // Assert: どちらの点でも「発行 − 登録 = 0」で、増分は宣言行 1 つぶんだけ。
    expect(spy4.importModule.mock.calls.length - layers4.size).toBe(0);
    expect(spy5.importModule.mock.calls.length - layers5.size).toBe(0);
    expect(spy5.importModule.mock.calls.length - spy4.importModule.mock.calls.length).toBe(1);
  });
});
