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

test('_refreshVisible: market_profile period row toggles with src (zp→表示 / dwell→非表示・ISSUE-079 後)', () => {
  const dialog = new PropertiesDialog({ document: fakeDoc(), def: get('market_profile'), instance: null });
  dialog._fieldEls = fakeFieldEls(['dispbp', 'va', 'src', 'period', 'mode']);

  dialog._values.src = 'zp';
  dialog._refreshVisible();
  assert.equal(dialog._fieldEls.get('period').row.style.display, '');

  dialog._values.src = 'dwell';
  dialog._refreshVisible();
  assert.equal(dialog._fieldEls.get('period').row.style.display, 'none');
  assert.equal(dialog._fieldEls.get('dispbp').row.style.display, ''); // 表示幅は常時表示
  assert.equal(dialog._fieldEls.get('va').row.style.display, '');     // 他フィールドは非回帰
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

test('_revalidate: visible dispbp violation DOES block OK (dispbp=0 min 違反 → ok=false・ISSUE-079)', () => {
  const dialog = new PropertiesDialog({ document: fakeDoc(), def: get('market_profile'), instance: null });
  dialog._fieldEls = fakeRevalidateEls(['dispbp', 'va', 'src', 'period', 'mode']);
  dialog._okBtn = { disabled: false };
  dialog._values.dispbp = 0; // MIN_VALUE(1) 違反。
  const ok = dialog._revalidate();
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

// ISSUE-079: resmode は撤去済み。segmented の汎用挙動は合成フィールド、実フィールドは mode で検証する。
const RESMODE_FIELD = {
  name: 'segdemo',
  enumValues: ['a', 'b'],
  enumLabels: { a: 'A', b: 'B' },
  value: 'a',
};
// ISSUE-082: リプレイモード撤去後の mode ENUM は [normal, sessions] の 2 択。
const MODE_FIELD = {
  name: 'mode',
  enumValues: ['normal', 'sessions'],
  enumLabels: { normal: '通常', sessions: '日別プロファイル' },
  value: 'normal',
};

test('_buildSegmented renders one button per enum option with active on current value', () => {
  const dialog = new PropertiesDialog({ document: segFakeDoc(), def: get('market_profile'), instance: null });
  const wrap = dialog._buildSegmented(RESMODE_FIELD);
  // 2 オプション = 2 ボタン（合成フィールド・ISSUE-079 で resmode 実フィールドは撤去）。
  assert.equal(wrap.children.length, 2);
  const [aBtn, bBtn] = wrap.children;
  assert.equal(aBtn.textContent, 'A');
  assert.equal(bBtn.textContent, 'B');
  // 現在値（a）のボタンだけ is-active。
  assert.equal(aBtn.classList.contains('is-active'), true);
  assert.equal(bBtn.classList.contains('is-active'), false);
});

test('_buildSegmented click updates _values and fires _onChange (mode=sessions へ切替・ISSUE-079 後)', () => {
  const dialog = new PropertiesDialog({ document: segFakeDoc(), def: get('market_profile'), instance: null });
  dialog._fieldEls = fakeFieldEls(['dispbp', 'va', 'src', 'period', 'mode']);
  dialog._okBtn = { disabled: false };

  const wrap = dialog._buildSegmented(MODE_FIELD);
  const [normalBtn, sessionsBtn] = wrap.children;

  // Act: 「日別プロファイル」ボタンをクリック。
  sessionsBtn.click();

  // Assert: 値更新 + アクティブ切替。
  assert.equal(dialog._values.mode, 'sessions');
  assert.equal(sessionsBtn.classList.contains('is-active'), true);
  assert.equal(normalBtn.classList.contains('is-active'), false);
});

// ISSUE-080: 選択中の option が無効化されたら最初の有効 option へ自動切替（zp→dwell が可視で跳ぶ）。
test('_refreshEnabled: 無効化された選択中 option は有効な先頭 option へ自動切替（日別×1m の zp→dwell）', () => {
  const dialog = new PropertiesDialog({
    document: segFakeDoc(), def: get('market_profile'), instance: null,
    context: { timeframe: '1m' },
  });
  dialog._fieldEls = fakeFieldEls(['dispbp', 'va', 'src', 'period', 'mode']);
  // 実 select を模す最小 fake（options 配列＋value）。
  const opts = [
    { value: 'dwell', disabled: false },
    { value: 'zp', disabled: false },
  ];
  const sel = { tagName: 'SELECT', options: opts, value: 'zp' };
  dialog._fieldEls.get('src').control = sel;
  dialog._values.src = 'zp';
  dialog._values.mode = 'sessions'; // 日別×1m → zp 無効。
  dialog._refreshEnabled();
  assert.equal(opts[1].disabled, true, 'zp option は無効化');
  assert.equal(dialog._values.src, 'dwell', '選択値は有効な dwell へ自動切替');
  assert.equal(sel.value, 'dwell');
  // 通常へ戻すと zp option は再有効化されるが、値は勝手に戻らない（ユーザー操作を尊重）。
  dialog._values.mode = 'normal';
  dialog._refreshEnabled();
  assert.equal(opts[1].disabled, false);
  assert.equal(dialog._values.src, 'dwell');
});
