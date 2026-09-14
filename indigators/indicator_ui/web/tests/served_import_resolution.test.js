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
const REPO_ROOT = resolve(WEB, '..', '..', '..');

// 走査根（配信ルート）の一覧。ISSUE-368 工程 2 是正 2 で**配列化**した。
//   由来: 走査根が indicator_ui だけで、replay 配信根の JS は 1 byte も見ていなかった
//   （replay 固有ファイルの import 誤りを検出できない＝ISSUE-268 と同型の穴）。
//
// **fallback を持たせる理由（実測 2026-08-20・上流前提の訂正）**:
//   replay の配信は dual-root である。`simulator/replay_ui/framework/static_file_server.py:90-103`
//   `resolve()` は web_dir で miss したとき shared_js_root（=indicator_ui/web）へ**同一 rel で
//   フォールバック**する。実測（StaticFileServer を直接呼び出し）:
//     /js/adapter/front/app_chrome_view.js → indigators/indicator_ui/web/... を返す（200）
//     /js/adapter/front/does_not_exist_anywhere.js → None（404）
//   統合 UI も `/replay/*` を replay 上流へプロキシする（unified_ui/router.py:197-233）だけなので
//   同じ規則になる。したがって「replay に symlink が無い＝404」ではない。ここで実体の在処まで
//   厳格に要求すると、実際には 200 で配信されているファイルを落とす**誤検出**になる。
//   本ガードが固定するのは実際の配信規則、すなわち「**どちらの根にも無ければ 404**」である。
const SERVED_ROOTS = [
  { web: WEB, fallback: null },
  { web: join(REPO_ROOT, 'simulator', 'replay_ui', 'web'), fallback: WEB },
];

// 当該配信根で URL 解決できるか（dual-root フォールバックを含む・上記 resolve() と同じ規則）。
function servedExists(target, jsRoot, fallbackWeb) {
  if (existsSync(target)) {
    return true;
  }
  if (!fallbackWeb) {
    return false;
  }
  return existsSync(join(fallbackWeb, 'js', relative(jsRoot, target)));
}

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
  for (const { web, fallback } of SERVED_ROOTS) {
    const jsRoot = join(web, 'js');
    for (const file of walk(jsRoot)) {
      const src = readFileSync(file, 'utf8');
      for (const m of src.matchAll(IMPORT_RE)) {
        const target = resolve(dirname(file), m[1]);
        if (!servedExists(target, jsRoot, fallback)) {
          offenders.push(`${relative(REPO_ROOT, file)} → ${m[1]}`);
        }
        // 配信ルートの外を指していないか（URL では辿れない）。
        if (!target.startsWith(jsRoot) && !target.startsWith(join(web, 'data'))) {
          offenders.push(`${relative(REPO_ROOT, file)} → ${m[1]}（配信ルート外）`);
        }
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
  for (const { web } of SERVED_ROOTS) {
    for (const file of walk(join(web, 'js', 'domain'))) {
      if (!existsSync(file)) broken.push(relative(REPO_ROOT, file));
    }
  }
  assert.deepEqual(broken, [], `壊れた symlink: ${broken.join(', ')}`);
});
