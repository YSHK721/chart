// api_segments.js — API エンドポイント第 1 セグメントの単一ソース（純データの表・葉モジュール）。
//
// なぜ要るか（SOLID 精査 2026-09-06 / .doc/solid_audit_20260906.md D-1）:
//   同じ「API エンドポイント集合」が sw_rewrite.js（リライト対象の判定）と op_log.js（通信記録の
//   対象判定）に独立記述され、既に乖離していた——op_log 側に tf_period_profile が無く当該通信が
//   操作ログに残らず、実在しない compute_seq を持っていた。二重定義は必ず取り残しを生むため、
//   集合の定義を本表 1 枚へ集約し、消費者は判定関数・正規表現を**導出**する（手書きの写しを禁止）。
//
// 表の意味:
//   - API_SEGMENTS         : 完全一致で API と判定する第 1 パスセグメント（/compute 等）。
//   - API_SEGMENT_PREFIXES : 前方一致で API と判定する族（market_profile / market_profile_forming 等、
//                            market_profile 系はまとめて API 扱い——旧 sw_rewrite の startsWith 規則）。
//   セグメント名は [a-z0-9_] のみ（正規表現へ無エスケープで埋め込むための不変条件。テストで固定）。
//
// 依存: なし（mode_table.js と同格の純データ表。app のモジュールを import しない）。

export const API_SEGMENTS = Object.freeze([
  'compute',
  'candles',
  'live_ticks',
  'forming_bar',
  'tf_period_profile',
  'catalog',
  'intraday',
  'available_days',
  // 取引密度帯（時刻帯の背景色）。ライブ・リプレイ双方の core が同一実装を持つ（/candles と同じ扱い）。
  'tickvol_profile',
]);

export const API_SEGMENT_PREFIXES = Object.freeze([
  'market_profile',
]);

// 第 1 パスセグメントが API か（sw_rewrite のリライト対象判定・完全一致 or 前方一致）。
export function isApiSegment(segment) {
  return API_SEGMENTS.includes(segment)
    || API_SEGMENT_PREFIXES.some((p) => segment.startsWith(p));
}

// URL 文字列に API セグメントが現れるか判定する正規表現（op_log の通信記録対象判定）。
//   `/<セグメント>` の直後がセグメント終端（/ ? # か末尾）であること＝ sw_rewrite の
//   第 1 セグメント抽出 `split(/[/?#]/)` と同じ終端規則。表から導出するため写しが存在しない。
export function apiUrlPattern() {
  const exact = API_SEGMENTS.join('|');
  const family = API_SEGMENT_PREFIXES.map((p) => `${p}[a-z0-9_]*`).join('|');
  return new RegExp(`/(?:${exact}|${family})(?:[/?#]|$)`);
}
