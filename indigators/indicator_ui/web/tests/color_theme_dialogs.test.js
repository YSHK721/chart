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
// 未指定行に導出値を見せる（段階 5-C-2・導出を「決める項目を減らす」機能として成立させる）
// ---------------------------------------------------------------------------
//
// 病因: 未指定の行はスウォッチが #000000・値欄が「未指定」としか出ず、ユーザーは「この色を
//   未指定にしたら何色になるか」を見られなかった。見えなければ、導出は決める項目を減らす機能に
//   ならない（未指定にするのが怖いので結局 14 行を埋めることになる）。
//
// 規律（壊してはならない）: 宣言の有無を持つのは**トグル**だけである。値から未指定を推論する
//   実装へ戻してはならない（color_theme_dialogs.js:87-95）。以下の検定は「表示は導出値になるが、
//   保存される roleColors は 1 件も増えない」を必ず対にして固定する。

test('TC-CD26 未指定行には導出値が「自動 #xxxxxx」として出る（導出元が揃ったとき）', () => {
  // Arrange: 地だけを宣言する。grid / border / text / level / muted / highlight が導出できる。
  const { root } = openEdit();
  const swatch = byData(root, 'themeSwatch', 'surface');
  // Act
  swatch.value = '#131722';
  swatch.fire('input');
  // Assert
  assert.equal(byData(root, 'themeValue', 'surface').textContent, '#131722', '宣言行は値そのまま');
  assert.equal(byData(root, 'themeValue', 'grid').textContent, '自動 #21242f');
  assert.equal(byData(root, 'themeValue', 'text').textContent, '自動 #d5d5d7');
  assert.equal(byData(root, 'themeValue', 'level').textContent, '自動 #8a8b91');
  assert.equal(byData(root, 'themeValue', 'muted').textContent, '自動 #686a71');
});

test('TC-CD27 導出値の表示はスウォッチにも反映される（見た目で色が分かる）', () => {
  // Arrange
  const { root } = openEdit();
  const swatch = byData(root, 'themeSwatch', 'surface');
  // Act
  swatch.value = '#131722';
  swatch.fire('input');
  // Assert
  assert.equal(byData(root, 'themeSwatch', 'text').value, '#d5d5d7');
  assert.equal(byData(root, 'themeSwatch', 'grid').value, '#21242f');
});

test('TC-CD28 導出値を見せても宣言は 1 件も増えない（宣言の持ち主はトグルのまま）', () => {
  // Arrange: 表示が導出値になっても、保存されるのは宣言した 1 語だけ（＝恒等の保証は不変）。
  const seen = [];
  const { root } = openEdit({ onSubmit: (arg) => { seen.push(arg); return { ok: true }; } });
  byData(root, 'themeField', 'name').value = 'T';
  const swatch = byData(root, 'themeSwatch', 'surface');
  swatch.value = '#131722';
  swatch.fire('input');
  // Act
  byData(root, 'themeAction', 'submit').fire('click');
  // Assert
  assert.deepEqual(seen[0].roleColors, { surface: '#131722' }, '導出値が宣言に混ざった');
  for (const token of ['grid', 'border', 'text', 'level', 'muted', 'highlight']) {
    assert.equal(byData(root, 'themeUse', token).checked, false, `${token}: トグルは OFF のまま`);
  }
});

test('TC-CD29 地を変えると未指定行の導出表示が同時に追随する', () => {
  // Arrange: 「surface を変えると従属色が追随する」＝導出が生きていることの体験そのもの。
  const { root } = openEdit();
  const swatch = byData(root, 'themeSwatch', 'surface');
  swatch.value = '#131722';
  swatch.fire('input');
  assert.equal(byData(root, 'themeValue', 'text').textContent, '自動 #d5d5d7', '前提');
  // Act: 地を白へ。対比側が黒に替わるので text は暗くなる。
  swatch.value = '#ffffff';
  swatch.fire('input');
  // Assert
  assert.equal(byData(root, 'themeValue', 'text').textContent, '自動 #2e2e2e');
  assert.equal(byData(root, 'themeValue', 'grid').textContent, '自動 #f0f0f0');
});

