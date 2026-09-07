// ladder_window（domain/ladder_window.js）— 現在値を中心とした表示窓の**幾何**。
//
// 設計入力（依頼者指示 2026-08-30「表示本数が多いので調整。縦スクロールは必要なし。
//   現在を中心に」）: 現在値の前後だけを建てる。窓の外は**建てない**——建ててから隠すと
//   捨てる色計算が毎描画発生する（絶対命令 §4.1「作ってから捨てる」）。
//
// なぜ View から出したのか（ISSUE-502 段階 4C・F-2）:
//   ここにあるのは長さの算術（器の実高・行高・見出しの高さ → 収まる本数）だけで、DOM を
//   一切必要としない。View に置くと「何本収まるか」を確かめるのに表を組む必要があり、
//   境界（1 本しか入らない・見出しの方が高い・拡大方向）の検証が版面の検定に混ざる。
//   実高の**測定**は DOM の仕事なので View に残し、測った数から**決める**規則だけを出す。
//
// 計算量: いずれも定数時間。走査も発行もしない。

/** 現在値を中心に表示する水準の本数（片側）の**上限**。
 *
 *  実際の半径は初回描画後に器の実高から適合させる（`fitRadius`・縦スクロールが出ない
 *  本数まで縮める）。窓の外の存在は版面の掲示欄が知らせる（無言の縮退禁止）。 */
export const WINDOW_RADIUS = 15;

/**
 * 建てる範囲（サーバ並び順の添字）。
 *
 * @param {object}  opts
 * @param {number}  opts.total      絞り込み後の全行数
 * @param {number}  opts.at         現在値行が入る位置
 * @param {number}  opts.radius     片側の本数
 * @param {boolean} opts.windowless 全期間（窓なし全量）か
 * @returns {{start: number, end: number}} `rows.slice(start, end)` の範囲
 */
export function sliceWindow({ total, at, radius, windowless }) {
  return windowless
    ? { start: 0, end: total }
    : { start: Math.max(0, at - radius), end: Math.min(total, at + radius) };
}

/**
 * 器の実高へ適合させた新しい半径。適合の必要が無ければ null。
 *
 * 縮める方向にしか動かさない（拡縮の往復で毎描画作り直さないため）。実高を測れない環境
 * （テストダブル）では呼び手が 0 / 非数を渡し、ここは null を返す＝上限のまま（検定は決定的）。
 *
 * @param {object} opts
 * @param {*}      opts.boxHeight     走査域の実高（clientHeight）
 * @param {*}      opts.contentHeight 中身の高さ（scrollHeight）
 * @param {number} opts.rowHeight     行 1 本の実高
 * @param {number} opts.rowCount      いま建っている行数（見出しの高さを逆算するため）
 * @param {number} opts.currentRadius 現在の半径
 * @returns {?number} 新しい半径、または null（適合不要・測定不能）
 */
export function fitRadius({
  boxHeight, contentHeight, rowHeight, rowCount, currentRadius,
}) {
  if (typeof boxHeight !== 'number' || typeof contentHeight !== 'number'
      || boxHeight <= 0 || contentHeight <= boxHeight) {
    return null;   // 溢れていない（または測れない）＝縮める理由が無い。
  }
  if (!rowHeight) {
    return null;
  }
  const headerH = contentHeight - rowCount * rowHeight;
  const capacity = Math.floor((boxHeight - headerH) / rowHeight);
  // 現在値行 1 本ぶんを引いた残りを上下へ折半する。1 本は必ず建てる。
  const next = Math.max(1, Math.floor((capacity - 1) / 2));
  return next >= currentRadius ? null : next;
}
