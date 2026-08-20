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
import { MSG_OTHER_PANE } from '../js/adapter/front/price_pick_resolver.js';

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

test('TC-SW05 両 root は同一モジュール由来の項目を注入する（項目定義を複製しない）', () => {
  // 対象の付け替え（依頼者裁定 2026-08-20）: 元は「リプレイ root は注入しない」を見ていたが、
  //   確定要件により**両 root が注入する**。守るべきは「項目定義を root へ複製しないこと」
  //   （複製すると文言・価格解決が 2 か所に割れる）。アサーションの形は変えていない。
  // Arrange / Act
  const replayRoot = readFileSync(
    fileURLToPath(new URL('../../../../simulator/replay_ui/web/js/adapter/front/composition_root_front.js', import.meta.url)),
    'utf8',
  );
  const liveRoot = read('composition_root_front.js');
  // Assert: どちらも共有配線のヘルパ経由（項目のラベルも価格解決も root には書かれていない）。
  for (const [name, src] of [['live', liveRoot], ['replay', replayRoot]]) {
    assert.equal(/この価格を損切りに設定/.test(src), false, `${name} root に項目の文言が複製されている`);
    assert.equal(/resolvePickedPrice\(/.test(src), false, `${name} root に価格解決が複製されている`);
  }
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

// ---------------------------------------------------------------------------
// ライブ／リプレイの対称性（依頼者裁定 2026-08-20・確定要件 ISSUE.md:6927
//   「ライブ＋リプレイ両方に載せる（chart_app_wiring の共有配線で 3 配信ページに同時掲載）」）
//
//   注入機構（contextMenuItems 引数・共有配線への無条件追加禁止）は維持したまま、
//   **両 root が同じ 3 項目を注入する**ことで対称にする。項目定義は複製せず同一モジュールを参照。
// ---------------------------------------------------------------------------

const replayRootSrc = () => readFileSync(
  fileURLToPath(new URL('../../../../simulator/replay_ui/web/js/adapter/front/composition_root_front.js', import.meta.url)),
  'utf8',
);

test('TC-SW09 リプレイ root も計算機を結線する（ボタンが押せない・モーダルが開かない状態を残さない）', () => {
  // Arrange / Act
  const src = replayRootSrc();
  // Assert: 協働子の遅延参照と受け渡しがそろっていないと、ツールバーのボタンは在るのに何も起きない。
  assert.match(src, /getPositionSizing:/, '協働子の遅延参照を installSharedUi へ渡していない（ボタンが死ぬ）');
  assert.match(src, /positionSizingDialog/, 'モーダルを wireControllerCollaborators へ渡していない（協働子が生えない）');
  assert.match(src, /registerVerticalPanBlocker/, '縦パンブロッカーの登録口を渡していない（drag・ピッカーが縦パンを止められない）');
});

test('TC-SW10 リプレイ root も右クリック 3 項目を注入する（ライブと対称・項目定義は複製しない）', () => {
  // Arrange / Act
  const src = replayRootSrc();
  // Assert
  assert.match(src, /contextMenuItems:/, 'リプレイ root が右クリック項目を注入していない');
  assert.match(
    src,
    /createPositionSizingContextItems\(/,
    '項目は共有配線のヘルパから作る（root へ項目定義を複製しない）',
  );
});

// ---------------------------------------------------------------------------
// 下段ペイン右クリックの案内（裁定 2026-08-20・是正 2026-08-20）
//
//   裁定は「オシレーターペイン上のクリックは**無効化＋案内**」（設計書 ピッカー経路の実測検証 7）。
//   8-c（右クリック）は notify() を実装済みだったが、**両 root が toast: null を渡していた**ため
//   本番では無音だった（押しても何も起きない＝裁定の未達）。告知先 `chartToast` は
//   installSharedUi の**内側**で生成されるため、引数を組み立てる時点では root から参照できない。
//   解決は既存規約と同じ遅延参照（getPositionSizing / getTemplates と同型の getter）。
// ---------------------------------------------------------------------------

test('TC-SW11 下段ペインの右クリックは共有トーストで案内する（無音の縮退をしない）', () => {
  // Arrange: 共有配線を組み、告知先（共有トースト）を観測する。
  //   show を後から差し替えて観測できること自体が「遅延参照で解決している」ことの実証になる
  //   （生成時に値を焼き付けていたら、この差し替えは効かない）。
  const ctx = bootAll();
  const seen = [];
  ctx.shared.chartToast.show = (m) => seen.push(m);
  const items = createPositionSizingContextItems({
    renderer: {
      priceAtCoordinate: (y) => 59000 - y,
      paneIndexAtCoordinate: (y) => (y >= 0 && y < 300 ? 0 : 1),
      snapCandidatesAt: () => [],
    },
    getPositionSizing: () => ctx.positionSizing,
    getToast: () => ctx.shared.chartToast,
  });
  // Act: 下段ペイン（y=350）で「この価格を損切りに設定」を選ぶ。
  items[0].onSelect({ x: 100, y: 350 });
  // Assert
  assert.deepEqual(seen, [MSG_OTHER_PANE], '下段ペインの右クリックが無音（裁定「無効化＋案内」の未達）');
});

test('TC-SW12 両 root が告知先を渡す（案内の結線を root で落とさない）', () => {
  // Arrange / Act
  const liveRoot = read('composition_root_front.js');
  const replayRoot = replayRootSrc();
  // Assert: 遅延参照で共有トーストを渡していること（toast: null のままだと本番だけ無音になる）。
  for (const [name, src] of [['live', liveRoot], ['replay', replayRoot]]) {
    assert.match(src, /getToast:\s*\(\)\s*=>\s*chartToast/, `${name} root が告知先を渡していない（案内が出ない）`);
  }
});

// ---------------------------------------------------------------------------
// アーム中はチャートがポインタを受け取れる（実 UI 実測 2026-08-20・R-P1 の機能不全）
//
//   実測: 「チャートで指定」を押した後も `.ps-dialog-backdrop.is-open` は
//   `position:fixed; inset:0; display:flex`（780x493＝ビューポート全面）のまま残り、
//   `document.elementFromPoint(390,260)` が `ps-dialog-section` を返した
//   ＝**チャートをホバーもクリックもできない**。ゴースト線も確定も起きず R-P1 が成立しない。
//   単体検定は fake DOM が重なりを持たないため全緑ですり抜けていた（ISSUE-425 と同型）。
//
//   採った形は「アーム中だけ backdrop を非モーダル化する」（下記 TC-SW13 の状態クラス）。
//   モーダルを閉じる案を採らない理由: `open()` は `close()`→再構築で `_fields` / `_prices` を
//   捨てるため、Step1/2/3 の全入力値を退避する新規の状態ストアが要る（必須要件
//   「アーム解除／確定後に入力状態が失われないこと」を満たすのに機構の新設が必要になる）。
//   透過なら DOM に手を触れないので入力保持は構造的に自明（TC-SW14 で固定）。
//
//   fake DOM は pointer-events を再現しないため、**観測点は状態クラス**に置き、
//   実際の透過は CSS ガード（TC-CS06）で固定する（2 点で挟んで実 UI の穴を塞ぐ）。
// ---------------------------------------------------------------------------

test('TC-SW13 アームするとモーダルが非モーダル化し、解除で戻る（チャートを覆ったままにしない）', () => {
  // Arrange: ツールバー → モーダルを開く。
  const ctx = bootAll();
  ctx.mounts.get('position-sizing-menu').children[0].fire('click');
  const backdrop = ctx.body.children.find((e) => e.dataset && e.dataset.psDialog === 'plan');
  assert.equal(backdrop.classList.contains('is-picking'), false, '開いた直後は通常のモーダル');
  // Act: 損切りの「チャートで指定」を押す（＝アーム）。
  const pick = flatten(backdrop).find((e) => e.dataset && e.dataset.psPick === 'stop');
  pick.fire('click');
  // Assert: アーム中はチャート面がポインタを受けられる状態へ移る。
  assert.equal(ctx.wired.positionSizing.picker.isArmed(), true, '前提: アームされている');
  assert.equal(
    backdrop.classList.contains('is-picking'),
    true,
    'アーム中もモーダルがチャートを覆ったまま（ホバーもクリックもできない＝R-P1 が成立しない）',
  );
  // Act: 解除（Esc・モーダル取消と同じ経路）。
  ctx.wired.positionSizing.picker.disarm();
  // Assert: 通常のモーダルへ戻る（透過のまま残すと本文がクリックできない）。
  assert.equal(backdrop.classList.contains('is-picking'), false, '解除後も非モーダルのまま残っている');
});

test('TC-SW14 アーム→解除でモーダルの入力状態が失われない（必須要件）', () => {
  // Arrange: モーダルを開き、Step1/Step3 の入力と価格欄へ値を入れる。
  const ctx = bootAll();
  ctx.mounts.get('position-sizing-menu').children[0].fire('click');
  const backdrop = ctx.body.children.find((e) => e.dataset && e.dataset.psDialog === 'plan');
  const field = (key) => flatten(backdrop).find((e) => e.dataset && e.dataset.psField === key);
  const price = (target) => flatten(backdrop).find((e) => e.dataset && e.dataset.psPrice === target);
  field('winRate').value = '41';
  price('entry:0').value = '58700';
  price('stop').value = '58340';
  // Act: アームして解除する。
  flatten(backdrop).find((e) => e.dataset && e.dataset.psPick === 'stop').fire('click');
  ctx.wired.positionSizing.picker.disarm();
  // Assert: 同じ要素が生きており、値がそのまま残っている（作り直していない）。
  assert.equal(field('winRate').value, '41', 'Step 1 の入力が消えた');
  assert.equal(price('entry:0').value, '58700', '建値の入力が消えた');
  assert.equal(price('stop').value, '58340', '損切りの入力が消えた');
});

// ---------------------------------------------------------------------------
// 右クリックはモーダル未オープンでも値が入る（工程 5 レビュー 🔴-1・node で再現）
//
//   再現: モーダルを一度も開かず（または × で閉じた後）に右クリック →「損切りに設定」を選ぶと
//   通知 0・stop=null・console 出力なし＝**完全無音**。原因は書き戻し先の `_prices` が
//   `close()` で空 Map に捨てられており、`setPrice` が `if (!input) return;` で黙って抜けること。
//   右クリックの意図は「この価格を計算機へ入れる」なので、閉じていれば開いてから書き戻す。
// ---------------------------------------------------------------------------

test('TC-SW15 モーダル未オープンでも右クリックで価格が入る（無音にしない）', () => {
  // Arrange: ツールバーを押さない＝モーダルは一度も開いていない。
  const ctx = bootAll();
  assert.equal(ctx.body.children.length, 0, '前提: モーダルは開いていない');
  const items = createPositionSizingContextItems({
    renderer: {
      priceAtCoordinate: (y) => 59000 - y,
      paneIndexAtCoordinate: () => 0,
      snapCandidatesAt: () => [],
    },
    getPositionSizing: () => ctx.positionSizing,
    getToast: () => ctx.shared.chartToast,
  });
  // Act: 「この価格を損切りに設定」を y=660（=58340）で選ぶ。
  items[0].onSelect({ x: 100, y: 660 });
  // Assert: モーダルが開き、その欄へ値が入っている。
  const dialogRoot = ctx.body.children.find((e) => e.dataset && e.dataset.psDialog === 'plan');
  assert.notEqual(dialogRoot, undefined, '右クリックしてもモーダルが開かない（値の行き先が無い＝無音）');
  const stop = flatten(dialogRoot).find((e) => e.dataset && e.dataset.psPrice === 'stop');
  assert.equal(stop.value, '58340', '価格が欄まで届いていない');
});

test('TC-SW16 モーダルを閉じた後の右クリック「建値に追加」でも値が入る（🔴-1 の別経路）', () => {
  // Arrange: 一度開いてから閉じる（× 相当）。
  const ctx = bootAll();
  ctx.mounts.get('position-sizing-menu').children[0].fire('click');
  ctx.wired.positionSizing.controller._dialog.close();
  const items = createPositionSizingContextItems({
    renderer: {
      priceAtCoordinate: (y) => 59000 - y,
      paneIndexAtCoordinate: () => 0,
      snapCandidatesAt: () => [],
    },
    getPositionSizing: () => ctx.positionSizing,
    getToast: () => ctx.shared.chartToast,
  });
  // Act: 「この価格を建値に追加」（items[1]）。
  items[1].onSelect({ x: 100, y: 300 });
  // Assert
  const dialogRoot = ctx.body.children.find((e) => e.dataset && e.dataset.psDialog === 'plan');
  assert.notEqual(dialogRoot, undefined, '閉じた後の右クリックが無音のまま');
  const entries = flatten(dialogRoot).filter((e) => e.dataset && /^entry:/.test(e.dataset.psPrice ?? ''));
  assert.equal(entries.some((e) => e.value === '58700'), true, '追加した建値が欄に無い');
});

test('TC-SW17 共有配線が drag へ「アーム中は掴まない」述語を渡す（🔴-2 の結線）', () => {
  // TC-VB06 は述語を注入した状態の**振る舞い**を固定するが、本番配線が注入し忘れても
  //   気づけない（実測: 結線を外す変異が TC-VB06 では Red にならなかった）。結線側を固定する。
  // Arrange / Act
  const wiring = read('chart_app_wiring.js');
  // Assert
  assert.match(
    wiring,
    /isGrabBlocked:\s*\(\)\s*=>\s*picker\.isArmed\(\)/,
    'drag に「ピッカーがアーム中なら掴まない」述語が渡っていない（アーム中に別の水準が動く）',
  );
});

test('TC-SW18 本番配線の drag とピッカーが同じアーム状態を見る（端から端まで）', () => {
  // Arrange: 実物の共有配線で組み上げた drag / picker を取り出す。
  const ctx = bootAll();
  const { drag, picker } = ctx.wired.positionSizing;
  // Act / Assert: アーム状態が drag の掴み可否へ本当に伝わっている。
  assert.equal(drag._isGrabBlocked(), false, 'アーム前は掴める');
  picker.arm('take');
  assert.equal(drag._isGrabBlocked(), true, 'アーム中も掴めてしまう（入力先が一意にならない）');
  picker.disarm();
  assert.equal(drag._isGrabBlocked(), false, '解除後も掴めないままになっている');
});

test('TC-SW19 重みカスタムを選んでも計算が止まらない（🔴-3 の throw を端から端まで塞ぐ）', () => {
  // 再現していた症状は「`weight_pattern='custom' には custom_weights が必要です` が throw され
  //   計算が停止」。モーダル単体の検定（TC-PD42）は渡す値までしか見ないので、実物の usecase を
  //   通して例外が出ないところまで固定する。
  // Arrange
  const ctx = bootAll();
  ctx.mounts.get('position-sizing-menu').children[0].fire('click');
  const dialogRoot = ctx.body.children.find((e) => e.dataset && e.dataset.psDialog === 'plan');
  const sel = flatten(dialogRoot).find((e) => e.dataset && e.dataset.psField === 'weightPattern');
  // Act / Assert
  sel.value = 'custom';
  assert.doesNotThrow(() => sel.fire('change'), '重みカスタムで計算が例外停止する');
  // 入力欄が出て、値を変えても止まらない。
  const inputs = flatten(dialogRoot).filter((e) => e.dataset && e.dataset.psCustomWeight !== undefined);
  assert.equal(inputs.length, 3, 'カスタム入力欄が出ていない');
  inputs[0].value = '2.5';
  assert.doesNotThrow(() => inputs[0].fire('input'), '重みの変更で計算が例外停止する');
});

test('TC-SW20 × でモーダルを閉じるとアームも解除される（Y-1・R-P1「モーダル側の取消で解除」）', () => {
  // 閉じてもアームが残ると、ピッカーの抑止（lwc 操作の抑止・縦パンブロッカー）が
  //   掛かったままになり、チャートが操作できないのに解除する手段が画面から消える。
  // Arrange
  const ctx = bootAll();
  ctx.mounts.get('position-sizing-menu').children[0].fire('click');
  const dialogRoot = ctx.body.children.find((e) => e.dataset && e.dataset.psDialog === 'plan');
  flatten(dialogRoot).find((e) => e.dataset && e.dataset.psPick === 'stop').fire('click');
  assert.equal(ctx.wired.positionSizing.picker.isArmed(), true, '前提: アーム中');
  // Act: × を押す（data-ps-action="cancel"）。
  flatten(dialogRoot).find((e) => e.dataset && e.dataset.psAction === 'cancel').fire('click');
  // Assert
  assert.equal(ctx.wired.positionSizing.picker.isArmed(), false, '閉じてもアームが残っている');
});

test('TC-SW21 開き直し（open が内部で close する）はアームを巻き添えにしない（Y-1 の副作用防止）', () => {
  // Arrange: 閉じていない状態から open を呼ぶと内部で close() が走る。
  const ctx = bootAll();
  const { controller, picker } = ctx.wired.positionSizing;
  controller.open();
  picker.arm('stop');
  // Act: もう一度開く（右クリック経路の _ensureOpen でも起こりうる）。
  controller.open();
  // Assert: 「閉じていなかったものを開き直した」だけでアームを解除しない。
  assert.equal(picker.isArmed(), true, '開き直しでアームが巻き添えで解除された');
});
