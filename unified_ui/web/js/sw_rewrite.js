// Service Worker のパスリライト純関数（スケルトン — Red フェーズ）。
//
// 契約（基本設計書 §2 / §3）:
//   既存フロントの root 相対 fetch（`/compute` 等）を、アクティブモードに応じて
//   `/live/*` or `/replay/*` へ前置き（prefix 付与）する純関数。
//   - API パス（`/compute`・`/candles`・`/intraday` 等）のみ前置きする
//   - 既に `/live/` `/replay/` prefix 付きのパスは不変（二重付与しない）
//   - 非 API 静的資産（`/`・`/index.html`・`/js/...`・`/vendor/...`・`/sw.js`）は不変
//   - クロスオリジン絶対 URL は不変（同一オリジンのみ対象）
//
// DOM / fetch / self に非依存の純関数として実装される（テスト容易性）。
//
// Red フェーズ: シグネチャのみ。本体は未実装で throw する。

// API エンドポイントの第 1 パスセグメント（基本設計書 §2 の振り分け表）。
// これらのみモード prefix を付与する。静的資産（js/vendor/index.html/sw.js 等）は不変。
const API_SEGMENTS = new Set([
  'compute',
  'candles',
  'live_ticks',
  'forming_bar',
  'tf_period_profile',
  'catalog',
  'intraday',
]);

// market_profile / market_profile_forming など market_profile 系はまとめて API 扱い。
function isApiSegment(segment) {
  return API_SEGMENTS.has(segment) || segment.startsWith('market_profile');
}

/**
 * @param {'live'|'replay'} mode アクティブモード
 * @param {string} path ブラウザが出すリクエストパス（root 相対 or 絶対 URL）
 * @returns {string} リライト後のパス
 */
export function rewritePath(mode, path) {
  // 空文字・非文字列は不変（異常系防御）。
  if (typeof path !== 'string' || path === '') {
    return path;
  }
  // クロスオリジン絶対 URL（scheme://…）は不変（同一オリジンのみ対象）。
  if (/^[a-z][a-z0-9+.-]*:\/\//i.test(path)) {
    return path;
  }
  // root 相対でなければ不変。
  if (!path.startsWith('/')) {
    return path;
  }
  // 既に prefix 付き（/live・/replay 配下）は二重付与しない。
  if (
    path === '/live' || path.startsWith('/live/')
    || path === '/replay' || path.startsWith('/replay/')
  ) {
    return path;
  }
  // 第 1 セグメント（query/hash 手前）を取り出し、API のときだけ prefix を付与する。
  const segment = path.slice(1).split(/[/?#]/)[0];
  if (!isApiSegment(segment)) {
    return path;
  }
  return `/${mode}${path}`;
}
