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

export function signClass(v) {
  if (v === null || v === undefined || !isFinite(v)) return "";
  if (v > 0) return "pos";
  if (v < 0) return "neg";
  return "";
}
