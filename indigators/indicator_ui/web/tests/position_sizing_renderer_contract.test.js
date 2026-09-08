// 計算機の協働子が受け取る renderer 面の**契約射影**（ISSUE-431・ISP）。
//
// 問題（ISSUE-368 工程 5 レビュー 🟡-4）: `chart_app_wiring.js` の PricePickController /
//   PriceLevelDragController は `ChartRenderer` 実体を丸ごと受け取っていた。実際に使う面は
//   ピッカーが 4 メソッド（priceAtCoordinate / paneIndexAtCoordinate / snapCandidatesAt /
//   suppressInteraction・実測 grep）、drag が 2 メソッド（priceAtCoordinate /
//   suppressInteraction）だけなのに、公開面は movePane / attachBackgroundPrimitive /
//   barInfoAt 等を含む。renderer 側の無関係な変更が計算機のテスト（fakeRenderer の写し）へ
//   波及する構造だった。
//
// 抜本的解決: **同一ファイル内の正解形**（`createHostView(controller, COLOR_THEME_HOST_CONTRACT)`）
//   に合わせ、契約（クライアント所有・host_view.js の規約）で必要な面だけへ射影する。
//   挙動不変・受け口の縮小のみ（値の変換はしない）。
//
// 観点:
//   (a) 本番配線（boot ＝実物の installSharedUi / wireControllerCollaborators）で生成された
//       picker / drag が持つ renderer は、契約外の面に触れると**例外**（フェイルクローズ）。
//   (b) 契約内の面は従来どおり働く（挙動不変）。
//   (c) 契約の中身は実測した使用面と一致する（面を余分に開けない＝ISP の実効性）。

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { boot } from './support/position_sizing_boot.js';
import { PRICE_PICK_HOST_CONTRACT } from '../js/adapter/front/price_pick_controller.js';
import { PRICE_LEVEL_DRAG_HOST_CONTRACT } from '../js/adapter/front/price_level_drag_controller.js';

// 銘柄仕様が解決できる ref（他の計算機検定と同じ・値の期待はここに書かない）。
const REF = 'jp225';

test('TC-RC01 ピッカーの renderer は契約外の面に触れると例外（丸受けの遮断）', () => {
  const ctx = boot(REF);
  const picker = ctx.wired.positionSizing.picker;
  // 契約外（renderer の公開面には在るがピッカーは使わない）。丸受けなら素通りする。
  assert.throws(() => picker._renderer.barInfoAt, TypeError, 'barInfoAt が素通りしている（丸受けのまま）');
  assert.throws(() => picker._renderer.attachBackgroundPrimitive, TypeError);
  assert.throws(() => picker._renderer.scrollToLatest, TypeError);
});

test('TC-RC02 drag の renderer は契約外の面に触れると例外（丸受けの遮断）', () => {
  const ctx = boot(REF);
  const drag = ctx.wired.positionSizing.drag;
  assert.throws(() => drag._renderer.barInfoAt, TypeError, 'barInfoAt が素通りしている（丸受けのまま）');
  // ピッカー専用の面も drag には開けない（クライアント別契約＝ISP）。
  assert.throws(() => drag._renderer.snapCandidatesAt, TypeError, 'drag にピッカーの面が開いている');
});

test('TC-RC03 契約内の面は従来どおり働く（挙動不変）', () => {
  const ctx = boot(REF);
  const picker = ctx.wired.positionSizing.picker;
  const drag = ctx.wired.positionSizing.drag;
  // fakeRenderer の規則（y=0 が RAW_TOP・1px=1 価格）がそのまま届く＝値の変換をしていない。
  assert.equal(typeof picker._renderer.priceAtCoordinate, 'function');
  assert.equal(picker._renderer.priceAtCoordinate(0), ctx.renderer.priceAtCoordinate(0));
  assert.equal(picker._renderer.paneIndexAtCoordinate(10), 0);
  assert.equal(typeof drag._renderer.priceAtCoordinate, 'function');
  assert.equal(typeof drag._renderer.suppressInteraction, 'function');
  const release = drag._renderer.suppressInteraction();
  assert.equal(typeof release, 'function', 'suppressInteraction の解除関数が届いていない');
});

test('TC-RC04 契約の中身は実測した使用面と一致する（面を余分に開けない）', () => {
  // ピッカー: 自身の suppressInteraction ＋ resolver（price_pick_resolver.js）が要求する 3 面。
  assert.deepEqual([...PRICE_PICK_HOST_CONTRACT.methods].sort(), [
    'paneIndexAtCoordinate', 'priceAtCoordinate', 'snapCandidatesAt', 'suppressInteraction',
  ]);
  // drag: priceAtCoordinate（掴んだ y → 価格）と suppressInteraction のみ。
  assert.deepEqual([...PRICE_LEVEL_DRAG_HOST_CONTRACT.methods].sort(), [
    'priceAtCoordinate', 'suppressInteraction',
  ]);
  // フィールドは 1 つも開けない（メソッドだけの面）。
  assert.deepEqual([...PRICE_PICK_HOST_CONTRACT.fields], []);
  assert.deepEqual([...PRICE_LEVEL_DRAG_HOST_CONTRACT.fields], []);
});
