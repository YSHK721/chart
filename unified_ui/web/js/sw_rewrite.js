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

// モード集合・URL prefix の単一ソース（基本設計書 §3.5.6 の表駆動化）。prefix の 2 値
//   ハードコードをここへ集約したので、第 4 モードの追加で本ファイルは変わらない。
import { MODE_PREFIXES, DEFAULT_MODE, prefixOf } from './mode_table.js';

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
  'available_days',
  // 取引密度帯（時刻帯の背景色）の帯定義。ライブ core・リプレイ core の双方が同一実装を持ち
  //   （リプレイは bridge でライブ側 controller を再利用）、同一入力で応答が byte 一致する。
  //   よってライブ専用ではなくアクティブモードの core へ回す（/candles と同じ扱い）。
  'tickvol_profile',
]);

// market_profile / market_profile_forming など market_profile 系はまとめて API 扱い。
function isApiSegment(segment) {
  return API_SEGMENTS.has(segment) || segment.startsWith('market_profile');
}

// ライブ core 専用セグメント（replay core=serve_replay 未実装＝/replay だと 404）。
//   応答は mode 非依存（完成期間の履歴プロファイル）ゆえアクティブモードに関わらず常にライブ core へ回す。
//   OCP 是正: 従来 rewritePath 本体にハードコードしていた ``segment === 'tf_period_profile'`` 特例を
//   本ルーティング表へ外出しし、本体は表参照で分岐する（ライブ専用 API 追加時に本体を改変しない）。
export const LIVE_ONLY_SEGMENTS = new Set([
  'tf_period_profile',
]);

/**
 * @param {string} mode アクティブモード（mode_table の全モード。未知値は既定モードの prefix へ）
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
  // 既に prefix 付き（表に載っている全モードの配下）は二重付与しない。判定は prefix 表の走査で行う
  //   （条件式に prefix を列挙すると、モードを増やすたびに本体を書き足すことになる＝OCP 違反）。
  for (const prefix of MODE_PREFIXES) {
    if (path === prefix || path.startsWith(prefix + '/')) {
      return path;
    }
  }
  // 第 1 セグメント（query/hash 手前）を取り出し、API のときだけ prefix を付与する。
  const segment = path.slice(1).split(/[/?#]/)[0];
  if (!isApiSegment(segment)) {
    return path;
  }
  // ライブ core 専用セグメント（LIVE_ONLY_SEGMENTS）はアクティブモードに関わらず常に /live へ回す。
  //   これが無いと「日別プロファイル」等がリプレイ中に 404（replay core 未実装）で描画されない。
  //   行き先の /live も表から引く（モード名の文字列を本体に書かない）。
  if (LIVE_ONLY_SEGMENTS.has(segment)) {
    return `${prefixOf(DEFAULT_MODE)}${path}`;
  }
  // prefix は表から引く（表に無ければ既定モードへ倒す＝全域性）。`` `/${mode}${path}` `` と
  //   **モード名をそのまま**前置すると、表に無い値（タイプミス・将来値・壊れた SW メッセージ）が
  //   `/nope/compute` というどの core にも存在しないパスを無言で作る。
  return `${prefixOf(mode)}${path}`;
}
