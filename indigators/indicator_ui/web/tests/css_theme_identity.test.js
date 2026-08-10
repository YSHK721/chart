// css_theme_identity.test.js — 通過条件 1（恒等）の全数固定（段階 5-D）。
//
// 何を守るか: 「テーマ未選択のとき、CSS が解決する色が接続前と 1 つ残らず同値」。これが崩れると、
//   テーマ機能を足しただけで既定の見た目が変わる＝退行である。1 色でもずれたら落ちる形にする。
//
// なぜ**目録**（色 → 出現回数）で固定するか: 個々の宣言を 1 件ずつ書き写すと 303 行の写経になり、
//   写し間違いがそのまま「正しいことになっている値」になる。目録なら、どの宣言がどの配線点を
//   読むかを変えても（＝設計を直しても）、画面に出る色の集合が動いていないことだけを見張れる。
//   宣言と配線点の対応は別の検定（current_price_css_tokens.test.js の走査）が持つ。
//
// 下の EXPECTED_* は**接続前の実ファイルからの実測値**である（設計値ではない）。
//   計測: 変更前の app.css / replay_bar.css に現れる色リテラルを、3 桁 hex を 6 桁へ、
//   rgba の空白を詰めて正規化したうえで数えたもの。合計 303 件 / 47 色、29 件 / 14 色。

import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

import { resolveAllChrome } from '../js/usecase/color_resolver.js';

const abs = (rel) => fileURLToPath(new URL(rel, import.meta.url));
const APP_CSS = readFileSync(abs('../css/app.css'), 'utf8');
const REPLAY_CSS = readFileSync(abs('../../../../simulator/replay_ui/web/css/replay_bar.css'), 'utf8');

// 色としての同一性で数える（#fff と #ffffff は同じ色・rgba の空白は色を変えない）。
function normalizeColor(value) {
  let s = String(value).trim().toLowerCase();
  if (/^#[0-9a-f]{3}$/.test(s)) {
    s = `#${s.slice(1).split('').map((c) => c + c).join('')}`;
  }
  return s.replace(/\s+/g, '');
}

