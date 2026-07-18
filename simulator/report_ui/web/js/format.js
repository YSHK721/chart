// 整形ヘルパ（詳細設計 §11.1 format.js・F-1 最小サブセット）。

export function fmtMoney(v) {
  if (v === null || v === undefined || !isFinite(v)) return "—";
  return Math.round(v).toLocaleString("ja-JP");
}

// 桁区切り付き固定小数（ja-JP ロケール・null/inf 耐性）。compare の指標表示が消費する。
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

// UNIX 秒（UTC）→ "YYYY.MM.DD hh:mm:ss"（試作 fmtT 準拠・DOM 非依存・テスト容易）。
// 明細テーブル（点15/16 連動ラベル）と比較チャートの時刻表示で共用する。
export function fmtT(t) {
  if (t === null || t === undefined || !isFinite(t)) return "";
  const d = new Date(t * 1000);
  const p = (n) => String(n).padStart(2, "0");
  return `${d.getUTCFullYear()}.${p(d.getUTCMonth() + 1)}.${p(d.getUTCDate())} ` +
    `${p(d.getUTCHours())}:${p(d.getUTCMinutes())}:${p(d.getUTCSeconds())}`;
}
