// installChartToolbar のモード切替ボタンを「定義配列」で受ける契約（基本設計書 §3.5.6 #10）。
//
// 解決する問題: 旧実装はモード切替ボタンを `enterReplay` という**真偽 1 個**で制御していた。
//   モードが 3 つ（ライブ / リプレイ / シミュレーション）になると、真偽フラグを増やすたびに
//   本 View の markup 分岐を書き足すことになる＝OCP 違反。ボタンの集合を呼び出し側から
//   **配列で注入**する形へ変え、モードが増えても本 View は変わらない状態にする。
//
// 依存方向の順守: ライブ core（本モジュール）は統合層（unified_ui）を import しない。
//   モード定義表は統合層が所有し、ここへは「id / ラベル / title の配列」として注入される
//   （standalone live は注入しない＝ボタン 0 個で従来どおり）。
//
// 構造: Arrange-Act-Assert。最小 DOM スタブ（色テーマ検定と同型）。

import { test } from 'node:test';
import assert from 'node:assert/strict';

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
  return {
    createElement: () => new El(),
    querySelector: (sel) => (sel === '#app' ? anchor : null),
  };
}

// 統合層が注入する 3 モード構成（unified_ui の定義表から作られる形と同じ）。
const MODE_BUTTONS = [
  { id: 'enter-replay', label: 'リプレイ', title: 'リプレイ表示のオン・オフ' },
  { id: 'enter-sim', label: 'シミュレーション', title: 'シミュレーション表示のオン・オフ' },
];

test('TC-MB01 modeButtons を注入すると各ボタンが id / ラベル / title / aria-pressed 付きで生える', () => {
  // Arrange
  const doc = makeDoc();
  // Act
  const html = installChartToolbar(doc, { liveFollow: true, modeButtons: MODE_BUTTONS }).innerHTML;
  // Assert
  for (const b of MODE_BUTTONS) {
    assert.ok(html.includes(`id="${b.id}"`), `${b.id} が生えていない`);
    assert.ok(html.includes(`>${b.label}</button>`), `${b.id} のラベルが ${b.label} でない`);
    assert.ok(html.includes(`title="${b.title}"`), `${b.id} の title が無い`);
  }
  // 点灯状態は applyModeUi が更新するため、初期値は全て false。
  assert.equal([...html.matchAll(/aria-pressed="false"/g)].length >= MODE_BUTTONS.length, true);
});

test('TC-MB02 ボタンは注入順に並び、それぞれ区切り（tb-sep）を伴う', () => {
  // Arrange
  const doc = makeDoc();
  // Act
  const html = installChartToolbar(doc, { liveFollow: true, modeButtons: MODE_BUTTONS }).innerHTML;
  // Assert
  const replay = html.indexOf('id="enter-replay"');
  const sim = html.indexOf('id="enter-sim"');
  assert.ok(replay >= 0 && sim >= 0);
  assert.ok(replay < sim, '注入順（リプレイ → シミュレーション）で並ぶ');
  assert.ok(
    html.includes('<span class="tb-sep"></span><button id="enter-sim"'),
    'ボタンの直前に区切りが入る（既存のリプレイボタンと同流儀）',
  );
});

test('TC-MB03 modeButtons が空配列ならボタンは 1 個も生えない', () => {
  // Arrange
  const doc = makeDoc();
  // Act
  const html = installChartToolbar(doc, { liveFollow: true, modeButtons: [] }).innerHTML;
  // Assert
  assert.equal([...html.matchAll(/id="enter-/g)].length, 0);
});

test('TC-MB04 後方互換: enterReplay:true の markup は modeButtons でリプレイ 1 件を渡した場合と byte 一致', () => {
  // Arrange
  const legacyHtml = installChartToolbar(makeDoc(), { liveFollow: true, enterReplay: true }).innerHTML;
  // Act
  const tableHtml = installChartToolbar(makeDoc(), {
    liveFollow: true,
    modeButtons: [{ id: 'enter-replay', label: 'リプレイ', title: 'リプレイ表示のオン・オフ' }],
  }).innerHTML;
  // Assert: 既存呼び出し（真偽フラグ）と表駆動の生成物が完全一致する＝移行で markup が動かない。
  assert.equal(tableHtml, legacyHtml);
});

// --- 🟡-6: 注入値の検証とエスケープ ---------------------------------------------------
//
// 本 View は受け取った定義を **innerHTML の文字列へ埋め込む**。注入元は統合層のモード定義表
//   だが、View 自身は注入元を選べない（それが注入の意味である）。埋め込む値を検証・
//   エスケープしない限り、id に属性を割り込ませる文字列が来れば markup が壊れ、label/title に
//   `<` や `"` が来れば要素構造が崩れる。壊れた markup は「ボタンが出ない」という無症状の
//   失敗になり、原因追跡が難しい。よって埋め込む直前で形を固定する。

test('TC-MB06 id が識別子の形でなければ例外（無言で壊れた markup を作らない）', () => {
  // Arrange / Act / Assert
  for (const badId of ['1abc', 'a b', 'x" onclick="y', '', 'a<b', null, undefined, 42]) {
    assert.throws(
      () => installChartToolbar(makeDoc(), { modeButtons: [{ id: badId, label: 'L', title: 'T' }] }),
      /id/,
      `不正な id (${String(badId)}) が通っている`,
    );
  }
});

test('TC-MB07 正当な id は通る（検証が過剰に効いていない）', () => {
  // Arrange / Act / Assert
  for (const okId of ['enter-sim', 'enter_sim', 'a', 'A1', 'x-1_y']) {
    const html = installChartToolbar(makeDoc(), { modeButtons: [{ id: okId, label: 'L', title: 'T' }] }).innerHTML;
    assert.ok(html.includes(`id="${okId}"`), `正当な id (${okId}) が拒否されている`);
  }
});

test('TC-MB08 label / title は HTML エスケープして埋め込む', () => {
  // Arrange
  const doc = makeDoc();
  // Act
  const html = installChartToolbar(doc, {
    modeButtons: [{ id: 'enter-x', label: '<b>&"L"</b>', title: 'a "b" & <c>' }],
  }).innerHTML;
  // Assert: 生の山括弧・引用符が属性/本文へ素通ししない。
  assert.ok(!html.includes('<b>'), 'label の生タグが素通りしている');
  assert.ok(html.includes('&lt;b&gt;&amp;&quot;L&quot;&lt;/b&gt;'), `label が実体参照化されていない: ${html}`);
  assert.ok(html.includes('title="a &quot;b&quot; &amp; &lt;c&gt;"'), `title が実体参照化されていない: ${html}`);
  // 属性を割り込ませられていない（button 要素は 1 個のまま）。
  assert.equal([...html.matchAll(/<button id="enter-x"/g)].length, 1);
});

test('TC-MB05 後方互換: modeButtons 未指定 + enterReplay:false はボタン 0 個（standalone live 不変）', () => {
  // Arrange / Act
  const html = installChartToolbar(makeDoc(), { liveFollow: false }).innerHTML;
  // Assert
  assert.equal([...html.matchAll(/id="enter-/g)].length, 0);
  assert.equal([...html.matchAll(/id="live-follow-toggle"/g)].length, 0);
});
