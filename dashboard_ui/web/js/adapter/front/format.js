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

/** 到達時間の表記 `YYYY/MM/DD HH:MM:SS`（依頼者指示 2026-08-30・ラダーの到達時間列）。
 *  時刻系は UTC（marketdata の date 列・チャート時間軸と同一。第 2 表の相対表記も UTC 日で
 *  数えている＝時刻系を 2 つにしない）。 */
export function formatReachTimestamp(unixSec) {
  const time = Number(unixSec);
  if (!Number.isFinite(time)) {
    return '';
  }
  const date = new Date(time * 1000);
  const pad = (n) => String(n).padStart(2, '0');
  return `${date.getUTCFullYear()}/${pad(date.getUTCMonth() + 1)}/${pad(date.getUTCDate())}`
    + ` ${pad(date.getUTCHours())}:${pad(date.getUTCMinutes())}:${pad(date.getUTCSeconds())}`;
}
