// position_sizing_context_items.js — 右クリックメニューの価格設定 3 項目（ISSUE-368 スライス 8-c）。
//
// 設計入力: .doc/POSITION_SIZING_CHART_INTEGRATION_DESIGN.md「追加要件裁定 R-P3」
//   （contextmenu で「この価格を損切りに設定／建値に追加／利確に設定」。価格解決は R-P2 と
//    同一のスナップ規則＝解決器の単一ソース）、「ピッカー経路の実測検証」3（既存 `ChartContextMenu`
//   へ**項目注入**する。自前 new は二重リスナー・共有配線への無条件追加は replay 汚染のため禁止）、
//   同 7 裁定（2026-08-20: 下段ペインのクリックは確定せず案内する）。
//
// 責務（SRP）: 「座標 → 価格 → 注入ハンドラ」を繋ぐだけ（copy_bar_info_item.js と同じ項目の作法）。
//   価格の作り方は `price_pick_resolver`（注入）、水準の持ち方は呼び出し側（usecase）の責務。
//   メニューは項目の中身を知らず、本モジュールはメニューの開閉を知らない（OCP）。

// 案内文言は理由コードと同じ場所（price_pick_resolver）から取る。ここへ書き写すと、
//   ピッカー（8-d）側の同一文言と 2 か所に割れる（裁定の文言変更で片方が取り残される）。
import { OTHER_PANE, MSG_OTHER_PANE, MSG_NO_PRICE } from './price_pick_resolver.js';

export const SET_STOP_LABEL = 'この価格を損切りに設定';
export const ADD_ENTRY_LABEL = 'この価格を建値に追加';
export const SET_TAKE_LABEL = 'この価格を利確に設定';

/**
 * @param {object} deps
 * @param {Function} deps.resolvePrice (context {x,y}) => { price, snapped, candidate, reason }。
 *   8-c/8-d 共通の `resolvePickedPrice` を束ねたもの（座標変換を本モジュールに持たせない）。
 * @param {?Function} [deps.onSetStop]  (price) => void
 * @param {?Function} [deps.onAddEntry] (price) => void
 * @param {?Function} [deps.onSetTake]  (price) => void
 * @param {?object} [deps.toast] ChartToastView 互換（show(text)）。未注入なら告知しない。
 * @returns {Array<{label:string,onSelect:Function}>} ChartContextMenu へ渡す項目。
 */
export function createPriceContextItems({
  resolvePrice, onSetStop = null, onAddEntry = null, onSetTake = null, toast = null,
} = {}) {
  const notify = (msg) => {
    if (toast && typeof toast.show === 'function') {
      toast.show(msg);
    }
  };
  const item = (label, handler) => ({
    label,
    onSelect: (context) => {
      const resolved = (typeof resolvePrice === 'function' ? resolvePrice(context) : null) || {};
      if (resolved.price === null || resolved.price === undefined) {
        // 確定しない理由をそのまま案内へ写す（裁定どおり下段ペインは専用文言）。
        notify(resolved.reason === OTHER_PANE ? MSG_OTHER_PANE : MSG_NO_PRICE);
        return;
      }
      if (typeof handler === 'function') {
        handler(resolved.price);
      }
    },
  });
  return [
    item(SET_STOP_LABEL, onSetStop),
    item(ADD_ENTRY_LABEL, onAddEntry),
    item(SET_TAKE_LABEL, onSetTake),
  ];
}
