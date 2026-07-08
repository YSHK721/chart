// indicator_legend_view.js（IndicatorLegendView）の仕様検証。
//
// 設計入力: ISSUE-038（IndicatorController の SRP 違反是正・View 描画の分離）。
//   凡例行 / お気に入り / ダイアログリストの純 DOM 構築を controller から切り出した adapter 層ビュー。
//   controller は行の view-model（label / visible / favorite / category）＋コールバックを注入し、
//   ビューは DOM を構築してイベント時にコールバックを発火するだけ（usecase/domain/lwc 非依存）。
// 構造: Arrange-Act-Assert（AAA）。実 DOM 非依存（fake document を注入）。
//   既存 crosshair_readout_view.test.js の fake document 流儀を踏襲する。

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { IndicatorLegendView } from '../js/adapter/front/indicator_legend_view.js';

// 最小 DOM スタブ。className / title / textContent / classList / children / append /
//   innerHTML / addEventListener（+ click 発火ヘルパ）を備える。
function fakeElement(tagName = 'div') {
  const el = {
    tagName,
    className: '',
    title: '',
    textContent: '',
    disabled: false,
    style: {},
    children: [],
    _classes: new Set(),
    _listeners: {},
    classList: {
      add(c) { el._classes.add(c); },
      remove(c) { el._classes.delete(c); },
      toggle(c, force) {
        const on = force === undefined ? !el._classes.has(c) : force;
        if (on) { el._classes.add(c); } else { el._classes.delete(c); }
        return on;
      },
      contains(c) { return el._classes.has(c); },
    },
    get innerHTML() { return this._innerHTML ?? ''; },
    set innerHTML(v) { this._innerHTML = v; if (v === '') { this.children = []; } },
    append(...nodes) { for (const n of nodes) { this.children.push(n); } },
    appendChild(n) { this.children.push(n); return n; },
    addEventListener(type, fn) { (this._listeners[type] ??= []).push(fn); },
    // テスト用: 登録済みハンドラを発火する。
    click(ev = {}) { for (const fn of this._listeners.click ?? []) { fn(ev); } },
  };
  return el;
}

function fakeDoc() {
  const byId = new Map();
  return {
    _byId: byId,
    createElement(tag) { return fakeElement(tag); },
    getElementById(id) { return byId.get(id) ?? null; },
  };
}

function withRoot(doc, id) {
  const root = fakeElement('div');
  doc._byId.set(id, root);
  return root;
}

function flatten(el, acc = []) {
  if (!el) return acc;
  acc.push(el);
  for (const c of el.children || []) { flatten(c, acc); }
  return acc;
}

function findByClass(root, cls) {
  return flatten(root).filter((n) => (n.className || '').split(' ').includes(cls));
}

// ===========================================================================
// renderLegend: 凡例行を構築する（label / eye / gear / close）
// ===========================================================================

test('renderLegend: builds one legend-row per row with label/eye/gear/close', () => {
  const doc = fakeDoc();
  const legend = withRoot(doc, 'legend');
  const view = new IndicatorLegendView({ document: doc });
  view.renderLegend([
    { label: 'sma (fast)', visible: true, onEye() {}, onGear() {}, onClose() {} },
    { label: 'mp', visible: false, onEye() {}, onGear() {}, onClose() {} },
  ]);
  assert.equal(findByClass(legend, 'legend-row').length, 2);
  const labels = findByClass(legend, 'legend-label').map((n) => n.textContent);
  assert.deepEqual(labels, ['sma (fast)', 'mp']);
  assert.equal(findByClass(legend, 'legend-eye').length, 2);
  assert.equal(findByClass(legend, 'legend-gear').length, 2);
  assert.equal(findByClass(legend, 'legend-remove').length, 2);
});

