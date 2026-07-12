// live_follow_controller.js（LiveFollowController・ライブ追従トグル）の仕様検証。
//
// 設計入力（確定仕様・状態機械）:
//   状態 _mode ∈ {FOLLOW, ANALYSIS}（初期 FOLLOW）。
//   - install(): ボタン click 配線 ＋ renderer.subscribeVisibleRange 配線 ＋ 初期 FOLLOW 適用。
//   - toggleManual(): FOLLOW↔ANALYSIS 手動切替。
//   - _onRangeChange(atRightEdge):
//       FOLLOW && !atRightEdge → ANALYSIS（stop / tint on / 消灯）
//       ANALYSIS && atRightEdge → FOLLOW（start / scrollToRealTime / tint off / 点灯）
//       同状態 → no-op（振動防止の核）。
//   - FOLLOW 適用: liveUpdater.start() ＋ tint off ＋ 点灯（再FOLLOW時は scrollToRealTime で catch-up）。
//   - ANALYSIS 適用: liveUpdater.stop() ＋ tint on ＋ 消灯。
//   - mode!=='b'（A方式）: ボタン disabled・配線しない（非活性）。
//   - DOM/renderer 不在: no-op 防御（例外を出さない）。
// 構造: Arrange-Act-Assert（AAA）。実 DOM・実 renderer 非依存（全 fake 注入）。

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { LiveFollowController } from '../js/adapter/front/live_follow_controller.js';

// liveUpdater.start/stop の呼び出し回数を記録する spy。
function fakeLiveUpdater() {
  return { starts: 0, stops: 0, start() { this.starts += 1; }, stop() { this.stops += 1; } };
}

// renderer fake。subscribeVisibleRange の cb を捕捉（fireRange で駆動）、scrollToRealTime/setAnalysisTint を記録。
function fakeRenderer() {
  return {
    _rangeCb: null,
    scrolls: 0,
    tints: [], // setAnalysisTint(on) の履歴（true=分析 tint on / false=復元）
    subscribeVisibleRange(cb) { this._rangeCb = cb; },
    scrollToRealTime() { this.scrolls += 1; },
    setAnalysisTint(on) { this.tints.push(!!on); },
    fireRange(atRightEdge) { if (this._rangeCb) { this._rangeCb(atRightEdge); } },
  };
}

// fake button（classList/setAttribute/addEventListener/disabled を記録）。
function fakeButton() {
  const classes = new Set();
  return {
    disabled: false,
    _attrs: {},
    _click: null,
    classList: {
      _set: classes,
      toggle(name, on) { if (on) { classes.add(name); } else { classes.delete(name); } },
      contains(name) { return classes.has(name); },
    },
    setAttribute(k, v) { this._attrs[k] = v; },
    getAttribute(k) { return this._attrs[k]; },
    addEventListener(type, fn) { if (type === 'click') { this._click = fn; } },
    click() { if (this._click) { this._click(); } },
  };
}

// fake document（getElementById(id) → 指定ボタン。id 不一致は null）。
function fakeDocument(button, buttonId = 'live-follow-toggle') {
  return { getElementById(id) { return id === buttonId ? button : null; } };
}

function setup({ mode = 'b', hasButton = true, hasDocument = true } = {}) {
  const liveUpdater = fakeLiveUpdater();
  const renderer = fakeRenderer();
  const button = fakeButton();
  const document = hasDocument ? fakeDocument(hasButton ? button : null) : null;
  const controller = new LiveFollowController({
    liveUpdater, renderer, document, buttonId: 'live-follow-toggle', mode,
  });
  return { controller, liveUpdater, renderer, button };
}

// 点灯判定: is-active クラス ＋ aria-pressed='true' ＋ 非 disabled。
function isLit(button) {
  return button.classList.contains('is-active')
    && button.getAttribute('aria-pressed') === 'true'
    && button.disabled === false;
}

test('初期FOLLOW: install で点灯・tint off・初期は start/scroll しない（初回 start は ready 後の入口が担う）', () => {
  const { controller, liveUpdater, renderer, button } = setup();

  controller.install();

  assert.equal(controller.mode, 'FOLLOW');
  // 初回 start は入口（index.html・ready 後）が担うため install では start しない（実タイマー副作用を出さない）。
  assert.equal(liveUpdater.starts, 0, '初期 FOLLOW は install で start しない');
  assert.equal(liveUpdater.stops, 0);
  assert.equal(renderer.tints.at(-1), false, '初期 FOLLOW は tint off');
  assert.equal(renderer.scrolls, 0, '初期 FOLLOW は catch-up scroll しない');
  assert.ok(isLit(button), '初期 FOLLOW はボタン点灯');
});

