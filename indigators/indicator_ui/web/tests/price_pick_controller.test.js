// price_pick_controller.js（アーム式ピッカー本体・ISSUE-368 スライス 8-d）のテスト。
//
// 設計入力（唯一の仕様源）: .doc/POSITION_SIZING_CHART_INTEGRATION_DESIGN.md
//   「追加要件裁定 R-P1」（モーダル各価格欄の「チャートで指定」を押すとチャートがピッカーモードに
//    なり、クロスヘア追従のゴースト線＋採用予定価格を表示。クリックで確定・Esc（またはモーダル側の
//    取消）で解除。入力先が常に一意＝誤入力が構造的に起きない）、
//   「R-P2」（採用予定値はピッカー中のツールチップで明示する）、
//   「ピッカー経路の実測検証」7 **裁定済（2026-08-20）**（下段ペインのクリックは確定させず
//    「価格チャート上で指定してください」を案内表示する）、
//   スライス 4 の実測（縦パンの抑止は `setUserInteraction(false)` と縦パンブロッカーの**両方**が要る。
//    片方だけだと掴んだ瞬間にチャートが縦にずれる）。
//
// 構造: Arrange-Act-Assert。DOM・renderer は fake（lwc 非依存）。

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { PricePickController } from '../js/adapter/front/price_pick_controller.js';

class El {
  constructor() {
    this.children = [];
    this.style = {};
    this.textContent = '';
    this.className = '';
    this.innerHTML = '';
    this.parentElement = null;
    this._cls = new Set();
    this._handlers = {};
  }

  get classList() {
    const s = this._cls;
    return {
      add: (c) => s.add(c),
      remove: (c) => s.delete(c),
      contains: (c) => s.has(c),
      toggle: (c, on) => {
        const next = on === undefined ? !s.has(c) : on;
        if (next) { s.add(c); } else { s.delete(c); }
      },
    };
  }

  appendChild(k) { k.parentElement = this; this.children.push(k); return k; }

  append(...kids) { for (const k of kids) { this.appendChild(k); } }

  querySelector() { return null; }

  getBoundingClientRect() { return { left: 0, top: 0 }; }

  addEventListener(type, fn) { (this._handlers[type] ||= []).push(fn); }

  fire(type, ev = {}) { (this._handlers[type] || []).forEach((fn) => fn(ev)); }
}

// 価格ペイン: y=0..299（1px=1 価格・y=0 で 59000）／下段ペイン: y=300..399。
function fakeRenderer({ candidates = [] } = {}) {
  const calls = { userInteraction: [] };
  return {
    calls,
    priceAtCoordinate: (y) => (Number.isFinite(y) ? 59000 - y : null),
    paneIndexAtCoordinate: (y) => {
      if (!Number.isFinite(y)) return null;
      if (y >= 0 && y < 300) return 0;
      if (y >= 300 && y < 400) return 1;
      return null;
    },
    snapCandidatesAt: () => candidates,
    setUserInteraction: (on) => calls.userInteraction.push(on),
  };
}

function build({ candidates = [] } = {}) {
  const wrap = new El();
  const container = new El();
  const doc = {
    createElement: () => new El(),
    querySelector: (sel) => (sel === '.chart-wrap' ? wrap : null),
    addEventListener: (type, fn) => { (doc._h ||= {})[type] = fn; },
    removeEventListener: () => {},
    _h: {},
  };
  const renderer = fakeRenderer({ candidates });
  const confirmed = [];
  const blockers = [];
  const picker = new PricePickController({
    container,
    renderer,
    document: doc,
    registerVerticalPanBlocker: (fn) => { blockers.push(fn); return () => { blockers.length = 0; }; },
    onConfirm: (target, price) => confirmed.push([target, price]),
  });
  picker.install();
  const blocked = () => blockers.some((fn) => fn());
  return {
    picker, container, doc, renderer, confirmed, blocked, wrap,
  };
}

// ゴースト（線＋採用予定価格のツールチップ）のテキスト。
const ghostText = (wrap) => wrap.children.map((h) => [h, ...h.children].map((e) => e.textContent ?? '').join(' ')).join(' ');

