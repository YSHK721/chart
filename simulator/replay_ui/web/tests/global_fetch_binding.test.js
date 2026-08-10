// global_fetch_binding.test.js — グローバル `fetch` を捕まえる箇所は必ず束縛する（ISSUE-349）。
//
// 病因（実 UI 実測で 2 度確定）: ネイティブ `fetch` は `this === window/globalThis` を要求する。
//   素の参照のまま注入すると、受け取った側が `this._fetch(...)` とメソッド呼び出しした瞬間に
//   "Failed to execute 'fetch' on 'Window': Illegal invocation" で必ず失敗する。
//
// なぜ**走査**で固定するのか: 既存の node 検定はどれも fake fetch を注入するため、既定値の経路を
//   1 度も通らない。つまり「振る舞いのテスト」ではこの不具合を構造的に捕まえられない（実際、
//   347 件が緑のままブラウザだけが壊れていた）。捕まえられるのは「グローバルを捕まえる場所で
//   束縛しているか」という**書き方の不変条件**だけであり、それをここで固定する。
//
// なぜ消費者側ではなくここか: 回避を消費者ごとに置くと、書き忘れた消費者が現れるたびに再発する
//   （forming_seq_client.js と composition_root_front.js が各々回避していた一方、後から入った
//   ReplayCursor と forming_plan_cache が再発させた）。レシーバが失われるのは「グローバルを
//   捕まえる場所」ただ 1 点なので、そこを塞ぐ。

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const JS_ROOT = join(dirname(fileURLToPath(import.meta.url)), '..', 'js');

function jsFiles(dir) {
  const out = [];
  for (const name of readdirSync(dir)) {
    const path = join(dir, name);
    // symlink 越しの共有実体（indicator_ui/web/js）は本スイートの対象外＝実体側で固定する。
    if (statSync(path).isDirectory()) {
      out.push(...jsFiles(path));
    } else if (name.endsWith('.js')) {
      out.push(path);
    }
  }
  return out;
}

// グローバル `fetch` を値として捕まえている箇所のうち、束縛していないもの。
//
//   捕捉は**行をまたぐ**（三項の条件と結果が別行に分かれる）ため、行単位ではなくファイル全体を
//   空白 1 個へ正規化してから見る。行単位で見ると、三項の**条件側**
//   （`typeof globalThis !== 'undefined' && globalThis.fetch` の出現）を捕捉と誤判定する。
//
//   除外するのは「捕まえていない出現」だけ:
//     `.bind(` が続く … 束縛済み ／ `?` `:` `)` が続く … 三項の条件・区切り（値を持ち出していない）
const CAPTURE_PATTERNS = [
  // 病因そのものの形（旧 replay.js:51 の既定値）。
  /\btypeof\s+fetch\s*!==\s*['"]undefined['"]\s*\?\s*fetch\b(?!\s*\.bind\s*\()/,
  // globalThis 経由で値として持ち出す形。
  /\bglobalThis\.fetch\b(?!\s*\.bind\s*\()(?!\s*[?:)])/,
];

// `//` 行コメントを落として空白を 1 個へ正規化する（説明文中の病因の書き写しを拾わない）。
function normalizeSource(text) {
  return text.split('\n')
    .map((line) => line.replace(/\/\/.*$/, ''))
    .join(' ')
    .replace(/\s+/g, ' ');
}

test('検出器の自己検査: 既知の不良形を実際に検出する（検出しない検出器を置かない）', () => {
  // Arrange: 左が ISSUE-349 の不良形、右が是正後の形。
  const bad = [
    "fetchImpl = (typeof fetch !== 'undefined' ? fetch : undefined)",
    'const f = globalThis.fetch;',
  ];
  const good = [
    "const boundFetch = (typeof globalThis !== 'undefined' && globalThis.fetch) ? globalThis.fetch.bind(globalThis) : undefined;",
    "const fetchFn = fetchImpl || (typeof globalThis !== 'undefined' && globalThis.fetch ? globalThis.fetch.bind(globalThis) : null);",
  ];
  // Act / Assert
  for (const src of bad) {
    assert.ok(CAPTURE_PATTERNS.some((re) => re.test(normalizeSource(src))), `検出漏れ: ${src}`);
  }
  for (const src of good) {
    assert.ok(!CAPTURE_PATTERNS.some((re) => re.test(normalizeSource(src))), `誤検出: ${src}`);
  }
});

test('グローバル fetch を捕まえる箇所は必ず globalThis へ束縛する（ISSUE-349 の再発防止）', () => {
  // Arrange
  const offenders = [];
  // Act
  for (const path of jsFiles(JS_ROOT)) {
    const src = normalizeSource(readFileSync(path, 'utf8'));
    if (CAPTURE_PATTERNS.some((re) => re.test(src))) {
      offenders.push(path.slice(JS_ROOT.length + 1));
    }
  }
  // Assert
  assert.deepEqual(offenders, [],
    `素の fetch を捕まえている（this 付き呼出で Illegal invocation になる）: ${offenders.join(', ')}`);
});

test('replay.js は束縛済みの fetch を既定値に使う（真因の在り処を逐語で固定）', () => {
  // Arrange: 不具合の真因は replay.js の既定値だった（forming_seq_client.js のコメントが名指し）。
  const src = readFileSync(join(JS_ROOT, 'replay.js'), 'utf8');
  // Act / Assert
  assert.ok(
    /globalThis\.fetch\.bind\(globalThis\)/.test(src),
    'replay.js が globalThis へ束縛した fetch を持っていない',
  );
  assert.ok(
    /fetchImpl\s*=\s*boundFetch\b/.test(src),
    'setupReplay の fetchImpl 既定値が束縛済みの値になっていない',
  );
});
