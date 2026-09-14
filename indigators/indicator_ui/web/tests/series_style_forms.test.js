// series_style_forms.test.js — スタイルタブ「表示形式」台帳の検定（ISSUE-502 段階 4D）。
//
// 何を固定するか:
//   L1 台帳: 選択肢の集合・並び・ゲートは台帳が唯一源（凍結・順序が表示順）。
//   L2 往復: 台帳の各エントリは「選択 → 永続化 → 再表示」で自分自身へ戻る。
//       是正前は「選択肢の組立（properties_dialog.js:512 付近）」と「選択値の分解（同 :615-624）」が
//       100 行離れた 2 箇所の手書きで、第 3 の表示形式を足すには両方を同時に直す必要があった。
//       片方だけ直すと「選べるのに保存されない」「保存値に戻せない」という無言の欠落になる。
//       往復を台帳全数で固定すれば、エントリを足したときにこの検定が自動で守る。
//   L3 差分: 永続差分の組立は純関数（DOM 非依存）で、変更した項目だけが載る。
//   L4 再発防止: 台帳外の表示形式リテラルが properties_dialog.js に現れたら Red。

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

import {
  DEFAULT_LINE_STYLE,
  SERIES_DISPLAY_FORMS,
  collectSeriesStyleDiff,
  decodeDisplayForm,
  displayFormInitial,
  displayFormOptions,
  usesUnifiedDisplayForm,
} from '../js/usecase/series_style_forms.js';

const DIALOG_SRC = readFileSync(
  fileURLToPath(new URL('../js/adapter/front/properties_dialog.js', import.meta.url)),
  'utf8',
);

// --------------------------------------------------------------------------- //
// L1 台帳
// --------------------------------------------------------------------------- //

test('L1 台帳は凍結され、各エントリが 4 項目（値・ゲート・display・線種）を宣言する', () => {
  assert.ok(Object.isFrozen(SERIES_DISPLAY_FORMS));
  for (const e of SERIES_DISPLAY_FORMS) {
    assert.ok(Object.isFrozen(e), `${e.value} が凍結されていない`);
    assert.deepEqual(Object.keys(e).sort(), ['display', 'gate', 'lineStyle', 'value']);
    assert.equal(typeof e.value, 'string');
    assert.ok(e.gate === null || typeof e.gate === 'string');
  }
  // 値は一意（同じ選択肢が 2 行に現れると分解が先着順に依存する）。
  const values = SERIES_DISPLAY_FORMS.map((e) => e.value);
  assert.equal(new Set(values).size, values.length);
});

test('L1 選択肢はゲートで決まり、並びは台帳順（既存 3 系列の並びを保存）', () => {
  const plain = { pointStyleEditable: false, barStyleEditable: false };
  assert.deepEqual(displayFormOptions(plain), ['solid', 'dotted', 'dashed']);
  assert.deepEqual(
    displayFormOptions({ ...plain, pointStyleEditable: true }),
    ['dot', 'solid', 'dotted', 'dashed'],
  );
  assert.deepEqual(
    displayFormOptions({ ...plain, barStyleEditable: true }),
    ['solid', 'dotted', 'dashed', 'bar'],
  );
  // 両ゲートは直交（同時に開けば両方出る）。
  assert.deepEqual(
    displayFormOptions({ pointStyleEditable: true, barStyleEditable: true }),
    ['dot', 'solid', 'dotted', 'dashed', 'bar'],
  );
});

test('L1 統合 select の対象判定はゲート付きエントリの有無で決まる（フラグ名を呼び出し側に書かない）', () => {
  assert.equal(usesUnifiedDisplayForm({}), false);
  assert.equal(usesUnifiedDisplayForm({ pointStyleEditable: true }), true);
  assert.equal(usesUnifiedDisplayForm({ barStyleEditable: true }), true);
  assert.equal(usesUnifiedDisplayForm({ pointStyleEditable: true, barStyleEditable: true }), true);
  // ゲート名は台帳が持つ集合と一致する（呼び出し側が別のフラグ名を使えば false になる）。
  const gates = SERIES_DISPLAY_FORMS.filter((e) => e.gate !== null).map((e) => e.gate);
  assert.deepEqual([...new Set(gates)].sort(), ['barStyleEditable', 'pointStyleEditable']);
});

