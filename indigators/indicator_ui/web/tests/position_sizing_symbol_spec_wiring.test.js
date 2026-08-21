// 銘柄仕様（呼び値）の結線（ISSUE-368 スライス S-6）のテスト。
//
// 設計入力（唯一の仕様源）: .doc/POSITION_SIZING_CHART_INTEGRATION_DESIGN.md
//   「追補: 工程 2」E-1〜E-4（供給は HTTP でなく生成物。丸めは E-02 PriceLevels の不変条件）、
//   同「丸めの適用点（全 7 経路）」（1 スナップ候補・2 素のクリック価格・3 ゴースト・4 ピッカー確定→
//    書き戻し・5 右クリック 3 項目・6 水準線 drag・7 手入力欄 `step`）、
//   同「フェイルセーフ」（仕様が解決できないとき **値ではなく機能を落とし理由を出す**。
//    ピッカー・右クリック・drag は確定しない／トーストで告知／**手入力は従来どおり可**）、
//   S-6 通過条件（spec の解決は front 配下 **1 か所**・新しい配管を作らない）。
//
// 除去する原因（ISSUE-368 実 UI 実測 2026-08-20）:
//   チャートから拾った価格が生の浮動小数 `62707.710070965324` のままモーダルへ書き戻り、
//   ゴーストラベルの表示 `62,708` と食い違っていた。
//
// 観点: ソース走査だけでは「渡してはいるが繋がっていない」を見逃す（ISSUE-291）。実物の共有配線で
//   組み上げ、**押した結果どの値が欄と水準に入るか**まで見る。
// 構造: Arrange-Act-Assert（AAA）。最小 DOM（版面アンカー .chart-wrap を持つ）。

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readdirSync, readFileSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';

import {
  installSharedUi, wireControllerCollaborators, createPositionSizingContextItems,
} from '../js/adapter/front/chart_app_wiring.js';
import { MSG_NO_SYMBOL_SPEC, MSG_OTHER_PANE } from '../js/adapter/front/price_pick_resolver.js';
import { lookupSymbolSpec } from '../js/adapter/front/symbol_spec_catalog.js';
import { priceOnLine } from '../js/adapter/front/price_format.js';

// 実 UI 実測の生値（小数部まで同じ）。y=0 をこの価格として 1px = 1 価格で下がる線形。
const RAW_TOP = 62707.710070965324;
// JP225 を載せた datasetRef（台帳＝生成物が権威。ここで tick の数値は書かない）。
const JP225_REF = 'jp225_tick';
const JP225_TICK = lookupSymbolSpec(JP225_REF).tick;

class El {
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

  // 実 DOM と同じく「代入で子を捨てる」（`_renderPriceRows` は innerHTML='' で欄を作り直す）。
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

function flatten(el, out = []) {
  for (const kid of el.children ?? []) { out.push(kid); flatten(kid, out); }
  return out;
}

// 価格ペイン（0）は y<300、下段（1）は y>=300。価格は生の浮動小数で返す（実 lwc と同じ）。
function fakeRenderer() {
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
    snapCandidatesAt: () => [],
  };
}

// 実物の共有配線で 1 式を組む（root と同じ受け渡し。datasetRef だけを差し替える）。
function boot(datasetRef) {
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
  const renderer = fakeRenderer();
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
  const seen = [];
  shared.chartToast.show = (m) => seen.push(m);
  return {
    doc, body, wrap, mounts, shared, wired, positionSizing, container, renderer, seen,
  };
}

const openDialog = (ctx) => ctx.mounts.get('position-sizing-menu').children[0].fire('click');
const dialogRoot = (ctx) => ctx.body.children.find((e) => e.dataset && e.dataset.psDialog === 'plan');
const priceInput = (ctx, target) => flatten(dialogRoot(ctx))
  .find((e) => e.dataset && e.dataset.psPrice === target);
const ghostLabel = (ctx) => flatten(ctx.wrap).find((e) => e.className === 'price-pick-label');
const contextItems = (ctx) => createPositionSizingContextItems({
  renderer: ctx.renderer,
  getPositionSizing: () => ctx.positionSizing,
  getToast: () => ctx.shared.chartToast,
});

// ---------------------------------------------------------------------------
// 原因の除去（ピッカー経路・経路 3/4）
// ---------------------------------------------------------------------------

