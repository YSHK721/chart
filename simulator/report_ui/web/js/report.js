// サマリー(Report) タブ（試作 index.html:849-921 buildReport 準拠・パリティ点 8）。
// 区間別 report dict を REPORT_GROUPS 章立てでテーブル描画する（章＝.rcard2 / 行＝項目|値）。
//
// 純ロジック（DOM 非依存・node:test 被覆）:
//   fmtReportVal（数値整形）/ reportRowClass（負値=neg・Net/Gross Profit=pos）/
//   reportRowsModel（章立て＋「その他」章のモデル化）。

import { REPORT_GROUPS, LABELS_JA } from "./glossary.js";

// 数値表示整形（試作 fmtVal）。整数→桁区切り / 桁過多小数→3桁 / ％・括弧・時刻・識別子はそのまま。
export function fmtReportVal(v) {
  if (v === null || v === undefined) return "";
  const s = String(v).trim(), t = s.replace(/[\s ]/g, "");
  if (/^-?\d+(\.0+)?$/.test(t)) return (+t).toLocaleString("ja-JP");
  if (/^-?\d+\.\d+$/.test(t)) {
    const dp = t.split(".")[1].length;
    return (+t).toLocaleString("ja-JP", { minimumFractionDigits: Math.min(dp, 3), maximumFractionDigits: 3 });
  }
  return s;
}

// 値クラス（試作 rtr）。負値=neg / Net|Gross Profit の正値=pos / それ以外=空。
export function reportRowClass(key, v) {
  const t = String(v).replace(/[\s ]/g, "");
  if (/^-[\d.]/.test(t)) return "neg";
  if (/Net Profit|Gross Profit/.test(key) && /^[\d.]/.test(t)) return "pos";
  return "";
}

// 章立て Report モデル（試作 buildReport の章×行を純化）。
//   返り値: [{ title, rows:[{key, labelJa, raw, disp, cls}] }]。
//   該当キーのある章のみ。どの章にも属さない report キーは末尾「その他」章へ（試作準拠）。
export function reportRowsModel(report) {
  const r = { ...(report || {}) };
  const used = new Set();
  const mkRow = (k) => {
    used.add(k);
    return { key: k, labelJa: LABELS_JA[k], raw: r[k], disp: fmtReportVal(r[k]), cls: reportRowClass(k, r[k]) };
  };
  const groups = [];
  for (const [title, keys] of REPORT_GROUPS) {
    const present = keys.filter((k) => k in r);
    if (present.length) groups.push({ title, rows: present.map(mkRow) });
  }
  const rest = Object.keys(r).filter((k) => !used.has(k));
  if (rest.length) groups.push({ title: "その他", rows: rest.map(mkRow) });
  return groups;
}

// --- DOM（buildReport・e2e 被覆） ----------------------------------------------

function _rtrHtml(row) {
  const kHtml = row.labelJa ? `${row.labelJa}<small>${row.key}</small>` : row.key;
  return `<tr><td class="k" data-gk="${row.key}">${kHtml}</td>` +
    `<td class="v ${row.cls}">${row.disp}</td></tr>`;
}

// サマリー(Report) タブを host(#reportGrid) へ描画する（区間別 report・selectSegment が呼ぶ）。
export function buildReport(host, report) {
  if (!host) return;
  const groups = reportRowsModel(report);
  host.innerHTML = groups.map((g) =>
    `<div class="rcard2"><table class="rtbl"><caption>${g.title}</caption><tbody>` +
    g.rows.map(_rtrHtml).join("") + "</tbody></table></div>"
  ).join("");
}
