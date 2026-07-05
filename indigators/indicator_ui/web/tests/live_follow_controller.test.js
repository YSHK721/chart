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

test('手動toggle: FOLLOW→ANALYSIS で stop・tint on・消灯', () => {
  const { controller, liveUpdater, renderer, button } = setup();
  controller.install();

  controller.toggleManual();

  assert.equal(controller.mode, 'ANALYSIS');
  assert.equal(liveUpdater.stops, 1, 'ANALYSIS 適用で stop');
  assert.equal(renderer.tints.at(-1), true, 'ANALYSIS は tint on');
  assert.equal(isLit(button), false, 'ANALYSIS はボタン消灯');
});

test('手動toggle: ANALYSIS→FOLLOW で start・scrollToRealTime・tint off・点灯', () => {
  const { controller, liveUpdater, renderer, button } = setup();
  controller.install();
  controller.toggleManual(); // → ANALYSIS
  const startsBefore = liveUpdater.starts;

  controller.toggleManual(); // → FOLLOW（再FOLLOW）

  assert.equal(controller.mode, 'FOLLOW');
  assert.equal(liveUpdater.starts, startsBefore + 1, '再FOLLOW で start');
  assert.equal(renderer.scrolls, 1, '再FOLLOW は scrollToRealTime で catch-up');
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

test('自動遷移 auto-on: ANALYSIS 中に右端復帰（atRightEdge=true）で FOLLOW へ', () => {
  const { controller, liveUpdater, renderer, button } = setup();
  controller.install();
  controller.toggleManual(); // → ANALYSIS
  const startsBefore = liveUpdater.starts;
  const scrollsBefore = renderer.scrolls;

  renderer.fireRange(true); // 右端復帰

  assert.equal(controller.mode, 'FOLLOW');
  assert.equal(liveUpdater.starts, startsBefore + 1, 'auto-on で start');
  assert.equal(renderer.scrolls, scrollsBefore + 1, 'auto-on は scrollToRealTime で catch-up');
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
