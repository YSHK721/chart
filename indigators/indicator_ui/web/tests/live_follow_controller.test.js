// live_follow_controller.js（LiveFollowController・ライブ追従トグル）の仕様検証。
//
// 設計入力（確定仕様・状態機械／ISSUE-118 ユーザー裁定 2026-07-18）:
//   状態 _mode ∈ {FOLLOW, ANALYSIS}（初期 FOLLOW）。
//   - 切替は**ライブボタンのクリックのみ**。旧仕様の自動遷移（可視範囲購読の右端離脱→ANALYSIS／
//     右端復帰→FOLLOW・_suppressAutoOff/_lastAtRightEdge の抑制機構）は削除した。
//   - install(): ボタン click 配線 ＋ 初期 FOLLOW 適用（可視範囲購読は配線しない）。
//   - toggleManual(): FOLLOW↔ANALYSIS 手動切替。
//   - FOLLOW 適用: 全ライブ更新系 start ＋ tint off ＋ 点灯（再FOLLOW時は無条件 scrollToRealTime で catch-up）。
//   - ANALYSIS 適用: 全ライブ更新系 stop ＋ tint on ＋ 消灯。
//   - DOM/renderer 不在: no-op 防御（例外を出さない）。
// 構造: Arrange-Act-Assert（AAA）。実 DOM・実 renderer 非依存（全 fake 注入）。

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { LiveFollowController } from '../js/adapter/front/live_follow_controller.js';

// liveUpdater.start/stop の呼び出し回数を記録する spy。
function fakeLiveUpdater() {
  return { starts: 0, stops: 0, start() { this.starts += 1; }, stop() { this.stops += 1; } };
}

// renderer fake。subscribeVisibleRange の cb を捕捉（配線されないことの検証用）、scroll/tint を記録。
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

function setup({ hasButton = true, hasDocument = true } = {}) {
  const liveUpdater = fakeLiveUpdater();
  const renderer = fakeRenderer();
  const button = fakeButton();
  const document = hasDocument ? fakeDocument(hasButton ? button : null) : null;
  const controller = new LiveFollowController({
    liveUpdater, renderer, document, buttonId: 'live-follow-toggle',
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
  assert.equal(liveUpdater.starts, 0, '初期 FOLLOW は install で start しない');
  assert.equal(liveUpdater.stops, 0);
  assert.equal(renderer.tints.at(-1), false, '初期 FOLLOW は tint off');
  assert.equal(renderer.scrolls, 0, '初期 FOLLOW は catch-up scroll しない');
  assert.ok(isLit(button), '初期 FOLLOW はボタン点灯');
});

test('ISSUE-118: install は可視範囲購読を配線しない（自動遷移の削除・B方式でも）', () => {
  const { controller, renderer } = setup();
  controller.install();
  assert.equal(renderer._rangeCb, null, 'subscribeVisibleRange は呼ばれない');
});

test('ISSUE-118: 右端離脱イベント相当が来ても状態・背景は変わらない（クリック以外で切り替わらない）', () => {
  const { controller, liveUpdater, renderer } = setup();
  controller.install();
  const snap = { stops: liveUpdater.stops, tints: renderer.tints.length };

  renderer.fireRange(false); // 旧仕様なら auto-off していた入力（購読が無いので届かない）

  assert.equal(controller.mode, 'FOLLOW', 'FOLLOW を維持');
  assert.equal(liveUpdater.stops, snap.stops, 'stop されない');
  assert.equal(renderer.tints.length, snap.tints, 'tint 適用は増えない');
});

test('FOLLOW/ANALYSIS で liveTickPlayer/formingBarUpdater も start/stop する（ANALYSIS で価格を凍結）', () => {
  const liveUpdater = fakeLiveUpdater();
  const player = fakeLiveUpdater();
  const forming = fakeLiveUpdater();
  const button = fakeButton();
  const controller = new LiveFollowController({
    liveUpdater, liveTickPlayer: player, formingBarUpdater: forming,
    renderer: fakeRenderer(), document: fakeDocument(button), buttonId: 'live-follow-toggle',
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

test('手動toggle: ANALYSIS→FOLLOW で start・無条件 scrollToRealTime(catch-up)・tint off・点灯（ISSUE-118）', () => {
  const { controller, liveUpdater, renderer, button } = setup();
  controller.install();
  controller.toggleManual(); // → ANALYSIS
  const startsBefore = liveUpdater.starts;

  controller.toggleManual(); // → FOLLOW

  assert.equal(controller.mode, 'FOLLOW');
  assert.equal(liveUpdater.starts, startsBefore + 1, '再FOLLOW で start');
  assert.equal(renderer.scrolls, 1, '再FOLLOW は無条件で scrollToRealTime（右端でも無害な no-op）');
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

// ISSUE-275: 合成根と同じ形（mode 引数なし）で構築しても活性化することを固定する。
//   かつて存在した A方式ゲート（mode!=='b' で disabled・未配線）は、A方式撤去（ISSUE-266/269）で
//   呼び出し側から mode が消えた結果**常に非活性側へ倒れ**、実 UI の「ライブ」ボタンが
//   グレーアウトしたまま押せない状態になっていた。検定は mode:'b' を渡していたため素通りした。
test('合成根と同じ構築（mode 引数なし）: install でボタンを活性化し click を配線する', () => {
  const liveUpdater = fakeLiveUpdater();
  const button = fakeButton();
  button.disabled = true; // index.html の初期状態（配線されるまで押せない）を再現。
  const controller = new LiveFollowController({
    liveUpdater, renderer: fakeRenderer(), document: fakeDocument(button),
    buttonId: 'live-follow-toggle',
  });

  controller.install();

  assert.equal(button.disabled, false, 'install がボタンを活性化する');
  assert.ok(isLit(button), '初期 FOLLOW で点灯する');
  button.click();
  assert.equal(controller.mode, 'ANALYSIS', 'click が配線されている');
  assert.equal(liveUpdater.stops, 1);
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
    liveUpdater, renderer: null, document, buttonId: 'live-follow-toggle',
  });

  assert.doesNotThrow(() => controller.install());
  assert.equal(controller.mode, 'FOLLOW');
  assert.ok(button.classList.contains('is-active'), 'renderer 不在でもボタンは点灯する');
});

// --- ライブ連動フック onLiveStateChange（present 固有・MP モード協調役への通知）-------------------
//   optional な連動フック。_applyFollow（→FOLLOW=true）/_applyAnalysis（→ANALYSIS=false）遷移で呼ぶ。
//   ISSUE-118 後は手動 toggle だけが遷移の入口。未注入は no-op。

test('連動フック: 手動 toggle 往復で遷移ごとに false→true を通知する', () => {
  const states = [];
  const controller = new LiveFollowController({
    liveUpdater: fakeLiveUpdater(), renderer: fakeRenderer(),
    document: fakeDocument(fakeButton()), buttonId: 'live-follow-toggle',
    onLiveStateChange: (isFollow) => states.push(isFollow),
  });
  controller.install();

  controller.toggleManual(); // FOLLOW→ANALYSIS
  controller.toggleManual(); // ANALYSIS→FOLLOW

  assert.deepEqual(states, [false, true], '各手動遷移で通知');
});

test('連動フック未注入: 手動遷移しても例外を出さない（no-op）', () => {
  const { controller } = setup(); // onLiveStateChange 未注入
  controller.install();

  assert.doesNotThrow(() => controller.toggleManual());
  assert.doesNotThrow(() => controller.toggleManual());
  assert.equal(controller.mode, 'FOLLOW');
});
