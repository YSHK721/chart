// 同名保存時の上書き確認（ユーザー指示 2026-07-28・承認済み）の検証。
//
// 追加仕様: 「同じ名前で保存した場合は上書き。上書きの場合は確認画面を表示する。」
//   - 保存押下時に正規化名（trim ＋小文字化）が既存テンプレートと一致するかを判定する。
//   - 一致しない場合は現行どおり即保存（挙動不変）。
//   - 一致する場合は保存せず確認 1 段へ入る（§5.5 削除の確認イディオムと同型）。
//   - 確認状態で名前を編集したら確認は解除される。
//   - 改名（UC-T05）の重複は現行どおり拒否（F-T1）＝本仕様の対象外。
// 重複判定は usecase の純関数で行う（ダイアログ側で文字列比較を再実装しない）。
// 構造: Arrange-Act-Assert（AAA）。

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { findTemplateByName, saveTemplate } from '../js/usecase/chart_templates.js';
import { ChartTemplateDialogs } from '../js/adapter/front/chart_template_dialogs.js';

// ---- 最小 DOM スタブ（chart_template_dialogs.test.js と同作法）--------------------
class El {
  constructor(tag = 'div') {
    this.tag = tag;
    this.children = [];
    this.dataset = {};
    this.textContent = '';
    this.type = '';
    this.value = '';
    this.checked = false;
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
    if (v === '') { for (const k of this.children) { k.parentNode = null; } this.children = []; }
  }

  append(...kids) { for (const k of kids) { if (k && typeof k === 'object') { k.parentNode = this; this.children.push(k); } } }

  appendChild(k) { this.append(k); return k; }

  removeChild(k) { this.children = this.children.filter((c) => c !== k); if (k) { k.parentNode = null; } return k; }

  setAttribute(k, v) { this.dataset[`attr_${k}`] = v; }

  focus() {}

  addEventListener(ev, fn) { (this._handlers[ev] ??= []).push(fn); }

  fire(ev, arg = {}) { for (const fn of this._handlers[ev] ?? []) { fn(arg); } }
}

function fakeDoc() {
  const body = new El('body');
  return { body, createElement: (t) => new El(t) };
}

function flatten(el, out = []) {
  for (const kid of el.children ?? []) { out.push(kid); flatten(kid, out); }
  return out;
}

function byData(root, key, value) {
  return flatten(root).find((e) => e.dataset && e.dataset[key] === value) ?? null;
}

const TEMPLATES = [
  { templateId: 'tpl#1', name: 'スイング', instances: [], createdAt: 1000, updatedAt: 1000 },
  { templateId: 'tpl#2', name: 'デイトレ', instances: [], createdAt: 1000, updatedAt: 1000 },
];

// 保存ダイアログを開き、既存テンプレート解決を注入する（controller を import しない＝DIP）。
function openSave({ templates = TEMPLATES, onSubmit = () => ({ ok: true }) } = {}) {
  const doc = fakeDoc();
  const dialogs = new ChartTemplateDialogs({ document: doc });
  dialogs.openSave({
    timeframeLabel: '日',
    indicatorNames: ['MAROD'],
    // 重複判定は usecase の純関数へ委譲する（ダイアログは文字列比較を持たない）。
    findExisting: (name) => findTemplateByName({ templates, name }),
    onSubmit,
  });
  const root = doc.body.children[0];
  return {
    doc,
    root,
    name: byData(root, 'tplField', 'name'),
    bind: byData(root, 'tplField', 'bind'),
    submit: byData(root, 'tplAction', 'submit'),
    error: byData(root, 'tplError', 'save'),
  };
}

// ---------------------------------------------------------------------------
// usecase: 正規化名一致の判定（純関数）
// ---------------------------------------------------------------------------

test('TC-O01 usecase: 正規化名一致は大文字小文字・前後空白の差を吸収して既存を返す', () => {
  // Arrange
  const templates = [{ templateId: 'tpl#9', name: 'Swing', instances: [], createdAt: 1, updatedAt: 1 }];
  // Act / Assert
  assert.equal(findTemplateByName({ templates, name: '  SWING  ' }).templateId, 'tpl#9', '空白と大小文字の差を吸収する');
  assert.equal(findTemplateByName({ templates, name: 'swing' }).templateId, 'tpl#9');
  assert.equal(findTemplateByName({ templates, name: 'デイトレ' }), null, '不一致は null');
});

test('TC-O02 usecase: 空名（trim 後 0 文字）は一致なし＝確認に入らない（F-T1 の検証へ委ねる）', () => {
  // Arrange / Act / Assert
  assert.equal(findTemplateByName({ templates: TEMPLATES, name: '   ' }), null);
  assert.equal(findTemplateByName({ templates: TEMPLATES, name: '' }), null);
});

test('TC-O03 usecase: saveTemplate の上書き判定は findTemplateByName と同一の一致規則である', () => {
  // Arrange: 正規化名が一致する入力で保存する
  const res = saveTemplate({ templates: TEMPLATES, lastSeq: 2, name: '  デイトレ  ', applied: [], now: 3000 });
  // Assert: 上書き（新規採番しない）
  assert.equal(res.templateId, findTemplateByName({ templates: TEMPLATES, name: 'デイトレ' }).templateId);
  assert.equal(res.templates.length, 2, '件数は増えない＝上書き');
  assert.equal(res.lastSeq, 2, '上書きでは採番しない');
});

