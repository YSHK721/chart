// tickvol_bands.js（domain）— 取引密度ハイライトの純ロジック（DOM/lwc/fetch 非依存）。
//
// バックエンド /tickvol_profile が返す帯（セッション日始端からの [startOff, endOff) 秒）を、
// 画面上のどのバーを塗るかへ変換する。帯の定義（集計・閾値）はバックエンドが唯一の権威で、
// 本モジュールは「帯 → 塗るバー」の写像だけを担う（規則の二重定義を作らない）。
//
// 塗る規則: **バーの時間スロットの 50% 以上が帯に入るとき、そのバーを塗る**。
//   表示足より短い帯（例 1h 足に対する 15 分帯）はスロットの 25% しか占めないため塗られない
//   ＝チャートの解像度を超えた帯を 4 倍に誇張しない。逆に帯の大半を覆うバーは必ず塗られる。
//   バー単位で判定するため、休場・祝日・短縮セッション・DST の 23h/25h 日でも「存在するバー」
//   しか塗られない（時刻レンジ直塗りだと空白域まで塗ってしまう）。
//
// セッション日境界は session_day.js が唯一の規則源（NY 17:00 起点・DST は IANA tz 委譲）。
// `start + 86400` は使わない（DST 日は 23h/25h）。

import { TF_BAR_SEC } from './tf_meta.js';
import { sessionDayStart, nextSessionDayStart } from './session_day.js';

// 対応時間足の上限（依頼者確定 2026-08-01: 1 時間足以下）。値の列挙でなく tf 台帳から導出する
//   （tf の追加・改名で集合がずれない）。
export const TICKVOL_BANDS_MAX_BAR_SEC = 3600;

export function tickvolBandsSupportsTf(tf) {
  const s = TF_BAR_SEC[tf];
  return s != null && s <= TICKVOL_BANDS_MAX_BAR_SEC;
}

// バーのスロット [off, off+barSec) と帯群の重なり秒数（帯は互いに素・昇順を仮定しない）。
export function bandOverlapSec(off, barSec, bands) {
  if (!Array.isArray(bands) || !(barSec > 0)) {
    return 0;
  }
  const end = off + barSec;
  let sum = 0;
  for (const b of bands) {
    const l = Math.max(off, b.startOff);
    const r = Math.min(end, b.endOff);
    if (r > l) {
      sum += r - l;
    }
  }
  return sum;
}

// 各バーを塗るか（boolean 配列・candles と同順同長）。
//   セッション日始端は昇順走査で日ごとに 1 回だけ解決する（Intl 変換をバー数ぶん呼ばない）。
export function paintedBarFlags(candles, bands, barSec) {
  const n = Array.isArray(candles) ? candles.length : 0;
  const out = new Array(n).fill(false);
  if (!n || !Array.isArray(bands) || !bands.length || !(barSec > 0)) {
    return out;
  }
  let dayStart = null;
  let dayEnd = null;
  for (let i = 0; i < n; i++) {
    const t = candles[i] && candles[i].time;
    if (!Number.isFinite(t)) {
      continue;
    }
    if (dayStart == null || t < dayStart || t >= dayEnd) {
      dayStart = sessionDayStart(t);
      dayEnd = nextSessionDayStart(t);
    }
    out[i] = bandOverlapSec(t - dayStart, barSec, bands) * 2 >= barSec;
  }
  return out;
}

// 連続する true を 1 本の帯（バー添字の閉区間）へまとめる。
export function mergeRuns(flags) {
  const runs = [];
  for (let i = 0; i < flags.length; i++) {
    if (!flags[i]) {
      continue;
    }
    if (runs.length && runs[runs.length - 1].to === i - 1) {
      runs[runs.length - 1].to = i;
    } else {
      runs.push({ from: i, to: i });
    }
  }
  return runs;
}

// 帯 → 描画レンジ（各帯の左端バー time / 右端バー time）。プリミティブはこれを x へ写像する。
//   バーが 1 本も該当しなければ空配列（＝塗らない）。
export function bandRangesForCandles(candles, bands, barSec) {
  return mergeRuns(paintedBarFlags(candles, bands, barSec)).map((r) => ({
    from: candles[r.from].time,
    to: candles[r.to].time,
  }));
}
