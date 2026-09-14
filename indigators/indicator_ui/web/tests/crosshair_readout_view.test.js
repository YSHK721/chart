// crosshair_readout_view.js（CrosshairReadoutView）の仕様検証。
//
// 設計入力: クロスヘア価格読み取り欄（左上固定オーバーレイ）— 読み取り DTO を受け取り
//   日時 + 始値/高値/安値/終値 + overlay 各行（系列色付き）を DOM 描画する。
//   usecase/domain を参照しない adapter 層ビュー（_renderLegend と同方針・YAGNI）。
// 構造: Arrange-Act-Assert（AAA）。実 DOM 非依存（fake document を注入）。
//   既存 properties_dialog.test.js / indicator_controller.test.js の fake document 流儀を踏襲する。

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { CrosshairReadoutView } from '../js/adapter/front/crosshair_readout_view.js';

// 最小 DOM スタブ（jsdom 等の新規依存を避ける）。createElement で要素ツリーを構築できる
//   最小限の要素（className / textContent / style / children / append / innerHTML）を備える。
function fakeElement(tagName = 'div') {
  const el = {
    tagName,
    className: '',
    textContent: '',
    style: {},
    children: [],
    get innerHTML() { return this._innerHTML ?? ''; },
    set innerHTML(v) { this._innerHTML = v; if (v === '') { this.children = []; } },
    append(...nodes) { for (const n of nodes) { this.children.push(n); } },
    appendChild(n) { this.children.push(n); return n; },
  };
  return el;
}

function fakeDoc() {
  const created = [];
  const byId = new Map();
  const doc = {
    _created: created,
    _byId: byId,
    createElement(tag) { const el = fakeElement(tag); created.push(el); return el; },
    getElementById(id) { return byId.get(id) ?? null; },
  };
  return doc;
}

// fake document に id 付きルート要素を登録するヘルパ。
function withRoot(doc, id) {
  const root = fakeElement('div');
  doc._byId.set(id, root);
  return root;
}

// 要素ツリーを再帰的に走査して全 textContent を連結する（描画文言の検査用）。
function allText(el) {
  if (!el) return '';
  let s = el.textContent || '';
  for (const c of el.children || []) {
    s += ' ' + allText(c);
  }
  return s;
}

// 要素ツリーを平坦化する（色検査用）。
function flatten(el, acc = []) {
  if (!el) return acc;
  acc.push(el);
  for (const c of el.children || []) {
    flatten(c, acc);
  }
  return acc;
}

const DTO = {
  time: 1277769600,
  ohlc: { open: 1.2, high: 1.6, low: 1.1, close: 1.5 },
  overlays: [
    { name: 'BULL', value: 100, color: '#2e9e5b' },
    { name: 'BEAR', value: 90, color: '#d2433a' },
  ],
};

// ===========================================================================
// render: OHLC を描画する
// ===========================================================================

test('render: writes open/high/low/close values into the readout element', () => {
  // Arrange
  const doc = fakeDoc();
  const root = withRoot(doc, 'crosshair-readout');
  const view = new CrosshairReadoutView({ document: doc, elementId: 'crosshair-readout' });
  // Act
  view.render(DTO);
  // Assert: OHLC の各値が描画文言に含まれる。
  const text = allText(root);
  assert.match(text, /1\.2/);
  assert.match(text, /1\.6/);
  assert.match(text, /1\.1/);
  assert.match(text, /1\.5/);
});

// ===========================================================================
// render: overlay 各行を系列色付きで描画する
// ===========================================================================

