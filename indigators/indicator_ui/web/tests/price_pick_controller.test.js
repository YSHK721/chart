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
    // 抑止は登録方式（ChartRenderer.suppressInteraction）。実物と同じく「最初の抑止で落ち、
    //   最後の解除で戻る」遷移を同じ配列へ記録するため、観測できる系列は従来と同一である。
    suppressInteraction() {
      calls.userInteraction.push(false);
      return () => { calls.userInteraction.push(true); };
    },
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
  // 書式は参照実装 :777（丸め＋桁区切り）。書式の権威は TC-PK09/10 が持つ。
  assert.match(text, /58,904/, '採用予定価格（スナップ後）を明示する');
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

// ---------------------------------------------------------------------------
// ゴーストラベルの価格書式（実 UI 実測 2026-08-20・要是正の最後の 1 件）
//
//   実測: `.price-pick-ghost textContent = "62698.25050922694"`＝**生の浮動小数**。
//   差分 2 の書式化がモーダル内に留まり、ピッカー面へ漏れていた。
//
//   採る規則は参照実装 `:777`（数直線マーカーの価格）= `Math.round(val).toLocaleString()`。
//   根拠: 設計書 :335 が `drawPriceLine :752-783` を「建値 / 損切り / ロスカットの数直線
//   （＝**チャート水準線の参照実装そのもの**）」と明記しており、ゴーストは
//   「これから置く水準線に添える価格」だから同じ面の規則に従う。
//   モーダル内の kv 行（`avgP.toFixed(0)` 等）は別の面の規則であり、混同しない。
// ---------------------------------------------------------------------------

test('TC-PK09 ゴーストの価格は参照実装 :777 の書式で出る（生の浮動小数を出さない）', () => {
  // Arrange: 候補なし＝素のクリック価格。clientY=1.25 → 59000-1.25 = 58998.75。
  const ctx = build();
  ctx.picker.arm('stop');
  // Act
  ctx.container.fire('pointermove', { clientX: 50, clientY: 1.25 });
  // Assert: Math.round(58998.75).toLocaleString() = '58,999'
  const text = ghostText(ctx.wrap);
  assert.match(text, /58,999/, '参照実装 :777 の書式（丸め＋桁区切り）で出ていない');
  assert.equal(/58998\.75|\.\d{3,}/.test(text), false, `生の浮動小数が出ている: ${text}`);
});

test('TC-PK10 スナップ時は書式化した価格に候補名を併記する（R-P2「採用予定値を明示」）', () => {
  // Arrange: y=96 の 4 価格（58904）に移動平均。
  const ctx = build({ candidates: [{ kind: 'series', label: 'sma20', price: 58904 }] });
  ctx.picker.arm('stop');
  // Act
  ctx.container.fire('pointermove', { clientX: 50, clientY: 100 });
  // Assert
  assert.match(ghostText(ctx.wrap), /58,904（sma20）/, '書式化した価格＋候補名の併記になっていない');
});

test('TC-PK11 OHLC 候補は日本語名で併記する（label はフィールド名・表示は View の責務）', () => {
  // Arrange
  const ctx = build({ candidates: [{ kind: 'ohlc', label: 'high', price: 58902 }] });
  ctx.picker.arm('stop');
  // Act
  ctx.container.fire('pointermove', { clientX: 50, clientY: 100 });
  // Assert
  assert.match(ghostText(ctx.wrap), /58,902（高値）/);
});
