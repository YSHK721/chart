// chart_context_menu.js（チャート版面の右クリックメニュー・ユーザー指示 2026-08-09）の仕様検証。
//
// 固定する規約:
//   - ローソク足上の右クリックでブラウザ標準メニューを抑止し、注入された項目を出す。
//   - 項目には右クリック位置（チャート要素左上基準の x/y）を渡す＝どの足かは項目側が解決する。
//   - 選択・外側クリック・Esc で閉じる。
//   - 器は .chart-wrap 配下へ本 View が生成する（配信ページの HTML へ複製しない）。
// 構造: Arrange-Act-Assert。実 DOM 非依存（fake document を注入）。

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { ChartContextMenu } from '../js/adapter/front/chart_context_menu.js';

function fakeElement(tagName = 'div') {
  const el = {
    tagName,
    className: '',
    textContent: '',
    style: {},
    children: [],
    parentElement: null,
    _classes: new Set(),
    _listeners: new Map(),
    classList: {
      add(c) { el._classes.add(c); },
      remove(c) { el._classes.delete(c); },
      contains(c) { return el._classes.has(c); },
      toggle(c, on) { if (on) { el._classes.add(c); } else { el._classes.delete(c); } },
    },
    get innerHTML() { return this._innerHTML ?? ''; },
    set innerHTML(v) { this._innerHTML = v; if (v === '') { this.children = []; } },
    append(...nodes) { for (const n of nodes) { this.children.push(n); n.parentElement = this; } },
    appendChild(n) { this.children.push(n); n.parentElement = this; return n; },
    querySelector(sel) {
      const name = sel.replace(/^[.#]/, '');
      return this.children.find((c) => c.className === name || c.id === name) ?? null;
    },
    addEventListener(type, fn) {
      if (!this._listeners.has(type)) { this._listeners.set(type, []); }
      this._listeners.get(type).push(fn);
    },
    fire(type, ev = {}) { for (const fn of this._listeners.get(type) ?? []) { fn(ev); } },
    getBoundingClientRect() { return { left: 100, top: 50, width: 800, height: 400 }; },
  };
  return el;
}

function fakeDoc() {
  const wrap = fakeElement('div');
  wrap.className = 'chart-wrap';
  const listeners = new Map();
  const doc = {
    _wrap: wrap,
    _listeners: listeners,
    createElement(tag) { return fakeElement(tag); },
    querySelector(sel) { return sel === '.chart-wrap' ? wrap : null; },
    addEventListener(type, fn) {
      if (!listeners.has(type)) { listeners.set(type, []); }
      listeners.get(type).push(fn);
    },
    removeEventListener(type, fn) {
      const arr = listeners.get(type) ?? [];
      const i = arr.indexOf(fn);
      if (i >= 0) { arr.splice(i, 1); }
    },
    fire(type, ev = {}) { for (const fn of [...(listeners.get(type) ?? [])]) { fn(ev); } },
  };
  return doc;
}

function setup(items) {
  const doc = fakeDoc();
  const container = fakeElement('div');
  const menu = new ChartContextMenu({ document: doc, container, items });
  menu.install();
  return { doc, container, menu };
}

// clientX/Y は版面/コンテナ矩形（left 100 / top 50）を引いた座標になる。
function rightClick(container, { clientX = 300, clientY = 150 } = {}) {
  let prevented = false;
  container.fire('contextmenu', {
    clientX, clientY, preventDefault() { prevented = true; },
  });
  return prevented;
}

function hostOf(doc) {
  return doc._wrap.children.find((c) => c.className === 'chart-context-menu') ?? null;
}

test('右クリックで標準メニューを抑止し、注入された項目を版面配下へ出す', () => {
  const { doc, container } = setup([{ label: '情報をコピーする', onSelect() {} }]);
  const prevented = rightClick(container);
  assert.equal(prevented, true);
  const host = hostOf(doc);
  assert.ok(host, '器 .chart-wrap 配下に .chart-context-menu が生成されていない');
  assert.deepEqual(host.children.map((c) => c.textContent), ['情報をコピーする']);
});

test('項目には右クリック位置（チャート要素左上基準）が渡る', () => {
  const seen = [];
  const { doc, container } = setup([{ label: 'x', onSelect: (ctx) => seen.push(ctx) }]);
  rightClick(container, { clientX: 300, clientY: 150 });
  hostOf(doc).children[0].fire('click', { stopPropagation() {} });
  assert.deepEqual(seen, [{ x: 200, y: 100 }]);   // 300-100 / 150-50
});

test('メニューは右クリック位置（版面基準）へ置く', () => {
  const { doc, container } = setup([{ label: 'x', onSelect() {} }]);
  rightClick(container, { clientX: 420, clientY: 260 });
  const host = hostOf(doc);
  assert.equal(host.style.left, '320px');
  assert.equal(host.style.top, '210px');
});

test('項目を選ぶと閉じる', () => {
  const { doc, container } = setup([{ label: 'x', onSelect() {} }]);
  rightClick(container);
  const host = hostOf(doc);
  assert.equal(host.classList.contains('is-hidden'), false);
  host.children[0].fire('click', { stopPropagation() {} });
  assert.equal(host.classList.contains('is-hidden'), true);
});

test('外側クリック・Esc で閉じる', () => {
  const { doc, container } = setup([{ label: 'x', onSelect() {} }]);
  rightClick(container);
  doc.fire('click', {});
  assert.equal(hostOf(doc).classList.contains('is-hidden'), true);

  rightClick(container);
  doc.fire('keydown', { key: 'Escape' });
  assert.equal(hostOf(doc).classList.contains('is-hidden'), true);
});

test('再度の右クリックで器を増やさない（再入で 1 つのまま）', () => {
  const { doc, container } = setup([{ label: 'x', onSelect() {} }]);
  rightClick(container);
  rightClick(container);
  const hosts = doc._wrap.children.filter((c) => c.className === 'chart-context-menu');
  assert.equal(hosts.length, 1);
  assert.equal(hosts[0].children.length, 1);   // 項目も積み上がらない
});

test('項目 0 件・DOM 不在は install が no-op（例外にしない）', () => {
  const doc = fakeDoc();
  const container = fakeElement('div');
  new ChartContextMenu({ document: doc, container, items: [] }).install();
  assert.equal(container._listeners.has('contextmenu'), false);
  new ChartContextMenu({ document: null, container, items: [{ label: 'x' }] }).install();
});
