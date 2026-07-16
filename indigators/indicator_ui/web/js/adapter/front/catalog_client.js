// IndicatorCatalogClient（adapter/front/catalog_client.js）— IndicatorCatalogPort 実装。
//
// 設計入力: 内部設計書 §7.1.5（list_indicators / get）。
// A方式プロトタイプ: API 化せず usecase/catalog.js の静的レジストリをラップする（TBD-03 静的化）。
//
// ISSUE-092 ③: param 既定値の単一情報源は back（Python catalog_schema）。load() が GET /catalog で
//   サーバ由来スキーマを取得し、レジストリへ overlay して既定値を解決する。フェッチ失敗時は静的値
//   （catalog.js リテラル）へフォールバックし UI が従来どおり動く（後方互換・オフライン耐性）。

import { list, get, applyServerDefaults } from '../../usecase/catalog.js';

export class IndicatorCatalogClient {
  // IndicatorDef[] を返す（usecase は読み取り専用複製を返す）。
  listIndicators() {
    return list();
  }

  // id -> IndicatorDef | null。
  get(id) {
    return get(id);
  }

  // GET /catalog で param 既定値の単一情報源（back）を取得し、レジストリへ overlay する。
  //   成功時 true。フェッチ不能 / 例外 / 非 ok レスポンス / 非 ok payload では overlay せず false を
  //   返す（静的既定へフォールバック＝UI 従来どおり）。fetchImpl 省略時は globalThis.fetch を束縛。
  async load(fetchImpl) {
    const fetchFn = fetchImpl
      || (typeof globalThis !== 'undefined' && globalThis.fetch
        ? globalThis.fetch.bind(globalThis) : null);
    if (!fetchFn) {
      return false;
    }
    try {
      const resp = await fetchFn('/catalog');
      if (!resp || !resp.ok) {
        return false;
      }
      const payload = await resp.json();
      if (!payload || payload.ok !== true || !payload.catalog) {
        return false;
      }
      applyServerDefaults(payload.catalog);
      return true;
    } catch {
      // ネットワーク断・JSON 破損等 → 静的値フォールバック（UI 従来どおり）。
      return false;
    }
  }
}
