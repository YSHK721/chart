// color_theme_wiring.test.js — テーマ協働子の「端から端まで」の結線（§5.5 起動時の復元・§7.4 段階 3）。
//
// 受け口（ColorThemeController / IndicatorController._activeColorTheme）を作っただけでは、
//   呼び手が居なければ無言で死ぬ（ISSUE-291 と同型の事故）。よって共有配線の単一ソース
//   （chart_app_wiring.js）が
//     (a) 起動時に保存済みテーマを解決して**クロムを 1 回だけ**配る（二重配信・ちらつきを作らない）
//     (b) 協働子を生成して controller.setColorThemeProvider へ **1 箇所で**結ぶ
//   ことを固定する。両 composition_root_front.js は無改変（＝同一 1 行の手書き複製を作らない）。
// 構造: Arrange-Act-Assert（AAA）。

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

import { composeChartShell, wireControllerCollaborators } from '../js/adapter/front/chart_app_wiring.js';
import { IndicatorController } from '../js/adapter/front/indicator_controller.js';
import { ColorThemeController, COLOR_THEME_HOST_CONTRACT } from '../js/adapter/front/color_theme_controller.js';
import { get } from '../js/usecase/catalog.js';
import { CHROME_CURRENT } from '../js/usecase/chrome_tokens.js';
import { PRESET_THEMES } from '../js/usecase/color_themes.js';

const THEMES_KEY = 'indicatorUi.themes.v1';
const ACTIVE_KEY = 'indicatorUi.activeTheme.v1';

// 同梱プリセット（§9 T-1）は**参照面で合成**される。永続層（themes.v1）＝保存された原形のみと、
//   一覧（colorThemes.themes()）＝合成後の集合は別物なので、以下では別々に表明する。
const PRESET = PRESET_THEMES[0];
const storedThemes = (env) => JSON.parse(env.storage._map.get(THEMES_KEY) ?? '{"themes":[]}').themes;

const THEME_A = Object.freeze({
  themeId: 'thm#1',
  name: 'Ocean',
  roleColors: Object.freeze({ surface: '#0a0b0c', bullish: '#00ff00', text: '#fafafa' }),
  tfModifier: null,
  createdAt: 1,
  updatedAt: 1,
});

// 最小の DOM / lwc スタブ（chrome_theme_wiring.test.js と同型）。
function makeEnv(seed = {}) {
  const chartOptionCalls = [];
  const seriesOptionCalls = [];
  const props = new Map();
  const series = {
    applyOptions: (o) => seriesOptionCalls.push(o),
    setData() {}, priceScale: () => ({ applyOptions() {} }),
  };
  const chart = {
    applyOptions: (o) => chartOptionCalls.push(o),
    addSeries: () => series,
    addPane: () => ({ addSeries: () => series, setStretchFactor() {}, setPreserveEmptyPane() {} }),
    panes: () => [],
    timeScale: () => ({
      height: () => 20,
      subscribeVisibleLogicalRangeChange() {},
      scrollToPosition() {},
    }),
    subscribeCrosshairMove() {},
  };
  const lwc = {
    ColorType: { Solid: 'solid' },
    CrosshairMode: { Normal: 0 },
    CandlestickSeries: 'C', LineSeries: 'L', HistogramSeries: 'H',
    createChart: () => chart,
  };
  const style = {
    setProperty: (k, v) => props.set(k, v),
    removeProperty: (k) => props.delete(k),
  };
  const el = () => {
    const node = {
      style: {}, dataset: {}, children: [], textContent: '', innerHTML: '',
      classList: { add() {}, remove() {}, toggle() {}, contains: () => false },
      appendChild(c) { node.children.push(c); return c; },
      insertBefore(c) { node.children.push(c); return c; },
      addEventListener() {}, removeEventListener() {},
      setAttribute() {}, removeAttribute() {}, getAttribute: () => null,
      remove() {}, focus() {}, closest: () => null,
      querySelector: () => null, querySelectorAll: () => [],
      getBoundingClientRect: () => ({ top: 0, left: 0, width: 100, height: 100 }),
    };
    return node;
  };
  const anchors = new Map();
  const doc = {
    documentElement: { style },
    createElement: () => el(),
    getElementById: () => null,
    querySelector: (sel) => {
      if (!anchors.has(sel)) {
        anchors.set(sel, el());
      }
      return anchors.get(sel);
    },
    querySelectorAll: () => [],
    addEventListener() {},
    body: el(),
  };
  const map = new Map(Object.entries(seed));
  const storage = {
    getItem: (k) => (map.has(k) ? map.get(k) : null),
    setItem: (k, v) => map.set(k, v),
    removeItem: (k) => map.delete(k),
    _map: map,
  };
  const fetch = async () => ({ ok: true, status: 200, json: async () => ({}) });
  return {
    lwc, doc, storage, fetch, chartOptionCalls, seriesOptionCalls, props,
    container: { clientHeight: 400 }, chart,
  };
}