// --------------------------------------------------------------------------- //
// L2 往復（台帳全数）
// --------------------------------------------------------------------------- //

test('L2 台帳の全エントリが「選択 → 永続化 → 再表示」で自分自身へ戻る（第3形式の追加もここで守られる）', () => {
  for (const e of SERIES_DISPLAY_FORMS) {
    const persisted = decodeDisplayForm(e.value);
    assert.equal(persisted.display, e.display, `${e.value}: display の写像が台帳と食い違う`);
    if (e.lineStyle === null) {
      assert.ok(!('style' in persisted), `${e.value}: 線種を持たない形式に style を載せている`);
    } else {
      assert.equal(persisted.style, e.lineStyle, `${e.value}: 線種の写像が台帳と食い違う`);
    }
    // 保存された行を読み直したとき、同じ選択値へ戻る（往復整合）。
    const row = { display: persisted.display, style: persisted.style ?? DEFAULT_LINE_STYLE };
    assert.equal(displayFormInitial(row), e.value, `${e.value}: 保存値から同じ選択へ戻れない`);
  }
});

test('L2 初期値: 実描画値（display / style）から選択値を決める・未指定は既定線種', () => {
  assert.equal(displayFormInitial({ display: 'dots', style: 'solid' }), 'dot');
  assert.equal(displayFormInitial({ display: 'bar', style: 'solid' }), 'bar');
  assert.equal(displayFormInitial({ display: 'line', style: 'dashed' }), 'dashed');
  // display 未指定（旧データ・静的フォールバック）は線種そのもの。
  assert.equal(displayFormInitial({ display: null, style: 'dotted' }), 'dotted');
  assert.equal(displayFormInitial({}), DEFAULT_LINE_STYLE);
  assert.equal(displayFormInitial({ display: null, style: null }), DEFAULT_LINE_STYLE);
});

test('L2 台帳外の値は既定（線として扱い値を線種に載せる）へ落ちる', () => {
  assert.deepEqual(decodeDisplayForm('__unknown__'), { display: 'line', style: '__unknown__' });
});

// --------------------------------------------------------------------------- //
// L3 永続差分の組立（DOM 非依存）
// --------------------------------------------------------------------------- //

const styleRow = (over = {}) => ({
  names: ['A'],
  color: { value: '#111111', initial: '#111111' },
  width: { value: '1', initial: '1' },
  style: { value: 'solid', initial: 'solid' },
  unified: null,
  ...over,
});

test('L3 無変更は空・変更した項目だけが載る', () => {
  assert.deepEqual(collectSeriesStyleDiff({ styleRows: [styleRow()] }), {});
  assert.deepEqual(
    collectSeriesStyleDiff({ styleRows: [styleRow({ color: { value: '#222222', initial: '#111111' } })] }),
    { A: { color: '#222222' } },
  );
  assert.deepEqual(
    collectSeriesStyleDiff({ styleRows: [styleRow({ width: { value: '3', initial: '1' } })] }),
    { A: { width: 3 } },
  );
  assert.deepEqual(
    collectSeriesStyleDiff({ styleRows: [styleRow({ style: { value: 'dashed', initial: 'solid' } })] }),
    { A: { style: 'dashed' } },
  );
});

test('L3 空欄の線幅は確定させない（入力途中で幅が 0 になる事故を作らない）', () => {
  assert.deepEqual(
    collectSeriesStyleDiff({ styleRows: [styleRow({ width: { value: '', initial: '1' } })] }),
    {},
  );
});

test('L3 入力を持たない項目（ヒート行の色・histogram の線幅/線種）は差分対象外', () => {
  const heat = styleRow({ color: null, width: null, style: null });
  assert.deepEqual(collectSeriesStyleDiff({ styleRows: [heat] }), {});
});

