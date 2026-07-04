// replay/stream.js — 足内更新ストリーム構築の純ロジック（DOM/lwc/fetch 非依存）。
//
// 参照実装＝プロト web/js/replay.js（buildStream/cap/flattenM1/synthM1/durationSecs/足内窓）。
//   fetch（/intraday）は副作用として View 側に残し、窓算出（intrabarWindow）と fetch 後の
//   純変換（buildStreamFromResponse）のみを抽出＝分岐・境界・数式を 1つも足さず/削らず。

import { ANIM_FINE, ANIM_COARSE } from './timing.js';

export { ANIM_FINE, ANIM_COARSE };

const DAY = 86400;

// 足内窓近似用の時間足→秒（replay.js: TF_SECS / durationSecs）。
const TF_SECS = { '1m': 60, '5m': 300, '15m': 900, '30m': 1800, '1h': 3600, '4h': 14400, '1D': 86400, '1W': 604800, '1M': 2592000 };
export function durationSecs(tf) {
  return TF_SECS[tf] || 86400;
}

// 最大 n 点へ間引く。高値/安値(極値)と先頭/末尾は必ず保持する（極値ティックを捨てると
//   後段でティックに無い高安が表示される＝「存在しない高安」バグ）。（replay.js: cap()）
export function cap(arr, n) {
  if (arr.length <= n) return arr;
  let iMax = 0, iMin = 0;
  for (let i = 1; i < arr.length; i++) {
    if (arr[i] > arr[iMax]) iMax = i;
    if (arr[i] < arr[iMin]) iMin = i;
  }
  const keep = new Set([0, arr.length - 1, iMax, iMin]); // 先頭/末尾/最高/最安は必ず残す
  const stride = arr.length / n;
  for (let k = 0; k < n; k++) keep.add(Math.floor(k * stride));
  return [...keep].sort((a, b) => a - b).map((i) => arr[i]);
}

// 各 M1 を O→H→L→C の 4 疑似ティックへ（1分OHLC）。（replay.js: flattenM1()）
export function flattenM1(m1) {
  const out = [];
  for (const b of m1) { out.push(b[0], b[1], b[2], b[3]); }
  return out;
}

// 各 M1 を O→mid→H→mid→L→mid→C の補間で多数化（全ティック合成）。（replay.js: synthM1()）
export function synthM1(m1) {
  const out = [];
  for (const [o, h, l, c] of m1) { out.push(o, (o + h) / 2, h, (h + l) / 2, l, (l + c) / 2, c); }
  return out;
}

// 足内窓を足境界から決める（ラベル規約依存）:
//   左ラベル(1m..1D・time=期間始端) → [time, 次足)。
//   右ラベル(1W=W-FRI/1M=ME・time=期間終端) → [前足+1日, 今足+1日)。（replay.js: buildStream 窓算出）
export function intrabarWindow({ timeframe, cd, prevCandle, nextCandle }) {
  const rightLabeled = (timeframe === '1W' || timeframe === '1M');
  if (rightLabeled) {
    const winStart = (prevCandle ? prevCandle.time : cd.time - durationSecs(timeframe)) + DAY;
    const winEnd = cd.time + DAY;
    return { winStart, winEnd };
  }
  const winStart = cd.time;
  const winEnd = nextCandle ? nextCandle.time : cd.time + durationSecs(timeframe);
  return { winStart, winEnd };
}

// fetch 後の 5 モード点列構築（open_only/math は fetch 前短絡だが、点列自体は cd のみに依存＝ここで返す）。
//   （replay.js: buildStream の各 return）
export function buildStreamFromResponse({ mode, cd, m1 = [], ticks = [] }) {
  if (mode === 'open_only') return { prices: [cd.open], note: '始値のみ1更新' };
  if (mode === 'math') return { prices: [cd.close], note: '終値で1回（足内更新なし）' };
  if (mode === 'real_ticks') { // 接点検証＝全ティック（cap 廃止・間引かない・絶対仕様）
    if (ticks.length) return { prices: ticks, note: `実ティック ${ticks.length}点（全件）` };
    if (m1.length) return { prices: cap(flattenM1(m1), ANIM_FINE), note: '実ティック無→M1 OHLC代替' };
    return { prices: [cd.close], note: '足内データ無→終値のみ' };
  }
  if (mode === 'ohlc_1min') { // 粗い（1分OHLC・分足ステップが見える）
    if (m1.length) return { prices: cap(flattenM1(m1), ANIM_COARSE), note: `1分OHLC ${m1.length}本` };
    return { prices: [cd.open, cd.high, cd.low, cd.close], note: 'M1無→日足OHLC4点' };
  }
  if (m1.length) return { prices: cap(synthM1(m1), ANIM_FINE), note: '全ティック合成(M1×補間)' };
  return { prices: [cd.open, cd.high, cd.low, cd.close], note: 'M1無→OHLC4点' };
}
