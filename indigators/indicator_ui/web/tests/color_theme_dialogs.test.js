// color_theme_dialogs.js（テーマ編集／管理モーダル DOM アダプター）のテスト。
//
// 設計入力（唯一の仕様源）: .doc/indicator-management-ui/基本設計_指標カラーテーマ.md v0.3.1
//   §6.3（テーマ編集ダイアログ＝名前入力／トークン 14 行は `COLOR_ROLES` から生成（手書き配列を
//        持たない・順序は台帳の並び）／各行は色スウォッチ＋現在値表示＋「未指定に戻す」／初期表示は
//        すべて未指定（恒等テーマ）／時間足の明度差は「使う」チェック（OFF＝`tfModifier: null`）＋
//        `TF_CODES` から生成した各足の数値入力（-1.00〜1.00・0.01 刻み）／保存押下時に正規化名が
//        既存と一致したら確認 1 段）、
//   §6.2（管理ダイアログ＝改名・削除。「テーマなし」は固定行であり管理対象ではない）、
//   §5.7 F-C1（検証失敗はダイアログ内インライン表示・既存データは不変）、
//   §7.1（controller を知らない＝DIP。判定・永続化は注入コールバックへ委譲。日本語ラベルの写像は
//        本モジュールが持ち、台帳に無いキーはトークン名をそのまま表示する）。
// 参照実装（同型元）: js/adapter/front/chart_template_dialogs.js ／ tests/chart_template_dialogs.test.js。
// 構造: Arrange-Act-Assert（AAA）。jsdom を避けた最小 DOM スタブ（同型元と同作法）。

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { ColorThemeDialogs, labelForRole } from '../js/adapter/front/color_theme_dialogs.js';
import { COLOR_ROLES } from '../js/domain/color_roles.js';
import { TF_CODES } from '../js/domain/tf_meta.js';

// ---- 最小 DOM スタブ（新規依存を追加しない）--------------------------------
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
    this.min = '';
    this.max = '';
    this.step = '';
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
  for (const kid of el.children ?? []) { out.push(kid); flatten(kid, out); }
  return out;
}

function textOf(el) {
  return [el, ...flatten(el)].map((e) => e.textContent ?? '').join(' ');
}

// data-theme-* 属性（dataset）で要素を引く。
function byData(root, key, value) {
  return flatten(root).find((e) => e.dataset && e.dataset[key] === value) ?? null;
}

function allWith(root, key) {
  return flatten(root).filter((e) => e.dataset && e.dataset[key] !== undefined);
}

const THEMES = [
  {
    themeId: 'thm#1', name: 'ダーク統一', roleColors: {}, tfModifier: null, createdAt: 1, updatedAt: 1,
  },
  {
    themeId: 'thm#2', name: '時間足別', roleColors: {}, tfModifier: {}, createdAt: 1, updatedAt: 1,
  },
];

// 編集ダイアログを開いて root を返す共通 Arrange。
function openEdit(opts = {}) {
  const doc = fakeDoc();
  const dialogs = new ColorThemeDialogs({ document: doc });
  dialogs.openEdit({ onSubmit: () => ({ ok: true }), ...opts });
  return { doc, dialogs, root: doc.body.children[0] };
}

// ---------------------------------------------------------------------------
// トークン 14 行の台帳導出（§6.3・OCP）
// ---------------------------------------------------------------------------

test('TC-CD01 トークン行は COLOR_ROLES から生成する（件数・順序が台帳と一致・手書き配列を持たない）', () => {
  // Arrange / Act
  const { root } = openEdit();
  // Assert
  const rows = allWith(root, 'themeRole');
  assert.deepEqual(
    rows.map((r) => r.dataset.themeRole),
    [...COLOR_ROLES],
    '行の集合と順序が台帳（domain/color_roles.js）と一致する＝台帳へ 1 語足せば行が増える',
  );
  assert.equal(rows.length, 14, '語彙は 14 種（§4.1.1）');
});

