// installSharedUi が右クリックメニューへ**項目を注入できる**ことの検証（ISSUE-368 スライス 8-c）。
//
// 設計入力（唯一の仕様源）: .doc/POSITION_SIZING_CHART_INTEGRATION_DESIGN.md
//   「ピッカー経路の実測検証」3: 既存 `ChartContextMenu` に**項目注入**する
//   （`installSharedUi` へ `contextMenuItems=[]` 引数を追加し root から渡す。
//    **共有配線への無条件追加は replay を汚染するため禁止**。自前 new は二重リスナーになるため禁止）。
//
// 観点: 「引数が生えているだけ」では、どこにも繋がっていない偽の口を見逃す。実際に右クリックして
//   出る項目まで見る（ISSUE-291「受け口だけでなく端から端まで結線を固定」）。
// 構造: Arrange-Act-Assert。最小 DOM スタブ（版面アンカー .chart-wrap を持つ）。

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

import { installSharedUi } from '../js/adapter/front/chart_app_wiring.js';

class El {
  constructor() {
    this.children = [];
    this.dataset = {};
    this.style = {};
    this.textContent = '';
    this.type = '';
    this.className = '';
    this.innerHTML = '';
    this.parentElement = null;
    this._handlers = {};
    this.classList = { add: () => {}, remove: () => {}, contains: () => false, toggle: () => {} };
  }

  appendChild(k) { k.parentElement = this; this.children.push(k); return k; }

  insertBefore(k) { k.parentElement = this; this.children.unshift(k); return k; }

  append(...kids) { for (const k of kids) { this.appendChild(k); } return undefined; }

  querySelector() { return null; }

  getBoundingClientRect() { return { left: 0, top: 0 }; }

  addEventListener(type, fn) { (this._handlers[type] ||= []).push(fn); }

  fire(type, ev = {}) { (this._handlers[type] || []).forEach((fn) => fn(ev)); }
}

function fakeDoc() {
  const wrap = new El();
  const app = new El();   // ツールバー・ダイアログのアンカー（app_chrome_view が要求する #app）。
  const doc = {
    createElement: () => new El(),
    querySelector: (sel) => {
      if (sel === '.chart-wrap') return wrap;
      if (sel === '#app') return app;
      return null;
    },
    getElementById: () => null,
    addEventListener: () => {},
    removeEventListener: () => {},
    body: new El(),
  };
  return { doc, wrap };
}

function fakeContainer() {
  return new El();
}

function fakeRenderer() {
  return {
    panPriceByPixels() {},
    handlePriceWheel: () => false,
    isOverPriceAxis: () => false,
    resetPriceZoom() {},
    setPaneHeight() {},
    isLatestBarVisible: () => true,
    scrollToLatest() {},
    barInfoAt: () => null,
  };
}

function boot(extra = {}) {
  const { doc, wrap } = fakeDoc();
  const container = fakeContainer();
  const shared = installSharedUi({
    container,
    renderer: fakeRenderer(),
    doc,
    getController: () => null,
    updatePaneHeight: () => {},
    ...extra,
  });
  return { container, wrap, shared };
}

// 右クリックして出た項目のラベル一覧（版面ホスト配下のボタン）。
function openMenuLabels(container, wrap, ev = { clientX: 120, clientY: 200 }) {
  container.fire('contextmenu', { ...ev, preventDefault() {} });
  const host = wrap.children[wrap.children.length - 1];
  return { host, labels: host.children.map((b) => b.textContent) };
}

test('TC-CX01 contextMenuItems 未注入なら従来どおり（項目は「情報をコピーする」だけ＝replay を汚染しない）', () => {
  // Arrange / Act
  const { container, wrap } = boot();
  const { labels } = openMenuLabels(container, wrap);
  // Assert
  assert.deepEqual(labels, ['情報をコピーする']);
});

test('TC-CX02 注入した項目が既存項目の後ろに出る（既存メニューは無改変・項目追加は OCP）', () => {
  // Arrange
  const { container, wrap } = boot({
    contextMenuItems: [
      { label: 'この価格を損切りに設定', onSelect: () => {} },
      { label: 'この価格を建値に追加', onSelect: () => {} },
    ],
  });
  // Act
  const { labels } = openMenuLabels(container, wrap);
  // Assert
  assert.deepEqual(labels, ['情報をコピーする', 'この価格を損切りに設定', 'この価格を建値に追加']);
});

test('TC-CX03 注入した項目の onSelect にはコンテナ基準の座標が渡る（価格解決の入力）', () => {
  // Arrange
  const seen = [];
  const { container, wrap } = boot({
    contextMenuItems: [{ label: '損切り', onSelect: (ctx) => seen.push(ctx) }],
  });
  // Act
  const { host } = openMenuLabels(container, wrap, { clientX: 42, clientY: 84 });
  host.children[1].fire('click');
  // Assert
  assert.deepEqual(seen, [{ x: 42, y: 84 }]);
});

test('TC-CX04 共有配線は自分では項目を足さない（無条件追加の禁止・注入機構の維持）', () => {
  // 対象の付け替え（依頼者裁定 2026-08-20）: 本検定は元は「リプレイ root は注入しない」を見ていたが、
  //   確定要件（ISSUE.md:6927「ライブ＋リプレイ両方に載せる」）により**両 root が注入する**形へ
  //   変わった。守るべき不変条件は「共有配線が無条件に足さないこと」＝どのページに出すかを root が
  //   決められること（計算機を載せないページが将来増えても、そのページだけ出ない状態を作れる）。
  //   よって対象を root から**仕組み単体（共有配線のソース）**へ付け替える。アサーションの形
  //   （equal(..., false)）は変えていない。
  // Arrange: 共有配線の実ファイルを読む。
  const src = readFileSync(
    fileURLToPath(new URL('../js/adapter/front/chart_app_wiring.js', import.meta.url)),
    'utf8',
  );
  // Act: installSharedUi の本体（次の export まで）で価格設定項目を自分で作っていないか。
  const body = src.slice(src.indexOf('export function installSharedUi'), src.indexOf('function createColorThemeUi'));
  // Assert
  assert.equal(
    /createPositionSizingContextItems\(|createPriceContextItems\(/.test(body),
    false,
    '共有配線が価格設定項目を自分で足している（どのページに出すかを root が決められなくなる）',
  );
});
