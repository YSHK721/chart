// color_theme_read_boundary.test.js — 永続層のテーマを「消費のための形」へ射影する読み出し境界
//   （基本設計_指標カラーテーマ.md §4.4・§4.9 前方互換・§5.1 処理 3・§5.3・§5.7 F-C3 / F-C9）。
//
// 病因 1（段階 3 のレビューで露呈）: `themes.v1` は外から書き換わりうる（旧版・手編集・他端末）。
//   gateway は `Array.isArray(obj.themes)` しか見ないため、`roleColors` に `'#ABC'` や `'red'` の
//   ような**未正規化値**が入ったまま消費者へ届く。すると消費者ごとの判定の細部（`== null` か
//   `isHex6` か）で結果が食い違い、水準線経路では既定色 `#2962ff` が捏造されて全水準線が青一色に
//   なった。判定を消費者ごとに足すのは同じ取り残しを増やすだけなので、**入口で形を揃える**。
//
// 病因 2（再レビューで露呈）: その「形を揃える」を**エンティティの書き換え**として実装したため、
//   未知トークン（§4.9 で温存が要求される領域）が読み込んだ時点で消え、改名しただけで永続層から
//   失われた（`{...t}` でトップレベルの未知キーは温存しているのに roleColors のキーだけ破壊され、
//   規則が非対称だった）。よって形を揃えるのは **消費のための射影**（読み取り専用の写像）に限り、
//   永続値は原形のまま保つ。解釈できない値の無視は消費側（resolver の isHex6 ガード）が担う。
//
// 構造: Arrange-Act-Assert（AAA）。

import { test } from 'node:test';
import assert from 'node:assert/strict';

import {
  adoptThemes, projectThemeForUse, unknownRoleTokens,
} from '../js/usecase/color_themes.js';
import { ColorThemeController, loadThemeState } from '../js/adapter/front/color_theme_controller.js';
import { LocalStorageThemeGateway } from '../js/adapter/front/local_storage_theme_gateway.js';
import { IndicatorController } from '../js/adapter/front/indicator_controller.js';
import { get } from '../js/usecase/catalog.js';
import { isNormalizedHex } from '../js/domain/color_value.js';

const THEMES_KEY = 'indicatorUi.themes.v1';
const ACTIVE_KEY = 'indicatorUi.activeTheme.v1';

function makeStorage(seed = {}) {
  const map = new Map(Object.entries(seed));
  return {
    getItem: (k) => (map.has(k) ? map.get(k) : null),
    setItem: (k, v) => map.set(k, v),
    removeItem: (k) => map.delete(k),
  };
}

// 外から書き込まれた（＝正規化されていない）テーマ集合。
const RAW_THEMES = [
  {
    themeId: 'thm#1',
    name: '手編集',
    roleColors: {
      level: '#ABC', // 3 桁・大文字（受理集合内・保存形ではない）
      primary: 'red', // 色として解釈できない（F-C9）
      surface: 'rgba(19, 23, 34, 0.5)', // 受理集合内・アルファ付き
      __unknown__: '#123456', // 語彙外（F-C3）
    },
    tfModifier: { '1D': 5, '99y': 0.5, '5m': 'x' },
    createdAt: 1,
    updatedAt: 1,
  },
];

// =========================================================================
// usecase: projectThemeForUse / adoptThemes / unknownRoleTokens（純関数）
// =========================================================================

test('TC-RB01 projectThemeForUse: roleColors の値をすべて保存形（小文字 hex6）へ射影する（§4.4）', () => {
  // Arrange / Act
  const { theme } = projectThemeForUse(RAW_THEMES[0]);
  // Assert
  assert.equal(theme.roleColors.level, '#aabbcc', '3 桁・大文字は保存形へ展開する');
  assert.equal(theme.roleColors.surface, '#131722', 'rgba() は hex6 へ（アルファは捨てる・§4.7）');
  for (const [token, value] of Object.entries(theme.roleColors)) {
    assert.ok(isNormalizedHex(value), `${token}: 正規化済み hex6 でない（${value}）`);
  }
});