test('TC-CD02 各トークン行は 色スウォッチ・現在値表示・「未指定に戻す」を持つ（§6.3）', () => {
  // Arrange / Act
  const { root } = openEdit();
  // Assert
  for (const token of COLOR_ROLES) {
    const swatch = byData(root, 'themeSwatch', token);
    assert.ok(swatch, `${token}: 色スウォッチが在席する`);
    assert.equal(swatch.type, 'color', `${token}: スウォッチは <input type="color">`);
    assert.ok(byData(root, 'themeValue', token), `${token}: 現在値表示が在席する`);
    assert.ok(byData(root, 'themeClear', token), `${token}: 「未指定に戻す」ボタンが在席する`);
  }
});

test('TC-CD03 日本語ラベルの写像は本モジュールが持ち、台帳に無いキーはトークン名をそのまま表示する（§7.1）', () => {
  // Arrange / Act / Assert
  assert.equal(labelForRole('bullish'), '強気・上方向');
  assert.equal(labelForRole('surface'), '面');
  assert.equal(labelForRole('__unknown__'), '__unknown__', '写像に無いキーはトークン名をそのまま表示する');
  for (const token of COLOR_ROLES) {
    assert.ok(labelForRole(token).length > 0, `${token}: ラベルが空でない`);
  }
});

// ---------------------------------------------------------------------------
// 初期表示（§6.3: すべて未指定＝恒等テーマ）
// ---------------------------------------------------------------------------

test('TC-CD04 初期表示はすべて未指定・名前は空（恒等テーマ・§6.3）', () => {
  // Arrange / Act
  const { root } = openEdit();
  // Assert
  assert.equal(byData(root, 'themeField', 'name').value, '', '名前の初期値は空');
  for (const token of COLOR_ROLES) {
    assert.equal(byData(root, 'themeValue', token).textContent, '未指定', `${token}: 初期表示は未指定`);
  }
});

test('TC-CD05 初期状態で保存すると roleColors は空・tfModifier は null（恒等テーマ・§4.4）', () => {
  // Arrange
  const seen = [];
  const { root } = openEdit({ onSubmit: (arg) => { seen.push(arg); return { ok: true }; } });
  byData(root, 'themeField', 'name').value = '恒等';
  // Act
  byData(root, 'themeAction', 'submit').fire('click');
  // Assert
  assert.equal(seen.length, 1);
  assert.deepEqual(seen[0].roleColors, {}, '未指定のトークンは 1 件も載らない');
  assert.equal(seen[0].tfModifier, null, '「使う」OFF は tfModifier: null（§6.3）');
  assert.equal(seen[0].name, '恒等', '名前は入力値をそのまま渡す（検証は usecase 側）');
});

test('TC-CD06 スウォッチを操作したトークンだけが roleColors に載る（§5.1 処理 3）', () => {
  // Arrange
  const seen = [];
  const { root } = openEdit({ onSubmit: (arg) => { seen.push(arg); return { ok: true }; } });
  byData(root, 'themeField', 'name').value = 'T';
  const swatch = byData(root, 'themeSwatch', 'bullish');
  // Act
  swatch.value = '#00ff00';
  swatch.fire('input');
  byData(root, 'themeAction', 'submit').fire('click');
  // Assert
  assert.deepEqual(seen[0].roleColors, { bullish: '#00ff00' }, '操作したトークンのみ宣言される');
  assert.equal(byData(root, 'themeValue', 'bullish').textContent, '#00ff00', '現在値表示が追随する');
});

