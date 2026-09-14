// 単一ソース共有 symlink の健全性（ISSUE-095 項目5・レビュー 🔵）。
//
// indicator_ui/web/js は market_profile/web/js の domain/adapter モジュールを symlink で
// 単一ソース共有する（session_ohlc.js・mp_source_capability.js・market_profile_actor.js 等）。
// symlink が壊れる（対象消失・パス誤り）と、B方式(served)/A方式(bundle) とも当該モジュールの
// 読込に失敗して UI がクラッシュする。symlink 非対応環境（一部 Windows checkout・zip 展開）や
// 対象ファイル移動時に切れる脆さがあるため、全 symlink が実ファイルへ解決することを構造的に固定する。

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readdirSync, lstatSync, statSync, realpathSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const JS_DIR = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', 'js');
const REPO_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..', '..', '..');

// 走査根の一覧（ISSUE-368 工程 2 是正 2 で**配列化**）。単一ソース共有は indicator_ui だけでなく
//   replay 配信根でも運用されている（replay の js 配下は 100 本超が symlink）。走査根が
//   indicator_ui だけだと、replay 側で symlink が壊れても本ガードは緑のままだった。
//   **読み先を増やしただけでアサーションは変えていない**。
const JS_DIRS = [
  JS_DIR,
  path.join(REPO_ROOT, 'simulator', 'replay_ui', 'web', 'js'),
];

// js/ 配下の symlink を再帰列挙する。
function findSymlinks(dir) {
  const out = [];
  for (const name of readdirSync(dir)) {
    const full = path.join(dir, name);
    const st = lstatSync(full);
    if (st.isSymbolicLink()) {
      out.push(full);
    } else if (st.isDirectory()) {
      out.push(...findSymlinks(full));
    }
  }
  return out;
}

test('single-source symlinks under web/js all resolve to existing files', () => {
  const links = JS_DIRS.flatMap((dir) => findSymlinks(dir));
  // 単一ソース共有は現に運用されている＝0 件なら列挙ロジックの破綻を疑う。
  assert.ok(links.length > 0, 'web/js に symlink が 1 件も見つからない（列挙ロジックの破綻の疑い）');

  const broken = [];
  const escaped = [];
  for (const link of links) {
    let real;
    try {
      real = realpathSync(link); // symlink を辿って実体パスを解決（対象不在なら throw）。
    } catch {
      broken.push(path.relative(REPO_ROOT, link));
      continue;
    }
    // 実体が通常ファイルであること。
    if (!statSync(real).isFile()) {
      broken.push(`${path.relative(REPO_ROOT, link)} -> ${path.relative(REPO_ROOT, real)}（非ファイル）`);
    }
    // 実体がリポジトリ内に収まること（外部を指す symlink は配布時に切れる）。
    if (!real.startsWith(REPO_ROOT + path.sep)) {
      escaped.push(`${path.relative(REPO_ROOT, link)} -> ${real}`);
    }
  }
  assert.deepStrictEqual(broken, [], `解決できない/非ファイルの symlink:\n${broken.join('\n')}`);
  assert.deepStrictEqual(escaped, [], `リポジトリ外を指す symlink:\n${escaped.join('\n')}`);
});