const shellOf = (env) => composeChartShell({
  lwc: env.lwc, container: env.container, doc: env.doc, storage: env.storage,
  fetch: env.fetch, datasetRef: 'jp225_m1', recentBars: 300,
});

function makeIndicatorController(shell) {
  const noop = () => {};
  return new IndicatorController({
    catalog: { listIndicators: () => [], get },
    compute: { compute: async () => ({ ok: true, generation: 0, series: [] }) },
    persistence: {
      loadApplied: () => [], saveApplied: noop, loadFavorites: () => [], saveFavorites: noop,
      loadUiState: () => ({}), saveUiState: noop, nextSeq: () => 1,
    },
    renderer: shell.renderer,
    document: null,
    datasetRef: 'jp225_m1',
    timeframe: '1D',
  });
}

// ---- setColorThemeProvider（IndicatorController への 1 メソッド加法）----------

test('setColorThemeProvider: 結線後に _activeColorTheme() が選択中テーマを返す', () => {
  // Arrange
  const env = makeEnv();
  const noop = () => {};
  const ctrl = new IndicatorController({
    catalog: { listIndicators: () => [], get },
    compute: { compute: async () => ({ ok: true, generation: 0, series: [] }) },
    persistence: {
      loadApplied: () => [], saveApplied: noop, loadFavorites: () => [], saveFavorites: noop,
      loadUiState: () => ({}), saveUiState: noop, nextSeq: () => 1,
    },
    renderer: { renderLine: noop, setData: noop, setVisible: noop, remove: noop },
    document: null,
  });
  assert.equal(ctrl._activeColorTheme(), null, '前提: 未結線ではテーマなし');
  // Act
  ctrl.setColorThemeProvider(() => THEME_A);
  // Assert
  assert.equal(ctrl._activeColorTheme(), THEME_A);
  assert.equal(env.chartOptionCalls.length, 0, 'setter がチャートへ副作用を持っている');
});

test('setColorThemeProvider: constructor 引数 colorThemeProvider は従来どおり効く（加法性）', () => {
  // Arrange
  const noop = () => {};
  const ctrl = new IndicatorController({
    catalog: { listIndicators: () => [], get },
    compute: { compute: async () => ({ ok: true, generation: 0, series: [] }) },
    persistence: {
      loadApplied: () => [], saveApplied: noop, loadFavorites: () => [], saveFavorites: noop,
      loadUiState: () => ({}), saveUiState: noop, nextSeq: () => 1,
    },
    renderer: { renderLine: noop, setData: noop, setVisible: noop, remove: noop },
    document: null,
    colorThemeProvider: () => THEME_A,
  });
  // Act / Assert
  assert.equal(ctrl._activeColorTheme(), THEME_A);
  ctrl.setColorThemeProvider(null);
  assert.equal(ctrl._activeColorTheme(), null, '非関数の再設定でテーマなしへ戻せない');
});

// ---- composeChartShell: 起動時の復元とクロム 1 回配信（§5.5）------------------

