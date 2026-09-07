// timeframes（adapter/front/timeframes.js）— 表示時間足の front 側の入口（並び・周期は導出）。
//
// 設計入力: 設計書 §3.4（時間足 ↔ テンプレートの紐付けの 8 本）／§5.1（第 2 表の列 = 表示
//   時間足 8 列）／§4.7（第 1 表の時間足欄）。
//
// 並びの唯一源は dashboard_ui/domain/horizon.py の `TIMEFRAME_ORDER`（ISSUE-502 D-9）。
//   かつては同じ 8 本を本ファイルにも書いており、Python 側と突き合わせる検定が 1 本も無かった。
//   束を組む側（template_binding_reader）と第 2 表の列を出す側（oscillator_sheet_view）は
//   どちらもこの並びで動くので、Python 側だけに足すと列と束が静かにずれる（表は表示され続ける
//   ため出力の検査では原理的に落ちない。MEMORY: no-hand-duplication-single-source）。
//   いまは `js/domain/dashboard_timeframes_generated.js`（生成物）を読むだけにし、
//   生成物の陳腐化は dashboard_ui/tests/contract/test_front_timeframes_parity.py が落とす。
//
// バー周期（`TIMEFRAME_REFRESH_MS`）も同じ理由で**導出**する（ISSUE-502 後続・残件 1）。
//   かつては 8 本の `barSec × 1000` をここに手で書き写しており、時間足台帳
//   （`js/domain/tf_ledger_generated.js` ← marketdata/tf_meta.py の TF_BAR_SEC）と
//   突き合わせる検定が **1 本も無かった**。台帳の barSec だけを直すと写しは古い周期のまま残り、
//   ローソクは表示され続けたまま「確定より前に取り直す／確定しても取り直さない」に静かに
//   倒れる——応答も表示も正しく見えるので、出力を見る検査では原理的に落ちない
//   （ISSUE-253 の floorable の写しのずれと同型）。新しい台帳は作らず、**既にある 2 つの
//   生成物の積**（表示する足 × その足の barSec）としてここで組む。

import { DASHBOARD_TIMEFRAMES } from '../../domain/dashboard_timeframes_generated.js';
import { TF_LEDGER } from '../../domain/tf_ledger_generated.js';

/** 表示する時間足（短い順）。domain の TIMEFRAME_ORDER の射影（生成物）をそのまま配る。 */
export { DASHBOARD_TIMEFRAMES };

/**
 * 表示する足 × 時間足台帳の `barSec` → 「足 → バー周期 ms」。
 *
 * 台帳は表示しない足（30m）も持つので、**表示する足で絞る**のがこの関数の仕事である。
 * 台帳に無い足を渡されたら黙って `undefined` を配らず落とす: 周期が引けない 1 本を
 * 素通しすると candle_poller はその足だけ毎 tick 発行へ倒れる（無言の縮退・応答も表示も
 * 正しいままなので出力の検査では落ちない）。結線より前のここで落とす。
 *
 * 入力を引数に取るのは、この畳み込み規則そのものを検定が動かせるようにするためである
 * （台帳と表示足はどちらも生成物なので、モジュール定数のままでは食い違いの側を作れない）。
 *
 * @param {readonly string[]} codes  表示する時間足（短い順）
 * @param {readonly {code: string, barSec: number}[]} ledger 時間足台帳
 * @returns {Object<string, number>} 時間足 → バー周期 ms（凍結済み）
 */
export function refreshMsOf(codes, ledger) {
  const barSecByCode = new Map(ledger.map((entry) => [entry.code, entry.barSec]));
  return Object.freeze(Object.fromEntries(
    codes.map((code) => {
      const barSec = barSecByCode.get(code);
      if (!Number.isFinite(barSec) || barSec <= 0) {
        throw new TypeError(
          `timeframes: 時間足 ${code} が時間足台帳（tf_ledger_generated.js）にありません`,
        );
      }
      return [code, barSec * 1000];
    }),
  ));
}

/** チャート一覧のローソク再取得周期（ms）＝各時間足の 1 バーの長さ。
 *
 *  値は手で書かず、時間足台帳の `barSec` を表示する足で絞って導出する（写しを作らない）。
 *
 *  これは**取得の刻み**であって足の暦定義ではない（1W/1M は 7 日・30 日の固定近似。
 *  境界の正確さはサーバの再集計が持ち、ここがずれても「確定直後の再取得が最大この誤差ぶん
 *  遅れる／早まる」だけで表示内容は壊れない）。水準・現在値は /reach_sheet 側が毎秒運ぶため、
 *  ローソクを足の確定より速く取り直しても新しい確定足は増えない＝取り直しは浪費になる。
 *  その浪費の不在を candle_poller の計算量テストが固定する。 */
export const TIMEFRAME_REFRESH_MS = refreshMsOf(DASHBOARD_TIMEFRAMES, TF_LEDGER);
