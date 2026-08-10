// menu_exclusive_close.test.js — チャートペイン上部のドロップダウン群の排他クローズ（ISSUE-366）。
//
// 設計入力（唯一の仕様源）: ユーザー指示（2026-08-10）
//   「テンプレートをクリック後にテーマをクリックすると、テンプレートの設定画面が消えない。
//     その他 UI を選択したときは、消える仕様にしろ。逆も同じ動きになっている。」
//   ＝ ツールバーのどのドロップダウンを開いても、他のドロップダウンは閉じる。
//
// 旧実装の欠陥（本テストが固定する回帰）: 各メニューが document へ「自分を閉じる」リスナを張り、
//   トリガーは `stopPropagation()` でそれを回避していた。この stopPropagation は**他メニューの
//   close リスナも同時に止める**ため、片方が開きっぱなしになった。
//
// 構造: Arrange-Act-Assert（AAA）。jsdom を避けた最小 DOM スタブ（他メニューテストと同作法）だが、
//   本テストの判定は「クリック位置がどの root の内側か」なので、スタブは `contains` を実装する。

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { ChartTemplateMenu } from '../js/adapter/front/chart_template_menu.js';
import { ColorThemeMenu } from '../js/adapter/front/color_theme_menu.js';
import { TimeframeMenu } from '../js/adapter/front/timeframe_menu.js';