test('TC-CD30 primary を変えると secondary / range / neutral が追随する', () => {
  // Arrange
  const { root } = openEdit();
  const swatch = byData(root, 'themeSwatch', 'primary');
  // Act
  swatch.value = '#42a5f5';
  swatch.fire('input');
  // Assert
  assert.equal(byData(root, 'themeValue', 'secondary').textContent, '自動 #8342f5');
  assert.equal(byData(root, 'themeValue', 'range').textContent, '自動 #42e1f5');
  assert.equal(byData(root, 'themeValue', 'neutral').textContent, '自動 #9f9f9f');
});

test('TC-CD31 導出元が揃わないトークンは「未指定」のまま（部分写像・既定色のまま）', () => {
  // Arrange: surface が無ければ grid は導けない。ここで既定色を捏造して見せると、
  //   「未指定にしたらこの色になる」という表示が嘘になる（実際は現行の既定色のまま）。
  const { root } = openEdit();
  const swatch = byData(root, 'themeSwatch', 'primary');
  // Act: primary だけを宣言する（surface 由来の 6 語は導けない）。
  swatch.value = '#42a5f5';
  swatch.fire('input');
  // Assert
  for (const token of ['grid', 'border', 'text', 'level', 'muted', 'highlight']) {
    assert.equal(byData(root, 'themeValue', token).textContent, '未指定',
      `${token}: 導出元が無いのに値を見せた`);
  }
  assert.equal(byData(root, 'themeSwatch', 'grid').value, '#000000', 'スウォッチも既定のまま');
});

test('TC-CD32 「未指定に戻す」で、その行が導出値の表示へ切り替わる（宣言 → 自動）', () => {
  // Arrange: text を明示宣言してから未指定へ戻すと、導出値が見えるようになる。
  const { root } = openEdit();
  const surface = byData(root, 'themeSwatch', 'surface');
  surface.value = '#131722';
  surface.fire('input');
  const text = byData(root, 'themeSwatch', 'text');
  text.value = '#d1d4dc';
  text.fire('input');
  assert.equal(byData(root, 'themeValue', 'text').textContent, '#d1d4dc', '前提: 宣言済み');
  assert.equal(byData(root, 'themeValue', 'level').textContent, '自動 #878b94',
    '前提: 宣言された text が導出元になる');
  // Act
  byData(root, 'themeClear', 'text').fire('click');
  // Assert
  assert.equal(byData(root, 'themeValue', 'text').textContent, '自動 #d5d5d7', '導出値へ切り替わる');
  assert.equal(byData(root, 'themeValue', 'level').textContent, '自動 #8a8b91',
    'level の導出元も導出値へ戻る（連鎖が追随する）');
});

test('TC-CD33b 宣言済み行のスウォッチは他行の変更で書き換えられない（ドラッグ中の値を奪わない）', () => {
  // 病因になり得る形: 表示を作り直すたびに全行のスウォッチへ値を書くと、ユーザーが**今つまんで
  //   いる**ピッカーの値まで書き換わる（実機では色が飛ぶ・選べなくなる）。書き換えてよいのは
  //   未指定の行だけである。宣言済み行は宣言値が唯一の持ち主。
  const { root } = openEdit();
  const bullish = byData(root, 'themeSwatch', 'bullish');
  bullish.value = '#00ff00';
  bullish.fire('input');
  // Act: 別の行（地）を何度も動かす＝導出の再計算が繰り返し走る。
  const surface = byData(root, 'themeSwatch', 'surface');
  for (const v of ['#131722', '#ffffff', '#0d1b3e']) {
    surface.value = v;
    surface.fire('input');
  }
  // Assert
  assert.equal(bullish.value, '#00ff00', '宣言済み行のスウォッチが書き換わった');
  assert.equal(byData(root, 'themeValue', 'bullish').textContent, '#00ff00');
});

test('TC-CD33 編集で開いたテーマでも、未宣言の行は導出値を見せる', () => {
  // Arrange: 保存済みテーマ（地だけ宣言）の編集。
  const theme = { themeId: 'thm#9', name: '地だけ', roleColors: { surface: '#131722' } };
  // Act
  const { root } = openEdit({ theme });
  // Assert
  assert.equal(byData(root, 'themeValue', 'surface').textContent, '#131722');
  assert.equal(byData(root, 'themeValue', 'text').textContent, '自動 #d5d5d7');
  assert.equal(byData(root, 'themeUse', 'text').checked, false, '導出を見せても宣言はしない');
});

