// 解除の**実経路**の検証（ISSUE-435 実装 1・端から端まで）。
//
// 設計入力（唯一の仕様源）: ISSUE.md ISSUE-435「抜本的解決 1」＋依頼者指示（2026-08-21）
//   「解除後、その水準は未設定（null）に戻り、**チャートの線も消える**こと」。
//
// 観点: 項目が出るだけでは「押しても何も起きない」を見逃す（ISSUE-291「受け口だけでなく
//   端から端まで結線を固定」）。実物の共有配線で組み上げ、右クリックの解除を押した結果
//   (a) モーダルの欄 (b) 水準（domain）(c) 水準線 primitive が描く線 の 3 つすべてを見る。
// 構造: Arrange-Act-Assert。DOM・renderer は support/position_sizing_boot.js の最小 fake。

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { boot, priceInput, contextItems } from './support/position_sizing_boot.js';

const JP225_REF = 'jp225_tick';   // 台帳: tick=1.0 / digits=0

// 価格 → y（可視範囲は広く取る）。実 lwc と同じく範囲外は null。
const Y_OF = (price) => (Number.isFinite(price) ? 62800 - price : null);

// 水準線 primitive が実際に描いた線の本数を数える（「線が消える」の観測点）。
function drawnLineCount(primitive) {
  const moves = [];
  primitive.attached({
    chart: { timeScale: () => ({ width: () => 800 }) },
    series: { priceToCoordinate: Y_OF },
    requestUpdate: () => {},
  });
  primitive.draw({
    useBitmapCoordinateSpace(fn) {
      fn({
        context: {
          save() {}, restore() {}, beginPath() {}, stroke() {}, fillText() {},
          moveTo(x, y) { moves.push(y); }, lineTo() {}, setLineDash() {},
          measureText: () => ({ width: 40 }),
          set strokeStyle(_v) {}, get strokeStyle() { return null; },
          set lineWidth(_v) {}, get lineWidth() { return null; },
          set fillStyle(_v) {}, get fillStyle() { return null; },
          set font(_v) {}, get font() { return null; },
          set textAlign(_v) {}, get textAlign() { return null; },
        },
        bitmapSize: { width: 800, height: 400 },
        mediaSize: { width: 800, height: 400 },
        horizontalPixelRatio: 1,
        verticalPixelRatio: 1,
      });
    },
  });
  return moves.length;
}

// 右クリック項目をラベルで選ぶ（並び順に依存しない）。
function selectByLabel(ctx, label) {
  const item = contextItems(ctx).find((i) => i.label === label);
  assert.ok(item, `右クリックメニューに項目が無い: ${label}`);
  item.onSelect({ x: 100, y: 10 });
}

test('TC-CP01 損切りを解除すると欄が空・水準が null・線が 1 本減る', () => {
  // Arrange: 右クリックで損切りを入れる（既存経路）。
  const ctx = boot(JP225_REF);
  contextItems(ctx)[0].onSelect({ x: 100, y: 10 });
  assert.equal(Number.isFinite(ctx.positionSizing.levels().stopPrice), true, '前提: 損切りが入っていない');
  const before = drawnLineCount(ctx.wired.positionSizing.primitive);
  // Act
  selectByLabel(ctx, '損切りを解除');
  // Assert
  assert.equal(ctx.positionSizing.levels().stopPrice, null, '水準が null に戻っていない');
  assert.equal(priceInput(ctx, 'stop').value, '', 'モーダルの欄が空になっていない');
  assert.equal(drawnLineCount(ctx.wired.positionSizing.primitive), before - 1, 'チャートの線が消えていない');
});

test('TC-CP02 建値は解除した本だけが消える（他の本・他の水準は動かない）', () => {
  // Arrange: 建値を 2 本入れる（「この価格を建値に追加」＝項目 1）。
  const ctx = boot(JP225_REF);
  contextItems(ctx)[1].onSelect({ x: 100, y: 10 });
  contextItems(ctx)[1].onSelect({ x: 100, y: 20 });
  const entries = ctx.positionSizing.levels().entryPrices;
  const kept = entries.filter(Number.isFinite);
  assert.equal(kept.length, 2, '前提: 建値が 2 本入っていない');
  const target = `建値 ${entries.findIndex(Number.isFinite) + 1}を解除`;
  // Act
  selectByLabel(ctx, target);
  // Assert: 解除した本は null・もう 1 本はそのまま。
  const after = ctx.positionSizing.levels().entryPrices;
  assert.equal(after.filter(Number.isFinite).length, 1, '解除で建値が 1 本だけ残っていない');
  assert.equal(after.includes(kept[1]), true, '解除していない建値まで消えた');
});

test('TC-CP03 解除した水準の解除項目は消える（設定済みでなくなる）', () => {
  // Arrange
  const ctx = boot(JP225_REF);
  contextItems(ctx)[2].onSelect({ x: 100, y: 10 });   // 利確に設定
  assert.equal(contextItems(ctx).some((i) => i.label === '利確を解除'), true, '前提: 解除項目が出ていない');
  // Act
  selectByLabel(ctx, '利確を解除');
  // Assert
  assert.equal(contextItems(ctx).some((i) => i.label === '利確を解除'), false, '解除後も項目が残っている');
});

test('TC-CP04 モーダルを閉じていても解除できる（書き戻し経路は 1 本＝欄を用意してから消す）', () => {
  // Arrange: 価格を入れてからモーダルを閉じる。
  const ctx = boot(JP225_REF);
  contextItems(ctx)[0].onSelect({ x: 100, y: 10 });
  ctx.shared.positionSizingDialog.close();
  assert.equal(ctx.shared.positionSizingDialog.isOpen(), false, '前提: モーダルが閉じていない');
  // Act
  selectByLabel(ctx, '損切りを解除');
  // Assert: 無音で落ちない（確定と同じく行き先を用意してから書く）。
  assert.equal(ctx.positionSizing.levels().stopPrice, null, '閉じた状態の解除が無音で落ちている');
});

test('TC-CP05 root が 1 回だけ作った項目一覧が、あとから設定された水準を反映する（実 UI の経路）', () => {
  // 実 UI では root が起動時に `createPositionSizingContextItems(...)` を**1 回**呼び、その戻り値が
  //   `ChartContextMenu` に握られたまま使われる（`composition_root_front.js:286` / `chart_context_menu.js:34`）。
  //   検定が毎回作り直していると、この「1 回だけ作る」経路の欠落を見逃す（ISSUE-291）。
  // Arrange: 起動時に 1 回だけ作る（水準はまだ空）。
  const ctx = boot(JP225_REF);
  const items = contextItems(ctx);
  assert.deepEqual([...items].map((i) => i.label).slice(3), [], '前提: 最初から解除項目が出ている');
  // Act: そのあとで損切りを入れる（右クリック・ピッカー・手入力のどれでも同じ）。
  ctx.positionSizing.setStopPrice(58340);
  // Assert: メニューが開くたびに読む形（for...of＝spread）で最新の項目が出る。
  assert.deepEqual([...items].map((i) => i.label).slice(3), ['損切りを解除'], 'root が握る一覧が更新されない');
});