test('TC-PK01 arm すると縦パンが止まる（ブロッカー ON ＋ setUserInteraction(false) の二重化）', () => {
  // Arrange
  const ctx = build();
  assert.equal(ctx.blocked(), false, 'アーム前は従来どおり縦パンできる');
  // Act
  ctx.picker.arm('stop');
  // Assert
  assert.equal(ctx.blocked(), true, 'ブロッカーが真＝アプリ自前の縦価格パンが始まらない');
  assert.deepEqual(ctx.renderer.calls.userInteraction, [false], 'lwc 側の操作も落とす');
  assert.equal(ctx.picker.armedTarget(), 'stop');
});

test('TC-PK02 ホバーで採用予定価格を表示する（スナップ時は候補名つき・R-P2）', () => {
  // Arrange: y=100 の 4 価格上に移動平均（＝許容 6px 内）。
  const ctx = build({ candidates: [{ kind: 'series', label: 'sma20', price: 58904 }] });
  ctx.picker.arm('stop');
  // Act
  ctx.container.fire('pointermove', { clientX: 50, clientY: 100 });
  // Assert
  const text = ghostText(ctx.wrap);
  assert.match(text, /58904/, '採用予定価格（スナップ後）を明示する');
  assert.match(text, /sma20/, 'どこへ吸ったかを明示する');
});

test('TC-PK03 クリックで確定し、書き戻して解除する（縦パン・lwc 操作も復元）', () => {
  // Arrange
  const ctx = build();
  ctx.picker.arm('entry:1');
  ctx.container.fire('pointermove', { clientX: 50, clientY: 100 });
  // Act
  ctx.container.fire('click', { clientX: 50, clientY: 100, button: 0 });
  // Assert
  assert.deepEqual(ctx.confirmed, [['entry:1', 58900]]);
  assert.equal(ctx.picker.isArmed(), false, '確定したら解除する（アームは 1 回 1 か所）');
  assert.equal(ctx.blocked(), false, '解除でブロッカーを外す');
  assert.deepEqual(ctx.renderer.calls.userInteraction, [false, true], 'lwc 操作を復元する');
});

test('TC-PK04 Esc で解除する（確定しない）', () => {
  // Arrange
  const ctx = build();
  ctx.picker.arm('take');
  // Act
  ctx.doc._h.keydown({ key: 'Escape' });
  // Assert
  assert.equal(ctx.picker.isArmed(), false);
  assert.deepEqual(ctx.confirmed, [], 'Esc は取消＝価格を書き戻さない');
  assert.equal(ctx.blocked(), false);
});

test('TC-PK05 モーダル側の取消（disarm）でも解除でき、冪等である', () => {
  // Arrange
  const ctx = build();
  ctx.picker.arm('stop');
  // Act
  ctx.picker.disarm();
  // Assert
  assert.equal(ctx.picker.isArmed(), false);
  assert.doesNotThrow(() => ctx.picker.disarm(), '二重解除で例外にしない');
  assert.deepEqual(ctx.renderer.calls.userInteraction, [false, true]);
});

test('TC-PK06 下段（オシレーター）ペインでは確定せず案内を表示する（裁定 2026-08-20）', () => {
  // Arrange
  const ctx = build();
  ctx.picker.arm('stop');
  // Act
  ctx.container.fire('pointermove', { clientX: 50, clientY: 350 });
  ctx.container.fire('click', { clientX: 50, clientY: 350, button: 0 });
  // Assert
  assert.deepEqual(ctx.confirmed, [], '下段ペインのクリックで価格を書き戻してはならない');
  assert.equal(ctx.picker.isArmed(), true, '確定していないのでアームは続く（押し直せる）');
  assert.match(ghostText(ctx.wrap), /価格チャート上で指定/, '案内文言は裁定どおり');
});

test('TC-PK07 非アーム時は表示も確定もしない（通常操作を奪わない）', () => {
  // Arrange
  const ctx = build();
  // Act
  ctx.container.fire('pointermove', { clientX: 50, clientY: 100 });
  ctx.container.fire('click', { clientX: 50, clientY: 100, button: 0 });
  // Assert
  assert.deepEqual(ctx.confirmed, []);
  assert.equal(ctx.blocked(), false);
  assert.equal(ghostText(ctx.wrap).includes('58900'), false);
});

test('TC-PK08 アーム対象の差し替えは後勝ち（入力先は常に一意＝R-P1 の要件）', () => {
  // Arrange
  const ctx = build();
  ctx.picker.arm('stop');
  // Act
  ctx.picker.arm('take');
  ctx.container.fire('click', { clientX: 50, clientY: 100, button: 0 });
  // Assert
  assert.deepEqual(ctx.confirmed, [['take', 58900]], '後から押した欄だけが入力先になる');
});
