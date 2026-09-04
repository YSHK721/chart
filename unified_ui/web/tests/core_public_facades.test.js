// 統合層が名指す **公開面 URL がすべて実在する**ことの固定（ISSUE-479 Wave2b J-5）。
//
// なぜ在るか（J-4 の検出力の裏返し）:
//   `js_cross_subsystem_paths.test.js`（G-3）は「内部階層を名指していないか」だけを見る。
//   これは形の検査なので、`/sim/js/public/存在しない.js` と書いても**緑のまま通る**。
//   統合層のこれらの URL は識別子渡しの動的 import で読まれるため、綴り間違い・置き換え忘れは
//   import 走査にも型にも現れず、実 UI で初めて 404 として現れる（ISSUE-291 と同型の「無言の死」）。
//   よって「名指した公開面が実在し、名指した名前を実際に公開しているか」を別に固定する。
//
// 公開面の規約（既存 2 本 live_public_api.js / replay_public_api.js の様式を検査へ落とす）:
//   - 中身を持たない（再輸出だけ）。ここに実装を書くと「公開用の第 2 実装」が生まれる。
//   - 再輸出は**名前を明示**する。`export *` は何を公開しているかがファイルから読めず、
//     消費者が要る名前が消えても気付けない。
//
// URL → リポジトリ実体の対応は**配信の地形**であり、production の JS はどれも知らない
//   （production が知ってよいのは URL だけ）。検定だけがここを知り、両者を突合する。
//
// 構造は AAA。テスト名は「対象_条件_期待結果」。

import { describe, test, expect } from 'vitest';
import { existsSync, readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

import { MODES } from '../js/mode_table.js';
import { collectSources, stripComments, PUBLIC_URL_RE } from '../../../tools/js_layer_guard.mjs';

const WEB = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const JS_ROOT = path.join(WEB, 'js');
const REPO_ROOT = path.resolve(WEB, '..', '..');

//: URL prefix → その core の web 配信根（リポジトリ相対）。
const CORE_WEB_ROOT = Object.freeze({
  '/live': 'indigators/indicator_ui/web',
  '/replay': 'simulator/replay_ui/web',
  '/sim': 'simulator/sim_ui/web',
  '/dashboard': 'dashboard_ui/web',
});

/** 公開面 URL（`/<core>/js/public/x.js`）→ リポジトリ上の絶対パス。 */
function repoPathOf(url) {
  const prefix = Object.keys(CORE_WEB_ROOT).find((p) => url.startsWith(`${p}/`));
  expect(prefix, `未知の core を名指している: ${url}`).toBeDefined();
  return path.join(REPO_ROOT, CORE_WEB_ROOT[prefix], url.slice(prefix.length + 1));
}

/** 統合層の JS 本文に現れる公開面 URL（重複排除・コメントは除外）。 */
function declaredPublicUrls() {
  const found = new Set();
  for (const source of collectSources([JS_ROOT]).values()) {
    for (const m of stripComments(source).matchAll(/'(\/[A-Za-z0-9_\-./]+\.js)'/g)) {
      if (PUBLIC_URL_RE.test(m[1])) {
        found.add(m[1]);
      }
    }
  }
  return [...found].sort();
}

describe('統合層が名指す core の公開面', () => {
  test('every_public_facade_url_named_by_the_root_resolves_to_a_real_file', () => {
    // Arrange
    const urls = declaredPublicUrls();

    // Assert: 空振り（0 件で緑）を塞いでから突合する。
    expect(urls.length).toBeGreaterThan(0);
    const missing = urls.filter((url) => !existsSync(repoPathOf(url)));
    expect(missing).toEqual([]);
  });

  test('a_declared_display_layer_export_is_actually_published_by_that_facade', () => {
    // Arrange: 表が宣言した据付関数名を、実体の公開面が名前で再輸出しているか。
    const declared = MODES.filter((m) => m.displayLayerPath);
    expect(declared.length).toBeGreaterThan(0);

    // Assert
    for (const row of declared) {
      const src = stripComments(readFileSync(repoPathOf(row.displayLayerPath), 'utf8'));
      expect(
        src,
        `${row.displayLayerPath} が ${row.displayLayerExport} を名前で公開していない`,
      ).toMatch(new RegExp(`export\\s*\\{[^}]*\\b${row.displayLayerExport}\\b[^}]*\\}\\s*from\\s*['"]`));
    }
  });

  test('a_public_facade_holds_no_implementation_of_its_own', () => {
    // Assert: 公開面は再輸出だけを置く（実装を書くと「公開用の第 2 実装」が生まれる）。
    //   コメントと空行を落とした残りが、すべて `export … from '…';` であること。
    for (const row of MODES.filter((m) => m.displayLayerPath)) {
      const lines = stripComments(readFileSync(repoPathOf(row.displayLayerPath), 'utf8'))
        .split('\n').map((l) => l.trim()).filter((l) => l !== '');
      expect(lines.length).toBeGreaterThan(0);
      for (const line of lines) {
        expect(line, `${row.displayLayerPath} に再輸出でない行がある: ${line}`)
          .toMatch(/^export\s.*\sfrom\s*['"][^'"]+['"];$/);
      }
    }
  });

  test('a_public_facade_reexports_only_modules_that_exist_under_its_own_core', () => {
    // Assert: 再輸出先が実在し、かつ**自 core の配信根の中**にあること（URL で辿れない外部を
    //   指すと、node のテストは通るのにブラウザは 404 になる＝served_import_resolution と同型）。
    for (const row of MODES.filter((m) => m.displayLayerPath)) {
      const facade = repoPathOf(row.displayLayerPath);
      const coreJsRoot = path.dirname(path.dirname(facade)); // …/web/js
      const src = stripComments(readFileSync(facade, 'utf8'));
      const specs = [...src.matchAll(/from\s*['"](\.[^'"]+)['"]/g)].map((m) => m[1]);
      expect(specs.length).toBeGreaterThan(0);
      for (const spec of specs) {
        const target = path.resolve(path.dirname(facade), spec);
        expect(existsSync(target), `${row.displayLayerPath} → ${spec} が実在しない`).toBe(true);
        expect(target.startsWith(coreJsRoot), `${spec} が配信根の外を指している`).toBe(true);
      }
    }
  });
});