// ---------------------------------------------------------------------------
// ライブプレビューの発火（段階 5-C-3・ダイアログ側）
// ---------------------------------------------------------------------------
//
// 規律: 発火点を増やさない。状態を変える入口は `setSpecified`（トークン行）と時間足入力だけで、
//   そこから 1 本の `onChanged` を通す。入口を増やすと「ある操作だけプレビューされない」が生まれる。
//   解除（`onPreview(null)`）はダイアログが閉じる 1 点（`close`）に集約する — キャンセル・×・
//   保存成功・別ダイアログによる置き換えのすべてが `close` を通るため、取り残しが構成上できない。

// onPreview の呼び出しを記録して開く共通 Arrange。
function openEditWithPreview(opts = {}) {
  const seen = [];
  const env = openEdit({ onPreview: (d) => seen.push(d), ...opts });
  return { ...env, seen };
}

test('TC-CD34 スウォッチ操作で onPreview が下書き（roleColors / tfModifier）付きで呼ばれる', () => {
  // Arrange
  const { root, seen } = openEditWithPreview();
  const initial = seen.length;
  const swatch = byData(root, 'themeSwatch', 'surface');
  // Act
  swatch.value = '#131722';
  swatch.fire('input');
  // Assert
  assert.equal(seen.length, initial + 1, '1 操作 = 1 発火');
  assert.deepEqual(seen[seen.length - 1], { roleColors: { surface: '#131722' }, tfModifier: null });
});

test('TC-CD35 トグル・「未指定に戻す」でも onPreview が呼ばれる（発火点は setSpecified 1 箇所）', () => {
  // Arrange
  const { root, seen } = openEditWithPreview();
  const use = byData(root, 'themeUse', 'bullish');
  // Act
  use.checked = true;
  use.fire('change');
  const afterToggle = seen[seen.length - 1];
  byData(root, 'themeClear', 'bullish').fire('click');
  // Assert
  assert.deepEqual(afterToggle.roleColors, { bullish: '#000000' }, 'トグルだけで宣言できる');
  assert.deepEqual(seen[seen.length - 1].roleColors, {}, '未指定に戻すと宣言が消える');
});

test('TC-CD36 時間足の入力でも onPreview が呼ばれる（もう 1 つの状態の入口）', () => {
  // Arrange
  const { root, seen } = openEditWithPreview();
  const toggle = byData(root, 'themeField', 'tf-enabled');
  // Act
  toggle.checked = true;
  toggle.fire('change');
  const afterEnable = seen[seen.length - 1];
  const cell = byData(root, 'themeTf', TF_CODES[0]);
  cell.value = '0.25';
  cell.fire('input');
  // Assert
  assert.ok(afterEnable.tfModifier && typeof afterEnable.tfModifier === 'object',
    '「使う」ON で tfModifier が載る');
  assert.equal(seen[seen.length - 1].tfModifier[TF_CODES[0]], 0.25, '数値入力が下書きへ届く');
});

test('TC-CD37 キャンセルでプレビューを解除する（onPreview(null)）', () => {
  // Arrange
  const { root, seen } = openEditWithPreview();
  const swatch = byData(root, 'themeSwatch', 'surface');
  swatch.value = '#131722';
  swatch.fire('input');
  // Act
  byData(root, 'themeAction', 'cancel').fire('click');
  // Assert
  assert.equal(seen[seen.length - 1], null, 'キャンセルで解除されない');
});

test('TC-CD38 保存成功でもプレビューを解除する（保存されたテーマで塗り直される）', () => {
  // Arrange
  const { root, seen } = openEditWithPreview({ onSubmit: () => ({ ok: true }) });
  byData(root, 'themeField', 'name').value = 'T';
  const swatch = byData(root, 'themeSwatch', 'surface');
  swatch.value = '#131722';
  swatch.fire('input');
  // Act
  byData(root, 'themeAction', 'submit').fire('click');
  // Assert
  assert.equal(seen[seen.length - 1], null);
});

