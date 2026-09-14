// dashboard_timeframes_generated.js — ダッシュボードの表示時間足（**自動生成・手で編集しない**）。
//
// 生成元: dashboard_ui/domain/horizon.py の TIMEFRAME_ORDER。
// 生成器: tools/gen_js_parity_golden.py（並びを変えたら再実行する）。
//
// なぜ生成物なのか（ISSUE-502 D-9）: 同じ 8 本を JS 側にも書くと第 2 定義になり、
//   片方だけ足したとき第 2 表の列（oscillator_sheet_view）と束（template_binding_reader）が
//   ずれる。ずれても表は表示され続けるので、出力の検査では原理的に落ちない
//   （ISSUE-253 / ISSUE-254 と同型の『静かなずれ』）。定義は Python ただ 1 つとし、
//   JS は生成された値を読むだけにする。陳腐化は
//   dashboard_ui/tests/contract/test_front_timeframes_parity.py が落とす。
//
//   並びは短い順。地平 3 段（短期 / 中期 / 長期）の切り出しもこの並びの添字で行う。
export const DASHBOARD_TIMEFRAMES = Object.freeze(['1m', '5m', '15m', '1h', '4h', '1D', '1W', '1M']);
