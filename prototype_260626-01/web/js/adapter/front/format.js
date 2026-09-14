// 読み取り欄・ウォーターマーク向けの値整形ユーティリティ（adapter/front 共有）。
//   chart_renderer.js（機能③）と crosshair_readout_view.js で重複していた整形を一元化する。

// 指標値の簡易整形。非有限（null/undefined/NaN/Infinity）は空文字。
export function fmtValue(v) {
  if (v === null || v === undefined || !Number.isFinite(v)) {
    return '';
  }
  return Number(v).toLocaleString(undefined, { maximumFractionDigits: 3 });
}
