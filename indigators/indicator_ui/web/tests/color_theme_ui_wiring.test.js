// テーマ UI（メニュー・ダイアログ）の「端から端まで」の結線。
//
// 設計入力（唯一の仕様源）: .doc/indicator-management-ui/基本設計_指標カラーテーマ.md v0.3.1
//   §7.1 配線（`installSharedUi` が menu の `install()` を呼ぶ／`wireControllerCollaborators` が
//        controller・gateway・menu・dialogs・applier を結ぶ）、
//   §6.2（行クリックで即適用・作成・管理）、§5.1（保存）、§5.3（改名・削除）、
//   §6.5（ライブ／リプレイで同一メニュー＝共有配線 1 箇所）。
//
// 受け口（ColorThemeMenu / ColorThemeDialogs）を作っただけでは、呼び手が居なければ無言で死ぬ
//   （ISSUE-291 と同型の事故）。本ファイルは「メニュー行のクリックが `colorThemes.applyTheme` まで
//   到達すること」を、**両 composition_root_front.js が実際に行う呼び出し面のまま**固定する。
// 構造: Arrange-Act-Assert（AAA）。
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

import { composeChartShell, installSharedUi, wireControllerCollaborators } from '../js/adapter/front/chart_app_wiring.js';
import { IndicatorController } from '../js/adapter/front/indicator_controller.js';
import { get } from '../js/usecase/catalog.js';

const THEMES_KEY = 'indicatorUi.themes.v1';
const ACTIVE_KEY = 'indicatorUi.activeTheme.v1';

const THEME_A = Object.freeze({
  themeId: 'thm#1',
  name: 'Ocean',
  roleColors: Object.freeze({ surface: '#0a0b0c', bullish: '#00ff00' }),
  tfModifier: null,
  createdAt: 1,
  updatedAt: 1,
});

// ---- DOM スタブ（メニュー・ダイアログを実際に生成できる最小実装）---------------
class El {
  constructor() {
    this.children = [];
    this.dataset = {};
    this.style = {};
    this.textContent = '';
    this.value = '';
    this.checked = false;
    this.type = '';
    this.id = '';
    this.title = '';
    this.min = '';
    this.max = '';
    this.step = '';
    this.parentNode = null;
    this._cls = new Set();
    this._handlers = {};
  }

  get className() { return [...this._cls].join(' '); }

  set className(v) { this._cls = new Set(String(v).split(/\s+/).filter(Boolean)); }

  get classList() {
    const s = this._cls;
    return {
      add: (c) => s.add(c),
      remove: (c) => s.delete(c),
      contains: (c) => s.has(c),
      toggle: (c, on) => {
        const next = on === undefined ? !s.has(c) : on;
        if (next) { s.add(c); } else { s.delete(c); }
      },
    };
  }

  get innerHTML() { return this._html ?? ''; }

  set innerHTML(v) {
    this._html = v;
    if (v === '') {
      for (const k of this.children) { k.parentNode = null; }
      this.children = [];
    }
  }

  append(...kids) {
    for (const k of kids) {
      if (k && typeof k === 'object') { k.parentNode = this; this.children.push(k); }
    }
  }

  appendChild(k) { this.append(k); return k; }

  insertBefore(k) { if (k) { k.parentNode = this; this.children.unshift(k); } return k; }

  removeChild(k) {
    this.children = this.children.filter((c) => c !== k);
    if (k) { k.parentNode = null; }
    return k;
  }

  closest(selector) {
    const keys = selector.split(',').map((s) => s.trim().replace(/^\[|\]$/g, ''));
    const dsKey = (attr) => attr.replace(/^data-/, '').replace(/-([a-z])/g, (_, c) => c.toUpperCase());
    let node = this;
    while (node) {
      if (keys.some((k) => node.dataset && node.dataset[dsKey(k)] !== undefined)) { return node; }
      node = node.parentNode;
    }
    return null;
  }

  setAttribute() {}

  removeAttribute() {}

  getAttribute() { return null; }

  remove() {}

  focus() {}

  querySelector() { return null; }

  querySelectorAll() { return []; }

  getBoundingClientRect() { return { top: 0, left: 0, width: 100, height: 100 }; }

  addEventListener(ev, fn) { (this._handlers[ev] ??= []).push(fn); }

  removeEventListener() {}

  fire(ev, arg = {}) { for (const fn of this._handlers[ev] ?? []) { fn(arg); } }
}

