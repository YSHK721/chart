// js_literals_single_source.test.js — チャート上の描画物からの色リテラル除去（段階 5-E）。
//
// 5-D は app.css / replay_bar.css を対象に「台帳の対象外を除きリテラル 0 件」を固定した。
//   本テストはその JS 版である。対象は「チャート上の描画物とその設定 UI」で、走査は
//   **4 形式 + チャネル配列**を見る（ISSUE-359 の是正: 形式を 1 つでも落とすと「置換完了」を
//   誤って主張しうる。8 桁 hex を `\b` で取りこぼした前例がある）。
//
// 例外は `THEME_EXEMPT_LITERALS`（**単一の台帳**）へ理由付きで登録する。CSS 用と JS 用に
//   台帳を割らないのは、同一概念に 2 つの名前ができると「次に例外を足す人がどちらに書けば
//   よいか分からなくなり、必ず取り残しが出る」ため。台帳が 1 つなら、例外を増やす道は 1 本しかない。

import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

import { THEME_EXEMPT_LITERALS } from '../js/usecase/chrome_tokens.js';

const abs = (rel) => fileURLToPath(new URL(rel, import.meta.url));

// 段階 5-E の対象ファイル。チャート上の描画物と、その色を扱う設定 UI。
//   ここに載っているファイルは「素の色リテラルを 1 つも持たない」ことが条件になる。
const TARGETS = [
  ['trade_markers_renderer.js', '../js/adapter/front/trade_markers_renderer.js'],
  ['pair_lines_primitive.js', '../js/adapter/front/pair_lines_primitive.js'],
  ['tickvol_bands_primitive.js', '../js/adapter/front/tickvol_bands_primitive.js'],
  ['series_drawer.js', '../js/adapter/front/series_drawer.js'],
  ['chart_renderer.js', '../js/adapter/front/chart_renderer.js'],
  ['current_price_view.js', '../js/adapter/front/current_price_view.js'],
  ['indicator_controller.js', '../js/adapter/front/indicator_controller.js'],
  ['properties_dialog.js', '../js/adapter/front/properties_dialog.js'],
  ['property_control_builders.js', '../js/adapter/front/property_control_builders.js'],
  ['color_theme_dialogs.js', '../js/adapter/front/color_theme_dialogs.js'],
];

function stripComments(src) {
  return src
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .replace(/(^|[^:])\/\/[^\n]*/g, '$1');
}