test('TC-SQ01 ピッカー確定でモーダルへ入る値とゴーストの表示が同じ価格になる（生値が水準へ入らない）', () => {
  // Arrange: JP225（刻み 1）。モーダルを開き「チャートで指定」で損切りをアームする。
  const ctx = boot(JP225_REF);
  openDialog(ctx);
  flatten(dialogRoot(ctx)).find((e) => e.dataset && e.dataset.psPick === 'stop').fire('click');
  // Act: y=0 をホバー（ゴースト）→ 同じ座標でクリック（確定）。素の価格は 62707.710070965324。
  ctx.container.fire('pointermove', { clientX: 100, clientY: 0 });
  const ghost = ghostLabel(ctx).textContent;
  ctx.container.fire('click', { clientX: 100, clientY: 0 });
  // Assert
  const written = priceInput(ctx, 'stop').value;
  assert.equal(/\.\d{3,}/.test(written), false, `生の浮動小数がモーダルへ書き戻っている: ${written}`);
  assert.equal(
    Number(written),
    Number(ghost.replace(/,/g, '')),
    `ゴーストの表示（${ghost}）とモーダルの値（${written}）が食い違っている`,
  );
  assert.equal(ctx.positionSizing.levels().stopPrice, 62708, '水準が刻み上にない');
  assert.equal(ghost, priceOnLine(62708), 'ゴーストは量子化された価格の書式（参照実装 :777）');
});

test('TC-SQ02 右クリックの 3 項目で入る価格も刻み上（経路 5）', () => {
  // Arrange
  const ctx = boot(JP225_REF);
  openDialog(ctx);
  const items = contextItems(ctx);
  // Act: 「この価格を損切りに設定」を y=10（素の価格 62697.710070965324）で選ぶ。
  items[0].onSelect({ x: 100, y: 10 });
  // Assert
  assert.equal(priceInput(ctx, 'stop').value, '62698');
  assert.equal(ctx.positionSizing.levels().stopPrice, 62698);
});

test('TC-SQ03 右クリック「建値に追加」も刻み上（K を増やす経路でも取り残さない）', () => {
  // Arrange
  const ctx = boot(JP225_REF);
  openDialog(ctx);
  // Act
  contextItems(ctx)[1].onSelect({ x: 100, y: 10 });
  // Assert
  const entries = ctx.positionSizing.levels().entryPrices;
  assert.equal(entries[entries.length - 1], 62698, '追加した建値が刻み上にない');
});

test('TC-SQ04 水準線 drag が作った水準も刻み上（resolver を通らない経路 6・domain の関門）', () => {
  // Arrange: drag は「いまの水準」を非破壊更新して applyLevels へ渡す。
  const ctx = boot(JP225_REF);
  openDialog(ctx);
  const levels = ctx.positionSizing.levels();
  // Act
  ctx.positionSizing.applyLevels(levels.withStop(RAW_TOP));
  // Assert: 初期水準に刻みが注入されていなければ生値のまま入る。
  assert.equal(ctx.positionSizing.levels().stopPrice, 62708);
});

// ---------------------------------------------------------------------------
// 手入力（経路 7）— DOM から**文字列**で来る
// ---------------------------------------------------------------------------

test('TC-SQ05 文字列の手入力が数値として量子化されて水準へ入る（経路 7）', () => {
  // Arrange: 実 DOM の input.value は文字列（'58700.4'）。
  const ctx = boot(JP225_REF);
  openDialog(ctx);
  const stop = priceInput(ctx, 'stop');
  // Act
  stop.value = '58700.4';
  stop.fire('input');
  // Assert: 文字列のまま domain へ渡すと `quantize` は「非有限な数ではない」ため素通しし、
  //   刻みに乗らない値が水準へ入る（domain の契約どおりの穴）。数値化は front の責務。
  assert.equal(ctx.positionSizing.levels().stopPrice, 58700);
});

test('TC-SQ06 空欄は従来どおり null のまま（入力途中を勝手に 0 へ倒さない）', () => {
  // Arrange
  const ctx = boot(JP225_REF);
  openDialog(ctx);
  const stop = priceInput(ctx, 'stop');
  stop.value = '58700.4';
  stop.fire('input');
  // Act: 打ち直しのために消す。
  stop.value = '';
  stop.fire('input');
  // Assert
  assert.equal(ctx.positionSizing.levels().stopPrice, null);
});

test('TC-SQ07 価格入力欄の step は銘柄の刻み（経路 7・矢印キーで刻みの外へ出られない）', () => {
  // Arrange / Act
  const ctx = boot(JP225_REF);
  openDialog(ctx);
  // Assert
  assert.equal(priceInput(ctx, 'stop').step, String(JP225_TICK));
  assert.equal(priceInput(ctx, 'entry:0').step, String(JP225_TICK));
});

// ---------------------------------------------------------------------------
// フェイルセーフ（仕様が解決できないとき）
//   無音で生値に落とさない。**値ではなく機能を落とし、理由を出す。**
// ---------------------------------------------------------------------------