test('renderLegend: eye title/text reflect visibility (byte-identical to controller)', () => {
  const doc = fakeDoc();
  const legend = withRoot(doc, 'legend');
  const view = new IndicatorLegendView({ document: doc });
  view.renderLegend([
    { label: 'a', visible: true, onEye() {}, onGear() {}, onClose() {} },
    { label: 'b', visible: false, onEye() {}, onGear() {}, onClose() {} },
  ]);
  const eyes = findByClass(legend, 'legend-eye');
  assert.equal(eyes[0].textContent, '👁');
  assert.equal(eyes[0].title, '非表示にする');
  assert.equal(eyes[1].textContent, '🙈');
  assert.equal(eyes[1].title, '表示する');
});

test('renderLegend: gear/close static titles and text', () => {
  const doc = fakeDoc();
  const legend = withRoot(doc, 'legend');
  const view = new IndicatorLegendView({ document: doc });
  view.renderLegend([{ label: 'a', visible: true, onEye() {}, onGear() {}, onClose() {} }]);
  const gear = findByClass(legend, 'legend-gear')[0];
  const close = findByClass(legend, 'legend-remove')[0];
  assert.equal(gear.textContent, '⚙');
  assert.equal(gear.title, '設定');
  assert.equal(close.textContent, '✕');
  assert.equal(close.title, '削除');
});

test('renderLegend: eye/gear/close clicks fire the row callbacks', () => {
  const doc = fakeDoc();
  const legend = withRoot(doc, 'legend');
  const view = new IndicatorLegendView({ document: doc });
  const calls = [];
  view.renderLegend([{
    label: 'a', visible: true,
    onEye() { calls.push('eye'); },
    onGear() { calls.push('gear'); },
    onClose() { calls.push('close'); },
  }]);
  findByClass(legend, 'legend-eye')[0].click();
  findByClass(legend, 'legend-gear')[0].click();
  findByClass(legend, 'legend-remove')[0].click();
  assert.deepEqual(calls, ['eye', 'gear', 'close']);
});

test('renderLegend: clears previous rows on re-render', () => {
  const doc = fakeDoc();
  const legend = withRoot(doc, 'legend');
  const view = new IndicatorLegendView({ document: doc });
  view.renderLegend([{ label: 'a', visible: true, onEye() {}, onGear() {}, onClose() {} }]);
  view.renderLegend([]);
  assert.equal(findByClass(legend, 'legend-row').length, 0);
});

test('renderLegend: missing legend element does not throw (defensive)', () => {
  const doc = fakeDoc();
  const view = new IndicatorLegendView({ document: doc });
  assert.doesNotThrow(() => view.renderLegend([{ label: 'a', visible: true, onEye() {}, onGear() {}, onClose() {} }]));
});

test('renderLegend: null document does not throw', () => {
  const view = new IndicatorLegendView({ document: null });
  assert.doesNotThrow(() => view.renderLegend([{ label: 'a', visible: true, onEye() {}, onGear() {}, onClose() {} }]));
});

// ===========================================================================
// renderDialogList: ダイアログの指標リスト行を構築する（star / name / cat）
// ===========================================================================

test('renderDialogList: builds ind-row per row with star/name/cat', () => {
  const doc = fakeDoc();
  const list = withRoot(doc, 'indicator-list');
  const view = new IndicatorLegendView({ document: doc });
  view.renderDialogList([
    { label: 'RSI', category: 'osc', favorite: true, onToggleFavorite() {}, onPick() {} },
    { label: 'SMA', category: 'ma', favorite: false, onToggleFavorite() {}, onPick() {} },
  ]);
  assert.equal(findByClass(list, 'ind-row').length, 2);
  assert.deepEqual(findByClass(list, 'ind-name').map((n) => n.textContent), ['RSI', 'SMA']);
  assert.deepEqual(findByClass(list, 'ind-cat').map((n) => n.textContent), ['osc', 'ma']);
});

