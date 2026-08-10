// routed_fetch.js — モード（live/replay）に応じて API 要求へ prefix を付ける fetch（葉モジュール）。
//
// なぜ在るか（ISSUE-362）:
//   統合層は当初、root 相対の API 要求（`/candles`・`/compute` 等）を **Service Worker** が
//   `/live/*` `/replay/*` へ書き換える設計だった（基本設計書 §2 / §3）。既存 core を無編集で
//   使うための仕掛けだが、この設計は**アプリの正しさをブラウザ機能の可用性に依存させる**。
//   SW が未登録・未制御・迂回（DevTools の "Bypass for network"）のいずれかになると、
//   全 API 要求が素パスのまま router へ届いて 404 になり、`"not found" is not valid JSON` の
//   連鎖で起動が死ぬ。実際に発生した（2026-08-10）。
//
//   ルーティングは**アプリ自身の責務**であって、環境が肩代わりしてくれる保証は無い。よって
//   prefix 付与をフロントの fetch 注入点へ移す。`bootstrap` は `fetch` を引数で受け
//   （`composition_root_front.js`）、配下のクライアントは全てその 1 つを使う（`this._fetch`）。
//   ここへ本モジュールを注入すれば、SW の有無に関わらず要求は必ず正しい core へ届く。
//
// SW との関係（共存・二重付与しない）:
//   書き換え規則は SW と同一の純関数 `rewritePath` を共有する（規則の単一源）。`rewritePath` は
//   **冪等**（`/live/` `/replay/` 配下は不変）なので、SW が生きている環境では
//   「front が付与 → SW は素通し」となり結果は一致する。SW を残すか外すかに関わらず正しい。
//
// 依存: `./sw_rewrite.js`（純関数）のみ。DOM/SW/グローバル fetch に非依存（注入で受ける）。

import { rewritePath } from './sw_rewrite.js';

// 同一オリジンの絶対 URL を root 相対（pathname + search + hash）へ落とす。
//   別オリジンは null（＝書き換え対象外）。
function toSameOriginPath(rawUrl, origin) {
  if (typeof rawUrl !== 'string' || rawUrl === '') {
    return null;
  }
  if (rawUrl.startsWith('/')) {
    return rawUrl;                       // 既に root 相対。
  }
  if (!/^[a-z][a-z0-9+.-]*:\/\//i.test(rawUrl)) {
    return null;                         // 相対パス（./x・x）は対象外＝不変。
  }
  let parsed;
  try {
    parsed = new URL(rawUrl);
  } catch {
    return null;
  }
  if (origin && parsed.origin !== origin) {
    return null;                         // クロスオリジンは不変。
  }
  return parsed.pathname + parsed.search + parsed.hash;
}

/**
 * モード対応の fetch を作る。
 *
 * @param {object} deps
 * @param {Function} deps.baseFetch 実 fetch（`globalThis.fetch.bind(globalThis)` 等）。
 * @param {Function} deps.getMode 現在のモードを返す（'live' | 'replay'）。呼び出しごとに読む
 *   （live↔replay の切替後も同じ関数実体のまま正しい core へ回るようにするため）。
 * @param {string} [deps.origin] 同一オリジン判定の基準。既定は `location.origin`。
 * @returns {Function} `fetch(input, init)` 互換。
 */
export function createRoutedFetch({ baseFetch, getMode, origin } = {}) {
  if (typeof baseFetch !== 'function') {
    throw new TypeError('createRoutedFetch: baseFetch が必要です');
  }
  const resolveOrigin = () => (
    origin !== undefined ? origin
      : (typeof location !== 'undefined' && location ? location.origin : undefined)
  );
  const resolveMode = () => {
    const m = typeof getMode === 'function' ? getMode() : getMode;
    return m === 'replay' ? 'replay' : 'live';   // 未知値は live へ倒す（既定モード）。
  };

  return function routedFetch(input, init) {
    // Request オブジェクト: URL だけ差し替えた Request を作り直す（method/headers/body を保つ）。
    if (typeof Request !== 'undefined' && input instanceof Request) {
      const path = toSameOriginPath(input.url, resolveOrigin());
      const next = path === null ? null : rewritePath(resolveMode(), path);
      if (next === null || next === path) {
        return baseFetch(input, init);
      }
      return baseFetch(new Request(next, input), init);
    }
    const raw = (typeof URL !== 'undefined' && input instanceof URL) ? input.href : input;
    const path = toSameOriginPath(raw, resolveOrigin());
    if (path === null) {
      return baseFetch(input, init);               // 対象外＝素通し（挙動不変）。
    }
    const next = rewritePath(resolveMode(), path);
    return next === path ? baseFetch(input, init) : baseFetch(next, init);
  };
}
