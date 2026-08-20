// position_sizing_menu.js（ポジションサイズ計算機のツールバー入口・DOM アダプター）のテスト。
//
// 設計入力（唯一の仕様源）: .doc/POSITION_SIZING_CHART_INTEGRATION_DESIGN.md
//   §6 アダプター設計（器＝installChartToolbar が生成する空マウント #position-sizing-menu／
//   項目 DOM は各モジュールが自分で生成する／**協働子は import せずコールバック注入・遅延参照**
//   ＝color_theme と同一規約）。
// 参照実装（同型元）: js/adapter/front/color_theme_menu.js ／ tests/color_theme_menu.test.js。
// 構造: Arrange-Act-Assert（AAA）。jsdom を避けた最小 DOM スタブ（同型元と同作法）。

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { PositionSizingMenu } from '../js/adapter/front/position_sizing_menu.js';

class El {
  constructor() {
    this.children = [];
    this.dataset = {};
    this.textContent = '';
    this.type = '';
    this.id = '';
    this.title = '';
    this.disabled = false;
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

  append(...kids) {
    for (const k of kids) { k.parentNode = this; this.children.push(k); }
  }

  appendChild(k) { this.append(k); return k; }

  addEventListener(ev, fn) { (this._handlers[ev] ??= []).push(fn); }

  fire(ev, arg = {}) { for (const fn of this._handlers[ev] ?? []) { fn(arg); } }
}

function flatten(el, out = []) {
  for (const kid of el.children ?? []) { out.push(kid); flatten(kid, out); }
  return out;
}

const textOf = (el) => [el, ...flatten(el)].map((e) => e.textContent ?? '').join(' ');

function build(opts = {}) {
  const mount = new El();
  const doc = {
    createElement: () => new El(),
    getElementById: (id) => (id === 'position-sizing-menu' ? mount : null),
    addEventListener: () => {},
    removeEventListener: () => {},
  };
  const menu = new PositionSizingMenu({ document: doc, ...opts });
  menu.install();
  return { menu, mount, trigger: mount.children[0] };
}

test('TC-PM01 空マウントへトリガーを生成する（項目 DOM は自分で作る＝HTML 直書きを作らない）', () => {
  // Arrange / Act
  const { mount, trigger } = build();
  // Assert
  assert.equal(mount.children.length, 1, 'マウント直下はトリガー 1 個');
  assert.equal(trigger.id, 'position-sizing-menu-trigger');
  assert.equal(trigger.type, 'button');
  assert.ok(textOf(trigger).includes('サイズ'), `トリガーの文言に「サイズ」を含む（実際: ${textOf(trigger)}）`);
});

test('TC-PM02 トリガークリックで注入コールバックを呼ぶ（協働子を import しない＝DIP）', () => {
  // Arrange
  const calls = [];
  const { trigger } = build({ onOpen: () => calls.push('open') });
  // Act
  trigger.fire('click');
  // Assert
  assert.deepEqual(calls, ['open']);
});

test('TC-PM03 コールバック未注入でもクリックで例外にならない（配線前の押下）', () => {
  // Arrange
  const { trigger } = build();
  // Act / Assert
  assert.doesNotThrow(() => trigger.fire('click'));
});

test('TC-PM04 DOM 不在（SSR・最小 fake）は no-op（例外にしない）', () => {
  // Arrange
  const menu = new PositionSizingMenu({ document: null });
  // Act / Assert
  assert.doesNotThrow(() => menu.install());
});

test('TC-PM05 器が無いページでは何も生成しない（器の所有は app_chrome_view）', () => {
  // Arrange
  const doc = { createElement: () => new El(), getElementById: () => null };
  const menu = new PositionSizingMenu({ document: doc, onOpen: () => {} });
  // Act / Assert
  assert.doesNotThrow(() => menu.install());
});