test('TC-CD39 保存失敗ではダイアログが開いたままなので解除しない（見ている色を勝手に戻さない）', () => {
  // Arrange: F-C1 の失敗はインライン表示で閉じない。閉じないのに色だけ戻ると、
  //   直している最中の色が消えて操作が続けられなくなる。
  const { root, seen } = openEditWithPreview({ onSubmit: () => ({ ok: false, code: 'empty' }) });
  const swatch = byData(root, 'themeSwatch', 'surface');
  swatch.value = '#131722';
  swatch.fire('input');
  // Act
  byData(root, 'themeAction', 'submit').fire('click');
  // Assert
  assert.notEqual(seen[seen.length - 1], null, '閉じていないのに解除された');
  assert.deepEqual(seen[seen.length - 1].roleColors, { surface: '#131722' });
});

test('TC-CD40 × ボタンでも解除する（閉じる経路はすべて close を通る）', () => {
  // Arrange
  const { doc, seen } = openEditWithPreview();
  const closeBtn = byData(doc.body.children[0], 'themeAction', 'cancel');
  // Act
  closeBtn.fire('click');
  // Assert
  assert.equal(seen[seen.length - 1], null);
});

test('TC-CD41 解除は 1 度だけ（閉じた後に close を重ねても再発火しない）', () => {
  // Arrange
  const { dialogs, seen } = openEditWithPreview();
  dialogs.close();
  const afterFirst = seen.length;
  // Act
  dialogs.close();
  dialogs.close();
  // Assert
  assert.equal(seen.length, afterFirst, '閉じた後に解除が繰り返された');
});

// ---------------------------------------------------------------------------
// 診断の表示（段階 5-C-4・非阻害）
// ---------------------------------------------------------------------------
//
// 規律:
//   - 診断対象は**射影後**（導出込み）の下書き。ユーザーが実際に見る色を診断しなければ意味がない
//     （宣言していない導出色の問題を見落とす）。
//   - **保存を妨げない**。保存ボタンを無効化しない・確認を挟まない。診断は助言であって合否ではない。
//   - 文言（何が・どれだけ）の生成は adapter の責務。usecase は事実（measured）だけを返す。
//   - 0 件のときは何も出さない（「問題ありません」のような無内容な行を足さない）。

const diagTextOf = (root) => {
  const box = byData(root, 'themeDiagnostics', 'edit');
  return box ? textOf(box) : null;
};
const diagRowsOf = (root) => {
  const box = byData(root, 'themeDiagnostics', 'edit');
  return box ? box.children : [];
};

test('TC-CD42 診断 0 件のときは行を 1 本も出さない（無内容な行を足さない）', () => {
  // Arrange: 地だけを宣言（導出込みで診断 0 件になることは TC-CD30 で実測済み）。
  const { root } = openEdit();
  const swatch = byData(root, 'themeSwatch', 'surface');
  // Act
  swatch.value = '#131722';
  swatch.fire('input');
  // Assert
  assert.deepEqual(diagRowsOf(root), [], '診断 0 件なのに行が出た');
  assert.equal(diagTextOf(root), '', '文字も出さない');
});

test('TC-CD43 W-C2: 「何が・どれだけ」が分かる（トークン名と実測コントラスト比が出る）', () => {
  // Arrange: 白地に teal。実測 CR 2.334（閾値 3.0）。
  const { root } = openEdit();
  const surface = byData(root, 'themeSwatch', 'surface');
  surface.value = '#ffffff';
  surface.fire('input');
  const bullish = byData(root, 'themeSwatch', 'bullish');
  // Act
  bullish.value = '#00bfa5';
  bullish.fire('input');
  // Assert
  const text = diagTextOf(root);
  assert.ok(text.includes('強気・上方向'), `トークン名が出ていない: ${text}`);
  assert.ok(text.includes('2.33'), `実測値が出ていない: ${text}`);
  assert.ok(text.includes('3.0'), `目安（閾値）が出ていない: ${text}`);
});

test('TC-CD44 W-C1: 同じ色になった 2 語の名前が出る', () => {
  // Arrange
  const { root } = openEdit();
  for (const token of ['bullish', 'primary']) {
    const s = byData(root, 'themeSwatch', token);
    s.value = '#00bfa5';
    s.fire('input');
  }
  // Act / Assert
  const text = diagTextOf(root);
  assert.ok(text.includes('強気・上方向'), text);
  assert.ok(text.includes('主出力'), text);
});

