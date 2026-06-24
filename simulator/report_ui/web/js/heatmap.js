// 最小単位ヒートマップ（SPEC#3・詳細設計 §11.1 heatmap.js / アーキ指針 §2・§3・§4）。
// agg.heat の wday×hour セル（最小単位項目）を「HTMLテーブル要素＋ヒートマップ配色の単一描画」で
// 表示し（テーブル形式表示とヒートマップ視覚化を両立）、セルクリックで該当 trade を抽出して
// linkage.applyFilter(ids,label) へ渡す（chart のマーカー抽出・table の dim と連動）。
//
// 連動方式（アーキ指針 §3）: heatmap→linkage→subscribeFilter 購読者（chart/table）。
// chart/table への直接 import は作らず、購読登録は main.js が行う（コールバック注入）。
//
// R-2（最重要・アーキ指針 §4）: front の (wday,hour) 判定を back（derive.heat_cells の
// weekday() Mon=0・UTC）と単一規約に固定する。wday インデックス = (getUTCDay()+6)%7（Mon=0）＋
// UTC 基準。back（fromtimestamp(ts,utc).weekday()）と同一 trade を選ぶ（境界: 日曜・hour0・hour23）。

import { aggOf } from "./data.js";

// wday インデックス規約（Mon=0..Sun=6）。back derive.WEEK と一致させる。
export const WEEKORDER = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

// entry_time(秒・UTC) を {wday, hour} へ写す（R-2 規約: (getUTCDay()+6)%7・UTC hour）。
export function wdayHourOf(entryTime) {
  const d = new Date(entryTime * 1000);
  return { wday: WEEKORDER[(d.getUTCDay() + 6) % 7], hour: d.getUTCHours() };
}

// trade の entry が (wday,hour) セルに属するか（R-2 判定の単位述語）。
export function tradeMatchesCell(t, wday, hour) {
  const wh = wdayHourOf(t.entry_time);
  return wh.wday === wday && wh.hour === hour;
}

// (wday,hour) セルに該当する trade id の Set を返す（linkage.applyFilter 入力）。
export function collectCellIds(trades, wday, hour) {
  const ids = new Set();
  for (const t of trades || []) {
    if (tradeMatchesCell(t, wday, hour)) ids.add(t.id);
  }
  return ids;
}

// id Set に該当する trade のうち entry_time 最小の 1 件を返す（onFocus ズーム対象・無ければ null）。
// collectCellIds と対をなす R-2 純関数（DOM 非依存・テスト容易）。
export function firstTradeInCell(trades, ids) {
  let first = null;
  for (const t of trades || []) {
    if (ids.has(t.id) && (first === null || t.entry_time < first.entry_time)) first = t;
  }
  return first;
}

// --- 配色（試作 index.html:651-717 準拠・同色相の濃淡を不透明で補間し高彩度を保つ） --------
const TEAL = [38, 166, 154], RED = [239, 83, 80], BLUE = [59, 130, 246];
const EMPTY_BG = "#161b22";        // 値なしセルの背景（無彩色・空セルと同系）
const DIM_FLOOR = 0.4;             // 強度 0 時の暗端（base 色をこの比率まで暗くする）
const RATIO_FLOOR = 0.25;          // 補間下限（強度 0 でも最低この明度を確保）
const TONE_LIFT = 0.03;            // グラデ上端の白寄せ量（立体感付与）
const WR_PIVOT = 50;               // 勝率配色の中立点（%）。これより上=緑/下=赤
const RGB_MAX = 255;

const lerpA = (lo, hi, t) => hi.map((h, i) => Math.round(lo[i] + (h - lo[i]) * t));

// base 色を強度 r(0..1) で濃淡補間し、上端を僅かに白寄せした縦グラデ文字列を返す。
function hue(base, r) {
  const cl = Math.max(0, Math.min(1, r));
  const c = lerpA(base.map((x) => Math.round(x * DIM_FLOOR)), base, RATIO_FLOOR + (1 - RATIO_FLOOR) * cl);
  const top = c.map((v) => Math.round(v + (RGB_MAX - v) * TONE_LIFT));
  return `linear-gradient(180deg,rgb(${top.join(",")}),rgb(${c.join(",")}))`;
}
const divCol = (v, mx) => (v == null ? EMPTY_BG : hue(v >= 0 ? TEAL : RED, Math.abs(v) / (mx || 1)));
const seqCol = (v, mx) => (v == null || !(mx > 0) ? EMPTY_BG : hue(BLUE, v / mx));
const wrCol = (v) => (v == null ? EMPTY_BG : hue(v >= WR_PIVOT ? TEAL : RED, Math.abs(v - WR_PIVOT) / WR_PIVOT));

const _round = (v) => Math.round(v).toLocaleString("ja-JP");

// heat cells（[{wday,hour,profit,count,wins}]）を (wday|hour)→cell の索引へ。
function _toMap(cells) {
  const m = {};
  for (const c of cells || []) m[c.wday + "|" + c.hour] = c;
  return m;
}

