// 水準線 drag が掴めないとき、理由を告知する（工程 5 🟡-2）。
//
// 設計入力（唯一の仕様源）: .doc/POSITION_SIZING_CHART_INTEGRATION_DESIGN.md
//   「フェイルセーフ（仕様が解決できないとき）」＝**無音で生値に落とさない。値ではなく機能を
//    落とし、理由を出す。** 未知 ref → ピッカー・右クリック・**drag は確定しない**／
//    トースト「この銘柄の価格の刻みが不明なため、チャートからの価格指定を無効にしています」。
//
// 是正前の実測: `chart_app_wiring.js` は `isGrabBlocked: () => picker.isArmed() || !symbolSpec`
//   で drag を止めるだけで、告知が 1 行も無かった
//   （`grep -n "toast\|MSG_" price_level_drag_controller.js` → 0 件）。
//   `symbolSpec` が解決できない構成で水準線を掴むと、線が動かず理由も出ない
//   ＝「掴めない」のか「バグ」なのか利用者に区別できない。
//
// 告知の形（本是正）: `PriceLevelDragController` に `onGrabBlocked`（**任意注入・既定 no-op**）を
//   足し、結線側が「刻みが不明なときだけ鳴らす」callback を渡す。**`isGrabBlocked` の真偽判定
//   そのものは変えない**（アーム中は従来どおり無告知＝連打で鳴らない）。
//
// 構造: Arrange-Act-Assert。DOM・renderer・primitive は fake。

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

import { PriceLevelDragController } from '../js/adapter/front/price_level_drag_controller.js';
import { createPriceLevels } from '../js/domain/price_levels.js';
import { MSG_NO_SYMBOL_SPEC } from '../js/adapter/front/price_pick_resolver.js';
import { boot } from './support/position_sizing_boot.js';

const HANDLE_Y = 100;      // 水準線が描かれている y（掴める）
const AWAY_Y = 250;        // 線から遠い y（掴む対象が無い）
const GRAB_TOLERANCE = 6;

class El {
  constructor() {
    this._handlers = {};
    this.children = [];
    this.style = {};
  }

  addEventListener(type, fn) { (this._handlers[type] ||= []).push(fn); }

  fire(type, ev = {}) { (this._handlers[type] || []).forEach((fn) => fn(ev)); }

  getBoundingClientRect() { return { left: 0, top: 0 }; }
}

function build({ grabBlocked = false, onGrabBlocked = undefined } = {}) {
  const container = new El();
  const primitive = {
    handleAt: (y, tol) => (Math.abs(y - HANDLE_Y) <= tol ? { kind: 'stop', index: 0 } : null),
  };
  const renderer = {
    priceAtCoordinate: (y) => 59000 - y,
    suppressInteraction: () => () => {},
  };
  let levels = createPriceLevels({
    direction: 'long', entryPrices: [58900], stopPrice: 58800, takePrice: null,
  });
  const drag = new PriceLevelDragController({
    container,
    renderer,
    primitive,
    grabTolerancePx: GRAB_TOLERANCE,
    getLevels: () => levels,
    onLevelsChange: (next) => { levels = next; },
    isGrabBlocked: () => grabBlocked,
    onGrabBlocked,
  });
  drag.install();
  return { drag, container, stopPrice: () => levels.stopPrice };
}

test('TC-GB01 掴めない状態で線を掴もうとしたら理由を告知する（無音にしない）', () => {
  // Arrange
  const notices = [];
  const ctx = build({ grabBlocked: true, onGrabBlocked: () => notices.push('blocked') });
  // Act
  ctx.container.fire('pointerdown', { button: 0, clientY: HANDLE_Y });
  // Assert
  assert.deepEqual(notices, ['blocked'], '掴めないのに理由が出ていない');
  assert.equal(ctx.drag.isDragging(), false, '掴めていないこと自体は従来どおり');
});

