// timeframes（adapter/front/timeframes.js）— 表示する 8 時間足の**唯一の並び**。
//
// 設計入力: 設計書 §3.4（時間足 ↔ テンプレートの紐付けの 8 本）／§5.1（第 2 表の列 = 表示
//   時間足 8 列）／§4.7（第 1 表の時間足欄）。domain 側の唯一定義は
//   dashboard_ui/domain/horizon.py の `TIMEFRAME_ORDER` であり、本モジュールはその表示側の対応物。
//
// なぜ 1 か所に置くか: 束を組む側（template_binding_reader）と第 2 表の列を出す側
//   （oscillator_sheet_view）が別々に同じ 8 本を持つと、片方だけ足したときに列と束がずれる。
//   ずれても表は表示され続けるので、出力の検査では落ちない（MEMORY: no-hand-duplication-single-source）。

/** 表示する時間足（短い順）。domain の TIMEFRAME_ORDER と同じ集合・同じ順。 */
export const DASHBOARD_TIMEFRAMES = Object.freeze(['1m', '5m', '15m', '1h', '4h', '1D', '1W', '1M']);

/** チャート一覧のローソク再取得周期（ms）＝各時間足の 1 バーの長さ。
 *
 *  これは**取得の刻み**であって足の暦定義ではない（1W/1M は 7 日・30 日の固定近似。
 *  境界の正確さはサーバの再集計が持ち、ここがずれても「確定直後の再取得が最大この誤差ぶん
 *  遅れる／早まる」だけで表示内容は壊れない）。水準・現在値は /reach_sheet 側が毎秒運ぶため、
 *  ローソクを足の確定より速く取り直しても新しい確定足は増えない＝取り直しは浪費になる。
 *  その浪費の不在を candle_poller の計算量テストが固定する。 */
export const TIMEFRAME_REFRESH_MS = Object.freeze({
  '1m': 60_000,
  '5m': 300_000,
  '15m': 900_000,
  '1h': 3_600_000,
  '4h': 14_400_000,
  '1D': 86_400_000,
  '1W': 604_800_000,
  '1M': 2_592_000_000,
});
