// chart_template_dialogs.js（保存／管理モーダル DOM アダプター）の Red テスト。
//
// 設計入力（唯一の仕様源）: .doc/indicator-management-ui/基本設計_チャートテンプレート.md v0.1.1
//   §6.2（「現在の構成を保存…」ダイアログ＝名前入力（初期値＝空）・保存対象の指標一覧
//        （読み取り専用プレビュー・件数と指標名）・「この時間足（例：日）に紐付ける」チェック
//        （既定 ON）／「管理」ダイアログ＝一覧＋改名・削除（削除は確認 1 段））、
//   §5.1 例外・F-T1（名前が空／40 文字超／上限は保存せずダイアログ内にインライン表示）、
//   §5.5（改名は検証して更新・削除は確認を 1 段挟む）、
//   §7.1（controller を知らない＝DIP。判定・永続化は注入コールバックへ委譲）。
// 参照実装（同型元）: js/adapter/front/properties_dialog.js（open で DOM 構築 → body へ追加、
//   close で parentNode から除去。document 注入・純 DOM）。
// 構造: Arrange-Act-Assert（AAA）。jsdom を避けた最小 DOM スタブ。

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { ChartTemplateDialogs } from '../js/adapter/front/chart_template_dialogs.js';

// ---- 最小 DOM スタブ（新規依存を追加しない・C-2）--------------------------------
class El {
  constructor(tag = 'div') {
    this.tag = tag;
    this.children = [];
    this.dataset = {};
    this.textContent = '';
    this.type = '';
    this.id = '';
    this.title = '';
    this.value = '';
    this.checked = false;
    this.disabled = false;
    this.placeholder = '';
    this.readOnly = false;
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
    if (v === '') {
      for (const k of this.children) { k.parentNode = null; }
      this.children = [];
    }
  }

  append(...kids) {
    for (const k of kids) {
      if (k && typeof k === 'object') { k.parentNode = this; this.children.push(k); }
    }
  }

  appendChild(k) { this.append(k); return k; }

  removeChild(k) {
    this.children = this.children.filter((c) => c !== k);
    if (k) { k.parentNode = null; }
    return k;
  }

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
  for (const kid of el.children ?? []) {
    out.push(kid);
    flatten(kid, out);
  }
  return out;
}

function textOf(el) {
  return [el, ...flatten(el)].map((e) => e.textContent ?? '').join(' ');
}

// data-tpl-* 属性（dataset）で要素を引く。
function byData(root, key, value) {
  return flatten(root).find((e) => e.dataset && e.dataset[key] === value) ?? null;
}

const TEMPLATES = [
  { templateId: 'tpl#1', name: 'スイング', instances: [], createdAt: 1, updatedAt: 1 },
  { templateId: 'tpl#2', name: 'デイトレ', instances: [], createdAt: 1, updatedAt: 1 },
];

// ---------------------------------------------------------------------------
// 保存ダイアログ（§6.2・UC-T01）
// ---------------------------------------------------------------------------

test('TC-D01 保存ダイアログ: 名前入力の初期値は空・紐付けチェックは既定 ON・指標プレビューを表示する（§6.2）', () => {
  // Arrange
  const doc = fakeDoc();
  const dialogs = new ChartTemplateDialogs({ document: doc });
  // Act
  dialogs.openSave({ timeframeLabel: '日', indicatorNames: ['BTLM', 'MAROD'], onSubmit: () => ({ ok: true }) });
  // Assert
  assert.equal(doc.body.children.length, 1, 'body へダイアログを 1 個だけ追加する');
  const root = doc.body.children[0];
  const name = byData(root, 'tplField', 'name');
  assert.ok(name, '名前入力が在席する');
  assert.equal(name.value, '', '名前の初期値は空（§6.2）');
  const bind = byData(root, 'tplField', 'bind');
  assert.ok(bind, '紐付けチェックが在席する');
  assert.equal(bind.checked, true, '「この時間足に紐付ける」は既定 ON（§6.2）');
  const text = textOf(root);
  assert.ok(text.includes('日'), `紐付け先の時間足を文言に含む（実際: ${text}）`);
  assert.ok(text.includes('BTLM') && text.includes('MAROD'), '保存対象の指標名を読み取り専用で表示する');
  assert.ok(text.includes('2'), '保存対象の件数を表示する');
});

test('TC-D02 保存ダイアログ: 保存クリックで入力値を onSubmit へ渡し、ok なら閉じる（UC-T01）', () => {
  // Arrange
  const doc = fakeDoc();
  const seen = [];
  const dialogs = new ChartTemplateDialogs({ document: doc });
  dialogs.openSave({
    timeframeLabel: '日',
    indicatorNames: ['BTLM'],
    onSubmit: (arg) => { seen.push(arg); return { ok: true }; },
  });
  const root = doc.body.children[0];
  byData(root, 'tplField', 'name').value = '  スイング  ';
  byData(root, 'tplField', 'bind').checked = false;
  // Act
  byData(root, 'tplAction', 'submit').fire('click');
  // Assert
  assert.deepEqual(seen, [{ name: '  スイング  ', bindCurrentTimeframe: false }], '入力値をそのまま渡す（検証は usecase 側）');
  assert.equal(doc.body.children.length, 0, '成功時は閉じる');
});