function inventory(text) {
  const counts = new Map();
  for (const m of text.matchAll(/#[0-9a-fA-F]{3,8}\b|rgba?\([^)]*\)/g)) {
    const key = normalizeColor(m[0]);
    counts.set(key, (counts.get(key) ?? 0) + 1);
  }
  return counts;
}

// var(--ct-X, fallback) を「テーマ未設定での解決値」へ展開する。fallback ではなく**解決値**を
//   使うのが要点: fallback が現行値でも、解決値が別物なら JS 動作時に色が変わる。両方が現行値で
//   あることを、この展開と下の目録一致が同時に固定する。
// fallback は rgba(...) を含みうるので、内側の括弧を 1 段許す。素朴な [^)]* は rgba( の
//   閉じ括弧で切れてしまい、fallback を取り違えたまま「一致した」ことにできてしまう。
const VAR_RE = /var\(--ct-([A-Za-z]+),\s*((?:[^()]|\([^()]*\))*)\)/g;

function expandThemeVars(css) {
  const { tokens, cssSlots } = resolveAllChrome(null);
  const resolved = { ...tokens, ...cssSlots };
  return css.replace(new RegExp(VAR_RE.source, 'g'), (whole, name) => {
    assert.ok(name in resolved, `--ct-${name} は台帳に無い（applier が書かない変数を CSS が読む）`);
    assert.notEqual(resolved[name], null, `--ct-${name} の解決値が null`);
    return resolved[name];
  });
}

// --- 接続前の実測目録（逐語）---------------------------------------------

const EXPECTED_APP = [
  ['#2a2e39', 60], ['#ffffff', 38], ['#d1d4dc', 36], ['#2962ff', 23], ['#787b86', 22],
  ['#9aa0ad', 14], ['#1e222d', 10], ['#131722', 9], ['#23272f', 8], ['#b2b5be', 8],
  ['#363a45', 7], ['#ef5350', 7], ['#e6e9ef', 5], ['rgba(0,0,0,.55)', 5], ['#b03a30', 4],
  ['rgba(0,0,0,.45)', 4], ['rgba(0,0,0,0.5)', 4], ['rgba(30,34,45,.82)', 4], ['#1e53e5', 3],
  ['#e0564a', 3], ['#5d616b', 2], ['#e0a24a', 2], ['#181b24', 1], ['#1c2030', 1],
  ['#26a69a', 1], ['#2962ff22', 1], ['#2a2410', 1], ['#2a3354', 1], ['#363b49', 1],
  ['#3a3d47', 1], ['#3a4050', 1], ['#44474f', 1], ['#5a4a18', 1], ['#6b7088', 1],
  ['#7b2233', 1], ['#93293e', 1], ['#b8bec9', 1], ['#cfd8ff', 1], ['#e0b84a', 1],
  ['#f0b400', 1], ['#ff6b6b', 1], ['rgba(0,0,0,.5)', 1], ['rgba(19,23,34,0.72)', 1],
  ['rgba(19,23,34,0.82)', 1], ['rgba(224,162,74,0.08)', 1], ['rgba(28,32,48,.95)', 1],
  ['rgba(41,98,255,0.6)', 1],
];

const EXPECTED_REPLAY = [
  ['#d1d4dc', 6], ['#2a2e39', 4], ['#787b86', 3], ['#b2b5be', 3], ['#0c0e15', 2],
  ['#222735', 2], ['#2962ff', 2], ['#161a25', 1], ['#1f2431', 1], ['#26a69a', 1],
  ['#4a4e5a', 1], ['#e6e8ea', 1], ['#ffffff', 1], ['rgba(0,0,0,.5)', 1],
];

function assertInventory(css, expected, label) {
  const actual = inventory(expandThemeVars(css));
  const want = new Map(expected);
  const keys = [...new Set([...want.keys(), ...actual.keys()])].sort();
  const diffs = keys
    .filter((k) => (want.get(k) ?? 0) !== (actual.get(k) ?? 0))
    .map((k) => `${k}: 接続前 ${want.get(k) ?? 0} → 接続後 ${actual.get(k) ?? 0}`);
  assert.deepEqual(diffs, [], `${label}: 解決される色が接続前から動いた\n${diffs.join('\n')}`);
}

test('通過条件 1: app.css がテーマ未設定で解決する色は接続前と全数一致（303 件 / 47 色）', () => {
  // Arrange / Act / Assert
  assertInventory(APP_CSS, EXPECTED_APP, 'app.css');
  const total = EXPECTED_APP.reduce((s, [, n]) => s + n, 0);
  assert.equal(total, 303, '接続前の実測総数');
  assert.equal(EXPECTED_APP.length, 47, '接続前の実測色数');
});

test('通過条件 1: replay_bar.css がテーマ未設定で解決する色は接続前と全数一致（29 件 / 14 色）', () => {
  // Arrange / Act / Assert
  assertInventory(REPLAY_CSS, EXPECTED_REPLAY, 'replay_bar.css');
  const total = EXPECTED_REPLAY.reduce((s, [, n]) => s + n, 0);
  assert.equal(total, 29, '接続前の実測総数');
  assert.equal(EXPECTED_REPLAY.length, 14, '接続前の実測色数');
});

test('通過条件 1: var() の fallback もテーマ未設定の解決値と文字列一致する（JS 不動作時も同一）', () => {
  // JS が :root へ書けない状況（SSR・スクリプト無効・F-C11）では fallback が最終値になる。
  //   解決値だけを合わせて fallback を放置すると、その状況でだけ色が変わる。
  const { tokens, cssSlots } = resolveAllChrome(null);
  const resolved = { ...tokens, ...cssSlots };
  for (const css of [APP_CSS, REPLAY_CSS]) {
    for (const m of css.matchAll(new RegExp(VAR_RE.source, 'g'))) {
      const [, name, fallback] = m;
      assert.equal(normalizeColor(fallback), normalizeColor(resolved[name]),
        `--ct-${name}: fallback ${fallback} と解決値 ${resolved[name]} が別の色`);
    }
  }
});

test('段階 5-D: 無効な色リテラルを持つ宣言が残っていない（パーサに破棄される宣言を作らない）', () => {
  // 接続前の app.css には `color: #5d6madd`（`m` は 16 進数字でない）があり、宣言はパーサに
  //   破棄されていた＝プレースホルダは UA 既定色で描かれていた。var() で包むと「宣言の破棄」から
  //   「computed-value time で無効」へ変わり、color が inherit して見た目が動く。よって削除した。
  //   意図した色は復元不能なので推測で埋めない（現行の見た目＝UA 既定色をそのまま保つ）。
  for (const css of [APP_CSS, REPLAY_CSS]) {
    // 走査対象は規則の**本体**（`{` と `}` の間）から、さらにコメントを除いたもの。
    //   セレクタ側の `#app` `#chart` は id であって色ではなく、本体内のコメントにも
    //   `#chart-overlay-tl` のような id 参照が現れるため、どちらも先に落とす。
    const bodies = [...css.replace(/\/\*[\s\S]*?\*\//g, '').matchAll(/\{([^{}]*)\}/g)]
      .map((m) => m[1]).join('\n');
    const bad = [...bodies.matchAll(/#[0-9a-zA-Z]{3,9}/g)]
      .map((m) => m[0])
      .filter((v) => !/^#([0-9a-fA-F]{3}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$/.test(v));
    assert.deepEqual(bad, [], `16 進として読めない色リテラル: ${bad.join(' / ')}`);
  }
});
