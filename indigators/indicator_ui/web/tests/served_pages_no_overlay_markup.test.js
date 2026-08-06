// 配信ページにチャート内オーバーレイの器を直書きさせない（ISSUE-276 再発防止）。
//
// 由来: 表示要素を index.html へ直書きすると、配信される 3 ページすべてへ同じマークアップを
//   手書き複製する義務が生まれる。取り残しは実際に 3 回起きた:
//     - リプレイ #rp-mode の option 5 件が欠落（commit 4079461）
//     - カテゴリボタンの二重表示（ISSUE-221・catalog_categories.test.js が別途固定）
//     - #pane-legends 欠落でペイン別凡例が全滅（ISSUE-276・実配信 unified_ui だけ取り残し）
//   器は描画する View が所有し生成する（overlay_host.js）。ページに要求するのは版面 1 つだけ。

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

// 配信される全ページ。1 つでも器を直書きすると複製同期の義務が復活する。
const SERVED_PAGES = [
  '../index.html',                                   // indicator_ui（ライブ core 8001）
  '../../../../unified_ui/web/index.html',           // 統合 UI（公開 8000・実際に配信される）
  '../../../../simulator/replay_ui/web/index.html',  // リプレイ（8281）
];

// HTML へ書いてはならない器。
//   pane-legends: View が所有し自分で生成する（ISSUE-277）。
//   legend      : ISSUE-276 で撤去した旧凡例（左上に全件を縦積み）。1 ページでも残ると、
//                 そのページだけ旧凡例とペイン別凡例の二重表示に戻る（実配信 unified_ui で発生）。
const VIEW_OWNED_HOSTS = ['pane-legends', 'legend'];

function readPage(rel) {
  return readFileSync(fileURLToPath(new URL(rel, import.meta.url)), 'utf8');
}

test('ISSUE-276 配信される全ページが View 所有の器を直書きしていない', () => {
  for (const page of SERVED_PAGES) {
    const html = readPage(page);
    for (const host of VIEW_OWNED_HOSTS) {
      assert.ok(
        !new RegExp(`id="${host}"`).test(html),
        `${page} に id="${host}" が直書きされている（View 所有の器を HTML へ複製している）`,
      );
    }
  }
});

test('ISSUE-276 配信される全ページが版面 .chart-wrap を持つ（View の唯一の要求）', () => {
  for (const page of SERVED_PAGES) {
    assert.match(readPage(page), /class="chart-wrap"/, `${page} に版面 .chart-wrap が無い`);
  }
});
