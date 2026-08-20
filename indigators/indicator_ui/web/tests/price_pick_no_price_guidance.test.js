// アーム中に「価格が取れない座標」を押したとき、理由を必ず案内する（工程 5 🟡-1）。
//
// 設計入力（唯一の仕様源）: .doc/POSITION_SIZING_CHART_INTEGRATION_DESIGN.md
//   「ピッカー経路の実測検証」7 裁定（2026-08-20 依頼者承認）＝**下段ペインのクリックは確定せず
//    案内する**。同「フェイルセーフ」＝**無音で生値に落とさない。値ではなく機能を落とし、理由を出す**。
//
// 是正前の実測（`price_pick_controller.js:168`）:
//   `this._showGhost(y, resolved.reason === OTHER_PANE ? MSG_OTHER_PANE : '', { line: false });`
//   `reason` が `NO_PRICE`（`paneIndexAtCoordinate` が `null`＝時間軸の帯・ペイン区切り線）の
//   ときだけ文言が**空文字**になり、画面上で何も起きず理由も出ない。同ファイル `:178` の
//   コメントは「黙って何も起きない状態にしない」と述べており、実装がコメントに反していた。
//   右クリック経路（`position_sizing_context_items.js:45`）は同じ `NO_PRICE` に `MSG_NO_PRICE` を
//   出しており、**2 経路で非対称**だった。
//
// 文言は理由コードと同居する単一ソース（`price_pick_resolver.js`）から取る＝写しを作らない。
//
// 構造: Arrange-Act-Assert。DOM・renderer は fake（lwc 非依存）。

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

import { PricePickController } from '../js/adapter/front/price_pick_controller.js';
import { createPriceContextItems } from '../js/adapter/front/position_sizing_context_items.js';
import {
  resolvePickedPrice, MSG_NO_PRICE, MSG_OTHER_PANE,
} from '../js/adapter/front/price_pick_resolver.js';

class El {
  constructor() {
    this.children = [];
    this.style = {};
    this.textContent = '';
    this.className = '';
    this._cls = new Set();
    this._handlers = {};
  }

  get classList() {
    const s = this._cls;
    return {
      add: (c) => s.add(c), remove: (c) => s.delete(c), contains: (c) => s.has(c), toggle() {},
    };
  }

  appendChild(k) { this.children.push(k); return k; }

  append(...kids) { this.children.push(...kids); }

  querySelector() { return null; }

  getBoundingClientRect() { return { left: 0, top: 0 }; }

  addEventListener(type, fn) { (this._handlers[type] ||= []).push(fn); }

  fire(type, ev = {}) { (this._handlers[type] || []).forEach((fn) => fn(ev)); }
}

// 価格ペイン: y=0..299／下段ペイン: y=300..399／**それ以外は null**
//   （実物の `chart_renderer.js:1071-1083` `paneIndexAtCoordinate` が時間軸の帯・
//    ペイン区切り線で `null` を返すのと同じ振る舞い）。
const TIME_AXIS_Y = 450;   // 時間軸の帯（どのペインでもない）
const OTHER_PANE_Y = 350;  // 下段（オシレーター）ペイン

function fakeRenderer() {
  return {
    priceAtCoordinate: (y) => (Number.isFinite(y) ? 59000 - y : null),
    paneIndexAtCoordinate: (y) => {
      if (!Number.isFinite(y)) return null;
      if (y >= 0 && y < 300) return 0;
      if (y >= 300 && y < 400) return 1;
      return null;
    },
    snapCandidatesAt: () => [],
    suppressInteraction() { return () => {}; },
  };
}

function build() {
  const wrap = new El();
  const container = new El();
  const doc = {
    createElement: () => new El(),
    querySelector: (sel) => (sel === '.chart-wrap' ? wrap : null),
    addEventListener() {},
    removeEventListener() {},
  };
  const renderer = fakeRenderer();
  const confirmed = [];
  const picker = new PricePickController({
    container,
    renderer,
    document: doc,
    onConfirm: (target, price) => confirmed.push([target, price]),
  });
  picker.install();
  return {
    picker, container, renderer, confirmed, wrap,
  };
}

