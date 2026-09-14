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
import { usableTick } from '../../domain/price_quantize.js';

/**
 * datasetRef から銘柄仕様を引く。
 *
 * **この関数が返す spec は「そのまま使える」ことを保証する**（呼び側が tick を検算しなくてよい）。
 *   使えない刻みを持つ台帳は「解決できた」と言わずに null を返す＝壊れた値が front を流れ始める
 *   起点を作らない。この保証があるので下流（共有配線・協働子）は `symbolSpec` の真偽だけを見る。
 *
 * @param {string} datasetRef データセット参照（'jp225_tick' 等）。
 * @returns {{symbol:string, tick:number, digits:number}|null}
 *   解決できないときは null（ref が文字列でない・未知 ref・銘柄に spec が無い・刻みが使えない）。
 *   返すのは**毎回新しいオブジェクト**（呼び側の書き換えが台帳や次の引き当てへ波及しない）。
 */
export function lookupSymbolSpec(datasetRef) {
  // 1 段目。`typeof` の検査が**この段の実効部分**である（実測 2026-08-20・工程 4）:
  //   これを外すと ref の暗黙の文字列化で `['jp225']` や `{toString(){return 'jp225'}}` が
  //   JP225 として引き当たる（実測 3 例が漏れた）。文字列でないものは ref ではない。
  //   一方 `Object.hasOwn(DATASET_SYMBOLS, ...)` の方は、**現時点の台帳では**単独の実効性を持たない:
  //   `Object.prototype` の全 12 プロパティ名を ref として与えても、それらが返す値（関数・
  //   `Object.prototype` 自身）は 2 段目の `Object.hasOwn(SYMBOL_SPECS, symbol)` が漏れなく塞ぐ
  //   （実測: 漏れ 0 件）。それでも残すのは、この保証が**2 段目の実装に依存**しているためである
  //   （2 段目を Map 化・構造変更した瞬間に 1 段目の穴が無言で開く）。各段が自分の添字参照を
  //   自分で守る形を保つ。
  if (typeof datasetRef !== 'string' || !Object.hasOwn(DATASET_SYMBOLS, datasetRef)) {
    return null;
  }
  const symbol = DATASET_SYMBOLS[datasetRef];
  if (!Object.hasOwn(SYMBOL_SPECS, symbol)) {
    return null;
  }
  const spec = SYMBOL_SPECS[symbol];
  // 3 段目: 刻みが量子化に使えるか。判定の定義は持たない（`domain/price_quantize.js` の
  //   `usableTick` が唯一源）。生成物が壊れていれば pytest（marketdata/tests）が先に赤くなるが、
  //   front 側でも「解決できた」と名乗らないことで、壊れた値が下流へ入る経路自体を閉じる。
  if (usableTick(spec.tick) === null) {
    return null;
  }
  return { symbol, tick: spec.tick, digits: spec.digits };
}
