// properties_dialog_composition.test.js — PropertiesDialog の責務分割と計算量ゲート（ISSUE-502 段階 4D）。
//
// 何を固定するか:
//   R1 構造: 「DOM 構築・イベント配線のみ」というファイル冒頭の宣言と実装を一致させる。
//       是正前は 829 行の 1 クラスが DOM 構築・フォーム値所有・検証と OK 制御・永続差分組立・
//       ドラッグ操作の 5 変更軸を持ち、宣言と実装が食い違っていた。ここでは「値・判定・差分・
//       ドラッグの本体が dialog に無い（協働子へ委譲されている）」を実コードで固定する。
//   R2 所有: _values / _variant は状態器（PropertyFormState）が所有する実体を指す
//       （呼び出し面は変えない＝既存の消費者を壊さない）。
//   C1 計算量（Test Spy＝生成回数を数える）: open 1 回で「作った要素 − 組み上がった要素 = 0」。
//       捨てる要素を作らない。分割で「作ってから捨てる」欠陥が入っても状態検証では落ちないため、
//       生成そのものを数える。
//   C2 オーダーの表明: フィールド数を 3 / 6 / 12 と変えても、1 フィールドあたりの要素生成回数と
//       述語評価回数が一定である（回数そのものは焼き込まない）。

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

import { PropertiesDialog } from '../js/adapter/front/properties_dialog.js';

const DIALOG_SRC = readFileSync(
  fileURLToPath(new URL('../js/adapter/front/properties_dialog.js', import.meta.url)),
  'utf8',
);

function stripComments(src) {
  return src
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .replace(/(^|[^:])\/\/[^\n]*/g, '$1');
}

const CODE = stripComments(DIALOG_SRC);

// --------------------------------------------------------------------------- //
// R1 構造 — 5 変更軸のうち 4 つの本体が dialog に無い
// --------------------------------------------------------------------------- //

// 状態器経由（`this._form.xxx(...)`）は委譲そのもの。dialog が規則を**直接**呼ぶ形だけを禁じる。
const RULE_FNS = ['validateForm', 'computeEnabled', 'computeVisible', 'resetToDefaults'];
const directRuleCalls = (src) =>
  RULE_FNS.filter((name) => new RegExp(`(?<!_form\\.)\\b${name}\\s*\\(`).test(src));

test('R1 検証・有効化・表示・既定復元の規則を dialog が自分で呼ばない（状態器へ委譲）', () => {
  const offenders = directRuleCalls(CODE);
  assert.deepEqual(offenders, [],
    `判定規則の呼び出しが dialog に残っている（PropertyFormState へ委譲すること）: ${offenders}`);
});

test('R1 検出器の自己検査: 直接呼び出しは捕捉し、委譲は誤検出しない', () => {
  assert.deepEqual(
    directRuleCalls('const visible = computeVisible(this._def, this._values, ctx);'),
    ['computeVisible'],
  );
  assert.deepEqual(directRuleCalls('const { ok } = this._form.validation();'), []);
  assert.deepEqual(directRuleCalls('this._form.resetToDefaults();'), []);
});

test('R1 ドラッグ操作の本体が dialog に無い（Pointer Events の配線は協働子が持つ）', () => {
  for (const token of ['pointermove', 'pointerup', 'clientX', 'clientY', '_offset', 'transform']) {
    assert.ok(!CODE.includes(token), `ドラッグ実装の断片が dialog に残っている: ${token}`);
  }
});

