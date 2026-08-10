// bar_info_text.js — 足 1 本の情報をクリップボード用テキストへ整形する純関数（ユーザー指示 2026-08-09）。
//
// 設計入力: ユーザー指示（2026-08-09）「情報ウィンドの価格情報や指標の値を一括コピーできる仕様。
//   日付・時間・四本値・指標をクリップボードへコピー。ローソク足上で右クリック →『情報をコピーする』」。
//
// 責務（SRP）: 受け取ったデータ（ChartRenderer.barInfoAt の DTO ＋ instanceId→ラベル）を
//   文字列へ写すだけ。DOM・クリップボード・lightweight-charts に一切触れない（純関数）。
//
// 表記の規約: **情報ウィンドの表示と同じ材料・同じ並び・同じ整形**にする。
//   1 行目: 銘柄 ＋ 時間足（ユーザー指摘 2026-08-10「コピーした情報がどのチャートか分からない」）。
//           貼り付け先には画面が無く、値だけでは同じ数字が別チャート・別足と区別できない。
//   2 行目: 日時（日付 + 時間。読み取り欄と同じ fmtTime＝format.js が単一情報源）
//   3 行目: 四本値（O/H/L/C。読み取り欄と同じラベルと整形 fmtValue）
//   4 行目: 当日 MP（POC/VA。読み取り欄が出しているときと同じ条件＝DTO に載っているときだけ）
//   以降  : 指標 1 件 1 行（ペイン別凡例と同じ並び＝ペイン順・適用順）。
//           行は「凡例のラベル ＋ パラメータ」＋系列ごとに「系列名 値」。凡例は系列名を
//           ツールチップに、パラメータを設定ダイアログに隠すが、貼り付け先ではどちらも見えない
//           （同じ指標でも期間が違えば別の値＝パラメータ無しでは値の意味が定まらない）。
//   区切りはタブ 1 文字（表計算へ貼ると列に割れ、テキストへ貼っても読める）。
//
// 値が無い（その足に材料が無い）系列は**出さない**。凡例が空欄を作らないのと同じ規約で、
//   空欄や 0 を捏造しない。値が 1 つも無い指標は行ごと出さない。

import { fmtValue, fmtTime } from './format.js';

const SEP = '\t';

// 四本値セルの定義（読み取り欄 OHLC_CELLS と同じ順・同じラベル）。
const OHLC_CELLS = Object.freeze([
  { key: 'open', label: 'O' },
  { key: 'high', label: 'H' },
  { key: 'low', label: 'L' },
  { key: 'close', label: 'C' },
]);

/**
 * 指標の見出し（凡例ラベル ＋ 適用中パラメータ）を組み立てる。
 *
 * 例: `RSI (length=14, source=close)`。パラメータが無い指標はラベルだけ。
 * 値がスカラー（数値・文字列・真偽）でないものは載せない（`[object Object]` を書かない）。
 *
 * @param {object} row  IndicatorController.legendRows() の 1 行（{ label, params, instanceId }）。
 * @returns {string}    見出し文字列。
 */
export function indicatorHeading(row) {
  if (!row) {
    return '';
  }
  const label = row.label || row.instanceId || '';
  const params = row.params;
  if (!params || typeof params !== 'object') {
    return label;
  }
  const cells = [];
  for (const [k, v] of Object.entries(params)) {
    if (v === null || v === undefined || v === '') {
      continue;
    }
    const t = typeof v;
    if (t !== 'number' && t !== 'string' && t !== 'boolean') {
      continue;
    }
    cells.push(`${k}=${v}`);
  }
  return cells.length > 0 ? `${label} (${cells.join(', ')})` : label;
}

// 指標 1 件の行。値が 1 つも無ければ null（行を作らない）。
function indicatorLine(entry, labels) {
  const cells = [];
  for (const v of entry.values ?? []) {
    const text = fmtValue(v.value);
    if (!text) {
      continue;   // その足に材料が無い系列は出さない（凡例が空欄を作らないのと同じ規約）。
    }
    cells.push(v.name ? `${v.name} ${text}` : text);
  }
  if (cells.length === 0) {
    return null;
  }
  const label = (labels && typeof labels.get === 'function' ? labels.get(entry.instanceId) : null)
    || entry.instanceId;
  return [label, ...cells].join(SEP);
}

/**
 * 足 1 本の情報をコピー用テキストへ整形する。
 *
 * @param {object|null} info      ChartRenderer.barInfoAt の戻り
 *                                { time, ohlc, sessionMP, indicators:[{instanceId, values:[{name,value}]}] }。
 * @param {object} [context]      コピー時点のチャート文脈。
 * @param {string} [context.symbol]      銘柄名（app_chrome_view の CHART_SYMBOL＝ツールバーと同一）。
 * @param {string} [context.timeframe]   時間足コード（'1D' 等・台帳 TF_CODES の表記）。
 * @param {Map} [context.labels]         instanceId → 見出し（indicatorHeading 済み）。未指定は instanceId 表記。
 * @returns {string}              コピーする文字列。材料が何も無ければ空文字（＝呼び出し側はコピーしない）。
 */
export function formatBarInfoText(info, { symbol = null, timeframe = null, labels = null } = {}) {
  if (!info) {
    return '';
  }
  const lines = [];
  // どのチャートの値か（銘柄・時間足）。片方しか無ければ有る方だけを書く（空欄を並べない）。
  const head = [symbol, timeframe].filter((s) => typeof s === 'string' && s.length > 0);
  if (head.length > 0) {
    lines.push(head.join(SEP));
  }
  const time = fmtTime(info.time);
  if (time) {
    lines.push(time);
  }
  if (info.ohlc) {
    const cells = [];
    for (const { key, label } of OHLC_CELLS) {
      const text = fmtValue(info.ohlc[key]);
      if (text) {
        cells.push(`${label} ${text}`);
      }
    }
    if (cells.length > 0) {
      lines.push(cells.join(SEP));
    }
  }
  if (info.sessionMP) {
    const mp = info.sessionMP;
    const poc = fmtValue(mp.poc);
    const val = fmtValue(mp.val);
    const vah = fmtValue(mp.vah);
    const cells = [];
    if (poc) {
      cells.push(`POC ${poc}`);
    }
    if (val && vah) {
      cells.push(`VA ${val}–${vah}`);
    }
    if (cells.length > 0) {
      lines.push(cells.join(SEP));
    }
  }
  for (const entry of info.indicators ?? []) {
    const line = indicatorLine(entry, labels);
    if (line) {
      lines.push(line);
    }
  }
  return lines.join('\n');
}