test('FOLLOW/ANALYSIS で liveTickPlayer/formingBarUpdater も start/stop する（ANALYSIS で価格を凍結）', () => {
  // 実機バグ根治: これらを止めないと ANALYSIS でも価格が更新され続け（トグルが効かない）、更新→自動 FOLLOW
  //   復帰→sessions 再 focus で手動ズームがリセットされる。FOLLOW/ANALYSIS で liveUpdater と同時に start/stop する。
  const liveUpdater = fakeLiveUpdater();
  const player = fakeLiveUpdater();
  const forming = fakeLiveUpdater();
  const button = fakeButton();
  const controller = new LiveFollowController({
    liveUpdater, liveTickPlayer: player, formingBarUpdater: forming,
    renderer: fakeRenderer(), document: fakeDocument(button), buttonId: 'live-follow-toggle', mode: 'b',
  });
  controller.install();
  controller.toggleManual(); // FOLLOW→ANALYSIS
  assert.equal(controller.mode, 'ANALYSIS');
  assert.deepEqual([liveUpdater.stops, player.stops, forming.stops], [1, 1, 1], 'ANALYSIS で全ライブ更新系を stop');
  controller.toggleManual(); // ANALYSIS→FOLLOW
  assert.equal(controller.mode, 'FOLLOW');
  assert.deepEqual([liveUpdater.starts, player.starts, forming.starts], [1, 1, 1], 'FOLLOW で全ライブ更新系を start');
});

test('手動toggle: FOLLOW→ANALYSIS で stop・tint on・消灯', () => {
  const { controller, liveUpdater, renderer, button } = setup();
  controller.install();

  controller.toggleManual();

  assert.equal(controller.mode, 'ANALYSIS');
  assert.equal(liveUpdater.stops, 1, 'ANALYSIS 適用で stop');
  assert.equal(renderer.tints.at(-1), true, 'ANALYSIS は tint on');
  assert.equal(isLit(button), false, 'ANALYSIS はボタン消灯');
});

test('手動toggle: ANALYSIS(panned)→FOLLOW で start・scrollToRealTime(catch-up)・tint off・点灯', () => {
  // panned（右端に居ない）状態からの手動 re-FOLLOW は scrollToRealTime で右端へ catch-up する。
  //   実 lwc 相当に false→true を発火する fake を使い、scroll 収束(true)で suppression が解除されることも担保。
  const liveUpdater = fakeLiveUpdater();
  const renderer = fakeRendererWithScrollEvents();
  const button = fakeButton();
  const controller = new LiveFollowController({
    liveUpdater, renderer, document: fakeDocument(button), buttonId: 'live-follow-toggle', mode: 'b',
  });
  controller.install();
  renderer.fireRange(false); // パン離脱 → auto-off → ANALYSIS（_lastAtRightEdge=false）
  assert.equal(controller.mode, 'ANALYSIS');
  const startsBefore = liveUpdater.starts;

  controller.toggleManual(); // → FOLLOW（panned なので scrollToRealTime で catch-up）

  assert.equal(controller.mode, 'FOLLOW');
  assert.equal(liveUpdater.starts, startsBefore + 1, '再FOLLOW で start');
  assert.equal(renderer.scrolls, 1, 'panned re-FOLLOW は scrollToRealTime で catch-up');
  assert.equal(renderer.tints.at(-1), false, '再FOLLOW は tint off');
  assert.ok(isLit(button), '再FOLLOW はボタン点灯');
});

test('ボタン click は手動 toggle を駆動する（FOLLOW→ANALYSIS）', () => {
  const { controller, liveUpdater, button } = setup();
  controller.install();

  button.click();

  assert.equal(controller.mode, 'ANALYSIS');
  assert.equal(liveUpdater.stops, 1);
});

test('自動遷移 auto-off: FOLLOW 中に右端離脱（atRightEdge=false）で ANALYSIS へ', () => {
  const { controller, liveUpdater, renderer, button } = setup();
  controller.install();

  renderer.fireRange(false); // 右端離脱

  assert.equal(controller.mode, 'ANALYSIS');
  assert.equal(liveUpdater.stops, 1, 'auto-off で stop');
  assert.equal(renderer.tints.at(-1), true);
  assert.equal(isLit(button), false);
});

