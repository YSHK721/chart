// causal_reveal_ids.js — 一括リビール（ISSUE-158 ②）の対象指標登録リスト（replay 専用・symlink しない）。
//
// 一括リビール＝再生開始時に全レンジを 1 回だけ計算し、再生中は計算済み系列を t 以下へ
// スライスして表示する（バーごとの HTTP/compute を省く）。
//
// 登録条件（実測ゲート・2026-07-23 検証）:
//   因果指標であり、リプレイの左端固定窓（limit=bar+1・candles[0] 起点＝replay.js の
//   `_recentBars = bar + 1` セマンティクス）において「全レンジ 1 回計算の各バー値」と
//   「バーごとその場計算の末尾値」が実データで完全一致（乖離 0）であること。
//   水平線（horizontal_line）ペイロードも窓長に対して不変であること。
//
// 検証実測（1m 実データ・窓 25 サンプル・全系列）:
//   btlm_trail / btlm_trail_marod / ma_marod / moving_averages → 444 比較点 max_dev = 0
//   （evq 4 系列・q5/q95・hlines 含む）。
//
// 未検証の指標は登録しない（fail-closed＝従来どおりバーごとその場計算）。追加時は上記と
// 同一の実測（tests 側スクリプト）で乖離 0 を確認してから登録すること。
export const CAUSAL_REVEAL_IDS = new Set([
  'btlm_trail',
  'btlm_trail_marod',
  'ma_marod',
  'moving_averages',
]);