test('起動: composeChartShell が themeStore と解決済みテーマ状態を返す', async () => {
  // Arrange
  const env = makeEnv({ [THEMES_KEY]: JSON.stringify({ themes: [THEME_A] }) });
  // Act
  const shell = await shellOf(env);
  // Assert
  assert.ok(shell.themeStore, 'themeStore が返っていない');
  // ThemeStorePort は 6 メンバーちょうど。協働子は在席ガード（typeof … === 'function'）を持たず
  //   全メンバーを呼ぶため、1 つでも欠けた store を配線すると削除記録が無言で消える（＝削除した
  //   同梱プリセットが復活する）。配線点で欠落を落とす。
  assert.deepEqual(
    [
      'loadThemes', 'saveThemes', 'loadActiveTheme', 'saveActiveTheme',
      'loadRemovedPresetIds', 'saveRemovedPresetIds',
    ].filter((m) => typeof shell.themeStore[m] !== 'function'),
    [],
    'ThemeStorePort の一部が欠けた store が配線されている',
  );
  assert.deepEqual(shell.themeState.themes, [THEME_A]);
  assert.equal(shell.themeState.activeThemeId, null);
  assert.equal(shell.themeState.theme, null);
});

test('起動: 保存済みテーマがあれば、その色で 1 回だけクロムを配る（二重配信なし）', async () => {
  // Arrange
  const env = makeEnv({
    [THEMES_KEY]: JSON.stringify({ themes: [THEME_A] }),
    [ACTIVE_KEY]: JSON.stringify({ themeId: 'thm#1', lastSeq: 1 }),
  });
  // Act
  const shell = await shellOf(env);
  // Assert
  assert.equal(env.chartOptionCalls.length, 1, `起動時のクロム配信が 1 回でない: ${env.chartOptionCalls.length}`);
  assert.equal(env.seriesOptionCalls.length, 1, '起動時のローソク配信が 1 回でない');
  assert.equal(env.chartOptionCalls[0].layout.background.color, '#0a0b0c');
  assert.equal(env.chartOptionCalls[0].layout.textColor, '#fafafa');
  assert.equal(env.seriesOptionCalls[0].upColor, '#00ff00');
  assert.equal(env.props.get('--ct-surface'), '#0a0b0c');
  assert.equal(shell.themeState.theme.themeId, 'thm#1');
});

test('起動: テーマ未選択なら現行既定のまま 1 回だけ配る（既定状態の見た目は不変）', async () => {
  // Arrange
  const env = makeEnv();
  // Act
  await shellOf(env);
  // Assert
  assert.equal(env.chartOptionCalls.length, 1);
  assert.equal(env.chartOptionCalls[0].layout.background.color, CHROME_CURRENT.layoutBackground);
  assert.equal(env.seriesOptionCalls[0].upColor, CHROME_CURRENT.candleUp);
});

test('起動 F-C6: dangling activeThemeId は null へ縮退して永続化され、既定色で 1 回配る', async () => {
  // Arrange
  const warned = [];
  const original = console.warn;
  console.warn = (m) => warned.push(String(m));
  try {
    const env = makeEnv({
      [THEMES_KEY]: JSON.stringify({ themes: [THEME_A] }),
      [ACTIVE_KEY]: JSON.stringify({ themeId: 'thm#404', lastSeq: 3 }),
    });
    // Act
    const shell = await shellOf(env);
    // Assert
    assert.equal(shell.themeState.activeThemeId, null);
    assert.deepEqual(JSON.parse(env.storage._map.get(ACTIVE_KEY)), { themeId: null, lastSeq: 3 });
    assert.equal(env.chartOptionCalls.length, 1);
    assert.equal(env.chartOptionCalls[0].layout.background.color, CHROME_CURRENT.layoutBackground);
    assert.ok(warned.length >= 1, 'F-C6 の警告が出ていない');
  } finally {
    console.warn = original;
  }
});

// ---- wireControllerCollaborators: 協働子の生成と provider 結線 ---------------

async function wireAll(env) {
  const shell = await shellOf(env);
  const controller = makeIndicatorController(shell);
  const wired = wireControllerCollaborators({
    controller,
    renderer: shell.renderer,
    doc: env.doc,
    fetch: env.fetch,
    datasetRef: 'jp225_m1',
    timeframe: '1D',
    recentBars: 300,
    templateStore: shell.templateStore,
    themeStore: shell.themeStore,
    themeState: shell.themeState,
    chromeThemeApplier: shell.chromeThemeApplier,
    chartTemplateMenu: null,
    chartTemplateDialogs: null,
    lwc: env.lwc,
    mainSeries: shell.mainSeries,
    chart: shell.chart,
    container: env.container,
    currentPriceView: shell.currentPriceView,
  });
  return { shell, controller, wired };
}

