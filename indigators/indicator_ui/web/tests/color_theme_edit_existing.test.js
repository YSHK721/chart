// color_theme_edit_existing.test.js — 保存済みテーマの**編集**（依頼者指示 2026-08-09）。
//
// 検出された欠陥: 編集ダイアログは常に「テーマ名は空・14 行すべて未指定」で開く。既存テーマの色を
//   1 色だけ直したくても、名前と 14 行を再入力するしかない。しかも同名で保存すると §5.1 処理 2 に
//   より roleColors が**丸ごと置換**されるため、入力しなかったトークンは消える。
//   結果として「一度確定したテーマは変更できない」状態になっていた。
//
// 直し方の要（症状を避けるのではなく原因を除去する）: ダイアログは「新規作成」専用ではなく
//   **既存テーマを初期値として受け取れる**器にする。初期値を渡さなければ従来どおり新規作成。
//   置換の規則（§5.1 処理 2）は変えない — 変えるべきは「置換前の値を編集の出発点にできること」。

import test from 'node:test';
import assert from 'node:assert/strict';

import { ColorThemeDialogs } from '../js/adapter/front/color_theme_dialogs.js';
import { COLOR_ROLES } from '../js/domain/color_roles.js';

// 最小の要素スタブ（DOM 非依存で構造だけを見る）。
function el(tag) {
  const node = {
    tagName: String(tag).toUpperCase(),
    children: [],
    dataset: {},
    classList: { add() {}, remove() {}, toggle() {}, contains: () => false },
    style: {},
    _listeners: {},
    set className(v) { this._class = v; },
    get className() { return this._class ?? ''; },
    append(...cs) { cs.forEach((c) => c && node.children.push(c)); },
    appendChild(c) { node.children.push(c); return c; },
    addEventListener(t, fn) { (node._listeners[t] ||= []).push(fn); },
    removeEventListener() {},
    setAttribute(k, v) { node[k] = v; },
    removeAttribute() {},
    getAttribute: (k) => node[k] ?? null,
    remove() {},
    focus() {},
    querySelector: () => null,
    querySelectorAll: () => [],
    contains: () => false,
  };
  return node;
}

function makeDoc() {
  const body = el('body');
  return {
    body,
    documentElement: el('html'),
    createElement: (t) => el(t),
    getElementById: () => null,
    querySelector: () => null,
    querySelectorAll: () => [],
    addEventListener() {},
    removeEventListener() {},
  };
}

// 生成済みツリーから条件に合う要素を集める。
function collect(node, pred, out = []) {
  if (!node || typeof node !== 'object') return out;
  if (pred(node)) out.push(node);
  for (const c of node.children ?? []) collect(c, pred, out);
  return out;
}

const EXISTING = Object.freeze({
  themeId: 'thm#7',
  name: '既存テーマ',
  roleColors: Object.freeze({ bullish: '#00ff00', surface: '#101010' }),
  tfModifier: null,
  createdAt: 1,
  updatedAt: 1,
});

function openEditWith(opts) {
  const doc = makeDoc();
  const dialogs = new ColorThemeDialogs({ document: doc });
  const root = dialogs.openEdit(opts);
  return { doc, dialogs, root: root ?? doc.body };
}

test('編集: 既存テーマを渡すとテーマ名が初期値として入る', () => {
  const { root } = openEditWith({ theme: EXISTING });
  const nameInput = collect(root, (n) => n.dataset && n.dataset.themeField === 'name')[0];
  assert.ok(nameInput, 'テーマ名入力が無い');
  assert.equal(nameInput.value, '既存テーマ', '既存の名前が初期表示されない＝毎回打ち直しになる');
});

test('編集: 宣言済みトークンだけがトグル ON・色つきで初期表示される', () => {
  const { root } = openEditWith({ theme: EXISTING });
  const toggles = collect(root, (n) => n.dataset && n.dataset.themeUse !== undefined);
  assert.equal(toggles.length, COLOR_ROLES.length, `トグルが 14 個でない（${toggles.length}）`);
  const byToken = Object.fromEntries(toggles.map((t) => [t.dataset.themeUse, t]));
  assert.equal(byToken.bullish.checked, true, '宣言済み bullish が ON でない');
  assert.equal(byToken.surface.checked, true, '宣言済み surface が ON でない');
  assert.equal(byToken.bearish.checked, false, '未宣言 bearish が ON になっている');
});

test('編集: 宣言済みトークンの色が初期値として入る（1 色だけ直せる）', () => {
  const { root } = openEditWith({ theme: EXISTING });
  const swatches = collect(root, (n) => n.type === 'color' && n.dataset && n.dataset.themeToken);
  const byToken = Object.fromEntries(swatches.map((s) => [s.dataset.themeToken, s]));
  assert.equal(byToken.bullish.value, '#00ff00');
  assert.equal(byToken.surface.value, '#101010');
});

test('編集: そのまま保存すると既存の宣言がすべて保たれる（置換で消えない）', () => {
  let submitted = null;
  const { root } = openEditWith({
    theme: EXISTING,
    onSubmit: (payload) => { submitted = payload; return { ok: true }; },
  });
  const save = collect(root, (n) => n.dataset && n.dataset.themeAction === 'submit')[0];
  assert.ok(save, '保存ボタンが無い');
  (save._listeners.click ?? []).forEach((fn) => fn({ preventDefault() {} }));
  assert.ok(submitted, 'onSubmit が呼ばれていない');
  assert.equal(submitted.name, '既存テーマ');
  assert.deepEqual(submitted.roleColors, { bullish: '#00ff00', surface: '#101010' },
    '編集せず保存しただけで宣言が失われてはならない');
});

test('編集: themeId を渡した保存要求には themeId が載る（新規採番でなく更新にする）', () => {
  let submitted = null;
  const { root } = openEditWith({
    theme: EXISTING,
    onSubmit: (payload) => { submitted = payload; return { ok: true }; },
  });
  const save = collect(root, (n) => n.dataset && n.dataset.themeAction === 'submit')[0];
  (save._listeners.click ?? []).forEach((fn) => fn({ preventDefault() {} }));
  assert.equal(submitted.themeId, 'thm#7', '編集対象の themeId が保存要求に載らない');
});

test('新規作成: theme を渡さなければ従来どおり空・全 14 行未指定で開く（後方互換）', () => {
  const { root } = openEditWith({});
  const nameInput = collect(root, (n) => n.dataset && n.dataset.themeField === 'name')[0];
  assert.equal(nameInput.value, '');
  const toggles = collect(root, (n) => n.dataset && n.dataset.themeUse !== undefined);
  assert.equal(toggles.length, COLOR_ROLES.length);
  assert.equal(toggles.every((t) => !t.checked), true, '新規作成で ON のトグルがある');
});
