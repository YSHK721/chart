// Worker のスクリプト URL が配信ルートで **実際に解決できる** ことを検定する（ISSUE-368 スライス 5）。
//
// なぜ既存の served_import_resolution.test.js では足りないか（実測）:
//   同検定の `IMPORT_RE = /(?:from|import)\s*\(?\s*['"](\.[^'"]+)['"]/g` は `from` / `import(` の
//   形しか拾わない。`new Worker(new URL('./x_worker.js', import.meta.url))` は **どちらでもない**ため
//   走査の対象外で、パスを 1 文字間違えても全テストが緑のまま実 UI だけが静かに壊れる
//   （ISSUE-268 と同型の失敗モード。あちらは symlink 欠落で node は緑・ブラウザは 404 だった）。
//
// 本検定が固定すること:
//   1. front 配下の `new Worker(new URL('<rel>', import.meta.url)` の対象が配信ルート配下に実在する
//   2. Worker 本体が **domain しか import しない**（設計書 §8 依存方向図）。
//      Worker には window も document も無く、DOM・lwc・fetch を掴んだ時点で実行時に落ちる。
//      「動かしてみたら落ちた」ではなく、構造で止める。

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, existsSync, readdirSync, statSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join, resolve, relative } from 'node:path';

const WEB = dirname(dirname(fileURLToPath(import.meta.url)));
const JS_ROOT = join(WEB, 'js');
const FRONT_ROOT = join(JS_ROOT, 'adapter', 'front');

// `new Worker(new URL('<rel>', import.meta.url)` の相対指定を抜く。
const WORKER_URL_RE = /new\s+Worker\s*\(\s*new\s+URL\s*\(\s*['"](\.[^'"]+)['"]\s*,\s*import\.meta\.url/g;
// Worker 本体が持ってよい import 先（domain のみ）。
const ALLOWED_WORKER_IMPORT_RE = /^\.\.\/\.\.\/domain\//;
// 相対 import 全般（served_import_resolution.test.js と同じ形）。
const IMPORT_RE = /(?:from|import)\s*\(?\s*['"](\.[^'"]+)['"]/g;
// Worker 本体が触ってはいけない実行環境 API（Worker スコープに存在しない／使わせない）。
const FORBIDDEN_IN_WORKER = ['document', 'window', 'fetch(', 'localStorage', 'lightweight-charts'];

function walk(dir) {
  const out = [];
  for (const name of readdirSync(dir)) {
    const p = join(dir, name);
    if (statSync(p).isDirectory()) { out.push(...walk(p)); } else if (name.endsWith('.js')) { out.push(p); }
  }
  return out;
}

// front 配下で宣言されている Worker エントリの一覧（[{ from, target }]）。
function workerEntries() {
  const found = [];
  for (const file of walk(FRONT_ROOT)) {
    const src = readFileSync(file, 'utf8');
    for (const m of src.matchAll(WORKER_URL_RE)) {
      found.push({ from: file, spec: m[1], target: resolve(dirname(file), m[1]) });
    }
  }
  return found;
}

test('front 配下に Worker エントリが宣言されている（走査が空振りしていない）', () => {
  assert.ok(workerEntries().length > 0,
    'Worker URL が 1 件も見つからない＝正規表現が現行の書き方を拾えていない可能性');
});

test('Worker のスクリプト URL が配信ルート配下に実在する', () => {
  const offenders = [];
  for (const entry of workerEntries()) {
    if (!existsSync(entry.target)) {
      offenders.push(`${relative(WEB, entry.from)} → ${entry.spec}（実在しない）`);
    } else if (!entry.target.startsWith(JS_ROOT)) {
      offenders.push(`${relative(WEB, entry.from)} → ${entry.spec}（配信ルート外）`);
    }
  }
  assert.deepEqual(offenders, [],
    'ブラウザが 404 になる Worker URL があります（node のテストは緑のまま実 UI だけ壊れる）:\n  '
    + offenders.join('\n  '));
});

test('Worker 本体は domain しか import しない（依存方向図 §8）', () => {
  const offenders = [];
  for (const entry of workerEntries()) {
    if (!existsSync(entry.target)) {
      continue;   // 実在しないことは上のテストが報告する。
    }
    const src = readFileSync(entry.target, 'utf8');
    for (const m of src.matchAll(IMPORT_RE)) {
      if (!ALLOWED_WORKER_IMPORT_RE.test(m[1])) {
        offenders.push(`${relative(WEB, entry.target)} → ${m[1]}`);
      }
    }
  }
  assert.deepEqual(offenders, [],
    'Worker 本体が domain 以外を import しています（Worker には DOM も lwc も無い）:\n  '
    + offenders.join('\n  '));
});

test('Worker 本体は DOM・fetch・lwc を参照しない（実行時に落ちる依存を構造で止める）', () => {
  const offenders = [];
  for (const entry of workerEntries()) {
    if (!existsSync(entry.target)) {
      continue;
    }
    const src = readFileSync(entry.target, 'utf8')
      .split('\n')
      .filter((line) => !line.trim().startsWith('//'))   // 説明文中の言及は対象外。
      .join('\n');
    for (const banned of FORBIDDEN_IN_WORKER) {
      if (src.includes(banned)) {
        offenders.push(`${relative(WEB, entry.target)} が ${banned} を参照している`);
      }
    }
  }
  assert.deepEqual(offenders, []);
});
