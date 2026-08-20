// 計算機一式が**共有配線 1 箇所**で組み上がることの検証（ISSUE-368 スライス 7）。
//
// 設計入力（唯一の仕様源）: .doc/POSITION_SIZING_CHART_INTEGRATION_DESIGN.md
//   スライス 7（`installSharedUi` にメニュー／ダイアログ install、`wireControllerCollaborators` に
//    協働子（primitive・drag・picker・worker gateway）を追加。**各 root は識別子の受け渡しのみ**）、
//   「ピッカー経路の実測検証」5（**スライス 4 の drag 未結線（`new PriceLevelDragController` 呼出 0 件）は
//    スライス 7 で解消**）、
//   §6（協働子はコールバック注入・遅延参照で結ぶ＝メニューは controller を import しない）。
//
// 観点: 「口が生えているだけ」では未結線を見逃す。実際に組み上げて
//   「ボタンを押す → モーダルが開く」「drag が水準を更新できる」ところまで見る。
// 構造: Arrange-Act-Assert。DOM・renderer は最小 fake。

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

import {
  installSharedUi, wireControllerCollaborators, createPositionSizingContextItems,
} from '../js/adapter/front/chart_app_wiring.js';

const FRONT = new URL('../js/adapter/front/', import.meta.url);
const read = (name) => readFileSync(fileURLToPath(new URL(name, FRONT)), 'utf8');