test('render: writes each overlay name/value with its series color', () => {
  // Arrange
  const doc = fakeDoc();
  const root = withRoot(doc, 'crosshair-readout');
  const view = new CrosshairReadoutView({ document: doc, elementId: 'crosshair-readout' });
  // Act
  view.render(DTO);
  // Assert: overlay 名・値が描画され、系列色が style.color に反映される。
  const text = allText(root);
  assert.match(text, /BULL/);
  assert.match(text, /100/);
  assert.match(text, /BEAR/);
  const nodes = flatten(root);
  const colored = nodes.filter((n) => n.style && n.style.color);
  const colors = colored.map((n) => n.style.color);
  assert.ok(colors.includes('#2e9e5b'), `expected BULL color #2e9e5b in ${JSON.stringify(colors)}`);
  assert.ok(colors.includes('#d2433a'), `expected BEAR color #d2433a in ${JSON.stringify(colors)}`);
});

test('render: sessionMP があれば当日 MP（POC/VA）行を描く（sessions のクロスヘア読み取り）', () => {
  const doc = fakeDoc();
  const root = withRoot(doc, 'crosshair-readout');
  const view = new CrosshairReadoutView({ document: doc, elementId: 'crosshair-readout' });
  view.render({
    time: 1277769600, ohlc: { open: 100, high: 110, low: 95, close: 108 }, overlays: [],
    sessionMP: { poc: 102, vah: 106, val: 98 },
  });
  const text = allText(root);
  assert.match(text, /POC/);
  assert.match(text, /102/);
  assert.match(text, /VA/);
  assert.match(text, /98/);
  assert.match(text, /106/);
});

test('render: sessionMP 無し（通常モード）は MP 行を描かない', () => {
  const doc = fakeDoc();
  const root = withRoot(doc, 'crosshair-readout');
  const view = new CrosshairReadoutView({ document: doc, elementId: 'crosshair-readout' });
  view.render({ time: 1, ohlc: { open: 1, high: 2, low: 0, close: 1 }, overlays: [] });
  assert.doesNotMatch(allText(root), /POC/);
});

// ===========================================================================
// render: 安全性（null / ohlc null / overlays 空でクラッシュしない・空表示）
// ===========================================================================

test('render(null): clears the element without throwing', () => {
  // Arrange
  const doc = fakeDoc();
  const root = withRoot(doc, 'crosshair-readout');
  const view = new CrosshairReadoutView({ document: doc, elementId: 'crosshair-readout' });
  view.render(DTO);
  // Act
  view.render(null);
  // Assert: クラッシュせず空表示（子要素なし）。
  assert.equal(root.children.length, 0);
});

test('render: ohlc null does not throw and omits OHLC values', () => {
  // Arrange
  const doc = fakeDoc();
  const root = withRoot(doc, 'crosshair-readout');
  const view = new CrosshairReadoutView({ document: doc, elementId: 'crosshair-readout' });
  // Act
  view.render({ time: 1277769600, ohlc: null, overlays: [{ name: 'BULL', value: 100, color: '#2e9e5b' }] });
  // Assert: overlay は出るが OHLC でクラッシュしない。
  const text = allText(root);
  assert.match(text, /BULL/);
});

test('render: empty overlays does not throw and shows only OHLC', () => {
  // Arrange
  const doc = fakeDoc();
  const root = withRoot(doc, 'crosshair-readout');
  const view = new CrosshairReadoutView({ document: doc, elementId: 'crosshair-readout' });
  // Act
  view.render({ time: 1277769600, ohlc: { open: 1.2, high: 1.6, low: 1.1, close: 1.5 }, overlays: [] });
  // Assert: OHLC は描画される（overlay 空でクラッシュしない）。
  const text = allText(root);
  assert.match(text, /1\.2/);
});

// ===========================================================================
// 構築の安全性: 対象要素が無くてもクラッシュしない（後方互換・防御）
// ===========================================================================

test('render: missing target element does not throw (defensive)', () => {
  // Arrange: getElementById が null を返す（要素未登録）。
  const doc = fakeDoc();
  const view = new CrosshairReadoutView({ document: doc, elementId: 'absent' });
  // Act + Assert: 例外を投げない。
  assert.doesNotThrow(() => view.render(DTO));
});
