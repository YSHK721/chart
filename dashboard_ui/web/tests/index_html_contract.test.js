// 配信ページ（index.html）が**版面だけ**を持ち、表を直書きしていないことの固定。
//
// なぜ機械的に検査するか（ISSUE-221 / ISSUE-276 の実測）: 表示要素を index.html へ直書きすると、
//   配信される各ページへ同じマークアップが手書き複製され、表示系統を足すたびに全ページを同期
//   する義務が生まれる。取り残しは実際に 3 回起きており、いずれも**無症状**だった
//   （overlay_host.js:1-22）。宣言ではなく検査で強制する（MEMORY: enforce-constraints-mechanically）。
//
// 併せて CSS が heat_scale の役割を奪っていないことも見る（配色の基準は 1 冊に 1 つ・§5.5.7）。

import { test, describe } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

const HTML = readFileSync(fileURLToPath(new URL('../index.html', import.meta.url)), 'utf8');
const CSS = readFileSync(fileURLToPath(new URL('../css/dashboard.css', import.meta.url)), 'utf8');

describe('index.html — 配信ページが持つのは版面だけ', () => {
  test('the_page_declares_no_table_markup_of_its_own', () => {
    // 表は View が生成し所有する。
    for (const tag of ['<table', '<thead', '<tbody', '<tr', '<td', '<th']) {
      assert.equal(HTML.includes(tag), false, `index.html が表を直書きしています: ${tag}`);
    }
  });

  test('the_page_provides_exactly_one_anchor_for_the_view_to_attach_to', () => {
    // DIP: View は注入されたアンカーに依存する。ページが要求されるのはこの 1 要素だけ。
    const anchors = HTML.match(/id="dashboard-anchor"/g) || [];
    assert.equal(anchors.length, 1);
  });

  test('the_page_boots_through_the_composition_root_not_through_ad_hoc_script', () => {
    assert.match(HTML, /composition_root_front\.js/);
    assert.match(HTML, /setupDashboardDisplay/);
  });

  test('the_stylesheet_defines_no_colour_scale_of_its_own', () => {
    // §5.5.7: 配色の基準は heat_scale.js が唯一源。CSS が p→色を持ち始めると第 2 定義になる。
    const withoutComments = CSS.replace(/\/\*[\s\S]*?\*\//g, '');
    for (const selector of ['dash-ladder-band', 'dash-osc-cell', 'dash-osc-tail-unscaled']) {
      const block = new RegExp(`\\.${selector}[^{]*\\{[^}]*background-color\\s*:`, 'i');
      assert.equal(block.test(withoutComments), false,
        `CSS が ${selector} の背景色を決めています（配色は heat_scale.js が唯一源）`);
    }
  });

  test('the_stylesheet_scopes_every_rule_under_the_view_owned_host', () => {
    // host は sim と共有の器。素の要素セレクタを置くと統合ページ全体へ規則が漏れる
    //   （sim が iframe を選んだのと同じ問題・style.css 波及の実測）。
    const rules = CSS.replace(/\/\*[\s\S]*?\*\//g, '')
      .split('}')
      .map((chunk) => chunk.split('{')[0].trim())
      .filter(Boolean);
    for (const selector of rules) {
      for (const part of selector.split(',')) {
        assert.match(part.trim(), /^\.dash-/,
          `統合ページへ漏れる選択子があります: ${part.trim()}`);
      }
    }
  });
});
