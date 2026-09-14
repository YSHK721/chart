// forming_fold.js（domain）— 形成中バーへ 1 ティックを畳み込む **唯一の規則**（ISSUE-272）。
//
// 規則: open は期間の最初のティックで固定。high/low は流入ティックの走行極値。close は当該ティック。
//   （volume の数え方とバー跨ぎの判定は呼び出し側の関心事＝本モジュールは持たない。）
//
// なぜ 1 箇所に置くか:
//   同じ規則が 4 箇所で独立に実装されていた（replay.js の animateForming / replay/forming_plan.js の
//   formingStatesAt / live_tick_player.js の _applyTick / Python の serve_live_tick_tails.forming_states）。
//   うち 2 つは自身のコメントで「ここがずれると一括計算の値が実際の描画状態と食い違う」
//   「ここがフロントとずれると描画状態と値が食い違う（ISSUE-232 で実際に起きた失敗モード）」と
//   注意書きしていた。**注意書きで守るのをやめ、実装を 1 つにする。**
//
// Python 側（serve_live_tick_tails.forming_states）は言語が違うため共有できない。
//   規則の一致は py_parity_golden の forming_fold ケースが拘束する（session_day / value_area と同方式）。
//
// 依存ゼロ（DOM・fetch・時間足の知識を持たない）。

/**
 * 形成中バーへ 1 ティックを畳み込んだ **新しい** OHLC を返す（非破壊）。
 *
 * @param {{open:number, high:number, low:number}} prev 直前までの形成中バー
 * @param {number} price 当該ティックの価格
 * @returns {{open:number, high:number, low:number, close:number}}
 */
export function foldTick(prev, price) {
  const p = Number(price);
  return {
    open: Number(prev.open),
    high: Math.max(Number(prev.high), p),
    low: Math.min(Number(prev.low), p),
    close: p,
  };
}

/**
 * 期間の最初のティックから形成中バーを開始する（open=high=low=close=price）。
 *
 * @param {number} price 最初のティックの価格
 * @returns {{open:number, high:number, low:number, close:number}}
 */
export function openBar(price) {
  const p = Number(price);
  return { open: p, high: p, low: p, close: p };
}
