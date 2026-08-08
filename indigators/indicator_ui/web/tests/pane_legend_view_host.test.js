// PaneLegendView のホスト所有（ISSUE-276 の再発防止）。
//
// 設計入力: 凡例の器は **PaneLegendView が所有し自分で生成する**。index.html には書かない。
//   旧構成は配信 3 ページ（indicator_ui / replay_ui / unified_ui）の HTML へ
//   `<div id="pane-legends">` を手書き複製する前提で、実配信の unified_ui だけ取り残された結果
//   `getElementById` が null → render が無言 no-op となり、凡例が全滅していた（2026-08-06 実測）。
// 構造: Arrange-Act-Assert（AAA）。実 DOM 非依存（fake document を注入）。

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { PaneLegendView } from '../js/adapter/front/pane_legend_view.js';

function fakeElement(tagName = 'div', className = '') {
  const el = {
    tagName,
    className,
    textContent: '',
    title: '',
    type: '',
    style: {},
    dataset: {},
    children: [],
    get innerHTML() { return this._innerHTML ?? ''; },
    set innerHTML(v) { this._innerHTML = v; if (v === '') { this.children = []; } },
    append(...nodes) { for (const n of nodes) { this.children.push(n); } },
    appendChild(n) { this.children.push(n); return n; },
    addEventListener() {},
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
  return el;
}

function fakeDoc(anchor) {
  return {
    createElement(tag) { return fakeElement(tag); },
    getElementById() { return null; },   // 左上オーバーレイ不在＝オフセット 0（本検証の関心外）
    querySelector(sel) { return (anchor && sel === '.chart-wrap') ? anchor : null; },
  };
}

const MODEL = {
  groups: [
    { paneIndex: 0, top: 0, height: 400, rows: [{ instanceId: 'ma#1', values: [{ name: 'MA', value: 100 }] }] },
    { paneIndex: 1, top: 402, height: 160, rows: [{ instanceId: 'osc#1', values: [{ name: 'OSC', value: 0.5 }] }] },
  ],
};
const ROWS = [
  { instanceId: 'ma#1', label: 'MA', visible: true },
  { instanceId: 'osc#1', label: 'OSC', visible: true },
];

test('HTML に器が無くても、View が版面配下へ自分でホストを生成して描画する', () => {
  const anchor = fakeElement('div', 'chart-wrap');
  const view = new PaneLegendView({ document: fakeDoc(anchor) });

  view.setInstances(ROWS);
  view.update(MODEL);

  const host = anchor.querySelector('.pane-legends');
  assert.ok(host, '版面配下に .pane-legends が生成されていない');
  assert.equal(host.children.length, 2, 'ペイン数ぶんの凡例グループが描かれていない');
  assert.equal(host.children[0].className, 'pane-legend');
});

test('再描画してもホストは 1 つのまま（クロスヘア移動のたびに器が増えない）', () => {
  const anchor = fakeElement('div', 'chart-wrap');
  const view = new PaneLegendView({ document: fakeDoc(anchor) });

  view.setInstances(ROWS);
  view.update(MODEL);
  view.update(MODEL);
  view.update(MODEL);

  const hosts = anchor.children.filter((c) => c.className === 'pane-legends');
  assert.equal(hosts.length, 1);
});

test('系列を持たない指標（アクター駆動型）も行が出る＝適用後に操作不能にならない', () => {
  // market_profile / tickvol_bands は自前プリミティブで描くため renderer にスロットが無く、
  //   renderer のモデルにも現れない（実測: ライブ診断で「スロットなし（未描画）」）。
  //   在席権威が renderer 側だと目/歯車/× を失い、旧 #legend 撤去後は除去手段が消える。
  const anchor = fakeElement('div', 'chart-wrap');
  const view = new PaneLegendView({ document: fakeDoc(anchor) });

  view.setInstances([...ROWS, { instanceId: 'market_profile#1', label: 'マーケットプロファイル', visible: true }]);
  view.update(MODEL);   // モデルは ma#1 / osc#1 しか知らない

  const host = anchor.querySelector('.pane-legends');
  const pane0 = host.children.find((c) => c.dataset.paneIndex === '0');
  assert.ok(pane0, '価格ペインの凡例グループが無い');
  assert.equal(pane0.children[0].textContent, '∿ 2', 'MP の行が価格ペインに数えられていない');
});

test('版面（.chart-wrap）が無いページでは例外＝無症状の全滅にしない', () => {
  const view = new PaneLegendView({ document: fakeDoc(null) });

  // 最初の描画（行の供給）で即座に落ちる＝取り残しに気付けない状態を作らない。
  assert.throws(() => view.setInstances(ROWS), /chart-wrap/);
});

test('DOM 非対応環境（SSR・純ロジックテスト）は従来どおり no-op', () => {
  const view = new PaneLegendView({ document: null });
  view.setInstances(ROWS);
  view.update(MODEL);   // 例外を投げない
});

// ---- 表示仕様（依頼者指示 2026-08-08） ----
//
//   1. 行の並びは「指標名 → 設定（操作）→ 値」。値は桁数で伸び縮みするため最後に置き、
//      操作ボタンの位置が値によって動かないようにする。
//   2. 基本は**オープン**（既定で開いた状態）。畳むのは利用者がチップを押したときだけ。
//   ISSUE-276 で既定を折りたたみ・並びを「名前 → 値 → 操作」としていたのを、本指示で変更した。

test('行の並びは 指標名 → 設定（目/歯車/×）→ 値', () => {
  const anchor = fakeElement('div', 'chart-wrap');
  const view = new PaneLegendView({ document: fakeDoc(anchor) });

  view.setInstances(ROWS);
  view.update(MODEL);

  const group = anchor.querySelector('.pane-legends').children[0];
  const rows = group.children.find((c) => c.className === 'pane-legend-rows');
  const row = rows.children[0];
  assert.deepEqual(
    row.children.map((c) => c.className),
    ['pane-legend-name', 'pane-legend-visibility', 'pane-legend-gear', 'pane-legend-remove', 'pane-legend-values'],
  );
});

test('既定はオープン（チップは is-open・行が最初から描かれる）', () => {
  const anchor = fakeElement('div', 'chart-wrap');
  const view = new PaneLegendView({ document: fakeDoc(anchor) });

  view.setInstances(ROWS);
  view.update(MODEL);

  const group = anchor.querySelector('.pane-legends').children[0];
  const chip = group.children.find((c) => c.className.includes('pane-legend-chip'));
  assert.ok(chip.className.includes('is-open'), '既定で開いていない');
  assert.ok(group.children.some((c) => c.className === 'pane-legend-rows'), '行が描かれていない');
});

test('チップを押せば畳める（開閉そのものは従来どおり利用者の操作）', () => {
  const anchor = fakeElement('div', 'chart-wrap');
  const view = new PaneLegendView({ document: fakeDoc(anchor) });
  view.setInstances(ROWS);
  view.update(MODEL);

  view.toggle(0);
  view.update(MODEL);

  const group = anchor.querySelector('.pane-legends').children[0];
  assert.equal(group.children.some((c) => c.className === 'pane-legend-rows'), false, '畳めていない');
});
