// IndicatorCatalogClient（adapter/front/catalog_client.js）— IndicatorCatalogPort 実装。
//
// 設計入力: 内部設計書 §7.1.5（list_indicators / get）。
// A方式プロトタイプ: API 化せず usecase/catalog.js の静的レジストリをラップする（TBD-03 静的化）。

import { list, get } from '../../usecase/catalog.js';

export class IndicatorCatalogClient {
  // IndicatorDef[] を返す（usecase は読み取り専用複製を返す）。
  listIndicators() {
    return list();
  }

  // id -> IndicatorDef | null。
  get(id) {
    return get(id);
  }
}