test('結線: wireControllerCollaborators が colorThemes を組み立てて返す', async () => {
  // Arrange
  const env = makeEnv({ [THEMES_KEY]: JSON.stringify({ themes: [THEME_A] }) });
  // Act
  const { wired } = await wireAll(env);
  // Assert
  assert.ok(wired.colorThemes instanceof ColorThemeController, 'colorThemes が返っていない');
  // 一覧: 同梱プリセット（先頭）＋ 永続層のテーマ。
  assert.deepEqual(wired.colorThemes.themes().map((t) => t.themeId), [PRESET.themeId, 'thm#1']);
  assert.deepEqual(
    wired.colorThemes.themes().find((t) => t.themeId === 'thm#1'), THEME_A,
    '永続層のテーマは原形のまま一覧へ載る',
  );
  // 永続層: 起動・結線だけではプリセットが書き込まれない（合成であって初期値の書き込みではない）。
  assert.deepEqual(storedThemes(env), [THEME_A]);
});

test('結線: controller._activeColorTheme() が協働子の選択中テーマを返す（端から端まで）', async () => {
  // Arrange
  const env = makeEnv({
    [THEMES_KEY]: JSON.stringify({ themes: [THEME_A] }),
    [ACTIVE_KEY]: JSON.stringify({ themeId: 'thm#1', lastSeq: 1 }),
  });
  // Act
  const { controller, wired } = await wireAll(env);
  // Assert
  assert.equal(controller._activeColorTheme().themeId, 'thm#1');
  // 適用を切り替えたら provider の戻り値も追随する（値を焼き付けていない）。
  wired.colorThemes.applyTheme(null);
  assert.equal(controller._activeColorTheme(), null);
});

test('結線: 協働子には host 全体ではなく ThemeHost 射影が渡る（契約外は実行時に落ちる）', async () => {
  // Arrange
  const env = makeEnv({ [THEMES_KEY]: JSON.stringify({ themes: [THEME_A] }) });
  // Act
  const { wired } = await wireAll(env);
  const host = wired.colorThemes._host;
  // Assert
  assert.doesNotThrow(() => host._state);
  assert.doesNotThrow(() => host._meta);
  assert.throws(() => host._renderer, /契約外の host メンバー/);
  assert.throws(() => host._persistAll, /契約外の host メンバー/);
  assert.equal(COLOR_THEME_HOST_CONTRACT.methods.length + COLOR_THEME_HOST_CONTRACT.fields.length, 4);
});

// ---- 複製の禁止（共有配線 1 箇所で完結する）---------------------------------

test('複製禁止: setColorThemeProvider の結線は共有配線 1 箇所だけ（両 root は無改変）', () => {
  // Arrange
  const read = (rel) => readFileSync(fileURLToPath(new URL(rel, import.meta.url)), 'utf8');
  const shared = read('../js/adapter/front/chart_app_wiring.js');
  const roots = [
    read('../js/adapter/front/composition_root_front.js'),
    read('../../../../simulator/replay_ui/web/js/adapter/front/composition_root_front.js'),
  ];
  // Act
  const wiredInShared = [...shared.matchAll(/setColorThemeProvider\(/g)].length;
  // Assert
  assert.equal(wiredInShared, 1, `共有配線の結線が 1 箇所でない: ${wiredInShared}`);
  assert.equal([...shared.matchAll(/new ColorThemeController\(/g)].length, 1);
  assert.equal([...shared.matchAll(/new LocalStorageThemeGateway\(/g)].length, 1);
  for (const src of roots) {
    assert.ok(!src.includes('setColorThemeProvider'), 'root がテーマ結線を手書きしている');
    assert.ok(!src.includes('ColorThemeController'), 'root が協働子を自前で組み立てている');
    assert.ok(!src.includes('LocalStorageThemeGateway'), 'root が gateway を自前で組み立てている');
  }
});