function makeEnv(seed = {}) {
  const series = {
    applyOptions() {}, setData() {}, priceScale: () => ({ applyOptions() {} }),
  };
  const chart = {
    applyOptions() {},
    addSeries: () => series,
    addPane: () => ({ addSeries: () => series, setStretchFactor() {}, setPreserveEmptyPane() {} }),
    panes: () => [],
    timeScale: () => ({
      height: () => 20, subscribeVisibleLogicalRangeChange() {}, scrollToPosition() {},
    }),
    subscribeCrosshairMove() {},
  };
  const lwc = {
    ColorType: { Solid: 'solid' },
    CrosshairMode: { Normal: 0 },
    CandlestickSeries: 'C',
    LineSeries: 'L',
    HistogramSeries: 'H',
    createChart: () => chart,
  };
  const byId = new Map();
  const anchors = new Map();
  const docHandlers = {};
  const doc = {
    documentElement: { style: { setProperty() {}, removeProperty() {} } },
    createElement: () => new El(),
    // メニューのマウントは実要素として返す（installChartToolbar の innerHTML 文字列とは独立に、
    //   各共有メニューが自分の器を引ける環境を作る）。器の在席そのものは
    //   color_theme_toolbar_mount.test.js が固定する。
    getElementById: (id) => {
      if (!byId.has(id)) { byId.set(id, new El()); }
      return byId.get(id);
    },
    querySelector: (sel) => {
      if (!anchors.has(sel)) { anchors.set(sel, new El()); }
      return anchors.get(sel);
    },
    querySelectorAll: () => [],
    addEventListener: (ev, fn) => { (docHandlers[ev] ??= []).push(fn); },
    removeEventListener: () => {},
    body: new El(),
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
    lwc, doc, storage, fetch, byId, chart, container: { clientHeight: 400 },
  };
}

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

// 両 composition_root_front.js が実際に行う呼び出し面を再現する（＝root は installSharedUi の
//   戻り値から menu / dialogs を取り出し、**明示的に** wire へ渡す。協働子は controller より後に
//   確定するため `getColorThemes` で遅延参照する）。テンプレート側（getTemplates /
//   chartTemplateMenu）と同一規約で、モジュール可変状態を介した暗黙の受け渡しは存在しない。
async function bootLikeRoot(env) {
  const shell = await composeChartShell({
    lwc: env.lwc,
    container: env.container,
    doc: env.doc,
    storage: env.storage,
    fetch: env.fetch,
    datasetRef: 'jp225_m1',
    recentBars: 300,
  });
  let chartTemplates = null;
  let colorThemes = null;
  const shared = installSharedUi({
    container: env.container,
    renderer: shell.renderer,
    doc: env.doc,
    getController: () => controller,
    updatePaneHeight: shell.updatePaneHeight,
    getTemplates: () => chartTemplates,
    getColorThemes: () => colorThemes,
    toolbar: { liveFollow: true, enterReplay: false },
  });
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
    chartTemplateMenu: shared.chartTemplateMenu,
    chartTemplateDialogs: shared.chartTemplateDialogs,
    colorThemeMenu: shared.colorThemeMenu,
    colorThemeDialogs: shared.colorThemeDialogs,
    themeStore: shell.themeStore,
    themeState: shell.themeState,
    chromeThemeApplier: shell.chromeThemeApplier,
    lwc: env.lwc,
    mainSeries: shell.mainSeries,
    chart: shell.chart,
    container: env.container,
    currentPriceView: shell.currentPriceView,
    now: () => 100,
  });
  chartTemplates = wired.chartTemplates;
  colorThemes = wired.colorThemes;
  return {
    shell, shared, controller, wired, mount: env.byId.get('color-theme-menu'),
  };
}

function flatten(el, out = []) {
  for (const kid of el.children ?? []) { out.push(kid); flatten(kid, out); }
  return out;
}

const rowsOf = (pop) => flatten(pop).filter((e) => e.dataset && e.dataset.themeId !== undefined);
const byData = (root, key, value) => flatten(root).find((e) => e.dataset && e.dataset[key] === value) ?? null;
const actionOf = (pop, name) => flatten(pop).find((e) => e.dataset && e.dataset.themeAction === name);

// ---------------------------------------------------------------------------
// install（§7.1 配線）
// ---------------------------------------------------------------------------

test('TC-CW01 installSharedUi がテーマメニューを組み立てて install する（§7.1）', async () => {
  // Arrange
  const env = makeEnv({ [THEMES_KEY]: JSON.stringify({ themes: [THEME_A] }) });
  // Act
  const { shared, mount } = await bootLikeRoot(env);
  // Assert
  assert.ok(shared.colorThemeMenu, 'installSharedUi の戻り値に colorThemeMenu が無い');
  assert.ok(shared.colorThemeDialogs, 'installSharedUi の戻り値に colorThemeDialogs が無い');
  assert.equal(mount.children.length, 2, '#color-theme-menu へトリガーとポップを生成する');
});

