// chart_window.js — 売買履歴チャートが「表示する範囲だけを持つ」ための区間の計算（純ロジック）。
//
// なぜ在るか（ISSUE-552/554 段階 2-2）: 1 分足の全履歴 run は 215 万本あり、足と値を丸ごと持つと
//   タブが落ちた（実測 2026-09-30）。画面が持つのは表示する範囲だけにし、利用者が端へ近づいたら
//   前後の区間をジョブの成果物から読み足す。本モジュールは「どこを読むか」「どこを捨てるか」
//   だけを決める。取得・描画・時計は持たない（View が行う）。DOM・lwc・fetch に触れない。
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

/**
 * 読み足す区間を決める（前と後それぞれ高々 1 回）。
 *
 * 「近づいた」の定義: 見えている端から持っている端までに残る足が、**見えている幅（1 画面ぶん）
 * より少ない**。run の端に着いている側は読まない。見えている範囲が読めなければ読まない。
 *
 * @param {{start: number, end: number}} held  持っている区間
 * @param {number} totalRows                   run の足の本数
 * @param {number} readRows                    1 回の本数
 * @param {{from: number, to: number}|null} visible 見えている範囲
 * @returns {Array<{side: 'before'|'after', start: number, end: number}>}
 */
export function planReads({ held, totalRows, readRows, visible }) {
  if (!visible || !Number.isFinite(visible.from) || !Number.isFinite(visible.to)) return [];
  const width = visible.to - visible.from;
  const heldRows = held.end - held.start;
  const reads = [];
  if (held.start > 0 && visible.from < width) {
    reads.push({ side: 'before', start: Math.max(0, held.start - readRows), end: held.start });
  }
  if (held.end < totalRows && (heldRows - 1) - visible.to < width) {
    reads.push({ side: 'after', start: held.end, end: Math.min(totalRows, held.end + readRows) });
  }
  return reads;
}

/**
 * 読んだ区間をつないだ後に持つ区間。上限を超えた分は、見ている位置（`viewCenter`・Bar 列の中の
 * 位置）から遠い側を捨てる。
 */
export function mergeWindow({ held, read, cap, viewCenter }) {
  const next = { start: Math.min(held.start, read.start), end: Math.max(held.end, read.end) };
  const over = (next.end - next.start) - cap;
  if (over <= 0) return next;
  const roomBefore = viewCenter - next.start;
  const roomAfter = next.end - viewCenter;
  return roomAfter >= roomBefore
    ? { start: next.start, end: next.end - over }
    : { start: next.start + over, end: next.end };
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