test('TC-CD45 W-C3: 上下の輝度差が足りないときに実測値つきで出る', () => {
  // Arrange: 実測 CR 1.0008（閾値 1.15）。
  const { root } = openEdit();
  for (const [token, value] of [['bullish', '#00bfa5'], ['bearish', '#00bfa6']]) {
    const s = byData(root, 'themeSwatch', token);
    s.value = value;
    s.fire('input');
  }
  // Act / Assert
  const text = diagTextOf(root);
  assert.ok(text.includes('1.00'), `実測値が出ていない: ${text}`);
  assert.ok(text.includes('1.15'), `目安が出ていない: ${text}`);
});

test('TC-CD46 診断は射影後（導出込み）に対して行う — 宣言していない導出色の問題も出る', () => {
  // Arrange: 白地 + 基点 5 語。実測では **neutral（2.647）と range（1.577）** も W-C2 を出すが、
  //   この 2 語はユーザーが宣言していない**導出色**である。宣言だけを診断すると見落とす。
  const { root } = openEdit();
  for (const [token, value] of [
    ['surface', '#ffffff'], ['bullish', '#00bfa5'], ['bearish', '#ff5252'],
    ['alert', '#ffa726'], ['primary', '#42a5f5'],
  ]) {
    const s = byData(root, 'themeSwatch', token);
    s.value = value;
    s.fire('input');
  }
  // Act / Assert
  const text = diagTextOf(root);
  assert.ok(text.includes('通常域'), `導出色 range の指摘が出ていない: ${text}`);
  assert.ok(text.includes('基準・中立'), `導出色 neutral の指摘が出ていない: ${text}`);
  assert.equal(byData(root, 'themeUse', 'range').checked, false, '前提: range は宣言していない');
});

test('TC-CD47 診断が出ていても保存は成功する（保存を妨げない・保存ボタンは無効化されない）', () => {
  // Arrange: W-C2 が出ている状態をつくる。
  const seen = [];
  const { root } = openEdit({ onSubmit: (arg) => { seen.push(arg); return { ok: true }; } });
  byData(root, 'themeField', 'name').value = 'T';
  const surface = byData(root, 'themeSwatch', 'surface');
  surface.value = '#ffffff';
  surface.fire('input');
  const bullish = byData(root, 'themeSwatch', 'bullish');
  bullish.value = '#00bfa5';
  bullish.fire('input');
  assert.notEqual(diagTextOf(root), '', '前提: 診断が出ている');
  const submit = byData(root, 'themeAction', 'submit');
  assert.equal(submit.disabled, false, '保存ボタンが無効化されている');
  // Act: 1 回押しただけで保存される（確認を挟まない）。
  submit.fire('click');
  // Assert
  assert.equal(seen.length, 1, '保存が呼ばれていない（確認を挟んでいる）');
  assert.deepEqual(seen[0].roleColors, { surface: '#ffffff', bullish: '#00bfa5' });
});

test('TC-CD48 診断は状態変更のたびに作り直される（直したら消える）', () => {
  // Arrange
  const { root } = openEdit();
  const surface = byData(root, 'themeSwatch', 'surface');
  surface.value = '#ffffff';
  surface.fire('input');
  const bullish = byData(root, 'themeSwatch', 'bullish');
  bullish.value = '#00bfa5';
  bullish.fire('input');
  assert.notEqual(diagTextOf(root), '', '前提: 診断が出ている');
  // Act: 地を暗くすると teal は浮き上がる。
  surface.value = '#131722';
  surface.fire('input');
  // Assert
  assert.equal(diagTextOf(root), '', '直したのに診断が残った（作り直していない）');
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
  // 依存は**内向き**のみ（§7.8）。段階 5-C で usecase（導出・診断）への参照が加わったため、
  //   固定するのは「domain だけ」ではなく「内向きだけ」＝ adapter を 1 本も参照しないこと。
  //   adapter を参照した瞬間に協働子・ホストへの経路ができ、DIP（§7.1）が壊れる。
  assert.ok(
    imports.every((p) => p.startsWith('../../domain/') || p.startsWith('../../usecase/')),
    `参照してよいのは domain と usecase だけ（依存は内向き・§7.8）: ${imports.join(', ')}`,
  );
  assert.ok(
    !imports.some((p) => p.includes('/adapter/') || p.startsWith('./')),
    `同層（adapter）を参照している: ${imports.join(', ')}`,
  );
});