test('renderDialogList: favorite star shows filled star + is-on class', () => {
  const doc = fakeDoc();
  const list = withRoot(doc, 'indicator-list');
  const view = new IndicatorLegendView({ document: doc });
  view.renderDialogList([
    { label: 'A', category: 'x', favorite: true, onToggleFavorite() {}, onPick() {} },
    { label: 'B', category: 'x', favorite: false, onToggleFavorite() {}, onPick() {} },
  ]);
  const stars = findByClass(list, 'ind-fav');
  assert.equal(stars[0].textContent, '★');
  assert.ok(stars[0].className.includes('is-on'));
  assert.equal(stars[1].textContent, '☆');
  assert.ok(!stars[1].className.includes('is-on'));
});

test('renderDialogList: star click stops propagation and fires onToggleFavorite (not onPick)', () => {
  const doc = fakeDoc();
  const list = withRoot(doc, 'indicator-list');
  const view = new IndicatorLegendView({ document: doc });
  const calls = [];
  let stopped = false;
  view.renderDialogList([{
    label: 'A', category: 'x', favorite: false,
    onToggleFavorite() { calls.push('fav'); },
    onPick() { calls.push('pick'); },
  }]);
  const star = findByClass(list, 'ind-fav')[0];
  star.click({ stopPropagation() { stopped = true; } });
  assert.ok(stopped, 'star click must stopPropagation to avoid triggering the row pick');
  assert.deepEqual(calls, ['fav']);
});

test('renderDialogList: row click fires onPick', () => {
  const doc = fakeDoc();
  const list = withRoot(doc, 'indicator-list');
  const view = new IndicatorLegendView({ document: doc });
  const calls = [];
  view.renderDialogList([{
    label: 'A', category: 'x', favorite: false,
    onToggleFavorite() { calls.push('fav'); },
    onPick() { calls.push('pick'); },
  }]);
  findByClass(list, 'ind-row')[0].click();
  assert.deepEqual(calls, ['pick']);
});

test('renderDialogList: clears previous rows on re-render', () => {
  const doc = fakeDoc();
  const list = withRoot(doc, 'indicator-list');
  const view = new IndicatorLegendView({ document: doc });
  view.renderDialogList([{ label: 'A', category: 'x', favorite: false, onToggleFavorite() {}, onPick() {} }]);
  view.renderDialogList([]);
  assert.equal(findByClass(list, 'ind-row').length, 0);
});

test('renderDialogList: missing list element does not throw (defensive)', () => {
  const doc = fakeDoc();
  const view = new IndicatorLegendView({ document: doc });
  assert.doesNotThrow(() => view.renderDialogList([{ label: 'A', category: 'x', favorite: false, onToggleFavorite() {}, onPick() {} }]));
});

// ===========================================================================
// setDialogOpen: is-open クラスの純トグル
// ===========================================================================

test('setDialogOpen(true/false) toggles is-open on the dialog element', () => {
  const doc = fakeDoc();
  const dialog = withRoot(doc, 'indicator-dialog');
  const view = new IndicatorLegendView({ document: doc });
  view.setDialogOpen(true);
  assert.ok(dialog.classList.contains('is-open'));
  view.setDialogOpen(false);
  assert.ok(!dialog.classList.contains('is-open'));
});

test('setDialogOpen: missing dialog element does not throw', () => {
  const doc = fakeDoc();
  const view = new IndicatorLegendView({ document: doc });
  assert.doesNotThrow(() => view.setDialogOpen(true));
});

// ===========================================================================
// setActive: グループ内で active 要素のみ is-active（純 DOM ヘルパ）
// ===========================================================================

test('setActive: only the active element gets is-active within the group', () => {
  const doc = fakeDoc();
  const view = new IndicatorLegendView({ document: doc });
  const a = fakeElement('button');
  const b = fakeElement('button');
  const c = fakeElement('button');
  const group = [a, b, c];
  view.setActive(group, b);
  assert.ok(!a.classList.contains('is-active'));
  assert.ok(b.classList.contains('is-active'));
  assert.ok(!c.classList.contains('is-active'));
});

test('setActive: null group does not throw', () => {
  const view = new IndicatorLegendView({ document: fakeDoc() });
  assert.doesNotThrow(() => view.setActive(null, null));
});