// ---------------------------------------------------------------------------
// dialogs: 確認 1 段（同名時のみ）
// ---------------------------------------------------------------------------

test('TC-O04 保存: 名前が既存と一致しない場合は 1 回目の押下で即保存する（既存挙動の回帰）', () => {
  // Arrange
  const calls = [];
  const ui = openSave({ onSubmit: (a) => { calls.push(a); return { ok: true }; } });
  ui.name.value = '新しい構成';
  // Act
  ui.submit.fire('click');
  // Assert
  assert.equal(calls.length, 1, '確認を挟まず保存する');
  assert.equal(ui.doc.body.children.length, 0, '成功時は閉じる');
});

test('TC-O05 保存: 名前が既存と一致する場合、1 回目の押下では保存せず確認状態に入る（上書き確認）', () => {
  // Arrange
  const calls = [];
  const ui = openSave({ onSubmit: (a) => { calls.push(a); return { ok: true }; } });
  ui.name.value = '  でいとれ  '; // 正規化名の一致は起きない（別名）
  ui.name.value = '  デイトレ  '; // 正規化名が tpl#2 と一致
  // Act
  ui.submit.fire('click');
  // Assert
  assert.deepEqual(calls, [], '1 回目の押下では保存しない');
  assert.equal(ui.doc.body.children.length, 1, 'ダイアログは開いたまま');
  assert.equal(ui.submit.textContent, '上書きする', 'ボタンのラベルを「上書きする」に変える（削除の確認と同型）');
  assert.ok(ui.error.textContent.includes('デイトレ'), `対象名をインライン表示する（実際: ${ui.error.textContent}）`);
  assert.equal(ui.error.classList.contains('is-confirm'), true, 'エラーではないため確認用の表示にする（配色をエラー色にしない）');
});

test('TC-O06 保存: 確認状態で「上書きする」を押すと保存が実行される', () => {
  // Arrange
  const calls = [];
  const ui = openSave({ onSubmit: (a) => { calls.push(a); return { ok: true }; } });
  ui.name.value = 'デイトレ';
  ui.submit.fire('click'); // 1 回目＝確認へ
  // Act
  ui.submit.fire('click'); // 2 回目＝上書き実行
  // Assert
  assert.deepEqual(calls, [{ name: 'デイトレ', bindCurrentTimeframe: true }], '入力値をそのまま渡して保存する');
  assert.equal(ui.doc.body.children.length, 0, '成功時は閉じる');
});

test('TC-O07 保存: 確認状態で名前を編集すると確認が解除され、ボタンは「保存」へ戻る', () => {
  // Arrange
  const calls = [];
  const ui = openSave({ onSubmit: (a) => { calls.push(a); return { ok: true }; } });
  ui.name.value = 'デイトレ';
  ui.submit.fire('click'); // 確認状態
  // Act: 名前を編集する
  ui.name.value = 'デイトレ2';
  ui.name.fire('input');
  // Assert
  assert.equal(ui.submit.textContent, '保存', 'ラベルが「保存」へ戻る');
  assert.equal(ui.error.textContent, '', '確認メッセージが消える');
  assert.equal(ui.error.classList.contains('is-confirm'), false, '確認表示も解除される');
  // Act: 編集後の名前は既存と一致しないため 1 回の押下で保存される
  ui.submit.fire('click');
  assert.deepEqual(calls, [{ name: 'デイトレ2', bindCurrentTimeframe: true }]);
});

test('TC-O08 保存: 確認解除後に再び既存名へ戻すと、改めて確認 1 段に入る', () => {
  // Arrange
  const calls = [];
  const ui = openSave({ onSubmit: (a) => { calls.push(a); return { ok: true }; } });
  ui.name.value = 'デイトレ';
  ui.submit.fire('click');      // 確認状態
  ui.name.value = 'スイング';    // 別の既存名へ編集
  ui.name.fire('input');        // 確認解除
  // Act
  ui.submit.fire('click');      // 別テンプレートとの一致 → 再度確認
  // Assert
  assert.deepEqual(calls, [], '編集後の名前でも確認を挟む');
  assert.equal(ui.submit.textContent, '上書きする');
  assert.ok(ui.error.textContent.includes('スイング'), '確認対象は編集後の名前のテンプレート');
});

test('TC-O09 保存: findExisting 未注入でも従来どおり動作する（後方互換・防御）', () => {
  // Arrange: findExisting を渡さない（既存の呼び出し形）
  const doc = fakeDoc();
  const calls = [];
  const dialogs = new ChartTemplateDialogs({ document: doc });
  dialogs.openSave({ timeframeLabel: '日', indicatorNames: [], onSubmit: (a) => { calls.push(a); return { ok: true }; } });
  const root = doc.body.children[0];
  byData(root, 'tplField', 'name').value = 'デイトレ';
  // Act
  byData(root, 'tplAction', 'submit').fire('click');
  // Assert
  assert.equal(calls.length, 1, '判定器が無ければ確認を挟まない（従来挙動）');
});