test('TC-SW01 スライス 4 の drag 未結線が解消している（共有配線が生成する）', () => {
  // Arrange / Act
  const wiring = read('chart_app_wiring.js');
  // Assert
  assert.match(wiring, /new PriceLevelDragController\(/, '水準線 drag が本番配線で生成されていない');
  assert.match(wiring, /new PricePickController\(/, 'アーム式ピッカーが本番配線で生成されていない');
  assert.match(wiring, /new PriceLevelLinesPrimitive\(/, '水準線 primitive が本番配線で生成されていない');
  assert.match(wiring, /new McWorkerGateway\(/, 'MC Worker ゲートウェイが本番配線で生成されていない');
  assert.match(wiring, /new PositionSizingController\(/, '協働子が本番配線で生成されていない');
});

test('TC-SW02 メニューは協働子を import しない（コールバック注入・遅延参照＝DIP）', () => {
  // Arrange / Act
  const menu = read('position_sizing_menu.js');
  const dialog = read('position_sizing_dialog.js');
  // Assert
  assert.equal(/^import /m.test(menu), false, 'メニューが何かを import している（注入で結ぶ規約）');
  assert.equal(/^import /m.test(dialog), false, 'モーダルが何かを import している（注入で結ぶ規約）');
});

test('TC-SW03 root は識別子の受け渡しだけ（配線ロジックを root へ複製しない）', () => {
  // Arrange / Act: ライブ root が計算機の部品を自前で new していないこと。
  const root = read('composition_root_front.js');
  // Assert
  for (const owned of [
    'PositionSizingController', 'PriceLevelDragController', 'PricePickController',
    'PriceLevelLinesPrimitive', 'McWorkerGateway', 'PositionSizingPlanUseCase',
  ]) {
    assert.equal(
      new RegExp(`new ${owned}\\(`).test(root),
      false,
      `root が ${owned} を自前で生成している（生成は共有配線の責務）`,
    );
  }
});

test('TC-SW04 ライブ root は計算機を結線する（識別子の受け渡しのみ・端から端まで）', () => {
  // Arrange / Act
  const root = read('composition_root_front.js');
  // Assert: 遅延参照の供給・協働子への受け渡し・右クリック項目の注入がそろっている。
  assert.match(root, /getPositionSizing:/, '協働子の遅延参照を installSharedUi へ渡していない（メニューが死ぬ）');
  assert.match(root, /positionSizingDialog/, 'モーダルを wireControllerCollaborators へ渡していない（協働子が生えない）');
  assert.match(root, /registerVerticalPanBlocker/, '縦パンブロッカーの登録口を協働子へ渡していない（drag・ピッカーが縦パンを止められない）');
  assert.match(root, /contextMenuItems:/, '右クリックの価格設定項目を注入していない（R-P3 が死ぬ）');
  assert.match(root, /createPositionSizingContextItems\(/, '項目の生成は共有配線のヘルパを使う（root へ配線を複製しない）');
});

test('TC-SW05 リプレイ root は右クリック項目を注入しない（replay 汚染の禁止・8-c の通過条件）', () => {
  // Arrange / Act
  const replayRoot = readFileSync(
    fileURLToPath(new URL('../../../../simulator/replay_ui/web/js/adapter/front/composition_root_front.js', import.meta.url)),
    'utf8',
  );
  // Assert
  assert.equal(/contextMenuItems/.test(replayRoot), false);
  assert.equal(/createPositionSizingContextItems/.test(replayRoot), false);
});

// ---------------------------------------------------------------------------
// 端から端まで（ISSUE-291「受け口だけでなく端から端まで結線を固定」）
//   ソース走査だけでは「渡してはいるが繋がっていない」を見逃す。実際に組み上げて
//   「ツールバーのボタンを押す → モーダルが開く」「右クリック項目 → 価格が入る」まで見る。
// ---------------------------------------------------------------------------

class El {
  constructor() {
    this.children = [];
    this.dataset = {};
    this.style = {};
    this.textContent = '';
    this.value = '';
    this.type = '';
    this.className = '';
    this.innerHTML = '';
    this.parentElement = null;
    this.parentNode = null;
    this._cls = new Set();
    this._handlers = {};
  }

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

function bootAll() {
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
  const renderer = {
    panPriceByPixels() {}, handlePriceWheel: () => false, isOverPriceAxis: () => false,
    resetPriceZoom() {}, setPaneHeight() {}, isLatestBarVisible: () => true, scrollToLatest() {},
    barInfoAt: () => null, setUserInteraction() {}, attachBackgroundPrimitive() {},
    setCandleObserver() {}, setPaneOrderObserver() {},
    priceAtCoordinate: (y) => 59000 - y,
    paneIndexAtCoordinate: (y) => (y >= 0 && y < 300 ? 0 : 1),
    snapCandidatesAt: () => [],
  };
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
    datasetRef: 'sample',
    timeframe: '1m',
    recentBars: 100,
    // テンプレート協働子は本検定の対象外だが生成は走る（共有配線の既存挙動）。最小の gateway を渡す。
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
  return {
    doc, body, mounts, shared, wired, positionSizing,
  };
}

test('TC-SW06 ツールバーのボタンを押すとモーダルが開く（遅延参照が本当に解決している）', () => {
  // Arrange
  const ctx = bootAll();
  const trigger = ctx.mounts.get('position-sizing-menu').children[0];
  assert.equal(ctx.body.children.length, 0, '押す前はモーダルが無い');
  // Act
  trigger.fire('click');
  // Assert
  const dialogs = ctx.body.children.filter((e) => e.dataset && e.dataset.psDialog === 'plan');
  assert.equal(dialogs.length, 1, 'ボタン → 協働子 → モーダルの経路が繋がっていない');
});

test('TC-SW07 右クリック項目で価格がモーダルの欄へ入る（R-P3 が端まで通っている）', () => {
  // Arrange
  const ctx = bootAll();
  ctx.mounts.get('position-sizing-menu').children[0].fire('click');   // モーダルを開く
  const items = createPositionSizingContextItems({
    renderer: {
      priceAtCoordinate: (y) => 59000 - y,
      paneIndexAtCoordinate: () => 0,
      snapCandidatesAt: () => [],
    },
    getPositionSizing: () => ctx.positionSizing,
  });
  // Act: 「この価格を損切りに設定」を y=660 で選ぶ（= 58340）。
  items[0].onSelect({ x: 100, y: 660 });
  // Assert
  const dialogRoot = ctx.body.children.find((e) => e.dataset && e.dataset.psDialog === 'plan');
  const stop = flatten(dialogRoot).find((e) => e.dataset && e.dataset.psPrice === 'stop');
  assert.equal(stop.value, '58340', '右クリック → 協働子 → モーダルの欄まで届いていない');
});

test('TC-SW08 モーダルの「チャートで指定」がピッカーをアームする（R-P1 が端まで通っている）', () => {
  // Arrange
  const ctx = bootAll();
  ctx.mounts.get('position-sizing-menu').children[0].fire('click');
  const dialogRoot = ctx.body.children.find((e) => e.dataset && e.dataset.psDialog === 'plan');
  const pick = flatten(dialogRoot).find((e) => e.dataset && e.dataset.psPick === 'stop');
  // Act
  pick.fire('click');
  // Assert
  assert.equal(ctx.wired.positionSizing.picker.isArmed(), true);
  assert.equal(ctx.wired.positionSizing.picker.armedTarget(), 'stop');
});
