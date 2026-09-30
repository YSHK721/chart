// chart_window.js — 売買履歴チャートが「表示する範囲だけを持つ」ための区間の計算（純ロジック）。
//
// なぜ在るか（ISSUE-552/554 段階 2-2）: 1 分足の全履歴 run は 215 万本あり、足と値を丸ごと持つと
//   タブが落ちた（実測 2026-09-30）。画面が持つのは表示する範囲だけにし、利用者が端へ近づいたら
//   前後の区間をジョブの成果物から読み足す。本モジュールは「どこを読むか」「どこを捨てるか」
//   だけを決める（2 つは同じ規則で一緒に決める＝`planReads`）。取得・描画・時計は持たない（View が行う）。DOM・lwc・fetch に触れない。
//
// 用語:
//   位置           … Bar 列の中の位置（0 始まり）。区間は半開 [start, end)。
//   持っている区間 … 画面がいま足と値を持っている区間（held）。
//   見えている範囲 … ChartRenderer の論理範囲 {from, to}。持っている区間の先頭を 0 とする位置で、
//                     端の外は範囲外の値（負・本数以上）になる。
//   1 回の本数     … 1 回の読みで取る足の本数（readRows）。
//   上限           … 持つ総量の上限（cap）。run の長さに依らない。

/**
 * 1 回の本数。ライブチャートが最初に読む本数（`recentBars`）を使い、サーバが宣言する 1 回の上限
 * （`maxReturnedRows`）より多くは読まない（超えると 413 で断られる）。
 */
export function readRowsOf({ recentBars, maxReturnedRows }) {
  return Math.min(recentBars, maxReturnedRows);
}

/**
 * 持つ総量の上限。1 回の本数の整数倍で、サーバが宣言する 1 回の上限を超えない最大
 * （1 回ぶんは必ず持てる）。宣言から導く——数を書かない。
 */
export function heldRowsCap({ readRows, maxReturnedRows }) {
  return readRows * Math.max(1, Math.floor(maxReturnedRows / readRows));
}

/** 最初に持つ区間＝run の末尾の 1 回ぶん（run が短ければ全部）。 */
export function tailWindow({ totalRows, readRows }) {
  return { start: Math.max(0, totalRows - readRows), end: totalRows };
}

/** 読む側の順（前・後）。 */
const SIDES = ['before', 'after'];

const clamp = (value, low, high) => Math.min(high, Math.max(low, value));

/**
 * 読み足す区間と、つないだ後に持つ区間を**一緒に**決める（前と後それぞれ高々 1 回）。
 *
 * 規則はここ 1 か所に置く。発行を決める段と捨てる段を分けると、読んだ区間そのものを捨てる
 * （読んでから捨てる）。分けていた頃は、上限まで持った後に広い表示範囲で操作すると、1 操作で
 * 前と後を読んで両方捨てた（独立レビュー 🟡-1・本番値で 3,000 行）。
 *
 * 守る範囲: 見えている範囲と、その両側の余白（`margin`）。余白は見えている幅（1 画面ぶん）。
 *   ただし守る範囲は上限に収める（幅 + 余白 × 2 ≤ 上限）ので、幅が上限の 1/3 を超えると余白は
 *   `(上限 − 幅) / 2` へ縮み、幅が上限以上なら 0。
 *   両端は外側の整数の位置へ丸める（位置は整数。読む条件と捨てる足が同じ境界を見る）。
 * 読む条件（「近づいた」）: 持っている区間が、その側で守る範囲を覆っていない。run の端に
 *   着いている側は読まない。見えている範囲が読めなければ読まない。
 * 捨てる足: 上限を超える分だけ、読む側と反対の端の、**守る範囲の外**の足。見えている足は捨てない。
 * 読む本数: 1 回の本数。ただし「上限までの空き + 捨てられる足」を超えない（超える分は、
 *   つないだ後に残らないので発行しない）。0 なら発行しない。
 *
 * @param {{start: number, end: number}} held  持っている区間
 * @param {number} totalRows                   run の足の本数
 * @param {number} readRows                    1 回の本数
 * @param {number} cap                         上限
 * @param {{from: number, to: number}|null} visible 見えている範囲
 * @param {Array<'before'|'after'>} [sides]    読む側（既定は前と後）
 * @returns {Array<{side: 'before'|'after', start: number, end: number,
 *                  next: {start: number, end: number}}>}
 *   `next` はその区間をつないだ後に持つ区間（並びの順につないだときの値）。
 */
export function planReads({ held, totalRows, readRows, cap, visible, sides = SIDES }) {
  if (!visible || !Number.isFinite(visible.from) || !Number.isFinite(visible.to)) return [];
  const width = visible.to - visible.from;
  const margin = clamp((cap - width) / 2, 0, width);
  // 守る範囲（Bar 列の中の**整数の位置**・両端を含む）。見えている範囲は小数で来る（lwc の論理範囲）
  //   ので、外側の整数の位置へ丸めてから使う。下の「覆っているか」と「捨てられる足」は、この同じ
  //   整数の境界だけを見る。小数のまま比べると、捨てられる足は `floor(keepTo)` まで数えるのに
  //   覆っているかは小数の `keepTo` と比べるので、前を読んで後を `floor(keepTo)` まで捨てた直後に
  //   「後を覆っていない」となり、後を読んで、いま読んだ前の一部を捨てた（独立レビュー 推奨 1・2・
  //   本番値で 1 操作 525 行・同じ位置の操作のたびに繰り返した）。
  const keepFrom = Math.floor(held.start + visible.from - margin);
  const keepTo = Math.ceil(held.start + visible.to + margin);
  const reads = [];
  let now = held;
  for (const side of sides) {
    const rows = now.end - now.start;
    const before = side === 'before';
    const uncovered = before
      ? now.start > 0 && now.start > keepFrom
      : now.end < totalRows && now.end - 1 < keepTo;
    if (!uncovered) continue;
    const wanted = Math.min(readRows, before ? now.start : totalRows - now.end);
    // 読む側と反対の端にある、守る範囲の外の足（捨てられる足）。
    const spare = clamp(before ? now.end - 1 - keepTo : keepFrom - now.start, 0, rows);
    const room = cap - rows;
    const length = Math.min(wanted, room + spare);
    if (length <= 0) continue;
    const dropped = Math.max(0, length - room);
    const next = before
      ? { start: now.start - length, end: now.end - dropped }
      : { start: now.start + dropped, end: now.end + length };
    reads.push(before
      ? { side, start: next.start, end: now.start, next }
      : { side, start: now.end, end: next.end, next });
    now = next;
  }
  return reads;
}

/**
 * 次に持つ区間（`next`）ぶんの列を、持っている列と読んだ列から位置どおりに作る。
 * 作るのは `names` の列だけ（描画に使う列。使わない列を持たない）。
 */
export function mergeColumns({ names, held, heldColumns, read, readColumns, next }) {
  const out = {};
  for (const name of names) {
    const first = read.side === 'before'
      ? { at: read.start, values: readColumns[name] }
      : { at: held.start, values: heldColumns[name] };
    const second = read.side === 'before'
      ? { at: held.start, values: heldColumns[name] }
      : { at: read.start, values: readColumns[name] };
    // 先に切ってからつなぐ（全部つないでから捨てない）。
    const cut = (part) => part.values.slice(
      Math.max(0, next.start - part.at), Math.max(0, next.end - part.at),
    );
    out[name] = cut(first).concat(cut(second));
  }
  return out;
}
