// chart_template_menu.js（テンプレートドロップダウン・DOM アダプター）の Red テスト。
//
// 設計入力（唯一の仕様源）: .doc/indicator-management-ui/基本設計_チャートテンプレート.md v0.1.1
//   §6.1（index.html には空マウント <div class="tpl-menu" id="tpl-menu"></div> のみ。項目 DOM は
//        共有 JS が生成する＝timeframe_menu.js の ISSUE-123 方針と同型）、
//   §6.2（保存済み一覧・各行の右側に紐付け先時間足をバッジ表示・activeTemplateId 一致行は is-active・
//        開閉挙動は時間足メニューと同一＝トリガークリックでトグル／項目選択で閉じる／外側クリックで閉じる）、
//   §7.1（controller を知らない＝DIP）。
// 参照実装（同型元）: js/adapter/front/timeframe_menu.js／tests/timeframe_menu.test.js。
// 構造: Arrange-Act-Assert（AAA）。jsdom を避けた最小 DOM スタブ（timeframe_menu.test.js と同作法）。
//
// ★ 本ファイルは Red フェーズ専用。対象モジュール js/adapter/front/chart_template_menu.js は未実装。
//
// ★ 仮名（設計書はマウント id（#tpl-menu）と is-active のみを定義し、トリガー／ポップの id・
//   項目のクラス名・行の識別属性・保存済み一覧の注入 API を定義していない。実装フェーズで確定する）:
//     class ChartTemplateMenu({ document, templates, bindings, activeTemplateId }) ; install()
//     保存済み行は dataset.templateId を持つ（timeframe_menu の dataset.timeframe と同型）
//     ポップの開閉クラスは is-hidden（timeframe_menu と同一の開閉挙動＝§6.2）
//   本テストは上記のうち「§6.2 が明記する挙動」と「マウントへの DOM 生成」に限定して固定し、
//   トリガー／ポップの id 文字列そのものは検証しない（未定義のため）。

import { test } from 'node:test';
import assert from 'node:assert/strict';

async function load() {
  return import('../js/adapter/front/chart_template_menu.js');
}

function fakeEl() {
  const el = {
    id: '', className: '', textContent: '', title: '', type: '',
    dataset: {}, children: [],
    _handlers: {},
    _cls: new Set(),
    classList: {
      toggle(c, on) {
        const has = el._cls.has(c);
        const next = on === undefined ? !has : on;
        if (next) el._cls.add(c); else el._cls.delete(c);
      },
      add(c) { el._cls.add(c); },
      remove(c) { el._cls.delete(c); },
      contains(c) { return el._cls.has(c); },
    },
    append(...kids) { el.children.push(...kids); },
    appendChild(kid) { el.children.push(kid); },
    addEventListener(ev, fn) { el._handlers[ev] = fn; },
    fire(ev, arg) { if (el._handlers[ev]) el._handlers[ev](arg); },
  };
  // className への代入で初期クラスを _cls へ反映する（実 DOM の最小模倣）。
  return new Proxy(el, {
    set(target, prop, value) {
      if (prop === 'className' && typeof value === 'string') {
        target._cls = new Set(value.split(/\s+/).filter(Boolean));
        target.classList.toggle = (c, on) => {
          const has = target._cls.has(c);
          const next = on === undefined ? !has : on;
          if (next) target._cls.add(c); else target._cls.delete(c);
        };
        target.classList.add = (c) => target._cls.add(c);
        target.classList.remove = (c) => target._cls.delete(c);
        target.classList.contains = (c) => target._cls.has(c);
      }
      target[prop] = value;
      return true;
    },
  });
}

// 生成ツリーを平坦化する（行・バッジの探索用）。
function flatten(el, out = []) {
  for (const kid of el.children ?? []) {
    out.push(kid);
    flatten(kid, out);
  }
  return out;
}

// 要素とその子孫のテキストを連結する（バッジ表示の検証用）。
function textOf(el) {
  return [el, ...flatten(el)].map((e) => e.textContent ?? '').join(' ');
}

const TEMPLATES = [
  { templateId: 'tpl#1', name: 'スイング', instances: [], createdAt: 1, updatedAt: 1 },
  { templateId: 'tpl#2', name: 'デイトレ', instances: [], createdAt: 1, updatedAt: 1 },
  { templateId: 'tpl#3', name: 'ボリューム分析', instances: [], createdAt: 1, updatedAt: 1 },
];
const BINDINGS = { '1D': 'tpl#1', '1W': 'tpl#1', '5m': 'tpl#2', '15m': 'tpl#2' };

function buildMenu({ templates = TEMPLATES, bindings = BINDINGS, activeTemplateId = null } = {}) {
  const mount = fakeEl();
  const docHandlers = {};
  const doc = {
    createElement: () => fakeEl(),
    getElementById: (id) => (id === 'tpl-menu' ? mount : null),
    addEventListener: (ev, fn) => { docHandlers[ev] = fn; },
  };
  const menu = new MenuClass({ document: doc, templates, bindings, activeTemplateId });
  menu.install();
  return { menu, mount, trigger: mount.children[0], pop: mount.children[1], docHandlers };
}

