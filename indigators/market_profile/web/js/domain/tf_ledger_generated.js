// tf_ledger_generated.js — 時間足台帳（**自動生成・手で編集しない**）。
//
// 生成元: marketdata/resample.py の TF_DESCRIPTORS ＋ marketdata/tf_meta.py の TF_BAR_SEC。
// 生成器: tools/gen_js_parity_golden.py（規則変更時に再実行する）。
//
// なぜ生成物なのか（ISSUE-254）: 台帳を JS 側にも書くと第 2 定義になり、派生属性
//   （floorable / calendar）が静かにずれる。実際 floorable の写しがずれて、ライブの
//   更新粒度が時間足で割れた（ISSUE-253: 1W/1M だけ tick 再生から脱落）。定義は Python
//   ただ 1 つとし、JS は生成された値を読むだけにする。陳腐化は parity 検定が落とす。
//
//   code      : 時間足コード（挿入順＝台帳の順序）
//   barSec    : 名目バー秒長（1W=7日・1M=30日。厳密境界はラベル規約が担う）
//   floorable : 単純 floor で期間始端を表せるか（1W/1M は false）
//   calendar  : セッション日（ブローカー暦日）で集計する上位足か
export const TF_LEDGER = Object.freeze([
  { code: '1m', barSec: 60, floorable: true, calendar: false },
  { code: '5m', barSec: 300, floorable: true, calendar: false },
  { code: '15m', barSec: 900, floorable: true, calendar: false },
  { code: '30m', barSec: 1800, floorable: true, calendar: false },
  { code: '1h', barSec: 3600, floorable: true, calendar: false },
  { code: '4h', barSec: 14400, floorable: true, calendar: false },
  { code: '1D', barSec: 86400, floorable: true, calendar: true },
  { code: '1W', barSec: 604800, floorable: false, calendar: true },
  { code: '1M', barSec: 2592000, floorable: false, calendar: true },
].map(Object.freeze));
