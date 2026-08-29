// 水準線ラベルの**結線**（ISSUE-435 実装 2）: 銘柄仕様の表示桁が primitive まで届くか。
//
// 設計入力（唯一の仕様源）: 依頼者裁定 D-2（2026-08-20・ISSUE-368）「線に添える価格の表示桁は
//   銘柄仕様の `digits`。権威は Python 台帳ただ 1 つで、front は生成物を配られて渡すだけ」。
//   ラベルはゴースト（price_pick_controller）と**同じ規則・同じ桁**でなければならない
//   （同じ価格が線とゴーストで違う文字列になると、どちらが本当か分からなくなる）。
//
// 観点: primitive 単体で桁を受け取れても、共有配線が渡していなければ実 UI では整数のままになる
//   （ISSUE-291「受け口だけでなく端から端まで結線を固定」）。実物の配線で組んで確かめる。
// 構造: Arrange-Act-Assert。DOM・renderer は support/position_sizing_boot.js の最小 fake。

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { boot, contextItems, RAW_TOP } from './support/position_sizing_boot.js';
import { priceOnLine } from '../js/adapter/front/price_format.js';
import { lookupSymbolSpec } from '../js/adapter/front/symbol_spec_catalog.js';

const TSLA_REF = 'sample';   // 台帳: tick=0.01 / digits=2（小数を持つ銘柄）

// primitive が描いたタグの文字列を集める（裁定 2026-08-21 で 2 段ラベル → 1 行タグへ変更）。
function drawnTexts(primitive) {
  const texts = [];
  primitive.attached({
    chart: { timeScale: () => ({ width: () => 800 }) },
    series: { priceToCoordinate: (p) => (Number.isFinite(p) ? 62800 - p : null) },
    requestUpdate: () => {},
  });
  primitive.draw({
    useBitmapCoordinateSpace(fn) {
      fn({
        context: {
          save() {}, restore() {}, beginPath() {}, moveTo() {}, lineTo() {}, stroke() {},
          setLineDash() {}, fillText(text) { texts.push(text); },
          fillRect() {}, measureText: (t) => ({ width: t.length * 7 }),
          set textBaseline(_v) {}, get textBaseline() { return null; },
          set fillStyle(_v) {}, get fillStyle() { return null; },
          set strokeStyle(_v) {}, get strokeStyle() { return null; },
          set lineWidth(_v) {}, get lineWidth() { return 1; },
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
  return texts;
}

test('TC-LW01 線に添える価格は台帳の桁で出る（ゴースト・欄と同じ規則）', () => {
  // Arrange: 小数を持つ銘柄で右クリックから損切りを入れる（y=10 → 生値 RAW_TOP-10）。
  const ctx = boot(TSLA_REF);
  contextItems(ctx)[0].onSelect({ x: 100, y: 10 });
  const spec = lookupSymbolSpec(TSLA_REF);
  const stop = ctx.positionSizing.levels().stopPrice;
  assert.equal(Number.isFinite(stop), true, '前提: 損切りが入っていない');
  assert.notEqual(Math.round(RAW_TOP - 10), stop, '前提: 小数を持たない価格では桁の違いが出ない');
  // Act
  const texts = drawnTexts(ctx.wired.positionSizing.primitive);
  // Assert: タグの中身が「項目名 価格」で、価格は台帳の桁（整数へ丸められていない）。
  assert.equal(
    texts.includes(`損切り ${priceOnLine(stop, spec.digits)}`),
    true,
    `桁が配られていない（または項目名が出ていない）: ${texts.join(' / ')}`,
  );
});
