// replay/stream.js — 足内更新ストリーム構築の純ロジック（DOM/lwc/fetch 非依存）。
//
// 参照実装＝プロト web/js/replay.js（buildStream/cap/flattenM1/synthM1/durationSecs/足内窓）。
//   fetch（/intraday）は副作用として View 側に残し、窓算出（intrabarWindow）と fetch 後の
//   純変換（buildStreamFromResponse）のみを抽出＝分岐・境界・数式を 1つも足さず/削らず。

import { ANIM_FINE, ANIM_COARSE } from './timing.js';
import { sessionDayStart, nextSessionDayStart } from '../domain/session_day.js';

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
//   左ラベル(1m..日中・time=期間始端) → [time, 次足)。
//   1D（ISSUE-130・セッション日集計）→ [セッション始端, 次足のセッション始端)。バーは dataset
//     rollup と同一のセッション日（NY 前日17:00＝夏21:00/冬22:00 UTC 起点・ラベルはブローカー暦日の
//     UTC 深夜）で集計されるため、足内 tick 窓もラベルでなくセッション境界で切る（日曜夕データは
//     月曜バーの窓先頭に属する）。
//   右ラベル(1W=W-FRI/1M=ME・time=期間終端) → [前足+1日, 今足+1日)。（replay.js: buildStream 窓算出）
export function intrabarWindow({ timeframe, cd, prevCandle, nextCandle }) {
  const rightLabeled = (timeframe === '1W' || timeframe === '1M');
  if (rightLabeled) {
    const winStart = (prevCandle ? prevCandle.time : cd.time - durationSecs(timeframe)) + DAY;
    const winEnd = cd.time + DAY;
    return { winStart, winEnd };
  }
  if (timeframe === '1D') {
    const winStart = sessionDayStart(cd.time);
    const winEnd = nextCandle ? sessionDayStart(nextCandle.time) : nextSessionDayStart(cd.time);
    return { winStart, winEnd };
  }
  const winStart = cd.time;
  const winEnd = nextCandle ? nextCandle.time : cd.time + durationSecs(timeframe);
  return { winStart, winEnd };
}

// 合成 dwell の点毎タイムスタンプ（窓等分・MP tick-live 用）。every_tick/ohlc_1min の cap 後 N 点へ
//   窓 [winStart, winEnd) を等分した secs[i] = winStart + (winEnd-winStart)*i/(N-1) を返す。
//   DwellAccumulator が隣接差分で dwell 化するため総 dwell=窓時間、secs[last]=winEnd（settle が winEnd へ収束
//   する基点）。窓未提供（backend 取得でなくクライアント合成＝M1 は取得済）・N<=1 は []（当バー MP skip・base 継続）。
//   winEnd は intrabarWindow（real_ticks と同一窓＝因果性・未来リークなし）。
function synthSecs(n, winStart, winEnd) {
  if (n <= 1 || winStart == null || winEnd == null || !(winEnd > winStart)) return [];
  const out = new Array(n);
  for (let i = 0; i < n; i++) out[i] = winStart + (winEnd - winStart) * i / (n - 1);
  return out;
}

// fetch 後の 5 モード点列構築（open_only/math は fetch 前短絡だが、点列自体は cd のみに依存＝ここで返す）。
//   （replay.js: buildStream の各 return）
//   secs（tick_secs 並行配列・MP tick-live 用）:
//     - real_ticks: 実ティック経路のみ実 tick_secs を ticks と同順で並走（byte 不変・窓は無視）。
//     - every_tick/ohlc_1min: winStart/winEnd 提供時は cap 後 N 点へ合成 dwell secs（窓等分）を並走生成。
//     - open_only/math・窓未提供・M1 代替(real_ticks): secs:[]（当バー MP skip・base 継続）。
//   prices は全分岐で従来と完全一致（挙動の正解＝既存 return を 1つも足さず/削らず）。
export function buildStreamFromResponse({ mode, cd, m1 = [], ticks = [], secs = [], winStart = null, winEnd = null }) {
  if (mode === 'open_only') return { prices: [cd.open], secs: [], note: '始値のみ1更新' };
  if (mode === 'math') return { prices: [cd.close], secs: [], note: '終値で1回（足内更新なし）' };
  if (mode === 'real_ticks') { // 接点検証＝全ティック（cap 廃止・間引かない・絶対仕様）
    if (ticks.length) return { prices: ticks, secs: Array.isArray(secs) ? secs : [], note: `実ティック ${ticks.length}点（全件）` };
    if (m1.length) return { prices: cap(flattenM1(m1), ANIM_FINE), secs: [], note: '実ティック無→M1 OHLC代替' };
    return { prices: [cd.close], secs: [], note: '足内データ無→終値のみ' };
  }
  if (mode === 'ohlc_1min') { // 粗い（1分OHLC・分足ステップが見える）
    const prices = m1.length ? cap(flattenM1(m1), ANIM_COARSE) : [cd.open, cd.high, cd.low, cd.close];
    const note = m1.length ? `1分OHLC ${m1.length}本` : 'M1無→日足OHLC4点';
    return { prices, secs: synthSecs(prices.length, winStart, winEnd), note };
  }
  // every_tick（全ティック合成）
  const prices = m1.length ? cap(synthM1(m1), ANIM_FINE) : [cd.open, cd.high, cd.low, cd.close];
  const note = m1.length ? '全ティック合成(M1×補間)' : 'M1無→OHLC4点';
  return { prices, secs: synthSecs(prices.length, winStart, winEnd), note };
}
