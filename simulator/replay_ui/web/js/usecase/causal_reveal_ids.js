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
// 検証実測（1m 実データ jp225_tick・窓 1500 本・バー 200〜1499 を 87 本刻みで 15 標本・全系列）:
//   tickvol → 61,107 比較点 max_dev = 0・欠落 0（tickvol / q10 / q90 / evq_med_hi /
//   evq_ext_hi / gpd_hi）。ISSUE-302（2026-08-09）。
//
// 未検証の指標は登録しない（fail-closed＝従来どおりバーごとその場計算）。追加時は上記と
// 同一の実測（tests 側スクリプト）で乖離 0 を確認してから登録すること。
export const CAUSAL_REVEAL_IDS = new Set([
  'btlm_trail',
  'btlm_trail_marod',
  'ma_marod',
  'moving_averages',
  // tickvol（ISSUE-302）: 未登録だったため、足送りのたびに全窓 compute を 1 本だけ発行し、
  //   それが再生の臨界経路（render の full 再計算）で待たれていた（実測 706ms/足＝境界停止の実体）。
  //   値は上記ゲートを乖離 0 で通過する（因果ローリング窓＋当該バー除外の水準＝窓長不変）。
  'tickvol',
]);
