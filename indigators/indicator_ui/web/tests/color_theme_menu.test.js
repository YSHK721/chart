// color_theme_menu.js（テーマドロップダウン・DOM アダプター）のテスト。
//
// 設計入力（唯一の仕様源）: .doc/indicator-management-ui/基本設計_指標カラーテーマ.md v0.3.1
//   §6.1（空マウント `<div class="color-theme-menu" id="color-theme-menu"></div>` は
//        app_chrome_view.installChartToolbar が生成し、項目 DOM は本モジュールが生成する）、
//   §6.2（行構成＝「テーマなし（既定色）」固定行 → 区切り → 保存済み各行 → 「新しいテーマを作成…」
//        →「管理（名前を変更・削除）…」／行クリックで即適用／activeThemeId 一致行は is-active／
//        「テーマなし」は常に先頭の固定行で削除も改名もできない／開閉挙動は時間足・テンプレートと同一）、
//   §7.1（controller を知らない＝DIP。適用・保存・管理は注入コールバックへ委譲）。
// 参照実装（同型元）: js/adapter/front/chart_template_menu.js ／ tests/chart_template_menu.test.js。
// 構造: Arrange-Act-Assert（AAA）。jsdom を避けた最小 DOM スタブ（同型元と同作法）。

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { ColorThemeMenu } from '../js/adapter/front/color_theme_menu.js';