test('L3 統合 select の差分は台帳が分解する（線種なし形式は style を載せない）', () => {
  const unified = (value, initial) => styleRow({ style: null, unified: { value, initial } });
  assert.deepEqual(
    collectSeriesStyleDiff({ styleRows: [unified('dot', 'solid')] }),
    { A: { display: 'dots' } },
  );
  assert.deepEqual(
    collectSeriesStyleDiff({ styleRows: [unified('bar', 'solid')] }),
    { A: { display: 'bar' } },
  );
  assert.deepEqual(
    collectSeriesStyleDiff({ styleRows: [unified('dotted', 'dot')] }),
    { A: { display: 'line', style: 'dotted' } },
  );
});

test('L3 bucket 粒度の行は構成系列すべてへ展開し、可視性差分と合流する', () => {
  const patch = collectSeriesStyleDiff({
    styleRows: [styleRow({ names: ['A', 'B'], color: { value: '#333333', initial: '#111111' } })],
    visibilityRows: [
      { names: ['A'], checked: false, initial: true },
      { names: ['B'], checked: true, initial: true },
    ],
  });
  assert.deepEqual(patch, { A: { color: '#333333', visible: false }, B: { color: '#333333' } });
});

// --------------------------------------------------------------------------- //
// L4 再発防止 — 台帳外の表示形式リテラルが dialog に現れたら Red
// --------------------------------------------------------------------------- //

function stripComments(src) {
  return src
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .replace(/(^|[^:])\/\/[^\n]*/g, '$1');
}

// 表示形式の語彙（select の値と永続化の display 値）。台帳の外で現れたら二重定義。
function displayFormLiterals(code) {
  const values = SERIES_DISPLAY_FORMS.map((e) => e.value);
  const displays = SERIES_DISPLAY_FORMS.map((e) => e.display);
  const vocabulary = [...new Set([...values, ...displays])];
  const hits = [];
  // 引用符の種類で検出を落とさない（単引用・二重引用・テンプレートの 3 形式を見る）。
  const quoted = (w) => `['"\`]${w}['"\`]`;
  for (const word of vocabulary) {
    // 'line' は系列種別（kind）の既定にも使う語なので、display への代入形だけを見る。
    const re = word === 'line'
      ? new RegExp(`display\\s*[:=]\\s*${quoted('line')}`, 'g')
      : new RegExp(quoted(word), 'g');
    for (const m of code.matchAll(re)) {
      hits.push(m[0]);
    }
  }
  return hits;
}

test('L4 properties_dialog.js に台帳外の表示形式リテラルが無い（第3形式の追加＝台帳 1 エントリ）', () => {
  const leaked = displayFormLiterals(stripComments(DIALOG_SRC));
  assert.deepEqual(leaked, [], `台帳の外に表示形式が書かれている:\n${leaked.join('\n')}`);
});

test('L4 走査器の自己検査（是正前の 2 箇所をどちらも実際に検出できる）', () => {
  // 是正前に実在した書き方（選択肢の組立と、選択値の分解）。片方でも取りこぼすと
  //   「0 件」は検出力の不足を意味してしまう。
  const encodeSide = "const opts = ['solid', 'dotted', 'dashed']; if (r.pointStyleEditable) opts.unshift('dot'); if (r.barStyleEditable) opts.push('bar');";
  const decodeSide = "if (v === 'dot') { f.display = 'dots'; } else { f.display = 'line'; f.style = v; }";
  assert.ok(displayFormLiterals(encodeSide).length >= 5, '選択肢の組立を検出できない');
  assert.ok(displayFormLiterals(decodeSide).length >= 2, '選択値の分解を検出できない');
  // 引用符を変えた書き方でも取りこぼさない（形式を 1 つ落とすと「0 件」が検出力不足を意味する）。
  assert.deepEqual(displayFormLiterals('const a = "dot";'), ['"dot"']);
  assert.deepEqual(displayFormLiterals('const b = `bar`;'), ['`bar`']);
  assert.deepEqual(displayFormLiterals('f.display = "line";'), ['display = "line"']);
  // 誤検出しないこと（系列種別の既定は表示形式ではない）。
  assert.deepEqual(displayFormLiterals("kind: s.kind ?? 'line',"), []);
});

test('L4 properties_dialog.js は台帳を import している（走査が空振りしていない）', () => {
  assert.match(DIALOG_SRC, /from '\.\.\/\.\.\/usecase\/series_style_forms\.js'/);
});