// ---------------------------------------------------------------------------
// 適用（UC-C02）— 行クリックが協働子まで到達する
// ---------------------------------------------------------------------------

test('TC-CW02 行クリックが colorThemes.applyTheme まで届く（menu → 協働子の明示結線）', async () => {
  // Arrange
  const env = makeEnv({ [THEMES_KEY]: JSON.stringify({ themes: [THEME_A] }) });
  const { wired, controller, mount } = await bootLikeRoot(env);
  const pop = mount.children[1];
  const row = rowsOf(pop).find((r) => r.dataset.themeId === 'thm#1');
  assert.ok(row, '前提: 保存済みテーマの行が生成されている');
  // Act
  pop.fire('click', { target: row });
  // Assert
  assert.equal(wired.colorThemes.activeThemeId(), 'thm#1', '協働子の選択中テーマが切り替わる');
  assert.equal(controller._activeColorTheme().themeId, 'thm#1', 'controller まで届く（端から端まで）');
});

test('TC-CW03 固定行「テーマなし」のクリックは applyTheme(null)＝既定色へ戻す（§6.2）', async () => {
  // Arrange
  const env = makeEnv({
    [THEMES_KEY]: JSON.stringify({ themes: [THEME_A] }),
    [ACTIVE_KEY]: JSON.stringify({ themeId: 'thm#1', lastSeq: 1 }),
  });
  const { wired, controller, mount } = await bootLikeRoot(env);
  const pop = mount.children[1];
  assert.equal(controller._activeColorTheme().themeId, 'thm#1', '前提: テーマ適用中');
  // Act
  pop.fire('click', { target: rowsOf(pop).find((r) => r.dataset.themeId === '') });
  // Assert
  assert.equal(wired.colorThemes.activeThemeId(), null);
  assert.equal(controller._activeColorTheme(), null, 'テーマなしへ戻る');
});

test('TC-CW04 適用後にメニュー行を再構築して選択状態を更新する', async () => {
  // Arrange
  const env = makeEnv({ [THEMES_KEY]: JSON.stringify({ themes: [THEME_A] }) });
  const { mount } = await bootLikeRoot(env);
  const pop = mount.children[1];
  const activeIds = () => rowsOf(pop).filter((r) => r.classList.contains('is-active')).map((r) => r.dataset.themeId);
  assert.deepEqual(activeIds(), [''], '前提: 起動時はテーマ未選択＝固定行が選択状態');
  // Act
  pop.fire('click', { target: rowsOf(pop).find((r) => r.dataset.themeId === 'thm#1') });
  // Assert
  assert.deepEqual(activeIds(), ['thm#1'], '適用した行が選択状態になる（再構築されている）');
});

// ---------------------------------------------------------------------------
// 作成（UC-C01）・管理（UC-C03）
// ---------------------------------------------------------------------------

test('TC-CW05 「新しいテーマを作成…」→ 編集ダイアログの保存が colorThemes.saveTheme まで届く（§5.1）', async () => {
  // Arrange
  const env = makeEnv();
  const { wired, mount } = await bootLikeRoot(env);
  const pop = mount.children[1];
  // Act
  pop.fire('click', { target: actionOf(pop, 'create') });
  const dialog = env.doc.body.children[0];
  assert.ok(dialog, '編集ダイアログが開く');
  byData(dialog, 'themeField', 'name').value = '新テーマ';
  const swatch = byData(dialog, 'themeSwatch', 'bullish');
  swatch.value = '#00ff00';
  swatch.fire('input');
  byData(dialog, 'themeAction', 'submit').fire('click');
  // Assert
  const saved = wired.colorThemes.themes();
  assert.equal(saved.length, 1, 'テーマが 1 件保存される');
  assert.equal(saved[0].name, '新テーマ');
  assert.deepEqual(saved[0].roleColors, { bullish: '#00ff00' }, '宣言したトークンだけが保存される');
  assert.equal(env.doc.body.children.length, 0, '成功時はダイアログを閉じる');
});

test('TC-CW06 保存は適用ではない（保存しても選択中テーマは変わらない・§5.1 後条件）', async () => {
  // Arrange
  const env = makeEnv();
  const { wired, controller, mount } = await bootLikeRoot(env);
  const pop = mount.children[1];
  // Act
  pop.fire('click', { target: actionOf(pop, 'create') });
  const dialog = env.doc.body.children[0];
  byData(dialog, 'themeField', 'name').value = '新テーマ';
  byData(dialog, 'themeAction', 'submit').fire('click');
  // Assert
  assert.equal(wired.colorThemes.activeThemeId(), null, '保存はチャートの色を変えない');
  assert.equal(controller._activeColorTheme(), null);
});

