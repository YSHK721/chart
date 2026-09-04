// snap_price_resolver.js（domain）— クリック価格のスナップ解決（ISSUE-368 スライス 8-a）。
//
// 設計入力: .doc/POSITION_SIZING_CHART_INTEGRATION_DESIGN.md「追加要件裁定 R-P2」
//   （近傍（px 許容内）の指標系列値・水準線・当該足 OHLC を候補とし最近傍へスナップ。
//    候補が無ければ**素のクリック価格**＝任意の場所で入力できる）。R-P3（右クリック）も
//   同じ規則を使うため、規則の実装はここ 1 本だけに置く（解決器の単一ソース）。
//
// 純関数（import 0・DOM/lwc/fetch を触らない）。候補の列挙は adapter（ChartRenderer）の責務で、
//   本モジュールは列挙済みのプレーンデータだけを受け取る（座標も lwc 型も知らない）。

/**
 * 候補から最近傍を選び、許容内なら候補価格・許容外／候補なしなら素のクリック価格を返す。
 *
 * 固定した規則:
 *   - **同距離は候補配列の先頭が勝つ**。比較は厳密な `<`（後続は同距離で置き換えない）。
 *     候補の並びは列挙側（ChartRenderer.snapCandidatesAt）が決めた順序であり、同じ価格に
 *     複数の候補が重なっても解決結果が毎回同じになる（決定性）。
 *   - 許容は**閉区間**（距離 == 許容 はスナップする）。
 *   - 非有限の値はいずれもフェイルクローズ側へ倒す（クリック価格が非有限なら null、
 *     許容が非有限ならスナップしない、候補の price が非有限ならその候補を使わない）。
 *
 * @param {Array<{kind?:string,label?:string,price:number}>} candidates 列挙済み候補（並び順が優先順）。
 * @param {number} clickPrice クリック位置の素の価格。
 * @param {number} tolerancePrice スナップ許容（価格差・px 許容から換算済み）。
 * @returns {{price:number, snapped:boolean, candidate:object|null}|null}
 *   非有限のクリック価格は null（黙って 0 へ倒さない＝画面外の値を下流へ流さない）。
 */
export function resolveSnappedPrice(candidates, clickPrice, tolerancePrice) {
  if (!Number.isFinite(clickPrice)) {
    return null;
  }
  const tol = Number.isFinite(tolerancePrice) ? tolerancePrice : -Infinity;
  let best = null;
  let bestDist = Infinity;
  for (const c of Array.isArray(candidates) ? candidates : []) {
    const dist = Math.abs(c.price - clickPrice);
    if (dist < bestDist) {
      bestDist = dist;
      best = c;
    }
  }
  if (best === null || bestDist > tol) {
    return { price: clickPrice, snapped: false, candidate: null };
  }
  return { price: best.price, snapped: true, candidate: best };
}