test('自動遷移 auto-on: ANALYSIS 中に右端復帰（atRightEdge=true）で FOLLOW へ（既に右端なので scroll しない）', () => {
  const { controller, liveUpdater, renderer, button } = setup();
  controller.install();
  controller.toggleManual(); // → ANALYSIS
  const startsBefore = liveUpdater.starts;
  const scrollsBefore = renderer.scrolls;

  renderer.fireRange(true); // 右端復帰（＝この時点で既に右端）

  assert.equal(controller.mode, 'FOLLOW');
  assert.equal(liveUpdater.starts, startsBefore + 1, 'auto-on で start');
  // auto-on は「右端復帰」で発火＝定義上すでに右端。no-op scroll でイベントが来ず suppression が stuck するのを
  //   避けるため scrollToRealTime を呼ばない（catch-up は既に右端なので不要）。
  assert.equal(renderer.scrolls, scrollsBefore, 'auto-on は既に右端＝scroll を呼ばない（suppression stuck 回避）');
  assert.equal(renderer.tints.at(-1), false);
  assert.ok(isLit(button));
});

test('同状態 no-op: FOLLOW 中の atRightEdge=true は副作用を追加しない（振動防止）', () => {
  const { controller, liveUpdater, renderer } = setup();
  controller.install();
  const snap = { starts: liveUpdater.starts, stops: liveUpdater.stops, scrolls: renderer.scrolls, tints: renderer.tints.length };

  renderer.fireRange(true); // FOLLOW のまま（右端維持）

  assert.equal(controller.mode, 'FOLLOW');
  assert.equal(liveUpdater.starts, snap.starts, 'no-op で start 追加なし');
  assert.equal(liveUpdater.stops, snap.stops);
  assert.equal(renderer.scrolls, snap.scrolls, 'no-op で scroll 追加なし');
  assert.equal(renderer.tints.length, snap.tints, 'no-op で tint 適用追加なし');
});

test('同状態 no-op: ANALYSIS 中の atRightEdge=false は副作用を追加しない', () => {
  const { controller, liveUpdater, renderer } = setup();
  controller.install();
  controller.toggleManual(); // → ANALYSIS
  const snap = { starts: liveUpdater.starts, stops: liveUpdater.stops, scrolls: renderer.scrolls, tints: renderer.tints.length };

  renderer.fireRange(false); // ANALYSIS のまま

  assert.equal(controller.mode, 'ANALYSIS');
  assert.equal(liveUpdater.starts, snap.starts);
  assert.equal(liveUpdater.stops, snap.stops, 'no-op で stop 追加なし');
  assert.equal(renderer.scrolls, snap.scrolls);
  assert.equal(renderer.tints.length, snap.tints);
});

test('A方式非活性: mode!=="b" は install でボタン disabled・配線しない（start しない）', () => {
  const { controller, liveUpdater, renderer, button } = setup({ mode: 'a' });

  controller.install();

  assert.equal(button.disabled, true, 'A方式はボタン disabled');
  assert.equal(liveUpdater.starts, 0, 'A方式は FOLLOW を適用せず start しない');
  assert.equal(renderer._rangeCb, null, 'A方式は可視範囲購読を配線しない');
  button.click();
  assert.equal(liveUpdater.stops, 0, 'A方式は click 未配線（副作用なし）');
});

test('DOM 不在 no-op: ボタン不在でも install は例外を出さず FOLLOW を適用する', () => {
  const { controller, liveUpdater } = setup({ hasButton: false });

  assert.doesNotThrow(() => controller.install());
  assert.equal(controller.mode, 'FOLLOW');
  assert.equal(liveUpdater.starts, 0, 'install は start しない（初回 start は入口が担う）');
});

test('DOM 不在 no-op: document 不在でも install は例外を出さない', () => {
  const { controller } = setup({ hasDocument: false });

  assert.doesNotThrow(() => controller.install());
  assert.equal(controller.mode, 'FOLLOW');
});

test('renderer 不在 no-op: renderer 未注入でも install は例外を出さず点灯する', () => {
  const liveUpdater = fakeLiveUpdater();
  const button = fakeButton();
  const document = fakeDocument(button);
  const controller = new LiveFollowController({
    liveUpdater, renderer: null, document, buttonId: 'live-follow-toggle', mode: 'b',
  });

  assert.doesNotThrow(() => controller.install());
  assert.equal(controller.mode, 'FOLLOW');
  assert.ok(button.classList.contains('is-active'), 'renderer 不在でもボタンは点灯する');
});