test('TC-CD07 「未指定に戻す」で当該トークンが未宣言へ戻る（§6.3）', () => {
  // Arrange
  const seen = [];
  const { root } = openEdit({ onSubmit: (arg) => { seen.push(arg); return { ok: true }; } });
  byData(root, 'themeField', 'name').value = 'T';
  const swatch = byData(root, 'themeSwatch', 'range');
  swatch.value = '#ffffff';
  swatch.fire('input');
  // Act
  byData(root, 'themeClear', 'range').fire('click');
  byData(root, 'themeAction', 'submit').fire('click');
  // Assert
  assert.deepEqual(seen[0].roleColors, {}, '未指定に戻したトークンは保存されない');
  assert.equal(byData(root, 'themeValue', 'range').textContent, '未指定', '現在値表示も未指定へ戻る');
});

// ---------------------------------------------------------------------------
// 宣言の有無は明示状態（§6.3・「未指定に戻す」と対）
// ---------------------------------------------------------------------------
//
// 病因: 「宣言済み」を `input` / `change` の発火から**推論**すると、実 DOM では値が変わらない
//   限りイベントが出ないため、スウォッチの初期値と同じ色（黒 #000000）は選んでも宣言されない。
//   宣言の有無は行ごとの明示状態（「この色を使う」トグル）が持ち、値との一致に依存させない。

test('TC-CD21 各トークン行は「この色を使う」トグルを持ち、初期値は OFF（＝未指定）', () => {
  // Arrange / Act
  const { root } = openEdit();
  // Assert
  for (const token of COLOR_ROLES) {
    const use = byData(root, 'themeUse', token);
    assert.ok(use, `${token}: 明示トグルが在席する`);
    assert.equal(use.type, 'checkbox', `${token}: トグルはチェックボックス`);
    assert.equal(use.checked, false, `${token}: 初期は未指定（恒等テーマ）`);
  }
});

test('TC-CD22 黒（#000000）を 1 操作で宣言できる（値がスウォッチ初期値と同じでも宣言される）', () => {
  // Arrange
  const seen = [];
  const { root } = openEdit({ onSubmit: (arg) => { seen.push(arg); return { ok: true }; } });
  byData(root, 'themeField', 'name').value = 'T';
  const use = byData(root, 'themeUse', 'surface');
  assert.equal(byData(root, 'themeSwatch', 'surface').value, '#000000', '前提: スウォッチの初期値は黒');
  // Act: トグルを入れるだけ（スウォッチの値は変えない＝実 DOM では input も change も出ない）
  use.checked = true;
  use.fire('change');
  byData(root, 'themeAction', 'submit').fire('click');
  // Assert
  assert.deepEqual(seen[0].roleColors, { surface: '#000000' });
  assert.equal(byData(root, 'themeValue', 'surface').textContent, '#000000', '現在値表示も追随する');
});

test('TC-CD23 トグルを OFF に戻すと未指定へ戻る（「未指定に戻す」と同じ状態）', () => {
  // Arrange
  const seen = [];
  const { root } = openEdit({ onSubmit: (arg) => { seen.push(arg); return { ok: true }; } });
  byData(root, 'themeField', 'name').value = 'T';
  const use = byData(root, 'themeUse', 'grid');
  use.checked = true;
  use.fire('change');
  // Act
  use.checked = false;
  use.fire('change');
  byData(root, 'themeAction', 'submit').fire('click');
  // Assert
  assert.deepEqual(seen[0].roleColors, {});
  assert.equal(byData(root, 'themeValue', 'grid').textContent, '未指定');
});

test('TC-CD24 「未指定に戻す」はトグルと同期する（状態の持ち主は 1 つ）', () => {
  // Arrange
  const { root } = openEdit();
  const use = byData(root, 'themeUse', 'level');
  use.checked = true;
  use.fire('change');
  // Act
  byData(root, 'themeClear', 'level').fire('click');
  // Assert
  assert.equal(use.checked, false, 'トグルの表示と内部状態がずれない');
  assert.equal(byData(root, 'themeValue', 'level').textContent, '未指定');
});

