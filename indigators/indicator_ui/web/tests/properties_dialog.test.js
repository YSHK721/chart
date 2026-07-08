// properties_dialog.js の純ヘルパ検証（node:test / node:assert）。
//
// 対象: adapter/front/properties_dialog.js のうち DOM 非依存な純関数 toHex のみ。
//   ダイアログ DOM 本体は jsdom 等の新規依存が必要（C-2 で禁止）のため E2E（xvfb）で
//   カバーし、ここでは node:test で検証可能な純ロジックに限定する（設計 §10.4）。
// 構造: Arrange-Act-Assert（AAA）。各テスト独立・再現可能（F.I.R.S.T）。

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { toHex, PropertiesDialog } from '../js/adapter/front/properties_dialog.js';
import { get } from '../js/usecase/catalog.js';

// 最小 DOM スタブ（jsdom 等の新規依存を避ける・C-2）。_buildAMethodNote は createElement
//   と className/dataset/textContent のみを使うため、これらを備えた要素を返せば足りる。
function fakeDoc() {
  return {
    createElement() {
      return { className: '', dataset: {}, textContent: '' };
    },
  };
}

const MIN_DEF = { id: 'tgp_btlm', displayNameKey: 'ind.tgp_btlm', params: [], series: [], compute: { variants: ['default'] } };

test('toHex: passes through a 6-digit hex unchanged (lowercased)', () => {
  assert.equal(toHex('#2E9E5B'), '#2e9e5b');
});

test('toHex: expands a 3-digit hex to 6 digits', () => {
  assert.equal(toHex('#0a0'), '#00aa00');
});

test('toHex: converts rgba() to #rrggbb dropping alpha (bull_color)', () => {
  // price_range_power bull_color 既定（lwc_chart.py:27）
  assert.equal(toHex('rgba(46, 158, 91, 0.9)'), '#2e9e5b');
});

test('toHex: converts rgb() to #rrggbb', () => {
  assert.equal(toHex('rgb(210, 67, 58)'), '#d2433a');
});

test('toHex: clamps out-of-range channel values into [0,255]', () => {
  assert.equal(toHex('rgb(300, -5, 128)'), '#ff0080');
});

test('toHex: returns safe default for unparseable input', () => {
  assert.equal(toHex('not-a-color'), '#2962ff');
  assert.equal(toHex(null), '#2962ff');
  assert.equal(toHex(42), '#2962ff');
});

// A 方式注記の出し分け（§9.3・H-1）: B 方式（served）では実反映されるため注記を出さない。
test('_buildAMethodNote returns a note element in A-mode (file://) with the a-method marker', () => {
  // Arrange
  const dialog = new PropertiesDialog({ document: fakeDoc(), def: MIN_DEF, instance: null, mode: 'a' });
  // Act
  const note = dialog._buildAMethodNote();
  // Assert
  assert.ok(note);
  assert.equal(note.className, 'prop-a-method-note');
  assert.equal(note.dataset.aMethodNote, '1');
});

test('_buildAMethodNote returns null in B-mode (served) so the A-method note is hidden', () => {
  // Arrange
  const dialog = new PropertiesDialog({ document: fakeDoc(), def: MIN_DEF, instance: null, mode: 'b' });
  // Act
  const note = dialog._buildAMethodNote();
  // Assert
  assert.equal(note, null);
});

test('PropertiesDialog defaults to A-mode when mode is omitted (backward compatible)', () => {
  const dialog = new PropertiesDialog({ document: fakeDoc(), def: MIN_DEF, instance: null });
  assert.ok(dialog._buildAMethodNote());
});

// 新規インスタンス（instance=null）の既定 variant は variants[0]。catalog の
// profit_band は variants=['robust','global'] のため、新規ダイアログ既定が是正版 robust に
// なることを固定する（既定 variant 解決の第2サイト・順序入替の波及を回帰固定）。
test('PropertiesDialog new-instance default variant follows variants[0] (robust for profit_band order)', () => {
  const DEF = { id: 'profit_band', displayNameKey: 'ind.profit_band', params: [], series: [], compute: { variants: ['robust', 'global'] } };
  const dialog = new PropertiesDialog({ document: fakeDoc(), def: DEF, instance: null });
  assert.equal(dialog._variant, 'robust');
});