test('TC-RB01b projectThemeForUse: 壊れた rgb() は「見た目 hex6」を作らず落ちる（§4.4 の不変条件）', () => {
  // Arrange: §4.9 が想定する脅威（旧版・手編集・他端末の themes.v1）に、数値として解釈できない
  //   チャネルを含む値を置く。クランプ（Math.min/max）は NaN を素通しするため、素朴に組み立てると
  //   `#NaN0405` のような「hex6 に見えない保存形」が出来てしまう（実測で発生した）。
  const raw = {
    themeId: 'thm#9',
    name: 'broken',
    roleColors: { bullish: 'rgb(1.2.3,4,5)', bearish: 'rgba(1.5.2, 3, 4)', primary: '#00ff00' },
    tfModifier: null,
    createdAt: 1,
    updatedAt: 1,
  };
  // Act
  const { theme } = projectThemeForUse(raw);
  // Assert: 壊れた値は落ち、残った値はすべて保存形。
  assert.equal(theme.roleColors.bullish, undefined, 'NaN チャネルの値を採用してはならない');
  assert.equal(theme.roleColors.bearish, undefined);
  assert.equal(theme.roleColors.primary, '#00ff00');
  for (const [token, value] of Object.entries(theme.roleColors)) {
    assert.ok(isNormalizedHex(value), `${token}: 正規化済み hex6 でない（${value}）`);
  }
});

test('TC-RB02 projectThemeForUse: 解釈できない値（F-C9）と語彙外キー（F-C3）は射影から落ちる', () => {
  // Arrange / Act
  const { theme, ignoredTokens } = projectThemeForUse(RAW_THEMES[0]);
  // Assert
  assert.equal('primary' in theme.roleColors, false, "F-C9: 'red' は未宣言として落とす");
  assert.equal('__unknown__' in theme.roleColors, false, 'F-C3: 語彙外キーは無視する');
  assert.deepEqual(ignoredTokens, ['__unknown__'], '無視した未知トークンは戻り値で報告する（R-4）');
});

test('TC-RB03 projectThemeForUse: tfModifier も §4.4 の値域・キー集合へ射影する', () => {
  // Arrange / Act
  const { theme } = projectThemeForUse(RAW_THEMES[0]);
  // Assert
  assert.deepEqual(theme.tfModifier, { '1D': 1 }, 'クランプ・台帳外キー除去・非数値除去');
});

test('TC-RB04 projectThemeForUse: 入力を破壊せず、他の属性は保つ（純関数）', () => {
  // Arrange
  const before = JSON.stringify(RAW_THEMES);
  // Act
  const { theme } = projectThemeForUse(RAW_THEMES[0]);
  // Assert
  assert.equal(JSON.stringify(RAW_THEMES), before, '入力を書き換えない（射影は読み取り専用）');
  assert.equal(theme.themeId, 'thm#1');
  assert.equal(theme.name, '手編集');
  assert.equal(theme.createdAt, 1);
});

test('TC-RB05 adoptThemes: テーマとして成立しない要素（null・非オブジェクト）だけを落とす', () => {
  // Arrange / Act
  const themes = adoptThemes([null, 'x', 3, { themeId: 'thm#9', roleColors: { __x__: '#123456' } }]);
  // Assert
  assert.deepEqual(themes.map((t) => t.themeId), ['thm#9']);
  assert.equal(themes[0].roleColors.__x__, '#123456', '成立する要素の中身は原形のまま（§4.9 温存）');
});

test('TC-RB06 projectThemeForUse: 恒等（既に正規化済みなら値が変わらない）', () => {
  // Arrange
  const src = {
    themeId: 'thm#1', name: 'n', roleColors: { level: '#123456' }, tfModifier: null, createdAt: 1, updatedAt: 2,
  };
  // Act
  const { theme, ignoredTokens } = projectThemeForUse(src);
  // Assert
  assert.deepEqual(theme, src);
  assert.deepEqual(ignoredTokens, []);
});

test('TC-RB06b projectThemeForUse: テーマとして成立しない入力は null（全域的）', () => {
  // Arrange / Act / Assert
  assert.deepEqual(projectThemeForUse(null), { theme: null, ignoredTokens: [] });
  assert.deepEqual(projectThemeForUse('x'), { theme: null, ignoredTokens: [] });
});

test('TC-RB06c unknownRoleTokens: 集合内の未知トークンを報告するだけで値は書き換えない（F-C3・R-4）', () => {
  // Arrange
  const before = JSON.stringify(RAW_THEMES);
  // Act
  const tokens = unknownRoleTokens(RAW_THEMES);
  // Assert
  assert.deepEqual(tokens, ['__unknown__']);
  assert.equal(JSON.stringify(RAW_THEMES), before);
});

