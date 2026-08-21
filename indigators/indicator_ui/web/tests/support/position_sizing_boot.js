// position_sizing_boot.js — 計算機まわりの検定で使う「実物の共有配線で 1 式を組む」補助（ISSUE-368 工程 3）。
//
// テスト補助であって検定ではない（`*.test.js` ではないので `npm test` の走査対象に入らない）。
//
// なぜ補助へ出すか: 同じ最小 DOM ＋ `installSharedUi` / `wireControllerCollaborators` の
//   呼び出し一式を検定ごとに書き写すと、配線の引数が増えたとき片方だけ取り残される
//   （プロジェクト規約「同じコードを手書き複製するな」）。**値の期待は各検定が持ち、
//   ここは組み立てだけ**を持つ（銘柄仕様・刻み・桁の数値はここに 1 つも書かない）。
//
// 差し替え口は 2 つだけ: `datasetRef`（銘柄仕様の解決結果が変わる）と `candidates`（スナップ候補）。

import {
  installSharedUi, wireControllerCollaborators, createPositionSizingContextItems,
} from '../../js/adapter/front/chart_app_wiring.js';

/** 実 UI 実測の生値（ISSUE-368）。y=0 をこの価格として 1px = 1 価格で下がる線形。 */
export const RAW_TOP = 62707.710070965324;

/** 最小 DOM 要素（実 DOM の「代入で子を捨てる」innerHTML まで再現する）。 */
export class El {
  constructor() {
    this.children = [];
    this.dataset = {};
    this.style = {};
    this.textContent = '';
    this.value = '';
    this.step = '';
    this.type = '';
    this.className = '';
    this.parentElement = null;
    this.parentNode = null;
    this._cls = new Set();
    this._handlers = {};
  }

  get innerHTML() { return ''; }

  set innerHTML(_v) { this.children = []; }

  get classList() {
    const s = this._cls;
    return {
      add: (c) => s.add(c),
      remove: (c) => s.delete(c),
      contains: (c) => s.has(c),
      toggle: (c, on) => { if (on === undefined ? !s.has(c) : on) { s.add(c); } else { s.delete(c); } },
    };
  }

  appendChild(k) { k.parentElement = this; k.parentNode = this; this.children.push(k); return k; }

  append(...kids) { for (const k of kids) { if (k && typeof k === 'object') this.appendChild(k); } }

  insertBefore(k) { k.parentNode = this; this.children.unshift(k); return k; }

  removeChild(k) { this.children = this.children.filter((c) => c !== k); return k; }

  querySelector() { return null; }

  setAttribute() {}

  getBoundingClientRect() { return { left: 0, top: 0 }; }

  addEventListener(t, fn) { (this._handlers[t] ||= []).push(fn); }

  fire(t, ev = {}) { (this._handlers[t] || []).forEach((fn) => fn(ev)); }
}

/** 要素木を平坦化する（data 属性で目的の要素を引くため）。 */
export function flatten(el, out = []) {
  for (const kid of el.children ?? []) { out.push(kid); flatten(kid, out); }
  return out;
}

// 価格ペイン（0）は y<300、下段（1）は y>=300。価格は生の浮動小数で返す（実 lwc と同じ）。
function fakeRenderer(candidates) {
  return {
    panPriceByPixels() {}, handlePriceWheel: () => false, isOverPriceAxis: () => false,
    // ISSUE-440: 幾何が動いたら凡例を引き直す面（ChartInteractionController が呼ぶ）。
    refreshPaneLegendIfGeometryChanged: () => false,
    setPaneAreaHeightProvider() {},
    resetPriceZoom() {}, setPaneHeight() {}, isLatestBarVisible: () => true, scrollToLatest() {},
    barInfoAt: () => null, setUserInteraction() {}, attachBackgroundPrimitive() {},
    setCandleObserver() {}, setPaneOrderObserver() {},
    suppressInteraction: () => () => {},
    priceAtCoordinate: (y) => RAW_TOP - y,
    paneIndexAtCoordinate: (y) => (y >= 0 && y < 300 ? 0 : 1),
    snapCandidatesAt: () => candidates,
  };
}

/**
 * 実物の共有配線で 1 式を組み、モーダルを開いた状態で返す。
 *
 * @param {string} datasetRef 銘柄仕様の引き当てに使う ref。
 * @param {Array<object>} [candidates] スナップ候補（`ChartRenderer.snapCandidatesAt` の戻り）。
 * @returns {object} 検定が触る口（doc / body / wrap / shared / wired / positionSizing / container / renderer / toasts）。
 */
export function boot(datasetRef, candidates = []) {
  const wrap = new El();
  const app = new El();
  const body = new El();
  const mounts = new Map();
  const doc = {
    body,
    createElement: () => new El(),
    querySelector: (sel) => {
      if (sel === '.chart-wrap') return wrap;
      if (sel === '#app') return app;
      return null;
    },
    getElementById: (id) => {
      if (!mounts.has(id)) { mounts.set(id, new El()); }
      return mounts.get(id);
    },
    addEventListener: () => {},
    removeEventListener: () => {},
  };
  const container = new El();
  const renderer = fakeRenderer(candidates);
  let positionSizing = null;
  const shared = installSharedUi({
    container,
    renderer,
    doc,
    getController: () => null,
    updatePaneHeight: () => {},
    getPositionSizing: () => positionSizing,
  });
  const wired = wireControllerCollaborators({
    controller: {
      setTimeframe() {}, registerActorController() {}, setAppliedObserver() {},
      setTimeframeObserver() {}, applyPaneOrder() {}, _timeframe: '1m',
    },
    renderer,
    doc,
    fetch: async () => ({ ok: false }),
    datasetRef,
    timeframe: '1m',
    recentBars: 100,
    templateStore: {
      loadTemplates: () => [], saveTemplates: () => {}, loadBindings: () => ({}),
      saveBindings: () => {}, loadTemplateSeq: () => 0, saveTemplateSeq: () => {},
    },
    chartTemplateMenu: shared.chartTemplateMenu,
    chartTemplateDialogs: shared.chartTemplateDialogs,
    positionSizingDialog: shared.positionSizingDialog,
    registerVerticalPanBlocker: shared.registerVerticalPanBlocker,
    chartToast: shared.chartToast,
    lwc: {},
    mainSeries: {},
    chart: {},
    container,
    currentPriceView: { render() {} },
  });
  positionSizing = wired.positionSizing ? wired.positionSizing.controller : null;
  const toasts = [];
  shared.chartToast.show = (m) => toasts.push(m);
  const ctx = {
    doc, body, wrap, mounts, shared, wired, positionSizing, container, renderer, toasts,
  };
  mounts.get('position-sizing-menu').children[0].fire('click');   // モーダルを開く
  return ctx;
}

export const dialogRoot = (ctx) => ctx.body.children
  .find((e) => e.dataset && e.dataset.psDialog === 'plan');

export const priceInput = (ctx, target) => flatten(dialogRoot(ctx))
  .find((e) => e.dataset && e.dataset.psPrice === target);

export const ghostLabel = (ctx) => flatten(ctx.wrap).find((e) => e.className === 'price-pick-label');

export const contextItems = (ctx) => createPositionSizingContextItems({
  renderer: ctx.renderer,
  getPositionSizing: () => ctx.positionSizing,
  getToast: () => ctx.shared.chartToast,
});