// 文字列形式の色（6 桁 / 3 桁 / 8 桁 hex・rgb(a)・hsl(a)）。テンプレート展開を含むものは
//   「値」ではなく「組み立て式」なので除外する（`${` の有無で機械的に判別できる）。
function stringLiterals(code) {
  return [...code.matchAll(/#[0-9a-fA-F]{3,8}|rgba?\([^)]*\)|hsla?\([^)]*\)/g)]
    .map((m) => m[0])
    .filter((v) => !v.includes('${'));
}

// チャネル配列形式の色（0..255 の 3 つ組）。`#` と `rgba(` だけを見る走査はこれを 1 件も
//   検出しないため、配列で書かれた色を見逃したまま「0 件」と主張できてしまう。
function channelArrays(code) {
  return [...code.matchAll(/\[\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(\d{1,3})\s*\]/g)]
    .filter((m) => [1, 2, 3].every((i) => Number(m[i]) <= 255))
    .map((m) => m[0]);
}

test('段階 5-E: 対象ファイルに素の色リテラルが残っていない（台帳の対象外を除き 0 件）', () => {
  // Arrange
  const exempt = new Set(THEME_EXEMPT_LITERALS.map((e) => e.literal));
  const leaked = [];
  // Act
  for (const [name, rel] of TARGETS) {
    const code = stripComments(readFileSync(abs(rel), 'utf8'))
      .replace(/var\(--ct-[A-Za-z]+,\s*(?:[^()]|\([^()]*\))*\)/g, 'VAR');
    for (const v of stringLiterals(code)) {
      if (!exempt.has(v)) leaked.push(`${name}: ${v}`);
    }
    for (const v of channelArrays(code)) {
      leaked.push(`${name}: ${v}（チャネル配列）`);
    }
  }
  // Assert
  assert.deepEqual(leaked, [], `リテラルが残っている:\n${leaked.join('\n')}`);
});

test('段階 5-E: 走査器の自己検査（4 形式 + 配列形をすべて実際に検出できる）', () => {
  // 「0 件」は、検出器が何も見つけられないだけでも成立してしまう。既知の陽性標本で
  //   検出力を同じ場所に示す（ISSUE-359 の再発防止＝形式を落としていないことの実証）。
  // Arrange: 4 形式 + 配列形の陽性標本。
  const positive = [
    "const a = '#131722';", // 6 桁
    "const b = '#fff';", // 3 桁
    "const c = '#2962ff22';", // 8 桁（\b では取りこぼす形）
    "const d = 'rgba(41, 98, 255, 0.07)';", // rgba
    "const e = 'hsla(240, 95%, 46%, 0.9)';", // hsla
  ].join('\n');
  // Act / Assert
  const hits = stringLiterals(positive);
  assert.deepEqual(hits, [
    '#131722', '#fff', '#2962ff22', 'rgba(41, 98, 255, 0.07)', 'hsla(240, 95%, 46%, 0.9)',
  ], '4 形式のいずれかを取りこぼしている');
  assert.deepEqual(channelArrays('const S = [46, 125, 50];'), ['[46, 125, 50]'],
    'チャネル配列形を検出できない');
  // 誤検出しないこと（走査が使い物にならなくならない）。
  assert.deepEqual(channelArrays('const SIZES = [300, 12, 4];'), []);
  assert.deepEqual(stringLiterals('const x = `rgba(${r}, ${g}, ${b}, ${a})`;'), []);
});

test('段階 5-E: 例外台帳は 1 つで、影 4 種 ＋ 透明 ＋ 入力センチネルを逐語で列挙する', () => {
  // 例外を増やす道が 1 本しかないことの固定。件数と理由を全数で押さえるため、暗黙に増やす
  //   抜け道が構造的に存在しない（自動追随する書き方にすると「見逃した色を例外にする」が通る）。
  assert.deepEqual(THEME_EXEMPT_LITERALS.map((e) => [e.literal, e.reason]), [
    ['rgba(0, 0, 0, .55)', 'shadow'],
    ['rgba(0, 0, 0, .5)', 'shadow'],
    ['rgba(0,0,0,0.5)', 'shadow'],
    ['rgba(0, 0, 0, .45)', 'shadow'],
    ['rgba(0,0,0,0)', 'transparent'],
    ['#000000', 'input-sentinel'],
  ]);
});

test('段階 5-E: 対象外の理由は 3 種のみで、いずれも「色ではないもの」である', () => {
  // 理由の集合を固定する。ここが緩むと「テーマにしにくいから対象外」という理由が入り込み、
  //   例外が実質的な逃げ道になる。3 種はいずれも「色として扱うと壊れるもの」である:
  //     shadow          … 影は色ではなく奥行き。白い地でも影は黒が正しい。
  //     transparent     … α=0 は「塗らない」の表現。トークン化すると不透明になり得る。
  //     input-sentinel  … <input type=color> の未指定状態を表す値。描画色ではない。
  const reasons = [...new Set(THEME_EXEMPT_LITERALS.map((e) => e.reason))].sort();
  assert.deepEqual(reasons, ['input-sentinel', 'shadow', 'transparent']);
});

test('段階 5-E: 系列の既定色は color_resolver の単一情報源を読む（複製を配線しない）', () => {
  // properties_dialog / property_control_builders の '#2962ff' は描画物の色ではなく
  //   「解決順ステップ 5 の既定色」の**写し**である。配線点を作るのは複製を正当化することに
  //   なるので、複製そのものを消して単一情報源を import する。
  for (const name of ['properties_dialog.js', 'property_control_builders.js']) {
    const rel = TARGETS.find(([n]) => n === name)[1];
    const src = readFileSync(abs(rel), 'utf8');
    assert.match(src, /DEFAULT_SERIES_COLOR/, `${name}: 既定色の単一情報源を読んでいない`);
  }
});
