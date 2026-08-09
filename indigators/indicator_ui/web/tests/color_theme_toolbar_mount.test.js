// app_chrome_view.installChartToolbar がテーマメニューの空マウントを生成することの固定。
//
// 設計入力（唯一の仕様源）: .doc/indicator-management-ui/基本設計_指標カラーテーマ.md v0.3.1
//   §6.1（ツールバーの並びは [NI225] [日 ▾] [ライブ] [テンプレート ▾] [テーマ ▾] [インジケーター]
//        [リプレイ]。空マウント `<div class="color-theme-menu" id="color-theme-menu"></div>` は
//        `app_chrome_view.installChartToolbar` が生成し、項目 DOM は color_theme_menu.js が生成する。
//        **index.html は 1 枚も触らない**＝ISSUE-278 #16 の規約）。
//
// 本ファイルが index.html の無改変まで固定する理由: 器を HTML へ直書きすると、配信 4 ページへ
//   同じマークアップを手書き複製する義務が復活する（ISSUE-278 #16 が撤去した状態への逆戻り）。
//   「View が生成した」ことだけを見るテストでは、HTML 側にも書かれてしまった状態を検出できない。
// 構造: Arrange-Act-Assert（AAA）。最小 DOM スタブ。

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

import { installChartToolbar } from '../js/adapter/front/app_chrome_view.js';

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

const INDEX_HTML = [
  '../../../../indigators/indicator_ui/web/index.html',
  '../../../../simulator/replay_ui/web/index.html',
  '../../../../simulator/report_ui/web/index.html',
  '../../../../unified_ui/web/index.html',
];

test('TC-CT01 ツールバーに #color-theme-menu の空マウントを生成する（§6.1）', () => {
  // Arrange
  const { doc } = makeDoc();
  // Act
  const bar = installChartToolbar(doc, {});
  // Assert
  assert.ok(bar.innerHTML.includes('id="color-theme-menu"'), 'テーマメニューの空マウントが無い');
  assert.ok(bar.innerHTML.includes('class="color-theme-menu"'), 'クラスは §1.3 の命名規約（color-theme-）に従う');
});

test('TC-CT02 空マウントは tpl-menu の直後・インジケーターボタンの前に置く（§6.1 の並び）', () => {
  // Arrange
  const { doc } = makeDoc();
  // Act
  const html = installChartToolbar(doc, {}).innerHTML;
  // Assert
  const tpl = html.indexOf('id="tpl-menu"');
  const theme = html.indexOf('id="color-theme-menu"');
  const indicator = html.indexOf('id="indicator-open-btn"');
  assert.ok(tpl >= 0 && theme >= 0 && indicator >= 0, '3 つの器がすべて在席する');
  assert.ok(tpl < theme, 'テーマはテンプレートの右隣（テンプレート → テーマ）');
  assert.ok(theme < indicator, 'テーマはインジケーターボタンより前');
});

test('TC-CT03 テーマの空マウントはツールバー 1 本につき 1 個だけ（統合 UI でもボタンは 1 個・E-17）', () => {
  // Arrange
  const { doc } = makeDoc();
  // Act
  const html = installChartToolbar(doc, { liveFollow: true, enterReplay: true }).innerHTML;
  // Assert
  assert.equal([...html.matchAll(/id="color-theme-menu"/g)].length, 1);
});

test('TC-CT04 冪等: 既にツールバーがあれば再生成しない（再 mount で器が増えない）', () => {
  // Arrange
  const { doc, anchor } = makeDoc();
  const existing = new El();
  anchor.querySelector = (sel) => (sel === '.toolbar' ? existing : null);
  // Act
  const bar = installChartToolbar(doc, {});
  // Assert
  assert.equal(bar, existing, '既存のツールバーを返す（二重生成しない）');
});

test('TC-CT05 index.html は 1 枚も触らない: 4 ページに color-theme-menu が 0 件（ISSUE-278 #16）', () => {
  // Arrange / Act / Assert
  for (const rel of INDEX_HTML) {
    const src = readFileSync(fileURLToPath(new URL(rel, import.meta.url)), 'utf8');
    assert.equal(
      [...src.matchAll(/color-theme-menu/g)].length,
      0,
      `${rel} に器が手書きされている（器は app_chrome_view が生成する＝ISSUE-278 #16）`,
    );
  }
});

test('TC-CT06 既存の器（tf-menu / tpl-menu）は無改変で 1 個ずつ在席する（既存を壊さない）', () => {
  // Arrange
  const { doc } = makeDoc();
  // Act
  const html = installChartToolbar(doc, {}).innerHTML;
  // Assert
  assert.equal([...html.matchAll(/id="tf-menu"/g)].length, 1);
  assert.equal([...html.matchAll(/id="tpl-menu"/g)].length, 1);
  assert.ok(html.includes('<div class="tpl-menu" id="tpl-menu"></div>'), 'テンプレートの器は従来のまま');
});
