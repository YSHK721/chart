// mp_capability_generated.js — MP ソース能力（**自動生成・手で編集しない**）。
//
// 生成元: market_profile_api/controller/tf_period_profile_controller.py の _ZP_TF_ALLOWED。
// 生成器: tools/gen_js_parity_golden.py（規則変更時に再実行する）。
//
// なぜ生成物なのか（ISSUE-264）: zp 対応 tf は台帳から導出できない『能力宣言』であり、
//   Python と JS の両方に手書きで存在していた。同期手段が無いため、ずれるとサーバは 400 を
//   返すのにフロントは選択可能なまま＝**無言の機能不全**になる（ISSUE-253 と同型）。
//   定義は Python ただ 1 つとし、JS は生成された値を読むだけにする。
export const ZP_SUPPORTED_TFS = Object.freeze(['15m', '30m', '1h', '4h', '1D', '1W', '1M']);
