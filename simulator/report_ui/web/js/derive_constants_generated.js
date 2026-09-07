// derive_constants_generated.js — front 表示定数（**自動生成・手で編集しない**）。
//
// 生成元: simulator/report_ui/usecase/derive.py の WEEK / HOLD_BUCKET_BOUNDS。
// 生成器: simulator/report_ui/tools/gen_report_js_constants.py（規則変更時に再実行する）。
//
// なぜ生成物なのか（ISSUE-502 D-2 / D-3）: 同じ事実を JS 側にも書くと第 2 定義になり、
//   ずれても例外が出ない。曜日順がずれれば front のフィルタが back の集計と別の trade を
//   選び、hold 境界がずれれば「保有時間別損益」の棒と抽出結果が食い違う——いずれも画面は
//   正常に見えたまま静かに壊れる（ISSUE-253 と同型）。定義は Python ただ 1 つとし、JS は
//   生成された値を読むだけにする。陳腐化は
//   simulator/report_ui/tests/unit/test_js_constants_single_source.py が落とす。
//
//   WEEKORDER          : wday インデックス規約（Mon=0..Sun=6・UTC 基準）
//   HOLD_BUCKET_BOUNDS : 保有時間バケット境界 [lo, hi) 半開区間とラベル
export const WEEKORDER = Object.freeze(["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]);

export const HOLD_BUCKET_BOUNDS = Object.freeze([
  { lo: 0, hi: 60, label: "<1m" },
  { lo: 60, hi: 120, label: "1-2m" },
  { lo: 120, hi: 300, label: "2-5m" },
  { lo: 300, hi: 600, label: "5-10m" },
  { lo: 600, hi: 1800, label: "10-30m" },
  { lo: 1800, hi: 3600, label: "30-60m" },
  { lo: 3600, hi: 1000000000, label: ">1h" },
].map(Object.freeze));