test('R1 永続差分の組立が dialog に無い（patch の構築は純関数が持つ）', () => {
  assert.ok(!/patch\s*\[/.test(CODE), '差分 patch の組立が dialog に残っている');
  assert.ok(!/\bfields\.(color|width|style|display)\b/.test(CODE), '差分フィールドの組立が dialog に残っている');
});

test('R1 4 つの協働子を import している（走査が空振りしていない）', () => {
  for (const re of [
    /from '\.\.\/\.\.\/usecase\/property_form_state\.js'/,
    /from '\.\.\/\.\.\/usecase\/series_style_forms\.js'/,
    /from '\.\/dialog_drag_controller\.js'/,
    /from '\.\/property_control_builders\.js'/,
  ]) {
    assert.match(DIALOG_SRC, re);
  }
});

test('R2 _values / _variant は状態器が所有する実体を指す（呼び出し面は不変）', () => {
  const def = numericDef(2);
  const dialog = new PropertiesDialog({ document: fakeDoc().doc, def });
  assert.equal(dialog._values, dialog._form.values, '_values が状態器の実体を指していない');
  // 外から直接書き換えても状態器の値として見える（既存テスト・コントロールの流儀）。
  dialog._values.p0 = 99;
  assert.equal(dialog._form.getValue('p0'), 99);
  dialog._variant = 'global';
  assert.equal(dialog._form.variant, 'global');
  // 入れ物ごとの差し替え（デフォルト復元）も状態器へ届く。
  dialog._values = { p0: 1, p1: 2 };
  assert.deepEqual(dialog._form.values, { p0: 1, p1: 2 });
});

// --------------------------------------------------------------------------- //
// 計算量ゲート — 最小 DOM スタブ（生成回数と親子関係を記録する）
// --------------------------------------------------------------------------- //

function fakeDoc() {
  const created = [];
  const makeClassList = (el) => ({
    add() {}, remove() {}, contains() { return false; },
    toggle() {}, _el: el,
  });
  const make = (tag) => {
    const el = {
      tagName: String(tag).toUpperCase(),
      className: '', dataset: {}, textContent: '', title: '', type: '',
      value: '', min: '', max: '', step: '', checked: false, selected: false,
      disabled: false, hidden: false, style: {}, children: [],
      setAttribute() {},
      addEventListener() {},
      querySelector() { return null; },
      querySelectorAll() { return []; },
      append(...kids) {
        for (const k of kids) {
          if (k && typeof k === 'object') this.children.push(k);
        }
      },
    };
    el.classList = makeClassList(el);
    created.push(el);
    return el;
  };
  const body = make('body');
  created.pop(); // body は createElement 由来ではない（発行数に数えない）。
  return {
    created,
    doc: { createElement: make, body },
  };
}

// root から到達できる要素（＝実際に組み上がったもの）を数える。
function attachedCount(root) {
  let n = 0;
  const stack = [root];
  const seen = new Set();
  while (stack.length > 0) {
    const el = stack.pop();
    if (!el || seen.has(el)) continue;
    seen.add(el);
    n += 1;
    for (const c of el.children ?? []) stack.push(c);
  }
  return n;
}

// フィールド n 本（すべて number）＋述語カウンタ付きの def。
function numericDef(n, counters = { visible: 0, enable: 0 }) {
  return {
    id: 'complexity_probe',
    displayNameKey: 'ind.probe',
    series: [],
    compute: { variants: ['default'] },
    counters,
    params: Array.from({ length: n }, (_, i) => ({
      name: `p${i}`, type: 'int', default: 1, constraints: [],
      conditionalVisible: () => { counters.visible += 1; return true; },
      conditionalEnable: () => { counters.enable += 1; return true; },
    })),
  };
}

// フィールド n 本のダイアログを 1 回開き、発行数・組上数・述語評価数を返す。
function openOnce(n) {
  const counters = { visible: 0, enable: 0 };
  const { created, doc } = fakeDoc();
  const dialog = new PropertiesDialog({ document: doc, def: numericDef(n, counters) });
  const root = dialog.open();
  return {
    issued: created.length,
    attached: attachedCount(root),
    visible: counters.visible,
    enable: counters.enable,
  };
}

test('C1 open 1 回で「作った要素 − 組み上がった要素」= 0（捨てる要素を作らない）', () => {
  for (const n of [3, 6, 12]) {
    const m = openOnce(n);
    assert.equal(m.issued - m.attached, 0,
      `フィールド ${n} 本: 発行 ${m.issued} / 組上 ${m.attached}（差 ${m.issued - m.attached}）`);
    assert.ok(m.issued > n, '走査が空振りしている（要素をほとんど作っていない）');
  }
});

test('C1 計算量ゲートの検出力: 作って捨てる変異で赤になる', () => {
  const { created, doc } = fakeDoc();
  const dialog = new PropertiesDialog({ document: doc, def: numericDef(3) });
  const root = dialog.open();
  doc.createElement('div'); // 作ったが誰にも append しない（＝捨てた計算）。
  assert.equal(created.length - attachedCount(root), 1, '捨てた要素を検出できていない');
});

test('C2 オーダーの表明: 1 フィールドあたりの要素生成が一定（3 / 6 / 12 の 3 点）', () => {
  const a = openOnce(3);
  const b = openOnce(6);
  const c = openOnce(12);
  // 回数そのものは焼き込まない。増分から求めた 1 フィールドあたりの生成数で
  //   中間点を予測し、実測と一致することで「フィールド数に線形・再生成なし」を固定する。
  const perField = (c.issued - a.issued) / (12 - 3);
  assert.ok(Number.isInteger(perField) && perField > 0, `1 フィールドあたりの生成が一定でない: ${perField}`);
  assert.equal(b.issued, a.issued + perField * (6 - 3),
    'フィールドを増やすと 1 本あたりの生成が増えている（作り直しが混ざっている）');
});

test('C2 オーダーの表明: 1 フィールドあたりの述語評価が一定（フィールドを増やしても増えない）', () => {
  const points = [3, 6, 12].map((n) => ({ n, ...openOnce(n) }));
  for (const key of ['visible', 'enable']) {
    const perField = points.map((p) => p[key] / p.n);
    assert.ok(perField.every((v) => Number.isInteger(v) && v > 0),
      `${key}: 1 フィールドあたりの評価回数が整数でない（${perField}）`);
    assert.equal(new Set(perField).size, 1,
      `${key}: フィールド数で 1 本あたりの評価回数が変わる（${perField}）＝オーダーが線形でない`);
  }
});
