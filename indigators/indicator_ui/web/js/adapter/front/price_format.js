// price_format.js — 価格・比率・金額の表示書式（ISSUE-368）。
//
// 設計入力（唯一の仕様源）: 参照実装 integrated_position_sizing_calculator.html。
//   **書式は参照実装が正解を定義する**。ここにあるのは参照実装の式の写しだけで、
//   規則を自分で決めない・定義の無い項目へ勝手に規則を足さない。
//
// なぜ独立モジュールか（SRP・単一ソース）:
//   同じ「価格を人が読む文字列にする」規則を、モーダル（position_sizing_dialog）と
//   ピッカーのゴースト（price_pick_controller）が別々に持つと必ず片方が取り残される。
//   実際に、差分 2 でモーダル側だけを書式化した結果、ゴーストに生の浮動小数
//   `62698.25050922694` が出続けていた（実 UI 実測 2026-08-20）。第 2 実装を作らない。
//
// **面によって規則が違う**ことに注意（参照実装がそう定義している）:
//   - 数直線マーカー（線に添える価格）  : `Math.round(val).toLocaleString()`（:777）
//   - モーダルの kv 行（表の中の価格）  : `val.toFixed(0)`（加重平均建値・損切り・ロスカット）
//   どちらか一方へ寄せると参照実装から乖離するため、両方を別の関数として持つ。
//
// 純関数のみ（import 0・DOM/lwc/fetch を触らない）。ロケールは参照実装と同じく既定に従う
//   （`toLocaleString()` を引数なしで呼ぶ＝参照実装の記述そのまま）。

/**
 * 線に添える価格（参照実装 `:777` の数直線マーカー）。
 *
 * 設計書 :335 が `drawPriceLine :752-783` を「建値 / 損切り / ロスカットの数直線
 * （＝**チャート水準線の参照実装そのもの**）」と定めているため、チャート上の水準線・
 * その予定位置（ゴースト）に添える価格はこの規則に従う。
 *
 * @param {number} value 価格。
 * @returns {string} 例: 58998.75 → '58,999'
 */
export function priceOnLine(value) {
  return Math.round(value).toLocaleString();
}

/** 表の中の価格（参照実装の kv 行 `r.avgP.toFixed(0)` 等）。例: 58650.4 → '58650' */
export function priceInTable(value) {
  return value.toFixed(0);
}

/** 比 → 百分率（小数 2 桁）。参照実装 `(f*100).toFixed(2)+'%'`（f 系・制約 f・採用 f）。 */
export function percent2(value) {
  return `${(value * 100).toFixed(2)}%`;
}

/** 比 → 百分率（小数 1 桁）。参照実装 `(rorAtKelly*100).toFixed(1)+'%'`（RoR・使用率等）。 */
export function percent1(value) {
  return `${(value * 100).toFixed(1)}%`;
}

/** 金額（円）。参照実装 `¥${Math.round(v).toLocaleString()}`。 */
export function yen(value) {
  return `¥${Math.round(value).toLocaleString()}`;
}

/** 小数 3 桁。参照実装 `q.toFixed(3)`（負け確率 q）。 */
export function decimal3(value) {
  return value.toFixed(3);
}

/** 小数 2 桁。参照実装 `r.rr.toFixed(2)`（報酬:リスク比）。 */
export function decimal2(value) {
  return value.toFixed(2);
}

/** 符号付き小数 3 桁。参照実装 `${ev>=0?'+':''}${ev.toFixed(3)}`（EV）。 */
export function signedFixed3(value) {
  return `${value >= 0 ? '+' : ''}${decimal3(value)}`;
}

/**
 * ロット（参照実装 `:1041` の fmtLot）。
 *   int は `Math.floor(x+1e-9).toLocaleString()`、dec は `x.toFixed(2)`。
 * @param {number} value ロット数。
 * @param {string} lotMode 'int' | 'dec'
 */
export function lotAmount(value, lotMode) {
  return lotMode === 'int'
    ? Math.floor(value + 1e-9).toLocaleString()
    : value.toFixed(2);
}
