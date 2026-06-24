// 整形ヘルパ（詳細設計 §11.1 format.js・F-1 最小サブセット）。

export function fmtMoney(v) {
  if (v === null || v === undefined || !isFinite(v)) return "—";
  return Math.round(v).toLocaleString("ja-JP");
}

export function cfmt(v, digits = 2) {
  // 非有限/null は "—"（試作 cfmt 準拠・null/inf 耐性）。
  if (v === null || v === undefined || !isFinite(v)) return "—";
  return Number(v).toFixed(digits);
}

// 桁区切り付き固定小数（ja-JP ロケール・null/inf 耐性）。compare の指標表示が消費する。
// cfmt（toFixed・区切りなし）とは別整形。比較タブ専用ヘルパの重複定義を集約する。
export function cfmtLocale(v, digits) {
  if (v === null || v === undefined || !isFinite(v)) return "—";
  return Number(v).toLocaleString("ja-JP",
    { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

export function signClass(v) {
  if (v === null || v === undefined || !isFinite(v)) return "";
  if (v > 0) return "pos";
  if (v < 0) return "neg";
  return "";
}