// ゴースト（線＋案内／採用予定価格）に出ている文字列すべて。
const ghostText = (wrap) => wrap.children
  .map((h) => [h, ...h.children].map((e) => e.textContent ?? '').join(' ')).join(' ').trim();

test('TC-NP01 価格が取れない座標のホバーで理由を案内する（無音にしない）', () => {
  // Arrange
  const ctx = build();
  ctx.picker.arm('stop');
  assert.equal(
    resolvePickedPrice({ renderer: ctx.renderer, x: 50, y: TIME_AXIS_Y }).reason, 'no_price',
    '前提が崩れている（この座標は no_price にならない）',
  );
  // Act
  ctx.container.fire('pointermove', { clientX: 50, clientY: TIME_AXIS_Y });
  // Assert
  assert.equal(ghostText(ctx.wrap), MSG_NO_PRICE, '理由が案内されていない（黙って何も起きない）');
});

test('TC-NP02 価格が取れない座標のクリックは確定せず、案内を出したままアームを続ける', () => {
  // Arrange
  const ctx = build();
  ctx.picker.arm('stop');
  // Act
  ctx.container.fire('click', { clientX: 50, clientY: TIME_AXIS_Y, button: 0 });
  // Assert
  assert.deepEqual(ctx.confirmed, [], '価格が取れないのに確定している');
  assert.equal(ctx.picker.isArmed(), true, '押し直せるようアームは続ける');
  assert.equal(ghostText(ctx.wrap), MSG_NO_PRICE, '押しても理由が出ない');
});

test('TC-NP03 下段ペインの案内は従来どおり（理由ごとに文言を選び分ける）', () => {
  // Arrange
  const ctx = build();
  ctx.picker.arm('stop');
  // Act
  ctx.container.fire('pointermove', { clientX: 50, clientY: OTHER_PANE_Y });
  // Assert
  assert.equal(ghostText(ctx.wrap), MSG_OTHER_PANE);
  assert.notEqual(MSG_OTHER_PANE, MSG_NO_PRICE, '2 つの理由が同じ文言では選び分けを検定できない');
});

test('TC-NP04 ピッカーと右クリックは同じ理由に同じ文言を出す（2 経路の対称性）', () => {
  // Arrange: 右クリック経路（8-c）を同じ resolver・同じ座標で動かす。
  const ctx = build();
  const shown = [];
  const items = createPriceContextItems({
    resolvePrice: (c) => resolvePickedPrice({ renderer: ctx.renderer, x: c.x, y: c.y }),
    toast: { show: (t) => shown.push(t) },
  });
  ctx.picker.arm('stop');
  // Act
  items[0].onSelect({ x: 50, y: TIME_AXIS_Y });
  ctx.container.fire('pointermove', { clientX: 50, clientY: TIME_AXIS_Y });
  // Assert
  assert.deepEqual(shown, [MSG_NO_PRICE]);
  assert.equal(ghostText(ctx.wrap), shown[0], '同じ理由なのに経路で案内が違う');
});

test('TC-NP05 文言の写しを作らない（定義は price_pick_resolver の 1 か所）', () => {
  // Arrange
  const src = readFileSync(
    fileURLToPath(new URL('../js/adapter/front/price_pick_controller.js', import.meta.url)),
    'utf8',
  );
  // Act / Assert: 文言そのものを書いていないこと（import で取ること）。
  assert.equal(
    src.includes(MSG_NO_PRICE), false,
    'ピッカーが案内文言を自前で持っている（裁定の文言変更で片方が取り残される）',
  );
  assert.match(src, /MSG_NO_PRICE/, '単一ソースの文言を参照していない');
});
