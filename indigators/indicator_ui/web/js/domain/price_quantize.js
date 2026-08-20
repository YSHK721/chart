// price_quantize.js（domain）— 価格を呼び値の刻みへ量子化する純関数（ISSUE-368 スライス S-3）。
//
// 設計入力: .doc/POSITION_SIZING_CHART_INTEGRATION_DESIGN.md「追補: 工程 2」E-4 / S-3。
//   除去する原因 β＝「丸めの適用点が 7 経路に散っている」（本ブランチで既に取り残しが発生し
//   `adapter/front/price_format.js:7-11` に記録がある）。**量子化の式はこのファイルにしか置かない**。
//   ここが唯一の実装であることは tests/price_quantize.test.js の構造検定で担保する。
//
// 関門の位置: 呼び出し側は `domain/price_levels.js`（E-02）の構築・更新口。
//   「刻み上にない価格は PriceLevels に存在できない」を不変条件にすることで、resolver を通らない
//   水準線 drag 経路（`adapter/front/price_level_drag_controller.js:157-176`）も迂回できなくなる。
//
// 依存ゼロ（import 0 行・DOM／lightweight-charts／fetch を一切知らない）。

/**
 * 価格を刻み `tick` の最近傍の倍数へ丸める。
 *
 * 固定した規則:
 *   - `Math.round(price / tick) * tick`。ちょうど半分の境界は上側へ（Math.round と同じ）。
 *   - 積の浮動小数残差は `tick` の小数桁数で丸め戻す（`8568.900000000001` を下流へ流さない）。
 *     桁数は `tick` から導出する（spec の `digits` を第 2 の入力にすると、`tick` と食い違ったとき
 *     どちらが正かが決められなくなるため。`tick=1.0→0 桁`・`tick=0.1→1 桁`）。
 *   - **素通し条件**: `price` が `null` / `undefined` / 非有限（`NaN`・`±Infinity`）のとき、
 *     および `tick` が `null` / 未指定のときは、値を一切変えずに返す（後方互換の既定）。
 *     無音で 0 や NaN へ倒さない＝呼び出し側の既存の失敗検知を壊さない。
 *
 * @param {number|null|undefined} price 丸める価格。
 * @param {number|null|undefined} [tick] 呼び値の刻み（未指定・null なら素通し）。
 * @returns {number|null|undefined} 刻み上の価格。素通し条件に該当する入力はそのまま返す。
 */
export function quantize(price, tick) {
  if (price === null || price === undefined || !Number.isFinite(price)) {
    return price;
  }
  if (tick === null || tick === undefined) {
    return price;
  }
  return Number((Math.round(price / tick) * tick).toFixed(decimalsOf(tick)));
}

/**
 * `tick` の小数桁数（`1` → 0・`0.1` → 1・`0.25` → 2・`1e-7` → 7）。
 *
 * 指数表記を分けて扱う理由: JS は 1e-6 未満の数を指数表記で文字列化する（`String(1e-7) === '1e-7'`）。
 *   小数点の位置だけを見ると桁数 0 と誤り、`quantize(1.23456789, 1e-7)` が無音で `1` を返す
 *   （＝設計「フェイルセーフ」の禁じる無音の誤答）。仮数部の桁数から指数を差し引いて求める。
 */
function decimalsOf(tick) {
  const [mantissa, exponent] = String(tick).toLowerCase().split('e');
  const dot = mantissa.indexOf('.');
  const mantissaDecimals = dot < 0 ? 0 : mantissa.length - dot - 1;
  const shift = exponent === undefined ? 0 : Number(exponent);
  return Math.max(0, mantissaDecimals - shift);
}