// ---- 最小 DOM スタブ（新規依存を追加しない）--------------------------------
class El {
  constructor() {
    this.children = [];
    this.dataset = {};
    this.textContent = '';
    this.type = '';
    this.id = '';
    this.title = '';
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

  // 実 DOM の Element.closest 相当（属性セレクタの OR のみを解釈する最小実装）。
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

function flatten(el, out = []) {
  for (const kid of el.children ?? []) { out.push(kid); flatten(kid, out); }
  return out;
}

function textOf(el) {
  return [el, ...flatten(el)].map((e) => e.textContent ?? '').join(' ');
}

const THEMES = [
  {
    themeId: 'thm#1', name: 'ダーク統一', roleColors: {}, tfModifier: null, createdAt: 1, updatedAt: 1,
  },
  {
    themeId: 'thm#2', name: '時間足別', roleColors: {}, tfModifier: {}, createdAt: 1, updatedAt: 1,
  },
];

function build(opts = {}) {
  const mount = new El();
  const docHandlers = {};
  const doc = {
    createElement: () => new El(),
    getElementById: (id) => (id === 'color-theme-menu' ? mount : null),
    addEventListener: (ev, fn) => { docHandlers[ev] = fn; },
    removeEventListener: () => {},
  };
  const menu = new ColorThemeMenu({ document: doc, themes: THEMES, ...opts });
  menu.install();
  return {
    menu, mount, trigger: mount.children[0], pop: mount.children[1], docHandlers,
  };
}

// 行（固定行 + 保存済み行）を宣言順に取り出す。固定行の themeId は ''（＝テーマなし）。
const rowsOf = (pop) => flatten(pop).filter((e) => e.dataset && e.dataset.themeId !== undefined);

// ---------------------------------------------------------------------------
// DOM 生成（§6.1）
// ---------------------------------------------------------------------------

test('TC-CM01 #color-theme-menu の空マウントへトリガーとポップを生成する・既定は閉（§6.1・§6.2）', () => {
  // Arrange / Act
  const { mount, pop, trigger } = build();
  // Assert
  assert.equal(mount.children.length, 2, 'マウントへトリガーとポップの 2 要素を生成する（HTML 直書きを作らない）');
  assert.ok(textOf(trigger).includes('テーマ'), `トリガーは「テーマ」を表示する（実際: ${textOf(trigger)}）`);
  assert.equal(pop.classList.contains('is-hidden'), true, '既定は閉（時間足・テンプレートメニューと同一の開閉挙動）');
});

test('TC-CM02 行構成と順序: 固定行 → 保存済み各行 → 作成 → 管理（§6.2）', () => {
  // Arrange / Act
  const { pop } = build();
  const rows = rowsOf(pop);
  const actions = flatten(pop).filter((e) => e.dataset && e.dataset.themeAction);
  // Assert
  assert.deepEqual(
    rows.map((r) => r.dataset.themeId),
    ['', 'thm#1', 'thm#2'],
    '先頭が固定行（themeId=""）で、以降は themes の宣言順',
  );
  assert.ok(textOf(rows[0]).includes('テーマなし'), `固定行は「テーマなし（既定色）」（実際: ${textOf(rows[0])}）`);
  assert.equal(textOf(rows[1]).includes('ダーク統一'), true, '保存済み行はテーマ名を表示する');
  assert.deepEqual(actions.map((a) => a.dataset.themeAction), ['create', 'manage'], '作成 → 管理 の順で末尾に置く');
  const text = textOf(pop);
  assert.ok(text.includes('保存済み'), `保存済みの区切りを置く（実際: ${text}）`);
  assert.ok(text.includes('新しいテーマを作成'), '「新しいテーマを作成…」を置く');
  assert.ok(text.includes('管理'), '「管理（名前を変更・削除）…」を置く');
});

test('TC-CM03 固定行は themes が 0 件でも常に先頭に在席する（§6.2）', () => {
  // Arrange / Act
  const { pop } = build({ themes: [] });
  const rows = rowsOf(pop);
  // Assert
  assert.deepEqual(rows.map((r) => r.dataset.themeId), [''], '保存済みが 0 件でも固定行は消えない');
});

test('TC-CM03b 区切りはテーマが 1 件以上あるときだけ置く（0 件で空見出しを出さない）', () => {
  // Arrange / Act: 保存済み 0 件（初回起動と同じ状態）。
  const empty = build({ themes: [] });
  const filled = build();
  // Assert
  assert.equal(
    textOf(empty.pop).includes('保存済み'), false,
    `見出しだけが宙に浮く（区切りの下に 1 行も無い）: ${textOf(empty.pop)}`,
  );
  assert.equal(textOf(filled.pop).includes('保存済み'), true, '1 件以上あれば従来どおり区切りを置く');
});

test('TC-CM04 固定行は削除・改名の操作面を持たない（§6.2）', () => {
  // Arrange / Act
  const { pop } = build();
  const fixed = rowsOf(pop)[0];
  // Assert
  const affordances = [fixed, ...flatten(fixed)].filter(
    (e) => e.dataset && (e.dataset.themeRename !== undefined || e.dataset.themeDelete !== undefined),
  );
  assert.deepEqual(affordances, [], '固定行に改名・削除の操作面を作らない（削除も改名もできない）');
});

// ---------------------------------------------------------------------------
// 選択状態（§6.2）
// ---------------------------------------------------------------------------

test('TC-CM05 activeThemeId に一致する行のみ is-active を持つ（§6.2）', () => {
  // Arrange / Act
  const { pop } = build({ activeThemeId: 'thm#2' });
  const active = rowsOf(pop).filter((r) => r.classList.contains('is-active')).map((r) => r.dataset.themeId);
  // Assert
  assert.deepEqual(active, ['thm#2'], 'activeThemeId 一致行のみ選択状態');
});

test('TC-CM06 activeThemeId = null のときは固定行「テーマなし」が選択状態（§6.2）', () => {
  // Arrange / Act
  const { pop } = build({ activeThemeId: null });
  const active = rowsOf(pop).filter((r) => r.classList.contains('is-active')).map((r) => r.dataset.themeId);
  // Assert
  assert.deepEqual(active, [''], 'テーマ未選択は固定行が選択状態');
});

// ---------------------------------------------------------------------------
// クリック委譲（§6.2・§7.1 DIP）
// ---------------------------------------------------------------------------

test('TC-CM07 行クリックで onSelect(themeId) を呼び、固定行は null を渡す（UC-C02）', () => {
  // Arrange
  const picked = [];
  const { pop } = build({ onSelect: (id) => picked.push(id) });
  const rows = rowsOf(pop);
  // Act
  pop.fire('click', { target: rows[1] });
  pop.fire('click', { target: rows[0] });
  // Assert
  assert.deepEqual(picked, ['thm#1', null], '保存済み行は themeId・固定行は null（＝既定色へ戻す）');
});

test('TC-CM08 委譲: 行の子要素（名前 span）クリックでも onSelect が発火し、選択で閉じる（実 DOM の e.target）', () => {
  // Arrange
  const picked = [];
  const { trigger, pop } = build({ onSelect: (id) => picked.push(id) });
  trigger.fire('click', { stopPropagation() {} });
  const row = rowsOf(pop).find((r) => r.dataset.themeId === 'thm#2');
  // Act
  pop.fire('click', { target: row.children[row.children.length - 1] });
  // Assert
  assert.deepEqual(picked, ['thm#2'], '子要素から行へ遡って適用要求を出す');
  assert.equal(pop.classList.contains('is-hidden'), true, '選択で閉じる');
});

test('TC-CM09 作成・管理はそれぞれ onCreate / onManage を呼ぶ（§6.2）', () => {
  // Arrange
  const calls = [];
  const { pop } = build({ onCreate: () => calls.push('create'), onManage: () => calls.push('manage') });
  const actions = flatten(pop).filter((e) => e.dataset && e.dataset.themeAction);
  // Act
  for (const a of actions) { pop.fire('click', { target: a }); }
  // Assert
  assert.deepEqual(calls, ['create', 'manage']);
});

// ---------------------------------------------------------------------------
// 開閉挙動（§6.2・時間足・テンプレートメニューと同一）
// ---------------------------------------------------------------------------

test('TC-CM10 開閉: トリガークリックでトグルし、外側（document）クリックで閉じる（§6.2）', () => {
  // Arrange
  const { trigger, pop, docHandlers } = build();
  // Act / Assert
  trigger.fire('click', { stopPropagation() {} });
  assert.equal(pop.classList.contains('is-hidden'), false, '1 回目で開く');
  trigger.fire('click', { stopPropagation() {} });
  assert.equal(pop.classList.contains('is-hidden'), true, '2 回目で閉じる');
  trigger.fire('click', { stopPropagation() {} });
  docHandlers.click();
  assert.equal(pop.classList.contains('is-hidden'), true, '外側クリックで閉じる');
});

// ---------------------------------------------------------------------------
// 再描画（適用・保存後に選択状態を更新する）
// ---------------------------------------------------------------------------

test('TC-CM11 再描画: 開くたびに provide の最新ビューモデルで一覧を作り直す（行が重複しない）', () => {
  // Arrange
  let vm = { themes: THEMES, activeThemeId: null };
  const { trigger, pop } = build({ provide: () => vm });
  // Act
  trigger.fire('click', { stopPropagation() {} });
  vm = {
    themes: [...THEMES, {
      themeId: 'thm#3', name: '新規', roleColors: {}, tfModifier: null, createdAt: 1, updatedAt: 1,
    }],
    activeThemeId: 'thm#3',
  };
  trigger.fire('click', { stopPropagation() {} }); // 閉じる
  trigger.fire('click', { stopPropagation() {} }); // 開く（再描画）
  // Assert
  const rows = rowsOf(pop);
  assert.deepEqual(rows.map((r) => r.dataset.themeId), ['', 'thm#1', 'thm#2', 'thm#3'], '重複せず最新集合を描く');
  assert.deepEqual(rows.filter((r) => r.classList.contains('is-active')).map((r) => r.dataset.themeId), ['thm#3']);
});

test('TC-CM12 render(vm): 部分注入でも既存値を保持して再描画する', () => {
  // Arrange
  const { pop, menu } = build();
  // Act
  menu.render({ activeThemeId: 'thm#1' });
  // Assert
  const rows = rowsOf(pop);
  assert.deepEqual(rows.map((r) => r.dataset.themeId), ['', 'thm#1', 'thm#2'], 'themes 未指定なら既存を保持');
  assert.deepEqual(rows.filter((r) => r.classList.contains('is-active')).map((r) => r.dataset.themeId), ['thm#1']);
});

// ---------------------------------------------------------------------------
// 防御（§6.1・timeframe_menu / chart_template_menu と同型）
// ---------------------------------------------------------------------------

test('TC-CM13 防御: DOM 不在・マウント欠落でも install は例外を投げない', () => {
  // Arrange / Act / Assert
  assert.doesNotThrow(() => new ColorThemeMenu({ document: null }).install());
  assert.doesNotThrow(() => new ColorThemeMenu({
    document: { createElement: () => ({}), getElementById: () => null },
  }).install());
});

test('TC-CM14 DIP: 本モジュールは controller / 協働子を import しない（§7.1）', async () => {
  // Arrange
  const { readFileSync } = await import('node:fs');
  const { fileURLToPath } = await import('node:url');
  const src = readFileSync(fileURLToPath(new URL('../js/adapter/front/color_theme_menu.js', import.meta.url)), 'utf8');
  // Act
  const imports = [...src.matchAll(/^\s*import\s.*?from\s+'([^']+)'/gm)].map((m) => m[1]);
  // Assert
  assert.ok(!imports.some((p) => p.includes('controller')), `controller を import している: ${imports.join(', ')}`);
});
