// 右クリック経路とピッカー経路の一致（ISSUE-368 工程 3・D-1 / D-2 の結線）のテスト。
//
// 設計入力（唯一の仕様源）: .doc/POSITION_SIZING_CHART_INTEGRATION_DESIGN.md
//   「追加要件裁定 R-P2/R-P3」・`price_pick_resolver.js:15-16`
//   （**「右クリックとピッカーで入る価格が違う」を起こさないため、呼び出し口は 2 つでも
//     規則の実装は 1 つに保つ**）、
//   「追補: 工程 2」丸めの適用点 経路 1（スナップ候補）・2（素のクリック価格）・3（ゴーストラベル）、
//   同 S-6（銘柄仕様の解決は front 配下 **1 か所**＝`chart_app_wiring`。ここでは解決せず配られた値を使う）、
//   依頼者裁定 2026-08-20（D-2）: ゴーストの表示桁は台帳の `digits` に従う。
//
// 除去する原因（実測 D-1）: `price_pick_controller.js` の `_resolve()` が
//   `resolvePickedPrice({renderer, x, y, tolerancePx})` を呼ぶだけで **spec を転送していない**。
//   その結果、量子化（経路 1・2）がピッカー経路だけ効かず、**同じ座標でも右クリックと
//   ピッカーで選ばれる候補が変わりうる**。
//
// 観点: ソース走査では「渡してはいるが繋がっていない」を見逃す（ISSUE-291）。実物の共有配線で
//   組み上げ、**同一座標を押した結果どの値・どの候補が入るか**を 2 経路で突き合わせる。
// 構造: Arrange-Act-Assert（AAA）。最小 DOM（版面アンカー .chart-wrap を持つ）。

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { lookupSymbolSpec } from '../js/adapter/front/symbol_spec_catalog.js';
import {
  boot, flatten, dialogRoot, priceInput, ghostLabel, contextItems,
} from './support/position_sizing_boot.js';

const JP225_REF = 'jp225_tick';   // 台帳: tick=1.0 / digits=0
const TSLA_REF = 'sample';        // 台帳: tick=0.01 / digits=2

// 量子化で**勝つ候補が入れ替わる**近接 2 候補（値は台帳の刻み 1.0 に対して設計した）。
//   素の価格 62707.710070965324 からの距離: 安値 0.51007 < 高値 0.58993 → 量子化しないと「安値」。
//   刻み 1.0 で丸めると 62708 に対し 安値 62707（距離 1）・高値 62708（距離 0）→「高値」。
//   差は 0.08 と 1.0 で、浮動小数の誤差（~1e-11）とは桁が 9 つ違う＝境界のきわどさに依存しない。
const NEAR_CANDIDATES = Object.freeze([
  Object.freeze({ kind: 'ohlc', label: 'low', price: 62707.2 }),
  Object.freeze({ kind: 'ohlc', label: 'high', price: 62708.3 }),
]);

// 右クリック「この価格を損切りに設定」（項目 0）を座標 y で選ぶ。
function pickByContextMenu(ctx, y) {
  contextItems(ctx)[0].onSelect({ x: 100, y });
  return ctx.positionSizing.levels().stopPrice;
}

// ピッカー：損切りをアーム → ホバー（ゴースト）→ 同一座標でクリック（確定）。
function pickByPicker(ctx, y) {
  flatten(dialogRoot(ctx)).find((e) => e.dataset && e.dataset.psPick === 'stop').fire('click');
  ctx.container.fire('pointermove', { clientX: 100, clientY: y });
  const ghost = ghostLabel(ctx).textContent;
  ctx.container.fire('click', { clientX: 100, clientY: y });
  return { price: ctx.positionSizing.levels().stopPrice, ghost };
}

// ---------------------------------------------------------------------------
// D-1: 2 経路が同一座標で同一の価格・同一の候補を返す
// ---------------------------------------------------------------------------

test('TC-PP01 同一座標・同一候補で、右クリックとピッカーが同じ価格を入れる（規則の実装は 1 つ）', () => {
  // Arrange: JP225（刻み 1）。量子化で勝つ候補が入れ替わる近接 2 候補を置く。
  const byMenu = pickByContextMenu(boot(JP225_REF, NEAR_CANDIDATES), 0);
  // Act
  const byPicker = pickByPicker(boot(JP225_REF, NEAR_CANDIDATES), 0);
  // Assert: 片方だけ量子化されていると、選ばれる候補が変わって値が食い違う。
  assert.equal(
    byPicker.price, byMenu,
    `同じ座標なのに経路で価格が違う（右クリック=${byMenu} / ピッカー=${byPicker.price}）`,
  );
  assert.equal(byMenu, 62708, '右クリック経路が刻み上の候補を選んでいない（前提の崩れ）');
});

test('TC-PP02 ピッカーは右クリックと同じ候補へ吸う（ゴーストが名指す候補が一致する）', () => {
  // Arrange: 量子化すると「高値」（62708.3 → 62708）が勝つ。量子化しないと「安値」（62707.2）。
  const ctx = boot(JP225_REF, NEAR_CANDIDATES);
  // Act
  const { ghost } = pickByPicker(ctx, 0);
  // Assert: 候補名まで一致していないと「同じ値になったのはたまたま」を見逃す。
  assert.equal(ghost, '62,708（高値）', `ピッカーが別の候補へ吸っている: ${ghost}`);
});

test('TC-PP03 銘柄仕様が解決できるとき、ゴーストの表示と欄へ入る値が一致する（候補あり）', () => {
  // Arrange
  const ctx = boot(JP225_REF, NEAR_CANDIDATES);
  // Act
  const { ghost } = pickByPicker(ctx, 0);
  // Assert
  assert.equal(Number(ghost.split('（')[0].replace(/,/g, '')), Number(priceInput(ctx, 'stop').value));
});

// ---------------------------------------------------------------------------
// D-2: ゴーストの表示桁は台帳の digits に従う（結線）
// ---------------------------------------------------------------------------

test('TC-PP04 digits=2 の銘柄ではゴーストが小数 2 桁で出る（整数固定にしない）', () => {
  // Arrange: 台帳の TSLA（tick=0.01 / digits=2）。候補なし＝素のクリック価格。
  const spec = lookupSymbolSpec(TSLA_REF);
  assert.deepEqual(
    { tick: spec.tick, digits: spec.digits }, { tick: 0.01, digits: 2 },
    '台帳の前提が変わっている（テストの前提を見直すこと）',
  );
  const ctx = boot(TSLA_REF, []);
  // Act
  const { ghost, price } = pickByPicker(ctx, 0);
  // Assert: 刻み 0.01 で入る値は 62707.71。整数固定のままだとゴーストは '62,708' で表示と値が乖離する。
  assert.equal(price, 62707.71, '刻み 0.01 で量子化されていない');
  assert.equal(ghost, '62,707.71', `ゴーストの表示桁が台帳の digits に従っていない: ${ghost}`);
  assert.equal(Number(ghost.replace(/,/g, '')), Number(priceInput(ctx, 'stop').value));
});

test('TC-PP05 digits=0 の銘柄ではゴーストは従来どおり整数（見た目の変化 0）', () => {
  // Arrange / Act
  const { ghost } = pickByPicker(boot(JP225_REF, []), 0);
  // Assert
  assert.equal(ghost, '62,708');
});
