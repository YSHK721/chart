// 読み取り欄・ウォーターマーク向けの値整形ユーティリティ（adapter/front 共有）。
//   chart_renderer.js（機能③）と crosshair_readout_view.js で重複していた整形を一元化する。

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