test('TC-CD25 スウォッチで色を選ぶとトグルも ON になる（表示と状態がずれない）', () => {
  // Arrange
  const { root } = openEdit();
  const swatch = byData(root, 'themeSwatch', 'alert');
  // Act
  swatch.value = '#123456';
  swatch.fire('change');
  // Assert
  assert.equal(byData(root, 'themeUse', 'alert').checked, true);
  assert.equal(byData(root, 'themeValue', 'alert').textContent, '#123456');
});

// ---------------------------------------------------------------------------
// 時間足の明度差（§6.3・§4.7）
// ---------------------------------------------------------------------------

test('TC-CD08 時間足の行は TF_CODES から生成する（件数・順序が台帳と一致・§6.3）', () => {
  // Arrange / Act
  const { root } = openEdit();
  // Assert
  const inputs = allWith(root, 'themeTf');
  assert.deepEqual(
    inputs.map((i) => i.dataset.themeTf),
    [...TF_CODES],
    '時間足の行は domain/tf_meta.js の TF_CODES と一致する＝手書きの配列を持たない',
  );
  for (const input of inputs) {
    assert.equal(input.type, 'number');
    assert.equal(String(input.min), '-1', '下限 -1.00（§6.3）');
    assert.equal(String(input.max), '1', '上限 1.00（§6.3）');
    assert.equal(String(input.step), '0.01', '0.01 刻み（§6.3）');
  }
});

test('TC-CD09 「使う」ON で tfModifier は TF_CODES 全キーの数値になる（既定 0＝変化なし・§6.3）', () => {
  // Arrange
  const seen = [];
  const { root } = openEdit({ onSubmit: (arg) => { seen.push(arg); return { ok: true }; } });
  byData(root, 'themeField', 'name').value = 'T';
  const toggle = byData(root, 'themeField', 'tf-enabled');
  assert.equal(toggle.checked, false, '前提: 「使う」の初期値は OFF');
  // Act
  toggle.checked = true;
  toggle.fire('change');
  byData(root, 'themeTf', TF_CODES[0]).value = '-0.30';
  byData(root, 'themeAction', 'submit').fire('click');
  // Assert
  assert.deepEqual(Object.keys(seen[0].tfModifier), [...TF_CODES], '全時間足のキーを持つ');
  assert.equal(seen[0].tfModifier[TF_CODES[0]], -0.3, '入力値を数値として渡す');
  assert.equal(seen[0].tfModifier[TF_CODES[1]], 0, '未入力は 0（変化なし）');
});

// ---------------------------------------------------------------------------
// 同名保存の確認 1 段（§6.3・テンプレートの上書き確認と同型）
// ---------------------------------------------------------------------------

test('TC-CD10 保存: 正規化名が既存と一致したら確認を 1 段挟む（1 回目は保存しない）（§6.3）', () => {
  // Arrange
  const saved = [];
  const { root } = openEdit({
    onSubmit: (arg) => { saved.push(arg); return { ok: true }; },
    findExisting: (name) => (String(name).trim().toLowerCase() === 'ダーク統一' ? THEMES[0] : null),
  });
  byData(root, 'themeField', 'name').value = 'ダーク統一';
  const submit = byData(root, 'themeAction', 'submit');
  // Act: 1 回目
  submit.fire('click');
  // Assert
  assert.deepEqual(saved, [], '1 回目のクリックでは保存しない（確認 1 段）');
  const error = byData(root, 'themeError', 'edit');
  assert.ok(error.textContent.includes('ダーク統一'), `上書き対象を提示する（実際: ${error.textContent}）`);
  // Act: 2 回目（確認）
  submit.fire('click');
  // Assert
  assert.equal(saved.length, 1, '確認後に保存する');
});