// buildMenu からクラス参照を差し込むための束縛（動的 import をテスト単位で行うため）。
let MenuClass = null;
async function withMenuClass() {
  ({ ChartTemplateMenu: MenuClass } = await load());
}

// ---------------------------------------------------------------------------
// DOM 生成（§6.1）
// ---------------------------------------------------------------------------

test('TC-M01 #tpl-menu の空マウントへトリガーとポップを生成する・既定は閉（§6.1・§6.2）', async () => {
  // Arrange
  await withMenuClass();
  // Act
  const { mount, pop } = buildMenu();
  // Assert
  assert.equal(mount.children.length, 2, 'マウントへトリガーとポップの 2 要素を生成する（HTML 直書きを作らない）');
  assert.equal(pop.classList.contains('is-hidden'), true, '既定は閉（時間足メニューと同一の開閉挙動）');
});

test('TC-M02 保存済み行を templates の宣言順に生成し、行は templateId で識別できる（§6.2）', async () => {
  // Arrange
  await withMenuClass();
  // Act
  const { pop } = buildMenu();
  const rows = flatten(pop).filter((e) => e.dataset && e.dataset.templateId);
  // Assert
  assert.deepEqual(rows.map((r) => r.dataset.templateId), ['tpl#1', 'tpl#2', 'tpl#3'], '保存済み一覧は宣言順');
  assert.ok(rows.every((r) => textOf(r).length > 0), '各行はテンプレート名を表示する');
});

// ---------------------------------------------------------------------------
// 開閉挙動（§6.2・時間足メニューと同一）
// ---------------------------------------------------------------------------

test('TC-M03 開閉: トリガークリックでトグルする（§6.2）', async () => {
  // Arrange
  await withMenuClass();
  const { trigger, pop } = buildMenu();
  // Act / Assert
  trigger.fire('click', { stopPropagation() {} });
  assert.equal(pop.classList.contains('is-hidden'), false, '1 回目で開く');
  trigger.fire('click', { stopPropagation() {} });
  assert.equal(pop.classList.contains('is-hidden'), true, '2 回目で閉じる');
});

test('TC-M04 開閉: 保存済み行（項目）の選択で閉じる（§6.2）', async () => {
  // Arrange
  await withMenuClass();
  const { trigger, pop } = buildMenu();
  trigger.fire('click', { stopPropagation() {} });
  // Act
  pop.fire('click', { target: { dataset: { templateId: 'tpl#2' } } });
  // Assert
  assert.equal(pop.classList.contains('is-hidden'), true);
});

test('TC-M05 開閉: 外側（document）クリックで閉じる（§6.2）', async () => {
  // Arrange
  await withMenuClass();
  const { trigger, pop, docHandlers } = buildMenu();
  trigger.fire('click', { stopPropagation() {} });
  // Act
  docHandlers.click();
  // Assert
  assert.equal(pop.classList.contains('is-hidden'), true);
});

// ---------------------------------------------------------------------------
// 紐付けバッジ・選択状態（§6.2）
// ---------------------------------------------------------------------------

test('TC-M06 保存済み行に紐付け先時間足をバッジ表示する（§6.2）', async () => {
  // Arrange
  await withMenuClass();
  // Act
  const { pop } = buildMenu();
  const rows = flatten(pop).filter((e) => e.dataset && e.dataset.templateId);
  const rowOf = (id) => rows.find((r) => r.dataset.templateId === id);
  // Assert
  const swing = textOf(rowOf('tpl#1'));
  assert.ok(swing.includes('1D') && swing.includes('1W'), `複数の紐付け先を表示する（実際: ${swing}）`);
  const day = textOf(rowOf('tpl#2'));
  assert.ok(day.includes('5m') && day.includes('15m'), `複数の紐付け先を表示する（実際: ${day}）`);
  assert.equal(textOf(rowOf('tpl#3')).includes('1D'), false, '紐付けの無いテンプレートに他行の足を表示しない');
});

test('TC-M07 activeTemplateId に一致する行のみ is-active を持つ（§6.2）', async () => {
  // Arrange
  await withMenuClass();
  // Act
  const { pop } = buildMenu({ activeTemplateId: 'tpl#2' });
  const rows = flatten(pop).filter((e) => e.dataset && e.dataset.templateId);
  // Assert
  const active = rows.filter((r) => r.classList.contains('is-active')).map((r) => r.dataset.templateId);
  assert.deepEqual(active, ['tpl#2'], 'activeTemplateId 一致行のみ選択状態');
});

test('TC-M08 防御: DOM 不在・マウント欠落でも install は例外を投げない（timeframe_menu と同型・§6.1）', async () => {
  // Arrange
  await withMenuClass();
  // Act / Assert
  assert.doesNotThrow(() => new MenuClass({ document: null }).install());
  assert.doesNotThrow(() => new MenuClass({
    document: { createElement: () => ({}), getElementById: () => null },
  }).install());
});