test('TC-CW07 保存後にメニューへ新しいテーマ行が現れる（一覧が追随する）', async () => {
  // Arrange
  const env = makeEnv();
  const { mount, doc } = { ...await bootLikeRoot(env), doc: env.doc };
  const pop = mount.children[1];
  // Act
  pop.fire('click', { target: actionOf(pop, 'create') });
  const dialog = doc.body.children[0];
  byData(dialog, 'themeField', 'name').value = '新テーマ';
  byData(dialog, 'themeAction', 'submit').fire('click');
  // Assert
  const names = rowsOf(pop).map((r) => r.dataset.themeId);
  assert.equal(names.length, 2, `固定行 ＋ 新テーマの 2 行（実際: ${names.join(', ')}）`);
});

test('TC-CW08 保存: 検証失敗（空名）は CODE がダイアログへ返りテーマは増えない（F-C1）', async () => {
  // Arrange
  const env = makeEnv();
  const { wired, mount } = await bootLikeRoot(env);
  const pop = mount.children[1];
  // Act
  pop.fire('click', { target: actionOf(pop, 'create') });
  const dialog = env.doc.body.children[0];
  byData(dialog, 'themeAction', 'submit').fire('click');
  // Assert
  assert.deepEqual(wired.colorThemes.themes(), [], '既存データは不変');
  assert.equal(env.doc.body.children.length, 1, '閉じない');
  assert.ok(byData(dialog, 'themeError', 'edit').textContent.length > 0, 'インライン表示する');
});

test('TC-CW09 「管理…」→ 改名が colorThemes.renameTheme まで届く（§5.3）', async () => {
  // Arrange
  const env = makeEnv({ [THEMES_KEY]: JSON.stringify({ themes: [THEME_A] }) });
  const { wired, mount } = await bootLikeRoot(env);
  const pop = mount.children[1];
  // Act
  pop.fire('click', { target: actionOf(pop, 'manage') });
  const dialog = env.doc.body.children[0];
  byData(dialog, 'themeRename', 'thm#1').fire('click');
  byData(dialog, 'themeRenameInput', 'thm#1').value = 'Lagoon';
  byData(dialog, 'themeRenameCommit', 'thm#1').fire('click');
  // Assert
  assert.equal(wired.colorThemes.themes()[0].name, 'Lagoon');
});

test('TC-CW10 「管理…」→ 削除が colorThemes.deleteTheme まで届く（確認 1 段の後・§5.3）', async () => {
  // Arrange
  const env = makeEnv({ [THEMES_KEY]: JSON.stringify({ themes: [THEME_A] }) });
  const { wired, mount } = await bootLikeRoot(env);
  const pop = mount.children[1];
  // Act
  pop.fire('click', { target: actionOf(pop, 'manage') });
  const dialog = env.doc.body.children[0];
  byData(dialog, 'themeDelete', 'thm#1').fire('click');
  assert.deepEqual(wired.colorThemes.themes(), [THEME_A], '確認前は削除しない');
  byData(dialog, 'themeDeleteConfirm', 'thm#1').fire('click');
  // Assert
  assert.deepEqual(wired.colorThemes.themes(), [], '確認後に削除される');
});

test('TC-CW11 管理ダイアログの一覧は保存済みテーマのみ（固定行は管理対象外・§6.2）', async () => {
  // Arrange
  const env = makeEnv({ [THEMES_KEY]: JSON.stringify({ themes: [THEME_A] }) });
  const { mount } = await bootLikeRoot(env);
  const pop = mount.children[1];
  // Act
  pop.fire('click', { target: actionOf(pop, 'manage') });
  const dialog = env.doc.body.children[0];
  // Assert
  const rows = flatten(dialog).filter((e) => e.dataset && e.dataset.themeRow !== undefined);
  assert.deepEqual(rows.map((r) => r.dataset.themeRow), ['thm#1']);
});

