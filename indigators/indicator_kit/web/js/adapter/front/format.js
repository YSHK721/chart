// 読み取り欄・ウォーターマーク向けの値整形ユーティリティ（adapter/front 共有）。
//   chart_renderer.js（機能③）と crosshair_readout_view.js で重複していた整形を一元化する。
//
// **価格と指標値は別の関数**（ISSUE-368 A-3・実 UI 実測 2026-08-20）:
//   価格（現在値・バー情報の OHLC）の表示桁は銘柄仕様 `digits` が決めるが、指標の値は決めない
//   （下段ペインには価格でない系列がある＝RSI・ma_marod。価格の桁を強制すると誤りになる）。
//   両者が `fmtValue` を共有していたため、価格軸だけ整数になり現在値は `65,721.051` のまま
//   残った。`fmtValue`＝指標値・`fmtPrice`＝価格 に分ける。

// 丸め＋桁区切りの規則そのものは価格書式の単一ソースが持つ（第 2 実装を作らない）。
//   `hasPriceDigits` は「台帳が桁を解決できたか」の判定で、解決できないときの落とし先だけが
//   面ごとに違う（線に添える価格＝参照実装どおり整数／本モジュール＝従来の `fmtValue`）。
import { priceOnLine, hasPriceDigits } from './price_format.js';

// epoch 秒（lightweight-charts の UTCTimestamp）を ISO 風の日時文字列 'YYYY-MM-DD HH:MM' へ。
//   business-day オブジェクト等は防御的に String 化する。
//   単一情報源: 読み取り欄（crosshair_readout_view）と足情報コピー（bar_info_text）が同じ表記を使う
//   ＝コピーした文字列と画面表示が食い違わない。
export function fmtTime(time) {
  if (time === null || time === undefined) {
    return '';
  }
  if (typeof time === 'number' && Number.isFinite(time)) {
    return new Date(time * 1000).toISOString().replace('T', ' ').slice(0, 16);
  }
  return String(time);
}

// 実時刻（epoch ミリ秒）を 'YYYY-MM-DD HH:MM:SS' へ。足の時刻（fmtTime）とは別物なので別名にする:
//   足は「どの期間の値か」（分精度・チャートの時間軸そのもの）、こちらは「いつ操作したか」という
//   瞬間（秒精度）。コピーの控えに操作時刻を残すために使う。基準は足の表記と同じ UTC で揃える
//   （同じ文面に UTC と現地時刻が混在すると、どちらの基準か読めなくなる）。
export function fmtInstant(epochMs) {
  if (!Number.isFinite(epochMs)) {
    return '';
  }
  return new Date(epochMs).toISOString().replace('T', ' ').slice(0, 19);
}

// 指標値の簡易整形。非有限（null/undefined/NaN/Infinity）は空文字。
export function fmtValue(v) {
  if (v === null || v === undefined || !Number.isFinite(v)) {
    return '';
  }
  return Number(v).toLocaleString(undefined, { maximumFractionDigits: 3 });
}

/**
 * 価格の整形（現在値・バー情報の OHLC）。表示桁は**銘柄仕様の `digits`**（ISSUE-368 A-3）。
 *
 * 権威は Python 台帳 `marketdata/symbol_spec.py` ただ 1 つで、front は解決結果を配られて
 * 渡すだけである（`digits` の既定値をここで決めない・台帳をここで引かない）。
 *
 * `digits` が解決できないとき（未指定・`null`・整数でない・負）は **従来（`fmtValue`）と
 * 完全に同一**へ落ちる。無音で誤った桁（例: 0 桁）に固定しない＝「決められない」を
 * 「整数だ」と偽らない（`chart_bootstrap` が価格軸で採る態度と同じ）。
 *
 * @param {number|null|undefined} v 価格。非有限は空文字（`fmtValue` と同じ契約）。
 * @param {number} [digits] 表示桁（台帳の `digits`）。
 * @returns {string} 例: 65721.051 → '65,721'（digits=0）・'65,721.05'（digits=2）
 */
export function fmtPrice(v, digits) {
  if (v === null || v === undefined || !Number.isFinite(v)) {
    return '';
  }
  return hasPriceDigits(digits) ? priceOnLine(v, digits) : fmtValue(v);
}