test('TC-GB02 線から離れた場所を押しても告知しない（無関係な操作で鳴らさない）', () => {
  // Arrange
  const notices = [];
  const ctx = build({ grabBlocked: true, onGrabBlocked: () => notices.push('blocked') });
  // Act
  ctx.container.fire('pointerdown', { button: 0, clientY: AWAY_Y });
  // Assert
  assert.deepEqual(notices, [], '掴む対象が無い場所で鳴っている（通常操作を奪っている）');
});

test('TC-GB03 掴める状態では告知しない（成功時に鳴らさない）', () => {
  // Arrange
  const notices = [];
  const ctx = build({ grabBlocked: false, onGrabBlocked: () => notices.push('blocked') });
  // Act
  ctx.container.fire('pointerdown', { button: 0, clientY: HANDLE_Y });
  // Assert
  assert.deepEqual(notices, []);
  assert.equal(ctx.drag.isDragging(), true, '掴めるはずの状態で掴めていない（前提の崩れ）');
});

test('TC-GB04 未注入なら従来どおり（既定 no-op・例外にしない）', () => {
  // Arrange
  const ctx = build({ grabBlocked: true });
  // Act / Assert
  assert.doesNotThrow(() => ctx.container.fire('pointerdown', { button: 0, clientY: HANDLE_Y }));
  assert.equal(ctx.drag.isDragging(), false);
});

test('TC-GB05 告知を足しても掴めない判定そのものは変わらない（水準は動かない）', () => {
  // Arrange
  const ctx = build({ grabBlocked: true, onGrabBlocked: () => {} });
  const before = ctx.stopPrice();
  // Act
  ctx.container.fire('pointerdown', { button: 0, clientY: HANDLE_Y });
  ctx.container.fire('pointermove', { buttons: 1, clientY: HANDLE_Y + 20 });
  // Assert
  assert.equal(ctx.stopPrice(), before, '掴めないはずなのに水準が動いた');
});

// ---------------------------------------------------------------------------
// 結線: 刻みが不明なときだけ鳴らす（文言は単一ソース）
// ---------------------------------------------------------------------------

// primitive の y 表は実描画でしか埋まらないため、掴み判定だけを差し替える
//   （検定対象は「結線が告知を配っているか」であって primitive の座標計算ではない）。
function grabAt(ctx) {
  ctx.wired.positionSizing.primitive.handleAt = () => ({ kind: 'stop', index: 0 });
  ctx.container.fire('pointerdown', { button: 0, clientY: HANDLE_Y });
}

test('TC-GB06 結線: 銘柄仕様が解決できない ref では掴めない理由を告知する', () => {
  // Arrange
  const ctx = boot('unknown_dataset_ref');
  ctx.toasts.length = 0;
  // Act
  grabAt(ctx);
  // Assert
  assert.deepEqual(ctx.toasts, [MSG_NO_SYMBOL_SPEC], 'drag 経路のフェイルセーフが無音のまま');
});

test('TC-GB07 結線: 解決できる ref ではアーム中に掴んでも鳴らない（連打で鳴らさない）', () => {
  // Arrange
  const ctx = boot('jp225_tick');
  ctx.wired.positionSizing.picker.arm('stop');
  ctx.toasts.length = 0;
  // Act
  grabAt(ctx);
  grabAt(ctx);
  // Assert
  assert.deepEqual(ctx.toasts, [], 'アーム中の掴みで告知が鳴っている（裁定どおり無告知のはず）');
});

test('TC-GB08 文言の写しを作らない（定義は price_pick_resolver の 1 か所）', () => {
  // Arrange
  const front = new URL('../js/adapter/front/', import.meta.url);
  const read = (n) => readFileSync(fileURLToPath(new URL(n, front)), 'utf8');
  // Act / Assert
  assert.equal(
    read('price_level_drag_controller.js').includes(MSG_NO_SYMBOL_SPEC), false,
    'drag が案内文言を自前で持っている（第 2 実装）',
  );
  assert.match(read('chart_app_wiring.js'), /MSG_NO_SYMBOL_SPEC/, '単一ソースの文言を参照していない');
});