// =========================================================================
// adapter: loadThemeState（読み出し境界）
// =========================================================================

test('TC-RB07 loadThemeState: 消費するテーマは射影済み・保持するテーマ集合は原形（§4.9）', () => {
  // Arrange
  const gateway = new LocalStorageThemeGateway(makeStorage({
    [THEMES_KEY]: JSON.stringify({ themes: RAW_THEMES }),
    [ACTIVE_KEY]: JSON.stringify({ themeId: 'thm#1', lastSeq: 1 }),
  }));
  // Act
  const state = loadThemeState(gateway);
  // Assert
  assert.equal(state.theme.roleColors.level, '#aabbcc', '消費側は正規化済み hex6 だけを見る');
  assert.equal('primary' in state.theme.roleColors, false);
  assert.equal(state.themes[0].roleColors.level, '#ABC', '永続値は原形のまま保持する');
  assert.equal(state.themes[0].roleColors.__unknown__, '#123456', '未知トークンを温存する（§4.9）');
});

test('TC-RB08 loadThemeState: 正規化しても選択中テーマ・採番の解決は従来どおり', () => {
  // Arrange
  const gateway = new LocalStorageThemeGateway(makeStorage({
    [THEMES_KEY]: JSON.stringify({ themes: RAW_THEMES }),
    [ACTIVE_KEY]: JSON.stringify({ themeId: 'thm#1', lastSeq: 0 }),
  }));
  // Act
  const state = loadThemeState(gateway);
  // Assert
  assert.equal(state.activeThemeId, 'thm#1');
  assert.equal(state.lastSeq, 1, '§4.10: lastSeq は既存 id の最大値以上へ引き上げる');
});

// =========================================================================
// §4.9 前方互換 / §5.3: 改名で roleColors は不変（未知トークンが永続層から消えない）
// =========================================================================

// 改名・削除だけを行う最小の host（協働子の契約 4 メンバー）。色の適用へは到達しない。
const themeHost = () => ({
  _state: { applied: [] },
  _meta: new Map(),
  _applyStoredStyles() {},
  _renderLegend() {},
});

test('TC-RB12 §4.9/§5.3: 未知トークンを含むテーマを読み込んで改名しても永続層に温存される', () => {
  // Arrange: 未知トークン（将来版・他端末が書いた領域）を含むテーマ 1 件。
  const storage = makeStorage({
    [THEMES_KEY]: JSON.stringify({
      themes: [{
        themeId: 'thm#1',
        name: '旧',
        roleColors: { bullish: '#00ff00', profit: '#123456' },
        tfModifier: null,
        createdAt: 1,
        updatedAt: 1,
      }],
    }),
    [ACTIVE_KEY]: JSON.stringify({ themeId: 'thm#1', lastSeq: 1 }),
  });
  const gateway = new LocalStorageThemeGateway(storage);
  const controller = new ColorThemeController(themeHost(), {
    gateway, state: loadThemeState(gateway), now: () => 2,
  });
  // Act
  controller.renameTheme('thm#1', '新');
  // Assert
  const persisted = JSON.parse(storage.getItem(THEMES_KEY)).themes[0];
  assert.equal(persisted.name, '新');
  assert.deepEqual(
    persisted.roleColors,
    { bullish: '#00ff00', profit: '#123456' },
    '改名で roleColors は不変（未知トークンを含めて 1 つも失わない）',
  );
});

test('TC-RB13 §4.9: 消費側（activeTheme）は未知トークンを無視し、語彙内の宣言だけを届ける', () => {
  // Arrange
  const storage = makeStorage({
    [THEMES_KEY]: JSON.stringify({
      themes: [{
        themeId: 'thm#1', name: 'n', roleColors: { bullish: '#00FF00', profit: '#123456' }, tfModifier: null,
      }],
    }),
    [ACTIVE_KEY]: JSON.stringify({ themeId: 'thm#1', lastSeq: 1 }),
  });
  const gateway = new LocalStorageThemeGateway(storage);
  const controller = new ColorThemeController(themeHost(), {
    gateway, state: loadThemeState(gateway), now: () => 2,
  });
  // Act
  const consumed = controller.activeTheme();
  // Assert
  assert.deepEqual(consumed.roleColors, { bullish: '#00ff00' }, '解釈できない領域は無視・値は保存形');
  assert.equal(controller.themes()[0].roleColors.profit, '#123456', '保持している値は原形のまま');
});

