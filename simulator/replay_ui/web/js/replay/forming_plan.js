// forming_plan.js — 足内一括計算（ISSUE-232）の純ロジック。DOM/fetch/chart に依存しない。
//
// 目的: 再生中「ローソクはティック毎に動くのに、指標はサーバ往復（実測 ~100ms）を待って
//   遅れて追いつく」問題を、**バー開始前に足内の各時点の指標値を一括計算しておく**ことで
//   解消する。描画時は計算済み値を同期スライスするだけ＝ローソクと同一同期ブロックで動く。
//
// 本モジュールが持つのは「どの時点を計算するか（サンプリング）」と「その時点の形成中バー
//   OHLC をどう作るか」の 2 点だけ。HTTP は forming_seq_client、駆動は replay.js が担う。

// 1 バーあたりの最大計算ステップ数。ティック数がこれを超えるモード（real_ticks の数千〜数万）
//   では等間隔サンプリングへ縮退する（全ティック計算は現実的でない＝実測 1 ステップ 6.6ms〜170ms）。
//   サンプル点では常にローソクと同時に動く（遅延ゼロ）。点間はローソクのみが動く。
export const MAX_FORMING_STEPS = 32;

// 足内の計算対象インデックス（昇順ユニーク・末尾 n-1 を必ず含む）。
//   末尾を必ず含めるのは、バー確定値（settle）を追加の往復なしで確定させるため。
export function sampleIndices(n, maxSteps = MAX_FORMING_STEPS) {
  if (!Number.isFinite(n) || n <= 0) {
    return [];
  }
  const cap = Math.max(1, Math.floor(maxSteps));
  if (n <= cap) {
    return Array.from({ length: n }, (_, i) => i);
  }
  const out = [];
  for (let k = 0; k < cap; k++) {
    const idx = Math.round((k * (n - 1)) / (cap - 1));
    if (out.length === 0 || out[out.length - 1] !== idx) {
      out.push(idx);
    }
  }
  if (out[out.length - 1] !== n - 1) {
    out.push(n - 1);
  }
  return out;
}

// 各インデックス時点の形成中バー OHLC。replay.js の animateForming と同一の畳み方
//   （open=prices[0] 固定・high/low は流入ティックの累積極値・close=prices[i]）。
//   ここがずれると一括計算の値が実際の描画状態と食い違うため、同一規則であることが本質。
export function formingStatesAt(cd, prices, indices) {
  if (!cd || !Array.isArray(prices) || prices.length === 0) {
    return [];
  }
  const open = prices[0];
  let hi = open;
  let lo = open;
  const wanted = new Set(indices);
  const out = [];
  for (let i = 0; i < prices.length; i++) {
    const p = prices[i];
    if (p > hi) hi = p;
    if (p < lo) lo = p;
    if (wanted.has(i)) {
      out.push({ time: cd.time, open, high: hi, low: lo, close: p });
    }
  }
  return out;
}

// 計画の陳腐化判定に使う署名。指標構成・variant・params・時間足・窓が 1 つでも変われば
//   計算済み値は使えない（歯車での設定変更・指標の追加削除が再生中に起きうる）。
//   不一致なら計画を破棄して従来経路（その場計算）へ落とす＝誤った値を描かない。
export function planSignature({ targets, timeframe, limit, untilTime }) {
  const t = (targets || []).map((x) => [
    x.instanceId, x.indicatorId, x.variant, JSON.stringify(x.params || {}),
  ].join('|')).join(';');
  return [t, timeframe, limit, untilTime].join('#');
}