// 実 lwc 再現 renderer: scrollToRealTime() は非同期/アニメで、収束前に stale な可視範囲
//   （atRightEdge=false）を発火してから、右端収束後に true を発火する。この「scroll 由来イベント」を
//   模擬する（従来 fake は scroll のイベントを模擬せず＝見せかけ緑になっていた回帰の再現）。
function fakeRendererWithScrollEvents() {
  return {
    _rangeCb: null,
    scrolls: 0,
    tints: [],
    subscribeVisibleRange(cb) { this._rangeCb = cb; },
    scrollToRealTime() {
      this.scrolls += 1;
      // programmatic scroll 中: 収束前 stale(false) → 収束後 settled(true) の順で range イベントが発火。
      if (this._rangeCb) { this._rangeCb(false); this._rangeCb(true); }
    },
    setAnalysisTint(on) { this.tints.push(!!on); },
    fireRange(atRightEdge) { if (this._rangeCb) { this._rangeCb(atRightEdge); } },
  };
}

test('回帰: ANALYSIS→手動FOLLOW で programmatic scroll の stale(false) に落とされず FOLLOW を維持する', () => {
  // Arrange: scrollToRealTime が false→true を発火する fake（実 lwc の非同期 scroll 再現）。
  const liveUpdater = fakeLiveUpdater();
  const renderer = fakeRendererWithScrollEvents();
  const button = fakeButton();
  const document = fakeDocument(button);
  const controller = new LiveFollowController({
    liveUpdater, renderer, document, buttonId: 'live-follow-toggle', mode: 'b',
  });
  controller.install();
  renderer.fireRange(false); // パン離脱 → auto-off → ANALYSIS（_lastAtRightEdge=false＝手動FOLLOWで実 scroll が動く）
  assert.equal(controller.mode, 'ANALYSIS');
  const stopsBefore = liveUpdater.stops; // auto-off の 1 回

  // Act: 手動で FOLLOW へ。panned なので _applyFollow が scrollToRealTime を呼び、その中で stale(false)→settled(true)。
  controller.toggleManual();

  // Assert: stale(false) に落とされず FOLLOW を維持（不具合が起きると即 ANALYSIS へ戻り mode=ANALYSIS）。
  assert.equal(controller.mode, 'FOLLOW', 'programmatic scroll の stale イベントで ANALYSIS へ戻ってはならない');
  assert.equal(button.getAttribute('aria-pressed'), 'true', 'FOLLOW 点灯を維持');
  assert.equal(renderer.scrolls, 1, 'panned re-FOLLOW で scrollToRealTime が実際に動く（scroll 経路を実行）');
  assert.equal(liveUpdater.stops, stopsBefore, 'scroll の stale(false) で再 stop されない');
});

test('回帰: scroll 収束後（settled true 観測後）は genuine パン離脱で通常どおり ANALYSIS へ落ちる（suppression 解除）', () => {
  // Arrange: 上と同じく scroll が false→true を発火する fake。
  const liveUpdater = fakeLiveUpdater();
  const renderer = fakeRendererWithScrollEvents();
  const button = fakeButton();
  const document = fakeDocument(button);
  const controller = new LiveFollowController({
    liveUpdater, renderer, document, buttonId: 'live-follow-toggle', mode: 'b',
  });
  controller.install();
  renderer.fireRange(false); // パン離脱 → ANALYSIS（_lastAtRightEdge=false）
  controller.toggleManual(); // → FOLLOW（panned なので scroll が false→true を発火・suppression は true で解除される）
  assert.equal(controller.mode, 'FOLLOW');
  assert.equal(renderer.scrolls, 1, 'panned re-FOLLOW で実際に scroll が動く（収束 true で suppression 解除）');

  // Act: scroll 収束後に、ユーザーが実際に過去へパン（右端離脱）。
  renderer.fireRange(false);

  // Assert: suppression は settled(true) で解除済みのため、通常どおり auto-off が働く（回帰させない）。
  assert.equal(controller.mode, 'ANALYSIS', 'scroll 収束後の genuine パン離脱は auto-off が復活する');
});

// no-op scroll 再現: scrollToRealTime() は「既に右端」だと何も動かず range イベントを一切発火しない。
//   この経路では「atRightEdge=true を観測したら解除」だけに頼ると解除イベントが来ず _suppressAutoOff が
//   armed のまま stuck し、以降の genuine な右端離脱(false)を swallow して auto-off が死ぬ（code-review 🔴）。
//   default の fakeRenderer が既に「scrollToRealTime は scrolls++ のみ・イベント無し」＝no-op scroll 模擬。