// =========================================================================
// 消費者（実利用点）: 水準線経路が既定色を捏造しないこと（F-C9・R-3）
// =========================================================================

const MA_METAS = [
  { name: 'MA', kind: 'line', color: '#1e88e5', baseColor: '#1e88e5', width: 1, style: 'solid', visible: true, heat: false },
];

function makeController(colorThemeProvider) {
  const noop = () => {};
  const levelCalls = [];
  const ctrl = new IndicatorController({
    catalog: { listIndicators: () => [], get },
    compute: { compute: async (req) => ({ ok: true, generation: req.generation ?? 0, series: [] }) },
    persistence: {
      loadApplied: () => [], saveApplied: noop, loadFavorites: () => [], saveFavorites: noop,
      loadUiState: () => ({}), saveUiState: noop, nextSeq: () => 1,
    },
    renderer: {
      renderLine: noop, renderHistogram: noop, renderHorizontal: noop,
      setData: noop, setVisible: noop, remove: noop,
      getSeriesStyles: () => MA_METAS.map((m) => ({ ...m })),
      applySeriesStyle: () => true,
      applyLevelLineColor: (instanceId, color) => { levelCalls.push(color); return true; },
    },
    document: null,
    mode: 'b',
    datasetRef: 'jp225_m1',
    timeframe: '1D',
    colorThemeProvider,
  });
  return { ctrl, levelCalls };
}

// 読み出し境界を通したテーマを供給する（本番と同じ経路: 永続層 → loadThemeState → 消費者）。
function themeFromStorage(roleColors) {
  const gateway = new LocalStorageThemeGateway(makeStorage({
    [THEMES_KEY]: JSON.stringify({
      themes: [{
        themeId: 'thm#1', name: 'n', roleColors, tfModifier: null, createdAt: 1, updatedAt: 1,
      }],
    }),
    [ACTIVE_KEY]: JSON.stringify({ themeId: 'thm#1', lastSeq: 1 }),
  }));
  return loadThemeState(gateway).theme;
}

test('TC-RB09 F-C9: level が色として解釈できない値でも、水準線は現行経路のまま（青一色にしない）', async () => {
  // Arrange
  const theme = themeFromStorage({ level: 'red' });
  const { ctrl, levelCalls } = makeController(() => theme);
  const inst = await ctrl.applyIndicator('moving_averages', 'default');
  levelCalls.length = 0;
  // Act
  ctrl._applyStoredStyles(inst.instanceId);
  // Assert
  assert.deepEqual(levelCalls, [null], '未宣言として扱い schemeColor / payload 色の現行経路へ戻す（R-3）');
});

test('TC-RB11 R-7: 水準線の色は「宣言の有無」を二重に判定しない（材料が無ければ色を書かない）', async () => {
  // Arrange: 読み出し境界を**通さない**生テーマ（境界が壊れた場合の多重防御）。
  //   宣言の判定を消費者側にもう 1 つ持つと、resolver の判定（isHex6）とずれた瞬間に
  //   既定色 #2962ff が捏造される。判定は resolver 1 つに委ね、材料が無ければ null を返させる。
  const raw = {
    themeId: 'thm#1', name: 'n', roleColors: { level: 'red' }, tfModifier: null,
  };
  const { ctrl, levelCalls } = makeController(() => raw);
  const inst = await ctrl.applyIndicator('moving_averages', 'default');
  levelCalls.length = 0;
  // Act
  ctrl._applyStoredStyles(inst.instanceId);
  // Assert
  assert.deepEqual(levelCalls, [null], '既定色を捏造しない（R-7）');
});

test('TC-RB10 F-C9: 受理集合内の未正規化値（#ABC）は保存形へ回復して水準線へ届く', async () => {
  // Arrange
  const theme = themeFromStorage({ level: '#ABC' });
  const { ctrl, levelCalls } = makeController(() => theme);
  const inst = await ctrl.applyIndicator('moving_averages', 'default');
  levelCalls.length = 0;
  // Act
  ctrl._applyStoredStyles(inst.instanceId);
  // Assert
  assert.deepEqual(levelCalls, ['#aabbcc']);
});