test('TC-D03 保存ダイアログ: onSubmit が ok:false ならダイアログ内にインライン表示し閉じない（F-T1）', () => {
  // Arrange
  const doc = fakeDoc();
  const dialogs = new ChartTemplateDialogs({ document: doc });
  dialogs.openSave({ timeframeLabel: '日', indicatorNames: [], onSubmit: () => ({ ok: false, code: 'empty' }) });
  const root = doc.body.children[0];
  // Act
  byData(root, 'tplAction', 'submit').fire('click');
  // Assert
  assert.equal(doc.body.children.length, 1, '失敗時は閉じない（既存データは不変・F-T1）');
  const error = byData(root, 'tplError', 'save');
  assert.ok(error && error.textContent.length > 0, 'ダイアログ内にインラインでエラーを表示する（F-T1）');
});

test('TC-D04 保存ダイアログ: キャンセル／× は onSubmit を呼ばずに閉じる（§6.2）', () => {
  // Arrange
  const doc = fakeDoc();
  let called = 0;
  const dialogs = new ChartTemplateDialogs({ document: doc });
  dialogs.openSave({ timeframeLabel: '日', indicatorNames: [], onSubmit: () => { called += 1; return { ok: true }; } });
  const root = doc.body.children[0];
  // Act
  byData(root, 'tplAction', 'cancel').fire('click');
  // Assert
  assert.equal(called, 0, 'キャンセルでは保存しない');
  assert.equal(doc.body.children.length, 0, '閉じる');
});

// ---------------------------------------------------------------------------
// 管理ダイアログ（§6.2・UC-T05）
// ---------------------------------------------------------------------------

test('TC-D05 管理ダイアログ: テンプレートを宣言順に一覧表示する（§6.2）', () => {
  // Arrange
  const doc = fakeDoc();
  const dialogs = new ChartTemplateDialogs({ document: doc });
  // Act
  dialogs.openManage({ templates: TEMPLATES, onRename: () => ({ ok: true }), onDelete: () => {} });
  // Assert
  const root = doc.body.children[0];
  const rows = flatten(root).filter((e) => e.dataset && e.dataset.tplRow);
  assert.deepEqual(rows.map((r) => r.dataset.tplRow), ['tpl#1', 'tpl#2'], '宣言順に一覧表示する');
  assert.ok(textOf(rows[0]).includes('スイング'), '行にテンプレート名を表示する');
});

test('TC-D06 管理ダイアログ: 改名は onRename へ委譲し、ok:false ならインライン表示して閉じない（§5.5・F-T1）', () => {
  // Arrange
  const doc = fakeDoc();
  const seen = [];
  const dialogs = new ChartTemplateDialogs({ document: doc });
  dialogs.openManage({
    templates: TEMPLATES,
    onRename: (id, name) => { seen.push([id, name]); return { ok: false, code: 'duplicate' }; },
    onDelete: () => {},
  });
  const root = doc.body.children[0];
  // Act: 改名を開始 → 入力 → 確定
  byData(root, 'tplRename', 'tpl#1').fire('click');
  byData(root, 'tplRenameInput', 'tpl#1').value = 'デイトレ';
  byData(root, 'tplRenameCommit', 'tpl#1').fire('click');
  // Assert
  assert.deepEqual(seen, [['tpl#1', 'デイトレ']], '改名は onRename（templateId, name）へ委譲する');
  const error = byData(root, 'tplError', 'manage');
  assert.ok(error && error.textContent.length > 0, '重複はインライン表示する（F-T1）');
  assert.equal(doc.body.children.length, 1, '失敗時は閉じない');
});

test('TC-D07 管理ダイアログ: 削除は確認を 1 段挟む（1 回目のクリックでは削除しない）（§5.5）', () => {
  // Arrange
  const doc = fakeDoc();
  const deleted = [];
  const dialogs = new ChartTemplateDialogs({ document: doc });
  dialogs.openManage({ templates: TEMPLATES, onRename: () => ({ ok: true }), onDelete: (id) => deleted.push(id) });
  const root = doc.body.children[0];
  // Act
  byData(root, 'tplDelete', 'tpl#2').fire('click');
  // Assert
  assert.deepEqual(deleted, [], '1 回目のクリックでは削除しない（確認 1 段）');
  const confirm = byData(root, 'tplDeleteConfirm', 'tpl#2');
  assert.ok(confirm, '確認要素を表示する');
  // Act: 確認
  confirm.fire('click');
  // Assert
  assert.deepEqual(deleted, ['tpl#2'], '確認後に onDelete（templateId）を呼ぶ');
});

test('TC-D08 防御: DOM 不在（document=null）でも open は例外を投げない（properties_dialog と同型）', () => {
  // Arrange
  const dialogs = new ChartTemplateDialogs({ document: null });
  // Act / Assert
  assert.doesNotThrow(() => dialogs.openSave({ onSubmit: () => ({ ok: true }) }));
  assert.doesNotThrow(() => dialogs.openManage({ templates: TEMPLATES }));
  assert.doesNotThrow(() => dialogs.close());
});
