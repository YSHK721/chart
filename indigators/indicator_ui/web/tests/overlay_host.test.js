// overlay_host.js（ensureOverlayHost）の仕様検証。
//
// 設計入力: チャートに重ねる表示系統のホスト要素は「そこへ描く View」が所有し自分で生成する。
//   ページが持つのは版面 .chart-wrap ただ 1 つ（SRP/OCP/DIP）。
// 回帰の由来: 表示要素を index.html へ直書きしていたため配信 3 ページへ手書き複製され、
//   unified_ui（実配信）の取り残しでペイン別凡例が全滅した（ISSUE-276・2026-08-06 実測）。
// 構造: Arrange-Act-Assert（AAA）。実 DOM 非依存（fake document を注入）。

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { ensureOverlayHost, CHART_ANCHOR_SELECTOR } from '../js/adapter/front/overlay_host.js';

// 最小 DOM スタブ（jsdom 等の新規依存を避ける）。className による querySelector だけを解する。
function fakeElement(tagName = 'div', className = '') {
  return {
    tagName,
    className,
    children: [],
    appendChild(n) { this.children.push(n); return n; },
    querySelector(sel) {
      const want = sel.startsWith('.') ? sel.slice(1) : sel;
      for (const c of this.children) {
        if (c.className === want) {
          return c;
        }
      }
      return null;
    },
  };
}

function fakeDoc(anchor) {
  return {
    createElement(tag) { return fakeElement(tag); },
    querySelector(sel) { return (anchor && sel === CHART_ANCHOR_SELECTOR) ? anchor : null; },
  };
}

test('版面配下にホスト要素を生成し、クラス名を所有者名にする', () => {
  const anchor = fakeElement('div', 'chart-wrap');
  const host = ensureOverlayHost(fakeDoc(anchor), { className: 'pane-legends' });

  assert.equal(host.className, 'pane-legends');
  assert.equal(anchor.children.length, 1);
  assert.equal(anchor.children[0], host);
});

test('再入してもホストを増やさない（既存を再利用する）', () => {
  const anchor = fakeElement('div', 'chart-wrap');
  const doc = fakeDoc(anchor);

  const first = ensureOverlayHost(doc, { className: 'pane-legends' });
  const second = ensureOverlayHost(doc, { className: 'pane-legends' });

  assert.equal(first, second);
  assert.equal(anchor.children.length, 1);
});

test('版面を直接注入できる（document への問い合わせを増やさない・DIP）', () => {
  const anchor = fakeElement('div', 'chart-wrap');
  const doc = { createElement: (t) => fakeElement(t) };   // querySelector を持たない document

  const host = ensureOverlayHost(doc, { className: 'pane-legends', anchor });

  assert.equal(anchor.children[0], host);
});

test('DOM はあるのに版面が無ければ例外（フェイルクローズ・無言 no-op にしない）', () => {
  const doc = fakeDoc(null);

  assert.throws(
    () => ensureOverlayHost(doc, { className: 'pane-legends' }),
    /chart-wrap/,
    '版面欠落を黙って握り潰している（ISSUE-276 と同じ無症状の全滅を再発させる）',
  );
});

test('DOM 非対応環境（SSR・純ロジックテスト）では null を返して描画しない', () => {
  assert.equal(ensureOverlayHost(null, { className: 'pane-legends' }), null);
  assert.equal(ensureOverlayHost({}, { className: 'pane-legends' }), null);
  // 要素生成しか持たないスタブ（版面を解決する手段が無い）＝契約違反ではなく縮退。
  //   実ブラウザの document は必ず querySelector を持つため、実配信ページはこの経路に入らない。
  assert.equal(ensureOverlayHost({ createElement: () => ({}) }, { className: 'pane-legends' }), null);
});

test('className は必須（所有者不明のホストを作らせない）', () => {
  const anchor = fakeElement('div', 'chart-wrap');
  assert.throws(() => ensureOverlayHost(fakeDoc(anchor), {}), /className/);
});
