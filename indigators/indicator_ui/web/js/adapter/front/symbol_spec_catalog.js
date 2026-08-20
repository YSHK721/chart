// symbol_spec_catalog.js — datasetRef → 銘柄仕様（呼び値・表示桁）の引き当て（ISSUE-368 スライス S-5）。
//
// 設計入力: .doc/POSITION_SIZING_CHART_INTEGRATION_DESIGN.md「追補: 工程 2」E-2 / E-3。
//   呼び値の**定義**は Python 台帳（`marketdata/symbol_spec.py` ＋ `marketdata/dataset_registry.py`）
//   ただ 1 つで、JS はその生成物 `domain/symbol_spec_generated.js` を**読むだけ**である
//   （HTTP route を作らない＝供給元が 1 つ・起動時 1 回の定数。route 化は無音フォールバックと
//    file:// 起動不能を新設する）。本モジュールはその生成物の 2 段
//   （ref→銘柄 ／ 銘柄→{tick,digits}）を 1 回の引き当てに畳むだけの薄い変換で、値を 1 つも持たない。
//
// 責務（SRP）: **引き当てるだけ**。丸めない（丸めは `domain/price_quantize.js` の 1 か所）・
//   既定値を持たない・例外を投げない。front 配下で銘柄仕様を解決するのはここ 1 か所に限る
//   （import 元が 1 ファイルであることは position_sizing_symbol_spec_wiring.test.js が固定する）。
//
// フェイルセーフ（設計「フェイルセーフ」節）: 未知 ref・spec 不在は **null** を返す。
//   `catalog_client.js:47-50` 型の「失敗したら静的既定へ無音フォールバック」は採らない
//   （ISSUE-278 #8 の事故形。別銘柄の刻みで丸めた価格は、誤りだと気づけないまま計算へ入る）。
//   呼び側は null を受けて**機能を落とし理由を出す**（値を落とさない）。

import { DATASET_SYMBOLS, SYMBOL_SPECS } from '../../domain/symbol_spec_generated.js';

/**
 * datasetRef から銘柄仕様を引く。
 *
 * @param {string} datasetRef データセット参照（'jp225_tick' 等）。
 * @returns {{symbol:string, tick:number, digits:number}|null}
 *   解決できないときは null（未知 ref・銘柄に spec が無い・ref が文字列でない）。
 *   返すのは**毎回新しいオブジェクト**（呼び側の書き換えが台帳や次の引き当てへ波及しない）。
 */
export function lookupSymbolSpec(datasetRef) {
  if (typeof datasetRef !== 'string' || !Object.hasOwn(DATASET_SYMBOLS, datasetRef)) {
    // 継承プロパティ（'toString' 等）を素の添字参照で拾わない。拾うと tick=undefined のまま
    //   「解決できた」と誤判定し、量子化が無音で素通しになる。
    return null;
  }
  const symbol = DATASET_SYMBOLS[datasetRef];
  if (!Object.hasOwn(SYMBOL_SPECS, symbol)) {
    return null;
  }
  const spec = SYMBOL_SPECS[symbol];
  return { symbol, tick: spec.tick, digits: spec.digits };
}