test('TC-SQ08 仕様が解決できない ref では「チャートで指定」がアームせず理由を告知する', () => {
  // Arrange: 台帳に無い datasetRef。
  const ctx = boot('unknown_dataset_ref');
  openDialog(ctx);
  // Act
  flatten(dialogRoot(ctx)).find((e) => e.dataset && e.dataset.psPick === 'stop').fire('click');
  // Assert
  assert.equal(ctx.wired.positionSizing.picker.isArmed(), false, '刻みが不明なままピッカーへ入っている');
  assert.deepEqual(ctx.seen, [MSG_NO_SYMBOL_SPEC], '無音で機能だけ死んでいる（理由が出ない）');
});

test('TC-SQ09 仕様が解決できない ref では右クリックで価格が入らず理由を告知する', () => {
  // Arrange
  const ctx = boot('unknown_dataset_ref');
  openDialog(ctx);
  // Act: 価格ペイン（y=10）で「この価格を損切りに設定」。
  contextItems(ctx)[0].onSelect({ x: 100, y: 10 });
  // Assert
  assert.equal(priceInput(ctx, 'stop').value, '', '刻みが不明なまま価格が入っている');
  assert.deepEqual(ctx.seen, [MSG_NO_SYMBOL_SPEC]);
});

test('TC-SQ10 仕様が解決できない ref では水準線 drag も掴めない（経路 6 も落とす）', () => {
  // Arrange / Act / Assert
  assert.equal(boot('unknown_dataset_ref').wired.positionSizing.drag._isGrabBlocked(), true);
  assert.equal(boot(JP225_REF).wired.positionSizing.drag._isGrabBlocked(), false, '解決できるのに掴めない');
});

test('TC-SQ11 仕様が解決できなくても手入力は従来どおり使える（人が打った値は人の責任）', () => {
  // Arrange
  const ctx = boot('unknown_dataset_ref');
  openDialog(ctx);
  const stop = priceInput(ctx, 'stop');
  // Act
  stop.value = '58700.4';
  stop.fire('input');
  // Assert: 丸めずにそのまま入る（刻みが不明なので丸めようがない＝勝手に決めない）。
  assert.equal(ctx.positionSizing.levels().stopPrice, 58700.4);
  assert.equal(stop.step, 'any', '刻みが不明なら step は従来どおり any');
});

test('TC-SQ12 仕様が解決できるときは下段ペインの案内が従来どおり出る（理由を取り違えない）', () => {
  // Arrange
  const ctx = boot(JP225_REF);
  openDialog(ctx);
  // Act: 下段ペイン（y=350）。
  contextItems(ctx)[0].onSelect({ x: 100, y: 350 });
  // Assert
  assert.deepEqual(ctx.seen, [MSG_OTHER_PANE]);
});

// ---------------------------------------------------------------------------
// 構造（S-6 通過条件）
// ---------------------------------------------------------------------------

test('TC-SQ13 銘柄仕様の解決は front 配下 1 か所だけ（配りは値で行う）', () => {
  // Arrange: front 配下の全 .js（テストは対象外）。
  const frontDir = fileURLToPath(new URL('../js/adapter/front/', import.meta.url));
  // Act
  const importers = readdirSync(frontDir)
    .filter((name) => name.endsWith('.js') && name !== 'symbol_spec_catalog.js')
    .filter((name) => /symbol_spec_catalog\.js/.test(readFileSync(join(frontDir, name), 'utf8')));
  // Assert: 解決点が増えると「どの銘柄の刻みで丸めたか」が経路ごとに割れる。
  assert.deepEqual(importers, ['chart_app_wiring.js'], `解決点が複数ある: ${importers.join(', ')}`);
});

test('TC-SQ14 生成物（台帳）を読むのは catalog だけ（front が台帳を直接引かない）', () => {
  // Arrange
  const frontDir = fileURLToPath(new URL('../js/adapter/front/', import.meta.url));
  // Act
  const readers = readdirSync(frontDir)
    .filter((name) => name.endsWith('.js'))
    .filter((name) => /symbol_spec_generated\.js/.test(readFileSync(join(frontDir, name), 'utf8')));
  // Assert
  assert.deepEqual(readers, ['symbol_spec_catalog.js'], `台帳の直接参照がある: ${readers.join(', ')}`);
});

test('TC-SQ15 ChartRenderer は銘柄仕様を知らない（upstream 隔離点へ持ち込まない）', () => {
  // Arrange / Act
  const src = readFileSync(fileURLToPath(new URL('../js/adapter/front/chart_renderer.js', import.meta.url)), 'utf8');
  // Assert
  assert.equal(/symbol_spec|quantize/.test(src), false, 'renderer が銘柄仕様・丸めを知っている');
});