test('PropertiesDialog existing-instance variant is preserved (global instance stays global)', () => {
  const DEF = { id: 'profit_band', displayNameKey: 'ind.profit_band', params: [], series: [], compute: { variants: ['robust', 'global'] } };
  const dialog = new PropertiesDialog({ document: fakeDoc(), def: DEF, instance: { variant: 'global' } });
  assert.equal(dialog._variant, 'global');
});

// _refreshVisible: conditionalVisible トグルの動的経路（DOM なしのロジック層検証・C-2）。
//   _fieldEls を fake row（style を持つ）で差し替え、range 値に応じて bins 行の display が
//   出没することを固定する（レンジ=自動→bins 表示 / レンジ=25→bins 非表示）。
function fakeFieldEls(names) {
  const map = new Map();
  for (const name of names) {
    map.set(name, {
      row: { style: {}, classList: { toggle() {}, add() {}, remove() {} } },
      control: null,
      error: { textContent: '' },
    });
  }
  return map;
}

test('_refreshVisible: market_profile bins/range rows toggle with resmode (bins→bins可視 / range→range可視)', () => {
  const dialog = new PropertiesDialog({ document: fakeDoc(), def: get('market_profile'), instance: null });
  dialog._fieldEls = fakeFieldEls(['resmode', 'bins', 'va', 'limit', 'src', 'range']);

  // Arrange/Act: 解像度=ビン → bins 表示・range 非表示。
  dialog._values.resmode = 'bins';
  dialog._refreshVisible();
  assert.equal(dialog._fieldEls.get('bins').row.style.display, '');
  assert.equal(dialog._fieldEls.get('range').row.style.display, 'none');

  // Act: 解像度=レンジ → bins 非表示・range 表示。
  dialog._values.resmode = 'range';
  dialog._refreshVisible();
  assert.equal(dialog._fieldEls.get('bins').row.style.display, 'none');
  assert.equal(dialog._fieldEls.get('range').row.style.display, '');
  assert.equal(dialog._fieldEls.get('va').row.style.display, ''); // 他フィールドは非回帰
  assert.equal(dialog._fieldEls.get('resmode').row.style.display, ''); // トグル自体は常時表示
});

// _revalidate: 隠しフィールド（computeVisible=false）の violation を OK 可否から除外する
//   安全分岐（行689付近の filter）の回帰固定。将来この除外 filter を撤去すると
//   「hidden bins 不正でも ok=true」テストが落ちる＝回帰網として機能する（設計 §5・トグル安全化）。
//   _revalidate は els.error.textContent / els.row.classList.add,remove / _okBtn.disabled を触るため
//   それらを備えた fake で差し替える（DOM なしのロジック層検証・C-2）。
function fakeRevalidateEls(names) {
  const map = new Map();
  for (const name of names) {
    map.set(name, {
      row: { classList: { add() {}, remove() {} } },
      control: null,
      error: { textContent: '' },
    });
  }
  return map;
}

test('_revalidate: hidden bins violation does NOT block OK (bins=0 invalid + resmode=range → ok=true)', () => {
  // Arrange: market_profile で bins を min 違反（0）にし、resmode=range で bins を非表示化。
  const dialog = new PropertiesDialog({ document: fakeDoc(), def: get('market_profile'), instance: null });
  dialog._fieldEls = fakeRevalidateEls(['resmode', 'bins', 'va', 'limit', 'src', 'range']);
  dialog._okBtn = { disabled: false };
  dialog._values.bins = 0;
  dialog._values.resmode = 'range';
  // Act
  const ok = dialog._revalidate();
  // Assert: 隠れた bins の violation は OK を阻害しない。
  assert.equal(ok, true);
  assert.equal(dialog._okBtn.disabled, false);
});

test('_revalidate: visible bins violation DOES block OK (bins=0 invalid + resmode=bins → ok=false)', () => {
  // Arrange: 同じ bins 不正でも resmode=bins では bins が表示中 → 阻害する。
  const dialog = new PropertiesDialog({ document: fakeDoc(), def: get('market_profile'), instance: null });
  dialog._fieldEls = fakeRevalidateEls(['resmode', 'bins', 'va', 'limit', 'src', 'range']);
  dialog._okBtn = { disabled: false };
  dialog._values.bins = 0;
  dialog._values.resmode = 'bins';
  // Act
  const ok = dialog._revalidate();
  // Assert: 表示中フィールドの violation は OK を阻害する。
  assert.equal(ok, false);
  assert.equal(dialog._okBtn.disabled, true);
});

