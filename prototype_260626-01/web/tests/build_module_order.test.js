// build.mjs の MODULE_ORDER 整合性（A方式バンドル漏れ防止・🟡-2 回帰）。
//
// A方式（file://）は build.mjs が MODULE_ORDER を連結し import 行を剥がして 1 スコープへ
// 収める。相対 import される実モジュールが MODULE_ORDER に未登録だと、シンボルが
// 「定義されないまま呼ばれる」状態でバンドルされ、A方式が実行時クラッシュする
// （新規 ESM 追加時に起きやすい）。本テストは「バンドル対象が相対 import する先は
// すべて MODULE_ORDER に含まれる」ことを構造的に固定する。

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const WEB_DIR = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');

function moduleOrder() {
  const src = readFileSync(path.join(WEB_DIR, 'build.mjs'), 'utf8');
  const block = src.match(/const MODULE_ORDER = \[([\s\S]*?)\];/);
  assert.ok(block, 'build.mjs に MODULE_ORDER 配列が見つからない');
  return [...block[1].matchAll(/'([^']+)'/g)].map((m) => m[1]);
}

function relativeImports(fileRel) {
  const src = readFileSync(path.join(WEB_DIR, fileRel), 'utf8');
  return [...src.matchAll(/from\s+['"](\.[^'"]+)['"]/g)].map((m) => m[1]);
}

test('build.mjs MODULE_ORDER contains every relatively-imported bundled module (A方式バンドル漏れ防止)', () => {
  const order = moduleOrder();
  const orderSet = new Set(order);
  for (const fileRel of order) {
    const dir = path.posix.dirname(fileRel);
    for (const spec of relativeImports(fileRel)) {
      // 相対 import を web/ 基準の posix パスへ正規化して MODULE_ORDER と突き合わせる。
      const resolved = path.posix.normalize(path.posix.join(dir, spec));
      assert.ok(
        orderSet.has(resolved),
        `${fileRel} が import する '${spec}'（=> ${resolved}）が MODULE_ORDER に未登録。`
        + ' A方式バンドルでシンボル未定義になる。build.mjs の MODULE_ORDER に追加すること。',
      );
    }
  }
});