// 5 ビュー定義（損益/IS-OOS差/取引回数/勝率/平均損益）。各 valFn は cell→{v,disp,title}|null。
function _viewDefs(cur, isM, oosM, mx) {
  return [
    {
      title: "損益（曜日×時間・選択区間）", hint: "緑=利益 / 赤=損失", gg: "P&L heatmap",
      valFn: (k) => { const c = cur[k]; return c ? { v: c.profit, disp: _round(c.profit), title: `${_round(c.profit)} JPY / ${c.count}件` } : null; },
      colFn: (v) => divCol(v, mx.mxP),
    },
    {
      title: "IS vs OOS 損益差（OOS−IS）", hint: "赤=OOSで悪化 / 緑=改善", gg: "IS-OOS diff",
      valFn: (k) => { const iv = isM[k] ? isM[k].profit : 0, ov = oosM[k] ? oosM[k].profit : 0; if (!isM[k] && !oosM[k]) return null; const d = ov - iv; return { v: d, disp: (d > 0 ? "+" : "") + Math.round(d), title: `IS ${_round(iv)} → OOS ${_round(ov)}` }; },
      colFn: (v) => divCol(v, mx.mxD),
    },
    {
      title: "取引回数（曜日×時間・選択区間）", hint: "濃青=多い", gg: "Trade count",
      valFn: (k) => { const c = cur[k]; return c ? { v: c.count, disp: String(c.count), title: `${c.count}件` } : null; },
      colFn: (v) => seqCol(v, mx.mxC),
    },
    {
      title: "勝率（曜日×時間・選択区間）", hint: "緑=高勝率 / 赤=低勝率（50%基準）", gg: "Win rate",
      valFn: (k) => { const c = cur[k]; if (!c || !c.count) return null; const wr = c.wins / c.count * 100; return { v: wr, disp: Math.round(wr) + "%", title: `勝率 ${wr.toFixed(1)}%（${c.wins}/${c.count}）` }; },
      colFn: wrCol,
    },
    {
      title: "平均損益/取引（曜日×時間・選択区間）", hint: "1取引あたりの優位性", gg: "Avg P&L/trade",
      valFn: (k) => { const c = cur[k]; if (!c || !c.count) return null; const a = c.profit / c.count; return { v: a, disp: String(Math.round(a)), title: `平均 ${a.toFixed(1)} JPY/件（${c.count}件）` }; },
      colFn: (v) => divCol(v, mx.mxA),
    },
  ];
}

function _maxes(cur, isM, oosM) {
  let mxP = 0, mxC = 0, mxA = 0, mxD = 0;
  for (const k in cur) {
    mxP = Math.max(mxP, Math.abs(cur[k].profit));
    mxC = Math.max(mxC, cur[k].count);
    if (cur[k].count) mxA = Math.max(mxA, Math.abs(cur[k].profit / cur[k].count));
  }
  for (const k in isM) { const d = (oosM[k] ? oosM[k].profit : 0) - (isM[k] ? isM[k].profit : 0); mxD = Math.max(mxD, Math.abs(d)); }
  for (const k in oosM) { if (!isM[k]) mxD = Math.max(mxD, Math.abs(oosM[k].profit)); }
  return { mxP, mxC, mxA, mxD };
}

// 1 ビューの HTMLテーブル文字列（テーブル形式表示＋ヒートマップ配色の単一描画）。
function _viewHtml(def) {
  let h = `<div class="heatBlock"><div class="heatTitle" data-gg="${def.gg}">${def.title}<small>${def.hint}</small></div><table class="heat"><tr><th>曜日\\時</th>`;
  for (let hr = 0; hr < 24; hr++) h += `<th>${hr}</th>`;
  h += "</tr>";
  for (const w of WEEKORDER) {
    h += `<tr><th>${w}</th>`;
    for (let hr = 0; hr < 24; hr++) {
      const r = def.valFn(w + "|" + hr);
      if (r && r.v != null) {
        h += `<td class="cell" data-w="${w}" data-h="${hr}" title="${w} ${hr}:00  ${r.title}" style="background:${def.colFn(r.v)}">${r.disp}</td>`;
      } else {
        h += '<td class="empty">·</td>';
      }
    }
    h += "</tr>";
  }
  return h + "</table></div>";
}

// ヒートマップ（5 ビュー）を host へ描画し、セルクリック→linkage.applyFilter を結線する。
// 引数: host=<div id=heatHost>, data=DATA（segments/trades/agg.heat）, seg=選択区間, linkage,
//        onFocus(optional)=最初の該当 trade へズームするコールバック（main.js が注入）。
export function buildHeatmap(host, data, seg, linkage, onFocus) {
  if (!host) return;
  const curCells = aggOf(data, seg).heat || [];
  const cur = _toMap(curCells);
  const isM = _toMap(aggOf(data, "is").heat);
  const oosM = _toMap(aggOf(data, "oos").heat);
  const mx = _maxes(cur, isM, oosM);

  host.innerHTML = _viewDefs(cur, isM, oosM, mx).map(_viewHtml).join("");

  // 選択区間の trades（フィルタ抽出対象）。R-2 規約で back セルと同一 trade を選ぶ。
  const trades = data.segments[seg].trades || [];

  host.querySelectorAll("td.cell").forEach((td) => {
    td.addEventListener("click", () => {
      host.querySelectorAll("td.cell.sel").forEach((x) => x.classList.remove("sel"));
      td.classList.add("sel");
      const w = td.dataset.w, hr = +td.dataset.h;
      const ids = collectCellIds(trades, w, hr); // R-2: back 分類と同一 trade
      linkage.applyFilter(ids, `${w} ${hr}:00`); // chart 抽出・table dim を購読者で駆動
      if (onFocus) {
        const first = firstTradeInCell(trades, ids); // 該当 trade のうち最古を focus 対象に
        if (first) onFocus(first.entry_time);
      }
    });
  });
}