test('TC-CD11 保存: 確認中に名前を編集したら確認は解除される（§6.3）', () => {
  // Arrange
  const saved = [];
  const { root } = openEdit({
    onSubmit: (arg) => { saved.push(arg); return { ok: true }; },
    findExisting: (name) => (String(name).trim().toLowerCase() === 'ダーク統一' ? THEMES[0] : null),
  });
  const nameInput = byData(root, 'themeField', 'name');
  const submit = byData(root, 'themeAction', 'submit');
  nameInput.value = 'ダーク統一';
  submit.fire('click');
  // Act
  nameInput.value = '別の名前';
  nameInput.fire('input');
  submit.fire('click');
  // Assert
  assert.equal(saved.length, 1, '別名になったので確認を挟まずそのまま保存する');
  assert.equal(saved[0].name, '別の名前');
});

// ---------------------------------------------------------------------------
// 検証失敗のインライン表示（F-C1）
// ---------------------------------------------------------------------------

test('TC-CD12 保存: onSubmit が ok:false なら CODE をインライン表示し閉じない（F-C1）', () => {
  // Arrange
  const doc = fakeDoc();
  const dialogs = new ColorThemeDialogs({ document: doc });
  dialogs.openEdit({ onSubmit: () => ({ ok: false, code: 'empty' }) });
  const root = doc.body.children[0];
  // Act
  byData(root, 'themeAction', 'submit').fire('click');
  // Assert
  assert.equal(doc.body.children.length, 1, '失敗時は閉じない（既存データは不変・F-C1）');
  const error = byData(root, 'themeError', 'edit');
  assert.ok(error.textContent.length > 0, 'ダイアログ内にインラインで表示する');
  assert.ok(error.textContent.includes('名前'), `code=empty を名前の文言へ写像する（実際: ${error.textContent}）`);
});

test('TC-CD13 保存: CODE の全語彙が固有の文言へ写像される（未知 code は既定文言）', () => {
  // Arrange
  const codes = ['empty', 'too_long', 'duplicate', 'limit', 'not_found'];
  const seen = new Set();
  // Act
  for (const code of codes) {
    const doc = fakeDoc();
    const dialogs = new ColorThemeDialogs({ document: doc });
    dialogs.openEdit({ onSubmit: () => ({ ok: false, code }) });
    const root = doc.body.children[0];
    byData(root, 'themeAction', 'submit').fire('click');
    seen.add(byData(root, 'themeError', 'edit').textContent);
  }
  // Assert
  assert.equal(seen.size, codes.length, `CODE ごとに異なる文言を出す（実際: ${[...seen].join(' / ')}`);
});

test('TC-CD14 キャンセルは onSubmit を呼ばずに閉じる（§6.3）', () => {
  // Arrange
  let called = 0;
  const doc = fakeDoc();
  const dialogs = new ColorThemeDialogs({ document: doc });
  dialogs.openEdit({ onSubmit: () => { called += 1; return { ok: true }; } });
  const root = doc.body.children[0];
  // Act
  byData(root, 'themeAction', 'cancel').fire('click');
  // Assert
  assert.equal(called, 0, 'キャンセルでは保存しない');
  assert.equal(doc.body.children.length, 0, '閉じる');
});

// ---------------------------------------------------------------------------
// 管理ダイアログ（§6.2・UC-C03）
// ---------------------------------------------------------------------------

test('TC-CD15 管理ダイアログ: 保存済みテーマのみを宣言順に一覧する（固定行は管理対象外・§6.2）', () => {
  // Arrange
  const doc = fakeDoc();
  const dialogs = new ColorThemeDialogs({ document: doc });
  // Act
  dialogs.openManage({ themes: THEMES, onRename: () => ({ ok: true }), onDelete: () => {} });
  // Assert
  const root = doc.body.children[0];
  const rows = allWith(root, 'themeRow');
  assert.deepEqual(rows.map((r) => r.dataset.themeRow), ['thm#1', 'thm#2'], '宣言順に一覧する');
  assert.equal(textOf(root).includes('テーマなし'), false, '「テーマなし（既定色）」は改名も削除もできない＝一覧に出さない');
});

