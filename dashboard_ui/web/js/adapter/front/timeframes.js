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
