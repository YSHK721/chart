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

import { styleRules, stripComments } from './_css.js';

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
    //
    // 読み方を `}` 分割から波括弧の数え上げ（_css.js）へ改めた（ISSUE-463）。旧実装は
    //   `@media` の前置きを選択子と誤認して落ちるため、暗色テーマを持てなかった。**検査する
    //   性質は変えていない**——「すべての規則の選択子が `.dash-` から始まる」ことである。
    const rules = styleRules(CSS);
    assert.ok(rules.length > 0, '規則が 1 つも読めていません（読み取りが壊れています）');
    for (const rule of rules) {
      const keyframes = rule.at.find((at) => at.startsWith('@keyframes'));
      if (keyframes) {
        // @keyframes の内側の「選択子」は時刻（from/to/%）であり要素を選ばない。
        //   共有ページへ漏れる面は**アニメーション名**（グローバル名前空間）なので、
        //   そちらへ同じ接頭辞を要求する。
        assert.match(keyframes, /^@keyframes\s+dash-/,
          `統合ページへ漏れるアニメーション名があります: ${keyframes}`);
        continue;
      }
      for (const part of rule.selector.split(',')) {
        assert.match(part.trim(), /^\.dash-/,
          `統合ページへ漏れる選択子があります: ${part.trim()}`);
      }
    }
  });

  test('the_stylesheet_looks_inside_at_rules_instead_of_skipping_them', () => {
    // 検定の検定: at-rule の中身を素通しにすると、上の検査は `@media` の内側に置かれた
    //   `:root { … }` を見逃す（＝無言の no-op に化ける）。中身を実際に見ていることを固定する。
    const nested = styleRules(CSS).filter((rule) => rule.at.length > 0);
    assert.ok(nested.length > 0, '入れ子（@media 等）の規則を 1 つも読めていません');
  });

  test('the_stylesheet_publishes_no_token_to_the_shared_document_root', () => {
    // 版面モックはパレットを `:root` へ置くが、本 CSS は共有ページの <head> へ差し込まれる
    //   （sheet_host.js）。`--bg` `--ink` のような一般名を `:root` へ出すと、同じ名前を使う
    //   移植元 style.css（sim）と衝突して統合ページの配色が壊れる。宣言先は host に閉じる。
    assert.equal(/:root/.test(stripComments(CSS)), false,
      'CSS が :root へトークンを公開しています（共有ページへ漏れます）');
  });
});