test('_revalidate: visible va violation blocks OK regardless of resmode (va=1.5 invalid + resmode=range → ok=false)', () => {
  // Arrange: va は常時表示（conditionalVisible なし）。範囲違反（0<va<1 に反する 1.5）は resmode に依らず阻害。
  const dialog = new PropertiesDialog({ document: fakeDoc(), def: get('market_profile'), instance: null });
  dialog._fieldEls = fakeRevalidateEls(['resmode', 'bins', 'va', 'limit', 'src', 'range']);
  dialog._okBtn = { disabled: false };
  dialog._values.va = 1.5;
  dialog._values.resmode = 'range'; // bins は隠れるが va は表示のまま
  // Act
  const ok = dialog._revalidate();
  // Assert
  assert.equal(ok, false);
  assert.equal(dialog._okBtn.disabled, true);
});

// ---- segmented レンダラ（ENUM を横並びボタン群で描く・解像度トグル移植）------------
// jsdom を避けた最小 DOM スタブ（button + classList + append + click 発火）で検証する（C-2）。
function segFakeDoc() {
  const make = () => ({
    className: '', dataset: {}, textContent: '', type: '', style: {},
    _handlers: {},
    _cls: new Set(),
    classList: {
      _owner: null,
      add(c) { this._owner._cls.add(c); },
      remove(c) { this._owner._cls.delete(c); },
      toggle(c, on) {
        const has = this._owner._cls.has(c);
        const next = on === undefined ? !has : on;
        if (next) this._owner._cls.add(c); else this._owner._cls.delete(c);
      },
      contains(c) { return this._owner._cls.has(c); },
    },
    children: [],
    append(...kids) { this.children.push(...kids); },
    addEventListener(ev, fn) { this._handlers[ev] = fn; },
    click() { if (this._handlers.click) this._handlers.click(); },
  });
  return {
    createElement() {
      const el = make();
      el.classList._owner = el; // classList から自要素の _cls を参照させる
      return el;
    },
  };
}

const RESMODE_FIELD = {
  name: 'resmode',
  enumValues: ['bins', 'range'],
  enumLabels: { bins: 'ビン', range: 'レンジ' },
  value: 'bins',
};

test('_buildSegmented renders one button per enum option with active on current value', () => {
  const dialog = new PropertiesDialog({ document: segFakeDoc(), def: get('market_profile'), instance: null });
  const wrap = dialog._buildSegmented(RESMODE_FIELD);
  // 2 オプション = 2 ボタン。
  assert.equal(wrap.children.length, 2);
  const [binsBtn, rangeBtn] = wrap.children;
  assert.equal(binsBtn.textContent, 'ビン');
  assert.equal(rangeBtn.textContent, 'レンジ');
  // 現在値（bins）のボタンだけ is-active。
  assert.equal(binsBtn.classList.contains('is-active'), true);
  assert.equal(rangeBtn.classList.contains('is-active'), false);
});

test('_buildSegmented click updates _values and fires _onChange (resmode=range → bins 行が消え range 行が出る)', () => {
  const dialog = new PropertiesDialog({ document: segFakeDoc(), def: get('market_profile'), instance: null });
  // _onChange が走ることで _refreshVisible が bins/range 行を出没させる経路を固定する。
  dialog._fieldEls = fakeFieldEls(['resmode', 'bins', 'va', 'limit', 'src', 'range']);
  dialog._okBtn = { disabled: false };

  const wrap = dialog._buildSegmented(RESMODE_FIELD);
  const [binsBtn, rangeBtn] = wrap.children;

  // Act: 「レンジ」ボタンをクリック。
  rangeBtn.click();

  // Assert: 値更新 + アクティブ切替 + _onChange 波及（bins 非表示 / range 表示）。
  assert.equal(dialog._values.resmode, 'range');
  assert.equal(rangeBtn.classList.contains('is-active'), true);
  assert.equal(binsBtn.classList.contains('is-active'), false);
  assert.equal(dialog._fieldEls.get('bins').row.style.display, 'none');
  assert.equal(dialog._fieldEls.get('range').row.style.display, '');
});
