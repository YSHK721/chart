// bootstrap のツールバー構成注入（基本設計書 §11.1 裁定 3 = L-1）。
//
// 解決する問題: ツールバー構成は `composition_root_front.js` に
//   `toolbar: { liveFollow: true, enterReplay: !!replay }` と**直書き**されていた。
//   統合層（unified_ui）が 3 モードのボタンを置きたくても、ライブ core の合成根を書き換える以外に
//   手段が無い＝拡張点の欠如。`bootstrap` が構成を引数で受け取れば、モードの集合を知っているのは
//   統合層だけ、という依存方向を保ったまま第 3・第 4 モードを足せる。
//
// 後方互換: 未注入時の構成は現行値（liveFollow:true / リプレイ層が注入されていれば enter-replay）と
//   等価でなければならない。standalone live のツールバーは 1 バイトも変わらない。
//
// 構造: Arrange-Act-Assert。lwc / fetch は Fake、DOM はツールバーの生成物だけを観測する最小スタブ。

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { bootstrap } from '../js/adapter/front/composition_root_front.js';

// --- 最小 DOM スタブ（app_chrome_view が要求する範囲だけ）-----------------------------
class El {
  constructor(tag = 'div') {
    this.tagName = tag.toUpperCase();
    this.children = [];
    this.className = '';
    this.id = '';
    this.innerHTML = '';
    this.style = {};
    this.dataset = {};
    this.firstChild = null;
    this.parentNode = null;
    this.attributes = {};
  }

  appendChild(k) { this.children.push(k); k.parentNode = this; return k; }

  insertBefore(k) { this.children.unshift(k); k.parentNode = this; return k; }

  removeChild(k) { this.children = this.children.filter((c) => c !== k); return k; }

  setAttribute(k, v) { this.attributes[k] = v; }

  getAttribute(k) { return this.attributes[k] ?? null; }

  addEventListener() {}

  removeEventListener() {}

  querySelector() { return null; }

  querySelectorAll() { return []; }

  getBoundingClientRect() { return { top: 0, left: 0, width: 0, height: 0, right: 0, bottom: 0 }; }
}

// #app（ツールバーのアンカー）と .chart-wrap（オーバーレイ器のアンカー）を持つ document。
//   どちらもフェイルクローズ契約（不在なら例外）を持つため、実ページと同じ 2 つだけ用意する。
//   それ以外の要素探索は null（各 View の防御で no-op になる）。
function makeDoc() {
  const anchor = new El();
  const chartWrap = new El();
  const doc = {
    anchor,
    chartWrap,
    createElement: (tag) => new El(tag),
    createElementNS: (_ns, tag) => new El(tag),
    createTextNode: () => new El('#text'),
    querySelector: (sel) => {
      if (sel === '#app') return anchor;
      if (sel === '.chart-wrap') return chartWrap;
      return null;
    },
    querySelectorAll: () => [],
    getElementById: () => null,
    addEventListener: () => {},
    removeEventListener: () => {},
    body: new El('body'),
    documentElement: new El('html'),
  };
  return doc;
}

// --- Fake lwc（v5）: composition が触る面だけを備える（既存 bootstrap 検定と同型）------------
function fakeLwc() {
  const mainSeries = { setData: () => {}, applyOptions: () => {}, setMarkers: () => {} };
  const pane = { setStretchFactor: () => {}, paneIndex: () => 0, getHTMLElement: () => new El() };
  const chart = {
    addSeries: () => mainSeries,
    timeScale: () => ({
      fitContent: () => {},
      subscribeVisibleLogicalRangeChange: () => {},
      applyOptions: () => {},
    }),
    panes: () => [pane],
    addPane: () => ({ ...pane, addSeries: () => ({ setData: () => {} }), paneIndex: () => 1 }),
    removePane: () => {},
    removeSeries: () => {},
    applyOptions: () => {},
    subscribeCrosshairMove: () => {},
    subscribeClick: () => {},
  };
  return {
    createChart: () => chart,
    ColorType: { Solid: 'solid' },
    CandlestickSeries: {}, LineSeries: {}, HistogramSeries: {}, AreaSeries: {}, BaselineSeries: {},
    createTextWatermark: () => ({ applyOptions: () => {}, detach: () => {} }),
  };
}

const noStorage = { getItem: () => null, setItem: () => {}, removeItem: () => {} };
const fakeFetch = async () => ({ ok: true, async json() { return { ok: true, candles: [] }; } });

// ツールバーの innerHTML を取り出す（#app 直下の先頭に挿入される）。
function toolbarHtml(doc) {
  const bar = doc.anchor.children.find((c) => c.className === 'toolbar');
  return bar ? bar.innerHTML : '';
}

async function boot(doc, extra = {}) {
  const { ready } = await bootstrap({
    lwc: fakeLwc(), container: new El(), doc, storage: noStorage, fetch: fakeFetch, ...extra,
  });
  await ready;
}

test('L-1a 注入なし（standalone live）: 従来どおり live-follow のみでモードボタンは無い', async () => {
  // Arrange
  const doc = makeDoc();
  // Act
  await boot(doc);
  // Assert
  const html = toolbarHtml(doc);
  assert.ok(html.includes('id="live-follow-toggle"'), 'ライブ追従トグルは常に置く（現行値）');
  assert.equal([...html.matchAll(/id="enter-/g)].length, 0, 'リプレイ層未注入ならモードボタンは無い');
});

test('L-1b 注入なし + リプレイ層あり: 従来どおり enter-replay が 1 個（現行値と等価）', async () => {
  // Arrange
  const doc = makeDoc();
  // Act: 統合 UI と同じくリプレイ層を注入するが、toolbar は渡さない。
  await boot(doc, { replay: {} });
  // Assert
  const html = toolbarHtml(doc);
  assert.equal([...html.matchAll(/id="enter-replay"/g)].length, 1);
  assert.equal([...html.matchAll(/id="enter-sim"/g)].length, 0);
});

test('L-1c toolbar を注入すると 3 モードのボタンがその構成どおりに並ぶ', async () => {
  // Arrange
  const doc = makeDoc();
  const modeButtons = [
    { id: 'enter-replay', label: 'リプレイ', title: 'リプレイ表示のオン・オフ' },
    { id: 'enter-sim', label: 'シミュレーション', title: 'シミュレーション表示のオン・オフ' },
  ];
  // Act
  await boot(doc, { replay: {}, toolbar: { liveFollow: true, modeButtons } });
  // Assert
  const html = toolbarHtml(doc);
  assert.ok(html.includes('id="live-follow-toggle"'));
  assert.equal([...html.matchAll(/id="enter-replay"/g)].length, 1);
  assert.equal([...html.matchAll(/id="enter-sim"/g)].length, 1);
  assert.ok(html.includes('>シミュレーション</button>'), 'ラベルは注入された定義に従う');
  assert.ok(
    html.indexOf('id="enter-replay"') < html.indexOf('id="enter-sim"'),
    '注入順で並ぶ',
  );
});

test('L-1d 注入した toolbar は liveFollow も上書きできる（構成の全体を受け取る）', async () => {
  // Arrange
  const doc = makeDoc();
  // Act
  await boot(doc, { replay: {}, toolbar: { liveFollow: false, modeButtons: [] } });
  // Assert
  const html = toolbarHtml(doc);
  assert.equal([...html.matchAll(/id="live-follow-toggle"/g)].length, 0);
  assert.equal([...html.matchAll(/id="enter-/g)].length, 0);
});
