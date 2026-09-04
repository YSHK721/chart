// chart_template_menu.js のクリック委譲（実 DOM の e.target 解決）とコールバック契約。
//
// 設計入力（唯一の仕様源）: .doc/indicator-management-ui/基本設計_チャートテンプレート.md v0.1.1
//   §6.2（行クリックで適用・「この時間足に紐付け」・「現在の構成を保存…」・「管理…」）、
//   §7.1（menu は controller を知らない＝コールバック注入で結ぶ・DIP）、U4（onSelect 注入）、
//   U6（開くたびに再描画する＝restore() との順序依存を構造的に作らない）。
//
// 本ファイルの追加理由（前フェーズの Red テスト chart_template_menu.test.js は無改変で温存する）:
//   保存済み行は名前 span ＋ バッジ span を子に持つため、実 DOM ではクリックの e.target が
//   子要素になりうる。前フェーズのテストは e.target に行そのものを与える経路のみを固定しており、
//   子要素からの解決（closest 遡上）は未検証だった。CSS（pointer-events）に依存せず成立することを固定する。
// 構造: Arrange-Act-Assert（AAA）。最小 DOM スタブ。

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { ChartTemplateMenu } from '../js/adapter/front/chart_template_menu.js';

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

const TEMPLATES = [
  { templateId: 'tpl#1', name: 'スイング', instances: [], createdAt: 1, updatedAt: 1 },
  { templateId: 'tpl#2', name: 'デイトレ', instances: [], createdAt: 1, updatedAt: 1 },
];

function build(opts = {}) {
  const mount = new El();
  const doc = {
    createElement: () => new El(),
    getElementById: (id) => (id === 'tpl-menu' ? mount : null),
    addEventListener: () => {},
  };
  const menu = new ChartTemplateMenu({
    document: doc, templates: TEMPLATES, bindings: { '1D': 'tpl#1' }, ...opts,
  });
  menu.install();
  return { menu, mount, trigger: mount.children[0], pop: mount.children[1] };
}

function flatten(el, out = []) {
  for (const kid of el.children ?? []) { out.push(kid); flatten(kid, out); }
  return out;
}

test('TC-M09 委譲: 行の子要素（名前 span）クリックでも onSelect(templateId) が発火する（実 DOM の e.target）', () => {
  // Arrange
  const picked = [];
  const { pop } = build({ onSelect: (id) => picked.push(id) });
  const row = flatten(pop).find((e) => e.dataset.templateId === 'tpl#2');
  const nameSpan = row.children[0];
  // Act
  pop.fire('click', { target: nameSpan });
  // Assert
  assert.deepEqual(picked, ['tpl#2'], '子要素から行へ遡って適用要求を出す');
  assert.equal(pop.classList.contains('is-hidden'), true, '選択で閉じる');
});

test('TC-M10 委譲: 「この時間足に紐付け」は onBind(templateId) / 紐付けなしは onBind(null)（§6.2）', () => {
  // Arrange
  const bound = [];
  const { pop } = build({ onBind: (id) => bound.push(id) });
  const items = flatten(pop).filter((e) => e.dataset.tplBind !== undefined);
  // Assert: 「紐付けなし」＋テンプレート数
  assert.equal(items.length, TEMPLATES.length + 1, '「紐付けなし」を先頭に持つ');
  // Act
  pop.fire('click', { target: items[0] });
  pop.fire('click', { target: items[2] });
  // Assert
  assert.deepEqual(bound, [null, 'tpl#2'], '紐付けなしは null・テンプレートは templateId');
});

test('TC-M11 委譲: 保存・管理はそれぞれ onSave / onManage を呼ぶ（§6.2）', () => {
  // Arrange
  const calls = [];
  const { pop } = build({ onSave: () => calls.push('save'), onManage: () => calls.push('manage') });
  const actions = flatten(pop).filter((e) => e.dataset.tplAction);
  // Act
  for (const a of actions) { pop.fire('click', { target: a }); }
  // Assert
  assert.deepEqual(calls, ['save', 'manage']);
});

test('TC-M12 再描画: 開くたびに provide の最新ビューモデルで一覧を作り直す（U6・行が重複しない）', () => {
  // Arrange
  let vm = { templates: TEMPLATES, bindings: {}, activeTemplateId: null };
  const { trigger, pop } = build({ provide: () => vm });
  // Act: 1 回目を開いた後にテンプレートを増やして開き直す
  trigger.fire('click', { stopPropagation() {} });
  vm = {
    templates: [...TEMPLATES, { templateId: 'tpl#3', name: '新規', instances: [], createdAt: 1, updatedAt: 1 }],
    bindings: { '5m': 'tpl#3' },
    activeTemplateId: 'tpl#3',
  };
  trigger.fire('click', { stopPropagation() {} }); // 閉じる
  trigger.fire('click', { stopPropagation() {} }); // 開く（再描画）
  // Assert
  const rows = flatten(pop).filter((e) => e.dataset.templateId);
  assert.deepEqual(rows.map((r) => r.dataset.templateId), ['tpl#1', 'tpl#2', 'tpl#3'], '重複せず最新集合を描く');
  assert.deepEqual(rows.filter((r) => r.classList.contains('is-active')).map((r) => r.dataset.templateId), ['tpl#3']);
});

test('TC-M13 render(vm): 部分注入でも既存値を保持して再描画する（U3）', () => {
  // Arrange
  const { pop, menu } = build();
  // Act
  menu.render({ activeTemplateId: 'tpl#1' });
  // Assert
  const rows = flatten(pop).filter((e) => e.dataset.templateId);
  assert.deepEqual(rows.map((r) => r.dataset.templateId), ['tpl#1', 'tpl#2'], 'templates 未指定なら既存を保持');
  assert.deepEqual(rows.filter((r) => r.classList.contains('is-active')).map((r) => r.dataset.templateId), ['tpl#1']);
});
