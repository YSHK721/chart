// 配信ルート（served・URL 解決）で相対 import が **実際に解決できる** ことを検定する（ISSUE-268）。
//
// なぜ node のテストでは足りないか:
//   共有モジュールは symlink で複数スライスへ配られる。node は import を **実ファイルの位置**から
//   解決するため、symlink の実体側（market_profile/web/js/...）に依存先があればテストは通る。
//   一方ブラウザは **URL** で解決するため、配信ルート（indicator_ui/web/js/...）に同名の
//   symlink が無いと 404 になり、UI が起動しない。
//
//   実際にこの差で本番が壊れた（ISSUE-267 の作業中）。market_profile_actor.js へ
//   `import '../../domain/growth_window.js'` を足したが indicator_ui 側に symlink が無く、
//   node テスト 1,071 件は全通過したまま実 UI が真っ白になった。
//
// 本検定は配信ルートを起点に相対 import を辿り、**そのルート配下にファイルが実在するか**を見る。

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, existsSync, readdirSync, statSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join, resolve, relative } from 'node:path';

const WEB = dirname(dirname(fileURLToPath(import.meta.url)));
const JS_ROOT = join(WEB, 'js');

function walk(dir) {
  const out = [];
  for (const name of readdirSync(dir)) {
    const p = join(dir, name);
    // symlink も対象（配信されるのは symlink 自身の URL）。
    const st = statSync(p);
    if (st.isDirectory()) out.push(...walk(p));
    else if (name.endsWith('.js')) out.push(p);
  }
  return out;
}

// `import ... from '<rel>'` / `import('<rel>')` の相対指定を抜く。
const IMPORT_RE = /(?:from|import)\s*\(?\s*['"](\.[^'"]+)['"]/g;

test('配信ルート配下の全 JS の相対 import が、同ルート配下で解決できる', () => {
  const offenders = [];
  for (const file of walk(JS_ROOT)) {
    const src = readFileSync(file, 'utf8');
    for (const m of src.matchAll(IMPORT_RE)) {
      const target = resolve(dirname(file), m[1]);
      if (!existsSync(target)) {
        offenders.push(`${relative(WEB, file)} → ${m[1]}`);
      }
      // 配信ルートの外を指していないか（URL では辿れない）。
      if (!target.startsWith(JS_ROOT) && !target.startsWith(join(WEB, 'data'))) {
        offenders.push(`${relative(WEB, file)} → ${m[1]}（配信ルート外）`);
      }
    }
  }
  assert.deepEqual(offenders, [],
    '配信ルートで解決できない import があります（node は通るがブラウザは 404 になる）:\n  '
    + offenders.join('\n  ')
    + '\n  共有モジュールなら js/domain 等へ symlink を張ってください。');
});

test('js/domain の symlink がすべて実ファイルへ解決する', () => {
  const broken = [];
  for (const file of walk(join(JS_ROOT, 'domain'))) {
    if (!existsSync(file)) broken.push(relative(WEB, file));
  }
  assert.deepEqual(broken, [], `壊れた symlink: ${broken.join(', ')}`);
});