// ---- 最小 DOM スタブ（`contains` 付き）--------------------------------------
class El {
  constructor() {
    this.children = [];
    this.dataset = {};
    this.textContent = '';
    this.type = '';
    this.id = '';
    this.title = '';
    this.style = {};
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

  get innerHTML() { return ''; }

  set innerHTML(v) {
    if (v === '') {
      for (const k of this.children) { k.parentNode = null; }
      this.children = [];
    }
  }

  append(...kids) {
    for (const k of kids) { k.parentNode = this; this.children.push(k); }
  }

  appendChild(k) { this.append(k); return k; }

  // 実 DOM の Node.contains 相当（自分自身も含む）。
  contains(node) {
    let n = node;
    while (n) {
      if (n === this) { return true; }
      n = n.parentNode;
    }
    return false;
  }

  closest(selector) {
    const keys = selector.split(',').map((s) => s.trim().replace(/^\[|\]$/g, ''));
    const dsKey = (attr) => attr.replace(/^data-/, '').replace(/-([a-z])/g, (_, c) => c.toUpperCase());
    let node = this;
    while (node) {
      if (keys.some((k) => node.dataset && node.dataset[dsKey(k)] !== undefined)) {
        return node;
      }
      node = node.parentNode;
    }
    return null;
  }

  addEventListener(ev, fn) { (this._handlers[ev] ??= []).push(fn); }

  fire(ev, arg = {}) { for (const fn of this._handlers[ev] ?? []) { fn(arg); } }
}

const TEMPLATES = [{
  templateId: 'tpl#1', name: '構成A', timeframe: '1D', createdAt: 1, updatedAt: 1,
}];
const THEMES = [{
  themeId: 'thm#1', name: 'ダーク統一', roleColors: {}, tfModifier: null, createdAt: 1, updatedAt: 1,
}];

// 3 つのメニューを**同一 document**へ install する（実 UI と同じ構成）。
function buildToolbar() {
  const mounts = {
    'tf-menu': new El(),
    'tpl-menu': new El(),
    'color-theme-menu': new El(),
  };
  const docListeners = [];
  const doc = {
    createElement: () => new El(),
    getElementById: (id) => mounts[id] ?? null,
    addEventListener: (ev, fn) => { if (ev === 'click') { docListeners.push(fn); } },
    removeEventListener: (ev, fn) => {
      const i = docListeners.indexOf(fn);
      if (i >= 0) { docListeners.splice(i, 1); }
    },
  };
  const timeframe = new TimeframeMenu({ document: doc });
  const template = new ChartTemplateMenu({ document: doc, templates: TEMPLATES });
  const theme = new ColorThemeMenu({ document: doc, themes: THEMES });
  timeframe.install();
  template.install();
  theme.install();

  // クリックの伝播: 要素の click ハンドラ → document の click リスナ（実 DOM のバブリング相当）。
  const clickOn = (el) => {
    let node = el;
    while (node) {
      node.fire('click', { target: el });
      node = node.parentNode;
    }
    for (const fn of [...docListeners]) { fn({ target: el }); }
  };

  const panel = (id) => mounts[id].children[1];   // [0]=トリガー / [1]=ポップ
  const trigger = (id) => mounts[id].children[0];
  const isOpen = (id) => !panel(id).classList.contains('is-hidden');
  return {
    doc, mounts, docListeners, clickOn, trigger, isOpen, menus: { timeframe, template, theme },
  };
}

// ---------------------------------------------------------------------------
// 排他クローズ（ユーザー指示 2026-08-10）
// ---------------------------------------------------------------------------

test('ISSUE-366 テンプレートを開いた状態でテーマを押すと、テンプレートは閉じる', () => {
  // Arrange
  const tb = buildToolbar();
  tb.clickOn(tb.trigger('tpl-menu'));
  assert.equal(tb.isOpen('tpl-menu'), true, '前提: テンプレートが開いている');
  // Act
  tb.clickOn(tb.trigger('color-theme-menu'));
  // Assert
  assert.equal(tb.isOpen('tpl-menu'), false, 'テンプレートの設定画面が残ってはならない');
  assert.equal(tb.isOpen('color-theme-menu'), true, '押したテーマ側は開く');
});

test('ISSUE-366 逆順（テーマ → テンプレート）でも同じ', () => {
  // Arrange
  const tb = buildToolbar();
  tb.clickOn(tb.trigger('color-theme-menu'));
  assert.equal(tb.isOpen('color-theme-menu'), true, '前提: テーマが開いている');
  // Act
  tb.clickOn(tb.trigger('tpl-menu'));
  // Assert
  assert.equal(tb.isOpen('color-theme-menu'), false, 'テーマの設定画面が残ってはならない');
  assert.equal(tb.isOpen('tpl-menu'), true, '押したテンプレート側は開く');
});

test('ISSUE-366 時間足メニューも同じ規律に乗る（3 者いずれの組でも同時に開かない）', () => {
  // Arrange / Act / Assert: 3 つを順に押し、常に押した 1 つだけが開いている。
  const tb = buildToolbar();
  const ids = ['tf-menu', 'tpl-menu', 'color-theme-menu'];
  for (const id of ids) {
    tb.clickOn(tb.trigger(id));
    assert.deepEqual(
      ids.filter((x) => tb.isOpen(x)), [id],
      `${id} を押した後に開いているのは ${id} だけであるべき`,
    );
  }
});

test('ISSUE-366 メニュー外の UI をクリックすると、開いていたメニューは閉じる', () => {
  // Arrange: どのマウントにも属さない別 UI 要素。
  const tb = buildToolbar();
  const otherUi = new El();
  tb.clickOn(tb.trigger('tpl-menu'));
  // Act
  tb.clickOn(otherUi);
  // Assert
  assert.equal(tb.isOpen('tpl-menu'), false, '外側クリックで閉じる（従来仕様の維持）');
});

test('ISSUE-366 自分のポップ内クリックでは、外側クリック判定で閉じられない（項目側の閉じるだけが効く）', () => {
  // Arrange
  const tb = buildToolbar();
  tb.clickOn(tb.trigger('tpl-menu'));
  const pop = tb.mounts['tpl-menu'].children[1];
  const cat = pop.children.find((c) => c.className.includes('tpl-menu-cat'));
  // Act: 見出し（項目ではない＝閉じる操作を持たない要素）を押す。
  tb.clickOn(cat);
  // Assert
  assert.equal(tb.isOpen('tpl-menu'), true, 'メニュー内クリックで勝手に閉じない');
});

// ---------------------------------------------------------------------------
// ISSUE-169 の性質を維持する（document リスナの線形蓄積が起きない）
// ---------------------------------------------------------------------------

test('ISSUE-169 再 install しても document の click リスナは 1 個のまま', () => {
  // Arrange
  const tb = buildToolbar();
  const before = tb.docListeners.length;
  // Act: 統合 UI のモードトグル相当（同一 document へ再 install）。
  tb.menus.timeframe.install();
  tb.menus.template.install();
  tb.menus.theme.install();
  // Assert
  assert.equal(before, 1, `document あたり click リスナは 1 個（実際: ${before}）`);
  assert.equal(tb.docListeners.length, 1, '再 install で増えない');
});

test('ISSUE-169 全メニューを dispose すると document の click リスナは外れる', () => {
  // Arrange
  const tb = buildToolbar();
  // Act
  tb.menus.timeframe.dispose();
  tb.menus.template.dispose();
  tb.menus.theme.dispose();
  // Assert
  assert.equal(tb.docListeners.length, 0, '最後の 1 件を外したら document リスナも外す');
});