test('TC-CD16 管理ダイアログ: 改名は onRename へ委譲し、ok:false ならインライン表示して閉じない（F-C1）', () => {
  // Arrange
  const doc = fakeDoc();
  const seen = [];
  const dialogs = new ColorThemeDialogs({ document: doc });
  dialogs.openManage({
    themes: THEMES,
    onRename: (id, name) => { seen.push([id, name]); return { ok: false, code: 'duplicate' }; },
    onDelete: () => {},
  });
  const root = doc.body.children[0];
  // Act
  byData(root, 'themeRename', 'thm#1').fire('click');
  byData(root, 'themeRenameInput', 'thm#1').value = '時間足別';
  byData(root, 'themeRenameCommit', 'thm#1').fire('click');
  // Assert
  assert.deepEqual(seen, [['thm#1', '時間足別']], '改名は onRename（themeId, name）へ委譲する');
  const error = byData(root, 'themeError', 'manage');
  assert.ok(error.textContent.length > 0, '重複はインライン表示する（F-C1）');
  assert.equal(doc.body.children.length, 1, '失敗時は閉じない');
});

test('TC-CD17 管理ダイアログ: 削除は確認を 1 段挟む（1 回目のクリックでは削除しない）（§5.3）', () => {
  // Arrange
  const doc = fakeDoc();
  const deleted = [];
  const dialogs = new ColorThemeDialogs({ document: doc });
  dialogs.openManage({ themes: THEMES, onRename: () => ({ ok: true }), onDelete: (id) => deleted.push(id) });
  const root = doc.body.children[0];
  // Act
  byData(root, 'themeDelete', 'thm#2').fire('click');
  // Assert
  assert.deepEqual(deleted, [], '1 回目のクリックでは削除しない（確認 1 段）');
  // Act
  byData(root, 'themeDeleteConfirm', 'thm#2').fire('click');
  // Assert
  assert.deepEqual(deleted, ['thm#2'], '確認後に onDelete（themeId）を呼ぶ');
});

test('TC-CD18 同時に 2 枚開かない（後勝ち・chart_template_dialogs と同型）', () => {
  // Arrange
  const doc = fakeDoc();
  const dialogs = new ColorThemeDialogs({ document: doc });
  // Act
  dialogs.openEdit({ onSubmit: () => ({ ok: true }) });
  dialogs.openManage({ themes: THEMES });
  // Assert
  assert.equal(doc.body.children.length, 1, 'body に残るのは 1 枚だけ');
  assert.equal(doc.body.children[0].dataset.themeDialog, 'manage', '後から開いた方が残る');
});

test('TC-CD19 防御: DOM 不在（document=null）でも open は例外を投げない（同型元と同じ）', () => {
  // Arrange
  const dialogs = new ColorThemeDialogs({ document: null });
  // Act / Assert
  assert.doesNotThrow(() => dialogs.openEdit({ onSubmit: () => ({ ok: true }) }));
  assert.doesNotThrow(() => dialogs.openManage({ themes: THEMES }));
  assert.doesNotThrow(() => dialogs.close());
});

test('TC-CD20 DIP: 本モジュールは controller / 協働子を import しない（§7.1）', async () => {
  // Arrange
  const { readFileSync } = await import('node:fs');
  const { fileURLToPath } = await import('node:url');
  const src = readFileSync(
    fileURLToPath(new URL('../js/adapter/front/color_theme_dialogs.js', import.meta.url)), 'utf8',
  );
  // Act
  const imports = [...src.matchAll(/^\s*import\s.*?from\s+'([^']+)'/gm)].map((m) => m[1]);
  // Assert
  assert.ok(!imports.some((p) => p.includes('controller')), `controller を import している: ${imports.join(', ')}`);
  assert.ok(
    imports.every((p) => p.startsWith('../../domain/')),
    `参照してよいのは domain 台帳だけ（依存は内向き・§7.8）: ${imports.join(', ')}`,
  );
});