test('TC-CW12 同名保存の確認 1 段は usecase の判定器で行う（ダイアログが文字列比較を持たない）', async () => {
  // Arrange
  const env = makeEnv({ [THEMES_KEY]: JSON.stringify({ themes: [THEME_A] }) });
  const { wired, mount } = await bootLikeRoot(env);
  const pop = mount.children[1];
  // Act
  pop.fire('click', { target: actionOf(pop, 'create') });
  const dialog = env.doc.body.children[0];
  byData(dialog, 'themeField', 'name').value = ' ocean ';   // 正規化名が既存と一致（trim＋小文字化）。
  const submit = byData(dialog, 'themeAction', 'submit');
  submit.fire('click');
  // Assert
  assert.equal(wired.colorThemes.themes().length, 1, '1 回目では保存しない（確認 1 段）');
  assert.equal(wired.colorThemes.themes()[0].name, 'Ocean', '名前も未変更');
  // Act: 確認
  submit.fire('click');
  // Assert
  assert.equal(wired.colorThemes.themes().length, 1, '上書きなので件数は増えない');
  assert.equal(wired.colorThemes.themes()[0].name, 'ocean', '上書き後の表記は入力のまま（§5.1 処理 2）');
});

// ---------------------------------------------------------------------------
// 複製の禁止（共有配線 1 箇所で完結し、両 root は無改変）
// ---------------------------------------------------------------------------

// installSharedUi → wireControllerCollaborators の受け渡しは**明示引数**である（モジュール可変
//   状態を介した暗黙のチャネルは持たない）。したがって結線の前提は「両 root が menu / dialogs を
//   wire へ転送し、協働子の遅延参照を install へ渡すこと」に尽きる。転送を落とすとメニューは
//   表示されるのに押しても何も起きない（無言の死・ISSUE-291 と同型）。実行時に例外で落とすことは
//   できない（menu 無しで wire を呼ぶ既存テストが正当に存在する）ため、前提を静的に固定する。
test('TC-CW14 前提: 両 root が menu / dialogs を明示的に転送する（暗黙チャネルを持たない）', () => {
  // Arrange
  const read = (rel) => readFileSync(fileURLToPath(new URL(rel, import.meta.url)), 'utf8');
  const roots = {
    live: read('../js/adapter/front/composition_root_front.js'),
    replay: read('../../../../simulator/replay_ui/web/js/adapter/front/composition_root_front.js'),
  };
  const shared = read('../js/adapter/front/chart_app_wiring.js');
  // 呼び出しの引数テキストを括弧対応で切り出す。
  const argsOf = (src, fname) => {
    const start = src.indexOf(`${fname}({`);
    assert.ok(start >= 0, `${fname} の呼び出しが見つからない`);
    let depth = 0;
    for (let i = src.indexOf('{', start); i < src.length; i += 1) {
      if (src[i] === '{') { depth += 1; }
      if (src[i] === '}') { depth -= 1; if (depth === 0) { return src.slice(start, i + 1); } }
    }
    return '';
  };
  const required = {
    installSharedUi: ['doc', 'getColorThemes'],
    wireControllerCollaborators: ['doc', 'colorThemeMenu', 'colorThemeDialogs'],
  };
  // Act / Assert
  for (const [name, src] of Object.entries(roots)) {
    for (const [fname, names] of Object.entries(required)) {
      const args = argsOf(src, fname);
      for (const ident of names) {
        assert.ok(
          new RegExp(`(^|[\\s,{])${ident}\\s*[,:}]`).test(args),
          `${name} root の ${fname} が ${ident} を渡していない（テーマ UI が無言で死ぬ）`,
        );
      }
    }
  }
  // 暗黙チャネル（モジュールスコープの可変状態）で受け渡していないこと。
  assert.ok(!/WeakMap|_THEME_UI/.test(shared), '共有配線がモジュール状態経由で UI を受け渡している');
});

test('TC-CW13 複製禁止: menu / dialogs の組み立ては共有配線 1 箇所だけ・両 root は無改変', () => {
  // Arrange
  const read = (rel) => readFileSync(fileURLToPath(new URL(rel, import.meta.url)), 'utf8');
  const shared = read('../js/adapter/front/chart_app_wiring.js');
  const roots = [
    read('../js/adapter/front/composition_root_front.js'),
    read('../../../../simulator/replay_ui/web/js/adapter/front/composition_root_front.js'),
  ];
  // Act / Assert
  assert.equal([...shared.matchAll(/new ColorThemeMenu\(/g)].length, 1, '組み立ては 1 箇所');
  assert.equal([...shared.matchAll(/new ColorThemeDialogs\(/g)].length, 1, '組み立ては 1 箇所');
  for (const src of roots) {
    assert.ok(!src.includes('ColorThemeMenu'), 'root がテーマメニューを自前で組み立てている');
    assert.ok(!src.includes('ColorThemeDialogs'), 'root がテーマダイアログを自前で組み立てている');
    assert.ok(!src.includes('color-theme-menu'), 'root が器の id を知っている（View の所有を壊している）');
  }
});
