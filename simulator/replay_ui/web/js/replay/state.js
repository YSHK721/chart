// replay/state.js — 再生状態機械の純ロジック（DOM/lwc/fetch/timer 非依存）。
//
// 参照実装＝プロト web/js/replay.js。bar/generation/animGen/pausedForm/followOn/autoFrame の
//   状態遷移の「判定」だけを抽出（値の算出）。副作用（chart/DOM/timer）は View 側に残す。
//   分岐・境界・数式を 1つも足さず/削らず。

export const RIGHT_MARGIN = 6;   // 最新足の右に置く余白（バー数）。（replay.js: RIGHT_MARGIN）
export const FOLLOW_BARS = 150;  // 直近窓追従モードの表示本数。（replay.js: FOLLOW_BARS）

// bar を [0, length-1] にクランプ。（replay.js: render() の bar 算出）
export function clampBar(target, length) {
  return Math.max(0, Math.min(length - 1, target));
}

// candles 全体から time >= target の最小 index を返す（二分探索・ceil 規約）。（replay.js: idxForTime()）
//   ※timeline_player.test.js の floor 規約とは異なる。再生位置=present−期間分の算出は replay.js 準拠。
export function idxForTime(candles, target) {
  let lo = 0, hi = candles.length - 1;
  while (lo < hi) { const m = (lo + hi) >> 1; if (candles[m].time < target) lo = m + 1; else hi = m; }
  return lo;
}

// 可視論理レンジ [bar-幅, bar+RIGHT_MARGIN]。幅=followOn?FOLLOW_BARS:activePeriodBars、
//   null=全期間（左端0）。（replay.js: applyView()）
export function visibleRange({ bar, followOn, activePeriodBars }) {
  const width = followOn ? FOLLOW_BARS : activePeriodBars;
  const from = (width == null) ? 0 : Math.max(0, bar - width);
  const to = bar + RIGHT_MARGIN;
  return { from, to };
}

// 表示だけを左端/右端へスクロールする論理レンジ（現在ズーム幅を維持）。（replay.js: scrollViewTo()）
export function scrollRange({ edge, currentRange, bar }) {
  const width = currentRange.to - currentRange.from;
  if (edge === 'left') return { from: 0, to: width };
  const to = bar + RIGHT_MARGIN;
  return { from: to - width, to };
}

// 期間プリセット選択＝replayStart(present−期間分) と activePeriodBars(可視窓の幅)。（replay.js: renderPresets onclick）
export function presetSelection({ candles, secs }) {
  const present = candles.length - 1;
  const presentTime = candles.length ? candles[present].time : 0;
  const replayStart = (secs == null) ? 0 : idxForTime(candles, presentTime - secs);
  const activePeriodBars = (secs == null) ? null : (present - replayStart);
  return { replayStart, activePeriodBars };
}

// 減光境界の time（replayStart>0 かつ candle 有 → その time、他は null=減光なし）。（replay.js: boundaryTimeValue()）
export function boundaryTimeValue({ replayStart, candles }) {
  return (replayStart > 0 && candles[replayStart]) ? candles[replayStart].time : null;
}

// 時間足で縮退するモード集合（1m は 足＝1分＝m1 1本 で ohlc_1min/every_tick が縮退）。（replay.js: syncModeOptions()）
export function degenerateModes(tf) {
  return (tf === '1m') ? new Set(['ohlc_1min', 'every_tick']) : new Set();
}

// 停止足の続き再開判定（pausedForm.time が現在足 time と一致するときのみ再開）。（replay.js: playLoop resume）
export function resumeDecision(pausedForm, currentCandle) {
  return !!(pausedForm && currentCandle && pausedForm.time === currentCandle.time);
}

// 後発レンダで破棄すべきか（render の generation 採否）。（replay.js: if (g !== generation) return）
export function isStale(g, generation) {
  return g !== generation;
}

// 実行中の足内形成が新形成に置換されたか（animGen supersede）。（replay.js: superseded()）
export function isSuperseded(myGen, animGen) {
  return myGen !== animGen;
}
