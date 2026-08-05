// A方式バンドル（file://）が**実際にビルドでき、構文として正しい**ことを検定する（ISSUE-265）。
//
// build.mjs は ES Modules の import 行を剥がして 1 つの IIFE スコープへ連結する。この方式は
// 「別名束縛（`import { X as Y }` → `const Y = X;`）が複数モジュールで衝突する」という破れ方を
// する。実際 indicator_controller.js と tickvol_bands_controller.js がともに
// `toggleVisible as facadeToggleVisible` を使っていたため `const facadeToggleVisible` が
// 二重宣言となり、**A方式は構文エラーで起動不能**になっていた。
//
// 既存の build_module_order.test.js は「相対 import 先が MODULE_ORDER に在るか」だけを見るため
// この破れ方を検出できない。本テストは実際に build.mjs を走らせ、生成物を構文解析する。
//
// 追跡下の out/prototype.html は手で保守されるため、ソース変更に対して**古くなっていても
// 気付けない**。本テストはソースから毎回組み直すので、陳腐化と構文破れの両方を落とす。

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { readFileSync, mkdtempSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const WEB = dirname(dirname(fileURLToPath(import.meta.url)));
const OUT = join(WEB, '..', 'out', 'prototype.html');

function buildAndExtract() {
  execFileSync(process.execPath, ['build.mjs'], { cwd: WEB, stdio: 'pipe' });
  const html = readFileSync(OUT, 'utf8');
  const blocks = [...html.matchAll(/<script[^>]*>([\s\S]*?)<\/script>/g)].map((m) => m[1]);
  assert.ok(blocks.length > 0, 'バンドルに script ブロックがある');
  return blocks.reduce((a, b) => (a.length >= b.length ? a : b));
}

test('build.mjs が成功し、生成物の JS が構文として正しい', () => {
  const js = buildAndExtract();
  const dir = mkdtempSync(join(tmpdir(), 'bundle-check-'));
  const file = join(dir, 'bundle.js');
  writeFileSync(file, js);
  // node --check は構文エラー（重複宣言を含む）を非ゼロ終了で落とす。
  execFileSync(process.execPath, ['--check', file], { stdio: 'pipe' });
});

test('フラットスコープに同名の const 宣言が重複していない', () => {
  const js = buildAndExtract();
  const counts = new Map();
  for (const line of js.split('\n')) {
    const m = line.match(/^const\s+([A-Za-z0-9_$]+)\s*=/);
    if (m) counts.set(m[1], (counts.get(m[1]) || 0) + 1);
  }
  const dupes = [...counts.entries()].filter(([, n]) => n > 1).map(([k, n]) => `${k}×${n}`);
  assert.deepEqual(dupes, [],
    `フラットスコープで const が重複宣言されています: ${dupes.join(', ')}。`
    + ' A方式が起動不能になります（別名の衝突は build.mjs が除去する）。');
});

test('Python 生成物がバンドルへ取り込まれている（MODULE_ORDER 登録漏れの検出）', () => {
  const js = buildAndExtract();
  // 台帳と MP 能力の生成シンボルが含まれること＝生成物が MODULE_ORDER に在ること。
  assert.match(js, /TF_LEDGER/, '時間足台帳の生成物が取り込まれている');
  assert.match(js, /ZP_SUPPORTED_TFS/, 'MP 能力の生成物が取り込まれている');
});
