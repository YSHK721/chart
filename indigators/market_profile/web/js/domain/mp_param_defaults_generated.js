// mp_param_defaults_generated.js — MP パラメータ既定値（**自動生成・手で編集しない**）。
//
// 生成元: market_profile_api/compute/market_profile.py の VA_PCT_DEFAULT。
// 生成器: tools/gen_js_parity_golden.py（既定変更時に再実行する）。
//
// なぜ生成物なのか（ISSUE-260）: バリューエリア比率という 1 つの業務パラメータの
//   決定権が UI・controller・compute・front domain の 4 面に分散し、`/market_profile` の
//   非増分 refresh 以外は UI をどう操作しても 0.70 のままだった（＝効かないツマミ）。
//   定義は Python ただ 1 つとし、JS は生成された値を読むだけにする。実効値（要求ごとの
//   解決結果）はサーバ応答（/market_profile_forming の vaPct）に従う。
export const VA_PCT_DEFAULT = 0.7;
