// forming_plan.js — 足内一括計算（ISSUE-232）の純ロジック。DOM/fetch/chart に依存しない。
//
// 目的: 再生中「ローソクはティック毎に動くのに、指標はサーバ往復（実測 ~100ms）を待って
//   遅れて追いつく」問題を、**バー開始前に足内の各時点の指標値を一括計算しておく**ことで
//   解消する。描画時は計算済み値を同期スライスするだけ＝ローソクと同一同期ブロックで動く。
//
// 本モジュールが持つのは「どの時点を計算するか（サンプリング）」と「その時点の形成中バー
//   OHLC をどう作るか」の 2 点だけ。HTTP は forming_seq_client、駆動は replay.js が担う。

// 足内の計算対象インデックス＝**ローソクが動く全点**（昇順・末尾 n-1 を含む）。
//
// ISSUE-233（間引きの廃止・ユーザー承認 2026-08-01）:
//   かつては 1 バーあたり 32 点の上限（MAX_FORMING_STEPS）で等間隔サンプリングへ縮退させて
//   いた。根拠は「1 ステップ 6.6ms〜170ms なので全ティック計算は現実的でない」だったが、
//   その所要は latest 計算が末尾 1 点のために窓全体を再計算していたことに由来する。真因を
//   除去した結果、実測構成 7 指標の 1 ステップ合計は約 425ms → 約 1.56ms になり、前提が
//   消滅した（1h・1分OHLC の 201 点で 0.31 秒 / 1 足 1.21 秒）。
//
//   上限を残す（あるいは値を上げる）のは「粒度の上限を人手で決める」応急処置であり、指標を
//   足すほど粒度が黙って落ちる構造を温存する。よって上限を持たず、**指標の更新回数は常に
//   ローソクの更新回数と一致する**（点間でローソクだけが動く区間を作らない）。
export function sampleIndices(n) {
  if (!Number.isFinite(n) || n <= 0) {
    return [];
  }
  return Array.from({ length: Math.floor(n) }, (_, i) => i);
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
