// format（adapter/front/format.js）— 版面の数値表記の唯一源。
//
// 第 1 表（reach_sheet_view）と第 2 表（oscillator_sheet_view）が同じ価格表記を使う。
// 別々に持つと片方だけ直したときに無言で食い違う（MEMORY: no-hand-duplication-single-source）。

/** 価格の表記（§4.7 の版面: 桁区切りあり・小数 1 桁）。 */
export function formatPrice(value) {
  return Number(value).toLocaleString('en-US', {
    minimumFractionDigits: 1,
    maximumFractionDigits: 1,
  });
}
