// pair_dim_alpha_single_source.test.js — 売買ペア減光 alpha の単一情報源の回帰ガード（ISSUE-095 項目3）。
//
// 背景（実測）: A方式（file:// フラットバンドル）は build.mjs が MODULE_ORDER を連結し import 行を剥がして
//   1 スコープ（IIFE）へ収める。トップレベル `const` 名が 2 モジュールで衝突すると
//   「Identifier '…' has already been declared」の SyntaxError となり A方式が実行時に全崩壊する。
//   実測collision: pair_lines_primitive.js の `const DIM_ALPHA = 0.15`（非ハイライト線減光）と
//   market_profile_primitive.js の `const DIM_ALPHA = 0.30`（累積バー減光・別値・別責務・別モジュール）。
//   is 修正: 0.15 側（pair_lines / trade_markers。同値・同責務=売買ペアのホバー減光）を
//   distinct-name な共有定数 PAIR_DIM_ALPHA へ単一化し、pair 側からトップレベル `DIM_ALPHA` を除去。
//   market_profile 側の `DIM_ALPHA=0.30` は不変（別値ゆえ統合不可・触れない領域）。
//
// 本テストは (A) 単一情報源 (B) 両利用側が共有定数を参照し自前 dim const を持たない、を固定する。
//   かつてあった (C)「A方式バンドルでトップレベル const 名の二重宣言が 0 件」は、A方式そのものを
//   廃止したため削除した（ISSUE-266）。名前衝突を強制する制約はもう存在しない。

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

import { PAIR_DIM_ALPHA } from '../js/adapter/front/pair_render_constants.js';

const WEB_DIR = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const read = (rel) => readFileSync(path.join(WEB_DIR, rel), 'utf8');

const CONST_RE = /^(?:export\s+)?const\s+([A-Za-z0-9_$]+)\s*=/gm;

function topLevelConsts(rel) {
  // build.mjs のフラット化と同基準（行頭・export 剥離後）でトップレベル const 名を抽出。
  return [...read(rel).matchAll(CONST_RE)].map((m) => m[1]);
}

test('(A) 単一情報源: PAIR_DIM_ALPHA は共有定数モジュールで 1 度だけ 0.15 と定義される', () => {
  assert.strictEqual(PAIR_DIM_ALPHA, 0.15, 'PAIR_DIM_ALPHA の値は 0.15（従来挙動不変）であること');
  const decls = topLevelConsts('js/adapter/front/pair_render_constants.js')
    .filter((n) => n === 'PAIR_DIM_ALPHA');
  assert.strictEqual(decls.length, 1, 'PAIR_DIM_ALPHA は共有モジュールで唯一宣言であること');
});

test('(B) 両利用側は共有定数を import して参照し、自前の DIM_ALPHA/_DIM_ALPHA を宣言しない', () => {
  for (const rel of [
    'js/adapter/front/pair_lines_primitive.js',
    'js/adapter/front/trade_markers_renderer.js',
  ]) {
    const src = read(rel);
    assert.match(
      src,
      /import\s*\{[^}]*\bPAIR_DIM_ALPHA\b[^}]*\}\s*from\s*['"]\.\/pair_render_constants\.js['"]/,
      `${rel} は PAIR_DIM_ALPHA を共有定数モジュールから import すること`,
    );
    assert.ok(src.includes('PAIR_DIM_ALPHA'), `${rel} は PAIR_DIM_ALPHA を参照すること`);
    const locals = topLevelConsts(rel).filter((n) => n === 'DIM_ALPHA' || n === '_DIM_ALPHA');
    assert.deepStrictEqual(
      locals, [],
      `${rel} は自前の DIM_ALPHA/_DIM_ALPHA を宣言しないこと（単一情報源化）`,
    );
  }
});