test('回帰: 自動ON(no-op scroll)後の genuine パン離脱で auto-off が働く（suppression が stuck しない）', () => {
  // Arrange: default fakeRenderer（scrollToRealTime はイベントを発火しない＝既に右端の no-op scroll 模擬）。
  const { controller, renderer } = setup();
  controller.install();
  renderer.fireRange(false); // パン離脱 → auto-off → ANALYSIS
  assert.equal(controller.mode, 'ANALYSIS');

  // Act: 右端へ復帰 → 自動ON（ANALYSIS→FOLLOW）。既に右端のため scroll は no-op でイベント来ず。
  renderer.fireRange(true);
  assert.equal(controller.mode, 'FOLLOW');
  // さらに genuine なパン離脱。
  renderer.fireRange(false);

  // Assert: suppression が stuck していなければ auto-off が働き ANALYSIS へ落ちる。
  assert.equal(controller.mode, 'ANALYSIS', 'no-op scroll 経由の自動ON後も auto-off が発火する（suppression stuck 禁止）');
});

// --- ライブ連動フック onLiveStateChange（present 固有・MP モード協調役への通知）-------------------
//   optional な連動フック。_applyFollow（→FOLLOW=true）/_applyAnalysis（→ANALYSIS=false）遷移で呼ぶ。
//   未注入は no-op＝既存ライブトグル挙動 byte 不変（上の全既存テストが未注入で不変を担保）。

test('連動フック: FOLLOW→ANALYSIS（auto-off）で onLiveStateChange(false) を呼ぶ', () => {
  const states = [];
  const liveUpdater = fakeLiveUpdater();
  const renderer = fakeRenderer();
  const button = fakeButton();
  const controller = new LiveFollowController({
    liveUpdater, renderer, document: fakeDocument(button), buttonId: 'live-follow-toggle', mode: 'b',
    onLiveStateChange: (isFollow) => states.push(isFollow),
  });
  controller.install();

  renderer.fireRange(false); // パン離脱 → ANALYSIS

  assert.equal(controller.mode, 'ANALYSIS');
  assert.deepEqual(states, [false], 'ANALYSIS 遷移で false を 1 回通知');
});

test('連動フック: ANALYSIS→FOLLOW（auto-on）で onLiveStateChange(true) を呼ぶ', () => {
  const states = [];
  const liveUpdater = fakeLiveUpdater();
  const renderer = fakeRenderer();
  const button = fakeButton();
  const controller = new LiveFollowController({
    liveUpdater, renderer, document: fakeDocument(button), buttonId: 'live-follow-toggle', mode: 'b',
    onLiveStateChange: (isFollow) => states.push(isFollow),
  });
  controller.install();
  controller.toggleManual(); // → ANALYSIS（false 通知）

  renderer.fireRange(true); // 右端復帰 → FOLLOW（true 通知）

  assert.equal(controller.mode, 'FOLLOW');
  assert.deepEqual(states, [false, true], '遷移ごとに false→true を通知');
});

test('連動フック: 手動 toggle 往復でも遷移ごとに通知する', () => {
  const states = [];
  const controller = new LiveFollowController({
    liveUpdater: fakeLiveUpdater(), renderer: fakeRenderer(),
    document: fakeDocument(fakeButton()), buttonId: 'live-follow-toggle', mode: 'b',
    onLiveStateChange: (isFollow) => states.push(isFollow),
  });
  controller.install();

  controller.toggleManual(); // FOLLOW→ANALYSIS
  controller.toggleManual(); // ANALYSIS→FOLLOW

  assert.deepEqual(states, [false, true], '各手動遷移で通知');
});

test('連動フック未注入: 遷移しても例外を出さない（byte 不変・no-op）', () => {
  const { controller, renderer } = setup(); // onLiveStateChange 未注入
  controller.install();

  assert.doesNotThrow(() => renderer.fireRange(false));
  assert.doesNotThrow(() => renderer.fireRange(true));
  assert.equal(controller.mode, 'FOLLOW');
});

test('回帰: 右端での手動 re-FOLLOW(no-op scroll)後の genuine パン離脱で auto-off が働く', () => {
  // Arrange: 右端に居る状態（_lastAtRightEdge=true）を range イベントで確定させる。
  const { controller, renderer } = setup();
  controller.install();
  renderer.fireRange(true); // 右端に居ることを記録
  controller.toggleManual(); // → ANALYSIS
  controller.toggleManual(); // → FOLLOW（既に右端なので scroll は no-op・イベント無し）
  assert.equal(controller.mode, 'FOLLOW');

  // Act: genuine なパン離脱。
  renderer.fireRange(false);

  // Assert: suppression が stuck していなければ auto-off が働く。
  assert.equal(controller.mode, 'ANALYSIS', '右端での手動 re-FOLLOW 後も auto-off が働く（suppression stuck 禁止）');
});
