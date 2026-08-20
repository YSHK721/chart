// price_pick_resolver.js — クリック座標 → 採用価格の解決（ISSUE-368 スライス 8-c/8-d 共通）。
//
// 設計入力: .doc/POSITION_SIZING_CHART_INTEGRATION_DESIGN.md「ピッカー経路の実測検証」2・4・7、
//   「追加要件裁定 R-P2/R-P3」（右クリックもアーム式ピッカーも**同一のスナップ規則**を使う）。
//
// 責務（SRP）: 座標を 1 本の手順で価格へ翻訳し、**確定しなかった理由の語彙（コードと案内文言）**を
//   1 か所で持つ。理由コードは元から「呼び出し側の案内表示の分岐に使う」ためのものなので、
//   対応する文言も同居させる（別モジュールへ散らすと、理由と文言の対応が入口ごとに割れる）。
//   1. `paneIndexAtCoordinate(y)` で**価格ペインか**を確かめる（裁定 2026-08-20: 下段ペインは無効）
//   2. `priceAtCoordinate(y)` で素の価格を得る
//   3. px 許容を**価格差へ換算**する（`priceAtCoordinate(y)` と `priceAtCoordinate(y+tolPx)` の差。
//      `priceToCoordinate` を front に生やさないための実測済みの手段）
//   4. `snapCandidatesAt(x)` の候補へ `resolveSnappedPrice`（domain）で吸わせる
//
// なぜ 1 本に閉じるか: 右クリック（8-c）とピッカー（8-d）で手順が割れると「右クリックとピッカーで
//   入る価格が違う」という食い違いが生まれる。呼び出し口は 2 つでも規則の実装は 1 つに保つ。
//
// 依存: renderer（隔離点の公開面のみ）と domain の純関数だけ。lwc API 名・DOM は触らない。

import { resolveSnappedPrice } from '../../domain/snap_price_resolver.js';

/** 確定しなかった理由（呼び出し側の案内表示の分岐に使う）。 */
export const OTHER_PANE = 'other_pane';        // 価格ペインの外（下段ペイン）＝裁定により無効
export const NO_PRICE = 'no_price';            // 価格に変換できない（可視範囲外・非対応環境）

// 理由に対応する案内文言。**理由コードと同居させる**（＝理由を増やしたら文言も同じ場所で増える）。
//   なぜここか: 案内を出す入口は右クリック（8-c）とピッカー（8-d）の 2 つあり、文言を各入口へ
//   書くと同じ文字列が 2 か所に写る。裁定の文言が変わったとき片方だけ残るのが「取り残し」で、
//   実際に本ブランチでは同一文字列が 2 モジュールへ複製されていた（本コミットで解消）。
//   構造ガード: price_pick_resolver.test.js が front 配下での literal 出現を 1 回に固定する。
export const MSG_OTHER_PANE = '価格チャート上で指定してください';
export const MSG_NO_PRICE = 'この位置の価格が取れません';

/** 掴み許容と揃えた既定の px 許容（price_level_drag_controller の既定と同じ考え方）。 */
export const DEFAULT_PICK_TOLERANCE_PX = 6;

// 価格ペインの番号。設計書「ピッカー経路の実測検証」2 が「価格ペイン（pane 0）」と定め、
//   実装上も固定される: メイン系列は `chart.addSeries`（既定 paneIndex=0）で作られ、
//   `ChartRenderer.movePane` は `_isPaneMovable` で価格ペインを移動元にも移動先にもしない。
//   よって「価格ペイン＝0」は決め打ちではなく構造的に保証された値である。
const PRICE_PANE_INDEX = 0;

/**
 * クリック（またはホバー）座標から採用予定価格を解決する。
 *
 * @param {object} args
 * @param {object} args.renderer ChartRenderer（priceAtCoordinate / paneIndexAtCoordinate / snapCandidatesAt）。
 * @param {number} args.x コンテナ左上基準の x（px）。
 * @param {number} args.y コンテナ左上基準の y（px）。
 * @param {number} [args.tolerancePx] スナップ許容（px）。
 * @returns {{price:(number|null), snapped:boolean, candidate:(object|null), reason:(string|null)}}
 *   確定しないときは price=null と reason を返す（0 や NaN を下流へ流さない）。
 */
export function resolvePickedPrice({
  renderer, x, y, tolerancePx = DEFAULT_PICK_TOLERANCE_PX,
} = {}) {
  const unresolved = (reason) => ({
    price: null, snapped: false, candidate: null, reason,
  });
  if (!renderer || typeof renderer.priceAtCoordinate !== 'function'
    || typeof renderer.paneIndexAtCoordinate !== 'function') {
    return unresolved(NO_PRICE);
  }
  // 1. 価格ペイン判定（フェイルクローズ: 判定できない環境では確定しない）。
  const pane = renderer.paneIndexAtCoordinate(y);
  if (pane === null || pane === undefined) {
    return unresolved(NO_PRICE);
  }
  if (pane !== PRICE_PANE_INDEX) {
    return unresolved(OTHER_PANE);
  }
  // 2. 素の価格。
  const price = renderer.priceAtCoordinate(y);
  if (price === null || price === undefined || !Number.isFinite(price)) {
    return unresolved(NO_PRICE);
  }
  // 3. px 許容 → 価格差（軸の向き・スケールに依らず、その場の 1px の価格幅から求める）。
  const shifted = renderer.priceAtCoordinate(y + tolerancePx);
  const tolerancePrice = Number.isFinite(shifted) ? Math.abs(shifted - price) : 0;
  // 4. スナップ（規則は domain の 1 本）。
  //    `resolveSnappedPrice` が null を返すのは**クリック価格が非有限のときだけ**（domain の契約・
  //    `snap_price_resolver.test.js` TC-SP05/TC-SP10 が固定）で、その場合は直前の 2 で既に
  //    返している。ここで null を再判定する分岐は**到達不能**なので置かない（起こり得ない失敗の
  //    ための分岐は、読む人に「起こり得る」と誤解させ、検定でも踏めない死んだ分岐になる）。
  const candidates = typeof renderer.snapCandidatesAt === 'function' ? renderer.snapCandidatesAt(x) : null;
  const snapped = resolveSnappedPrice(candidates, price, tolerancePrice);
  return { ...snapped, reason: null };
}
