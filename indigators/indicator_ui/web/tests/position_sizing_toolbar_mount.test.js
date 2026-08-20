// app_chrome_view.installChartToolbar がポジションサイズ計算機の空マウントを生成することの固定。
//
// 設計入力（唯一の仕様源）: .doc/POSITION_SIZING_CHART_INTEGRATION_DESIGN.md
//   スライス 6（`app_chrome_view.js` に空マウント 1 行追加
//   `<div class="position-sizing-menu" id="position-sizing-menu"></div>`。
//   項目 DOM は position_sizing_menu.js が生成し、**index.html は 1 枚も触らない**）、
//   §6 アダプター設計（器＝installChartToolbar が生成する空マウント #position-sizing-menu）。
//
// 同型元: color_theme_toolbar_mount.test.js。ただし INDEX_HTML の一覧は**手書き複製せず**
//   単一ソース（tests/index_html_pages.js）を共有する（工程 2 是正 1）。
//
// 本ファイルが index.html の無改変まで固定する理由: 器を HTML へ直書きすると、配信 4 ページへ
//   同じマークアップを手書き複製する義務が復活する（ISSUE-278 #16 が撤去した状態への逆戻り）。
// 構造: Arrange-Act-Assert（AAA）。最小 DOM スタブ。

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

import { installChartToolbar } from '../js/adapter/front/app_chrome_view.js';
import { INDEX_HTML } from './index_html_pages.js';

class El {
  constructor() {
    this.children = [];
    this.className = '';
    this.id = '';
    this.innerHTML = '';
    this.firstChild = null;
  }

  appendChild(k) { this.children.push(k); return k; }

  insertBefore(k) { this.children.unshift(k); return k; }

  querySelector() { return null; }
}

function makeDoc() {
  const anchor = new El();
  const doc = {
    createElement: () => new El(),
    querySelector: (sel) => (sel === '#app' ? anchor : null),
  };
  return { doc, anchor };
}

test('TC-PS01 ツールバーに #position-sizing-menu の空マウントを生成する', () => {
  // Arrange
  const { doc } = makeDoc();
  // Act
  const bar = installChartToolbar(doc, {});
  // Assert
  assert.ok(bar.innerHTML.includes('id="position-sizing-menu"'), '計算機メニューの空マウントが無い');
  assert.ok(
    bar.innerHTML.includes('class="position-sizing-menu"'),
    'クラスは既存の器と同じ命名規約（id と同名のクラス）に従う',
  );
});

test('TC-PS02 空マウントはテーマメニューの直後・インジケーターボタンの前に置く', () => {
  // Arrange
  const { doc } = makeDoc();
  // Act
  const html = installChartToolbar(doc, {}).innerHTML;
  // Assert
  const theme = html.indexOf('id="color-theme-menu"');
  const sizing = html.indexOf('id="position-sizing-menu"');
  const indicator = html.indexOf('id="indicator-open-btn"');
  assert.ok(theme >= 0 && sizing >= 0 && indicator >= 0, '3 つの器がすべて在席する');
  assert.ok(theme < sizing, '計算機はテーマの右隣');
  assert.ok(sizing < indicator, '計算機はインジケーターボタンより前');
});

test('TC-PS03 空マウントはツールバー 1 本につき 1 個だけ（統合 UI でもボタンは 1 個）', () => {
  // Arrange
  const { doc } = makeDoc();
  // Act
  const html = installChartToolbar(doc, { liveFollow: true, enterReplay: true }).innerHTML;
  // Assert
  assert.equal([...html.matchAll(/id="position-sizing-menu"/g)].length, 1);
});

test('TC-PS04 冪等: 既にツールバーがあれば再生成しない（再 mount で器が増えない）', () => {
  // Arrange
  const { doc, anchor } = makeDoc();
  const existing = new El();
  anchor.querySelector = (sel) => (sel === '.toolbar' ? existing : null);
  // Act
  const bar = installChartToolbar(doc, {});
  // Assert
  assert.equal(bar, existing, '既存のツールバーを返す（二重生成しない）');
  assert.equal(bar.innerHTML, '', '既存ツールバーへ器を継ぎ足さない');
});

test('TC-PS05 index.html は 1 枚も触らない: 4 ページに position-sizing-menu が 0 件', () => {
  // Arrange / Act / Assert
  for (const rel of INDEX_HTML) {
    const src = readFileSync(fileURLToPath(new URL(rel, import.meta.url)), 'utf8');
    assert.equal(
      [...src.matchAll(/position-sizing-menu/g)].length,
      0,
      `${rel} に器が手書きされている（器は app_chrome_view が生成する＝ISSUE-278 #16）`,
    );
  }
});

test('TC-PS06 既存の器（tf-menu / tpl-menu / color-theme-menu）は無改変で 1 個ずつ在席する', () => {
  // Arrange
  const { doc } = makeDoc();
  // Act
  const html = installChartToolbar(doc, {}).innerHTML;
  // Assert
  assert.equal([...html.matchAll(/id="tf-menu"/g)].length, 1);
  assert.equal([...html.matchAll(/id="tpl-menu"/g)].length, 1);
  assert.equal([...html.matchAll(/id="color-theme-menu"/g)].length, 1);
  assert.ok(html.includes('<div class="tpl-menu" id="tpl-menu"></div>'), 'テンプレートの器は従来のまま');
  assert.ok(
    html.includes('<div class="color-theme-menu" id="color-theme-menu"></div>'),
    'テーマの器は従来のまま',
  );
});
