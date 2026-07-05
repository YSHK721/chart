// indicator_controller.js × indicator_legend_view.js の結線検証（ISSUE-038 回帰ガード）。
//
// 目的: 凡例/ダイアログの純 DOM 構築を IndicatorLegendView へ移設した後も、controller が
//   正しい view-model（label / MP 判定に基づく eye/close 分岐 / favorite / pick）を組み立て、
//   View 経由で従来と同一の DOM・イベント挙動を再現することを固定する。
//   純 View 単体は indicator_legend_view.test.js が担当。本ファイルは controller→View の結線を検証する。
// 構造: Arrange-Act-Assert（AAA）。実 DOM 非依存（fake document を注入）。

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { IndicatorController } from '../js/adapter/front/indicator_controller.js';
import { get } from '../js/usecase/catalog.js';

function fakeElement(tagName = 'div') {
  const el = {
    tagName,
    className: '',
    title: '',
    textContent: '',
    disabled: false,
    style: {},
    children: [],
    dataset: {},
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
    click(ev = {}) { for (const fn of this._listeners.click ?? []) { fn(ev); } },
  };
  return el;
}

function fakeDoc() {
  const byId = new Map();
  const doc = {
    _byId: byId,
    createElement(tag) { return fakeElement(tag); },
    getElementById(id) { return byId.get(id) ?? fakeElement('div'); },
    querySelectorAll() { return []; },
  };
  // bind() が参照する主要要素を登録（id 一致で解決させる）。
  for (const id of ['legend', 'indicator-list', 'indicator-dialog', 'indicator-open-btn',
    'indicator-dialog-close', 'indicator-search']) {
    byId.set(id, fakeElement('div'));
  }
  return doc;
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

function spyRenderer() {
  const calls = [];
  const rec = (name) => (...args) => calls.push({ name, args });
  return {
    calls,
    renderLine: rec('renderLine'),
    renderHistogram: rec('renderHistogram'),
    renderHorizontal: rec('renderHorizontal'),
    updateSeriesTail: rec('updateSeriesTail'),
    setData: rec('setData'),
    setVisible: rec('setVisible'),
    remove: rec('remove'),
    setCandles: rec('setCandles'),
  };
}

function makeController({ doc, renderer, marketProfile = null }) {
  const noop = () => {};
  return new IndicatorController({
    catalog: { get },
    compute: { compute: async () => ({ ok: true, generation: 0, series: [] }) },
    persistence: {
      loadApplied: () => [], saveApplied: noop,
      loadFavorites: () => [], saveFavorites: noop,
      loadUiState: () => ({}), saveUiState: noop, nextSeq: () => 1,
    },
    renderer,
    document: doc,
    marketProfile,
  });
}

// ===========================================================================
// 通常指標: legend 行の label と close→renderer.remove（非 MP 経路）
// ===========================================================================

test('legend renders normal indicator label and close routes to renderer.remove', async () => {
  // Arrange
  const doc = fakeDoc();
  const renderer = spyRenderer();
  const ctrl = makeController({ doc, renderer });
  ctrl.bind();
  // Act: 通常指標を適用 → 凡例に 1 行。
  await ctrl.applyIndicator('moving_averages', 'default');
  const legend = doc.getElementById('legend');
  const rows = findByClass(legend, 'legend-row');
  // Assert: 行が 1 つ、label は表示名（displayNameKey 末尾）。
  assert.equal(rows.length, 1);
  assert.equal(findByClass(legend, 'legend-label')[0].textContent, 'moving_averages');
  // close クリックは非 MP 経路 = renderer.remove を呼ぶ。
  findByClass(legend, 'legend-remove')[0].click();
  assert.ok(renderer.calls.some((c) => c.name === 'remove'), 'close should route to renderer.remove for a normal indicator');
});

test('legend eye click on normal indicator routes to renderer.setVisible', async () => {
  const doc = fakeDoc();
  const renderer = spyRenderer();
  const ctrl = makeController({ doc, renderer });
  ctrl.bind();
  await ctrl.applyIndicator('moving_averages', 'default');
  const legend = doc.getElementById('legend');
  findByClass(legend, 'legend-eye')[0].click();
  assert.ok(renderer.calls.some((c) => c.name === 'setVisible'), 'eye should route to renderer.setVisible for a normal indicator');
});

// ===========================================================================
// Market Profile: eye/close は MP 専用ハンドラ（actor）へ分岐し renderer には触れない
// ===========================================================================

test('legend close on market_profile routes to actor.setEnabled(false), not renderer.remove', async () => {
  // Arrange: MP アクター stub。
  const events = [];
  const marketProfile = {
    setParams(p) { events.push(['setParams', p]); },
    async setEnabled(v) { events.push(['setEnabled', v]); },
    detach() { events.push(['detach']); },
  };
  const doc = fakeDoc();
  const renderer = spyRenderer();
  const ctrl = makeController({ doc, renderer, marketProfile });
  ctrl.bind();
  // Act
  await ctrl.applyIndicator('market_profile', 'default');
  const legend = doc.getElementById('legend');
  findByClass(legend, 'legend-remove')[0].click();
  await Promise.resolve();
  // Assert: MP 経路 = actor.setEnabled(false)、renderer.remove は呼ばれない。
  assert.ok(events.some(([n, v]) => n === 'setEnabled' && v === false), 'MP close must disable the actor');
  assert.ok(!renderer.calls.some((c) => c.name === 'remove'), 'MP close must not call renderer.remove');
});

// ===========================================================================
// ダイアログリスト: favorite 切替と行 pick が controller ハンドラへ結線される
// ===========================================================================

test('dialog list star toggles favorite via persistence, row pick applies indicator', async () => {
  // Arrange
  const saved = [];
  const doc = fakeDoc();
  const renderer = spyRenderer();
  const noop = () => {};
  const ctrl = new IndicatorController({
    catalog: { get },
    compute: { compute: async () => ({ ok: true, generation: 0, series: [] }) },
    persistence: {
      loadApplied: () => [], saveApplied: noop,
      loadFavorites: () => [], saveFavorites: (f) => saved.push([...f]),
      loadUiState: () => ({}), saveUiState: noop, nextSeq: () => 1,
    },
    renderer,
    document: doc,
  });
  ctrl.bind();
  // Act: ダイアログリストを描画。
  ctrl._renderDialogList();
  const list = doc.getElementById('indicator-list');
  const rows = findByClass(list, 'ind-row');
  assert.ok(rows.length > 0, 'dialog list should render at least one indicator row');
  // star クリック（stopPropagation 経由）→ favorite 永続化が呼ばれる。
  const star = findByClass(list, 'ind-fav')[0];
  star.click({ stopPropagation() {} });
  assert.ok(saved.length > 0, 'toggling favorite should persist favorites');
  // 行 pick → 指標適用で凡例に 1 行増える。
  const legendBefore = findByClass(doc.getElementById('legend'), 'legend-row').length;
  findByClass(list, 'ind-row')[0].click();
  await new Promise((r) => setTimeout(r, 0));
  const legendAfter = findByClass(doc.getElementById('legend'), 'legend-row').length;
  assert.ok(legendAfter > legendBefore, 'picking a row should apply the indicator and add a legend row');
});
