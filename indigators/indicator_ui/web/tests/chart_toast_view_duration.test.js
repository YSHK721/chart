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
