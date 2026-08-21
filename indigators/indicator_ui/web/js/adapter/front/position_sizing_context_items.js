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
// 解除項目の名前は**表示名の単一ソース**から作る（ISSUE-435）。ここへ「損切り」と書き写すと、
//   モーダルの欄・アーム中バー・水準線ラベルと同じ表が 4 つ目に増える。
import { priceTargetLabel } from './price_format.js';

export const SET_STOP_LABEL = 'この価格を損切りに設定';
export const ADD_ENTRY_LABEL = 'この価格を建値に追加';
export const SET_TAKE_LABEL = 'この価格を利確に設定';

/** 解除項目の名前（表示名 + 接尾辞）。接尾辞をここ 1 か所に置く（項目ごとに書かない）。 */
export const clearLabel = (target) => `${priceTargetLabel(target)}を解除`;

/**
 * @param {object} deps
 * @param {Function} deps.resolvePrice (context {x,y}) => { price, snapped, candidate, reason }。
 *   8-c/8-d 共通の `resolvePickedPrice` を束ねたもの（座標変換を本モジュールに持たせない）。
 * @param {?Function} [deps.onSetStop]  (price) => void
 * @param {?Function} [deps.onAddEntry] (price) => void
 * @param {?Function} [deps.onSetTake]  (price) => void
 * @param {?object} [deps.toast] ChartToastView 互換（show(text)）。未注入なら告知しない。
 * @param {?Function} [deps.onClear] (target) => void。解除（'entry:i' / 'stop' / 'take'）。
 * @param {?Function} [deps.getLevels] () => {entryPrices,stopPrice,takePrice}。**呼ばれた時点の**
 *   水準（未注入なら解除項目を出さない＝従来どおりの 3 項目）。
 * @returns {Array<{label:string,onSelect:Function}>} ChartContextMenu へ渡す項目。
 */
export function createPriceContextItems({
  resolvePrice, onSetStop = null, onAddEntry = null, onSetTake = null, toast = null,
  onClear = null, getLevels = null,
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
  // 解除項目（ISSUE-435 実装 1）。**価格解決を通さない**: 解除は「いま在る水準を消す」操作で
  //   座標を 1 つも使わないため、座標から価格が作れるかどうかと無関係に成立する
  //   （下段ペインの右クリックでも効く。したがって既存 3 項目の案内トーストも要らない＝
  //    出す条件が無いのに案内だけ出すと「押しても入らない」と同じ誤解を生む）。
  //   銘柄仕様（刻み）が未解決でも落とさない: フェイルセーフが落とすのは
  //   「チャート由来の**価格を作る**経路」であって、価格を捨てる経路ではない。
  const clearItem = (target) => ({
    label: clearLabel(target),
    onSelect: () => {
      if (typeof onClear === 'function') {
        onClear(target);
      }
    },
  });
  return [
    item(SET_STOP_LABEL, onSetStop),
    item(ADD_ENTRY_LABEL, onAddEntry),
    item(SET_TAKE_LABEL, onSetTake),
    ...clearTargets(typeof getLevels === 'function' ? getLevels() : null).map(clearItem),
  ];
}

/**
 * 解除できる対象名の一覧（＝**設定済み**の水準だけ）。
 *
 * 「設定済み」の判定をここ 1 か所に置く: 水準は未入力を `null` で表し、打ちかけ・不正入力は
 * 非有限で届きうる（`_emitLevels` の `Number(el.value)`）。どちらも「線が引かれていない」状態で、
 * 水準線 primitive が線を描く条件（`Number.isFinite`）と同じ式にしてある。ここだけ緩いと
 * 「線が無いのに解除項目が出る」ことになる。
 *
 * 並びはモーダルの価格欄と同じ（建値 1..K → 損切り → 利確・`_renderPriceRows` の順）。
 * メニューと欄で順序が違うと、同じ集合を 2 通りの並びで覚えることになる。
 */
function clearTargets(levels) {
  if (!levels) {
    return [];
  }
  const targets = [];
  const entries = Array.isArray(levels.entryPrices) ? levels.entryPrices : [];
  entries.forEach((price, index) => {
    if (Number.isFinite(price)) {
      targets.push(`entry:${index}`);
    }
  });
  if (Number.isFinite(levels.stopPrice)) {
    targets.push('stop');
  }
  if (Number.isFinite(levels.takePrice)) {
    targets.push('take');
  }
  return targets;
}

/**
 * **開くたびに読み直される**項目一覧を作る（ISSUE-435 実装 1）。
 *
 * なぜ必要か（実測）: `ChartContextMenu` は構築時に受け取った配列参照を保持し
 * （`chart_context_menu.js:34`）、開くたびにそれを `for (const item of this._items)` で読む
 * （同 :122）。解除項目は「設定済みの水準ぶんだけ」出す＝一覧は install 時点で確定できないが、
 * メニュー側は 1 バイトも変えられない（設計「ピッカー経路の実測検証」3: 自前 new は
 * 二重リスナー、無条件追加は replay 汚染）。**唯一の読み直しの契機は反復の開始**なので、
 * そこを最新化の一点にする。
 *
 * なぜ「開くたびに push で更新する」形か: 添字・`length`・`map` が反復結果と食い違わないため。
 * 既存の呼び出し側（`items[0].onSelect(...)`）と boot 補助はそのまま動く。
 *
 * 採らなかった案と理由:
 *   - メニュー側へ `items()` 関数の口を足す: `ChartContextMenu` の改変（禁止）。
 *   - contextmenu を先に受けて配列を差し替える: 同一要素のリスナー登録順、または
 *     祖先の capture に依存する。前者は配線の並べ替えで無音で壊れ、後者は最小 DOM の
 *     fake で発火しない＝この一覧の増減を検定で押さえられなくなる。
 *
 * @param {Function} build () => Array<{label,onSelect}>。呼ばれた時点の項目一覧を返す。
 * @returns {Array} 反復のたびに `build()` の内容へ更新される配列。
 */
export function liveMenuItems(build) {
  const items = build();
  Object.defineProperty(items, Symbol.iterator, {
    value() {
      const next = build();
      items.length = 0;
      items.push(...next);
      return Array.prototype[Symbol.iterator].call(items);
    },
  });
  return items;
}
