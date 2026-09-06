// ISSUE-383: ChartToastView.show の呼び出し単位 durationMs 上書き（能動通知はログ場所まで
//   読ませる必要があり既定 1.6 秒では短い）。省略時は従来既定＝既存呼び出しの挙動不変。
// 構造: Arrange-Act-Assert（AAA）。DOM 非依存（fake document / fake timer 注入）。

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { ChartToastView } from '../js/adapter/front/chart_toast_view.js';

// overlay_host が要求する最小 DOM（.chart-wrap 配下へ host 要素を生成する）。
function fakeDoc() {
  const host = {
    isConnected: true, textContent: '', className: '',
    classList: {
      _set: new Set(),
      add(c) { this._set.add(c); },
      remove(c) { this._set.delete(c); },
      contains(c) { return this._set.has(c); },
    },
    appendChild() {},
  };
  const wrap = { appendChild() {}, querySelector() { return null; } };
  return {
    querySelector(sel) { return sel === '.chart-wrap' ? wrap : null; },
    createElement() { return host; },
    _host: host,
  };
}

function fakeTimers() {
  const timers = [];
  return {
    timers,
    setTimeout(fn, ms) { const id = timers.length; timers.push({ fn, ms }); return id; },
    clearTimeout(id) { if (timers[id]) timers[id].cleared = true; },
  };
}

test('show(text): 省略時は既定 durationMs（従来挙動不変）', () => {
  const t = fakeTimers();
  const view = new ChartToastView({
    document: fakeDoc(), durationMs: 1600, setTimeout: t.setTimeout, clearTimeout: t.clearTimeout,
  });
  view.show('hello');
  assert.equal(t.timers[0].ms, 1600);
});

test('show(text, ms): この 1 回だけ表示時間を上書きする', () => {
  const t = fakeTimers();
  const view = new ChartToastView({
    document: fakeDoc(), durationMs: 1600, setTimeout: t.setTimeout, clearTimeout: t.clearTimeout,
  });
  view.show('guard', 10000);
  assert.equal(t.timers[0].ms, 10000);
  view.show('next');
  assert.equal(t.timers[1].ms, 1600); // 上書きは 1 回限り（既定へ戻る）
});

// ISSUE-492 / ISSUE-275 型の防止: 本番の合成根は `{ document }` だけで構築する
//   （chart_app_wiring.js:248）。timer 注入（seam）だけの世界で緑にせず、キー不在の
//   既定経路（globalThis タイマー・既定 1600ms）を最低 1 つ実行する。
//   既定は**構築時**に globalThis.setTimeout を bind するため、差し替えは構築前に行う。
test('本番形（document のみ）: 既定タイマーと既定 1600ms で配線される', () => {
  const scheduled = [];
  const realSetTimeout = globalThis.setTimeout;
  const realClearTimeout = globalThis.clearTimeout;
  globalThis.setTimeout = (fn, ms) => { scheduled.push({ fn, ms }); return scheduled.length; };
  globalThis.clearTimeout = () => {};
  try {
    const doc = fakeDoc();
    const view = new ChartToastView({ document: doc });
    view.show('hello');
    assert.equal(doc._host.textContent, 'hello');
    assert.equal(scheduled[0].ms, 1600);      // 既定 durationMs（コメント「既定 1.6 秒」の契約）
    scheduled[0].fn();                        // 既定タイマー経由で消える
    assert.equal(doc._host.classList.contains('is-hidden'), true);
  } finally {
    globalThis.setTimeout = realSetTimeout;
    globalThis.clearTimeout = realClearTimeout;
  }
});

test('show(text, 不正値): 0 以下・非数値は既定へフォールバック', () => {
  const t = fakeTimers();
  const view = new ChartToastView({
    document: fakeDoc(), durationMs: 1600, setTimeout: t.setTimeout, clearTimeout: t.clearTimeout,
  });
  view.show('a', 0);
  view.show('b', 'long');
  assert.equal(t.timers[0].ms, 1600);
  assert.equal(t.timers[1].ms, 1600);
});
