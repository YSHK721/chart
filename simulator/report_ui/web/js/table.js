// 取引明細テーブル（詳細設計 §11.1 table.js / アーキ指針 §3）。
// SPEC §2.2.2 の 11 列 ＋ Profit 列を trades[] を一次ソースに描画する（Symbol 列は meta.symbol 射影）。
// 列ソートの比較は副作用のない純関数 compareTrades として切り出す（テスト容易化）。
// hover は mouseover delegation で linkage.setHover(id,'table') を呼ぶ（DOM 結線は main.js）。
// Profit 列は試作 prototype_260623-02 準拠で kind="pl"・td.pl.pos/neg 配色（緑=利益/赤=損失）。

import { fmtMoney } from "./format.js";

// 列定義: [key, label, kind]。kind は描画/比較の型ヒント（time/num/txt/side/pl）。
// trades[] のフィールド名に射影（open_time=entry_time, exit_time=close time, state/comment=comment 写像）。
// 末尾 Profit は試作の取引明細 12 列目（kind="pl"・損益配色）に一致させる。
export const COLS = [
  ["open_time", "Open Time", "time"],
  ["order", "Order", "num"],
  ["symbol", "Symbol", "txt"],
  ["type", "Type", "side"],
  ["volume", "Volume", "txt"],
  ["price", "Price", "num"],
  ["sl", "S / L", "txt"],
  ["tp", "T / P", "txt"],
  ["exit_time", "Time", "time"],
  ["state", "State", "txt"],
  ["comment", "Comment", "txt"],
  ["profit", "Profit", "pl"],
];

// Profit セル表示（試作 fmtMoney 準拠: 正は "+" 前置・桁区切り・null/inf は "—"）。
function _money(v) {
  return (v > 0 ? "+" : "") + fmtMoney(v);
}

// 列ソート比較（純関数）。定義済み値の型で数値/文字列を判定し、欠落は型相応のゼロ値で扱う。
export function compareTrades(a, b, key, dir) {
  let x = a[key];
  let y = b[key];
  const defined = x !== undefined && x !== null ? x : y;
  if (typeof defined === "string") {
    x = x == null ? "" : String(x);
    y = y == null ? "" : String(y);
    return dir * x.localeCompare(y);
  }
  x = x == null ? 0 : x;
  y = y == null ? 0 : y;
  return dir * (x - y);
}

// trades[] 16キーを SPEC 11列キーの行ビューへ射影（詳細設計 §4.4・Symbol は meta.symbol）。
// id を併せて保持し、hover / marker の単一 id 空間（id=order=i+1）を維持する。
export function projectRow(t, symbol) {
  return {
    id: t.id,
    open_time: t.entry_time,
    order: t.order,
    symbol: symbol,
    type: t.side,
    volume: t.volume,
    price: t.entry_price,
    sl: t.sl,
    tp: t.tp,
    exit_time: t.exit_time,
    state: t.comment,
    comment: t.comment,
    profit: t.profit,
  };
}

// 射影済み行のセル文字列。
function _cell(row, key) {
  const v = row[key];
  return v === null || v === undefined ? "" : String(v);
}

// hover の双方向結線（入力: mouseover→setHover / 出力: subscribe→hl 付与）を linkage に対し
// 一度だけ登録する。区間切替で buildTradeTable が再呼び出しされても結線が累積しない（リーク防止）。
// 副作用は「その時点で結線済みの hostTable」に適用する。hostTable / linkage は区間切替で不変の
// 同一インスタンスのため、初回登録のクロージャ参照で全区間を賄える（冪等）。
let _hoverWiredTable = null;
let _hoverWired = false; // 結線済みフラグ（linkage の状態機械を汚染しないよう table 側で保持）
function _wireHoverHighlight(hostTable, linkage) {
  _hoverWiredTable = hostTable; // 直近に構築したテーブルを副作用対象とする
  if (_hoverWired) return;
  _hoverWired = true;
  // 入力: mouseover delegation → linkage.setHover(id, 'table')。
  hostTable.addEventListener("mouseover", (e) => {
    const tr = e.target.closest("tr.tw");
    if (tr) linkage.setHover(+tr.dataset.id, "table");
  });
  // 出力: hover 状態購読 → 該当行に hl クラス付与（DOM 副作用は購読者側）。
  linkage.subscribe((id) => {
    const host = _hoverWiredTable;
    if (!host) return;
    host.querySelectorAll("tr.tw.hl").forEach((r) => r.classList.remove("hl"));
    if (id != null) {
      const row = host.querySelector(`tr.tw[data-id="${id}"]`);
      if (row) row.classList.add("hl");
    }
  });
}

// 行クリック→チャート移動（試作 focusTime）の結線。hover と同じく一度だけ登録し、
// 最新の onFocus を module 参照で保持する（区間切替で再構築されても累積しない・冪等）。
let _focusCb = null;
let _clickWired = false;
function _wireRowClickFocus(hostTable, onFocus) {
  _focusCb = onFocus; // 直近の onFocus（focusTime は同一インスタンスのため全区間を賄える）
  if (_clickWired) return;
  _clickWired = true;
  hostTable.addEventListener("click", (e) => {
    const tr = e.target.closest("tr.tw");
    if (tr && _focusCb) _focusCb(+tr.dataset.t); // tr.dataset.t = 該当 trade の entry_time
  });
}

// 明細テーブルを構築（ヘッダ＋初期行）し、linkage と結線する。
// 引数: hostTable=<table id=tradeTable>, segment（trades[]/meta.symbol）, linkage,
//        onFocus(optional)=行クリックで該当 trade の時刻へチャートを移動するコールバック（main 注入）。
export function buildTradeTable(hostTable, segment, linkage, onFocus) {
  const symbol = (segment.meta && segment.meta.symbol) || "";
  const rows = (segment.trades || []).map((t) => projectRow(t, symbol)); // SPEC 11列へ射影
  const state = { sortKey: "order", sortDir: 1 };

  const thead = hostTable.querySelector("thead");
  // ヘッダは [data-k=列キー] + ラベルのみ。ソート方向の視覚インジケータ（矢印）は本フェーズの
  // スコープ外（後段）のため、未配線（常に空・CSS 未定義・JS 未更新）の span は出力しない。
  thead.innerHTML =
    "<tr>" +
    COLS.map((c) => `<th data-k="${c[0]}">${c[1]}</th>`).join("") +
    "</tr>";
  thead.querySelectorAll("th").forEach((th) => {
    th.addEventListener("click", () => {
      const k = th.dataset.k;
      state.sortDir = state.sortKey === k ? -state.sortDir : 1;
      state.sortKey = k;
      renderRows();
    });
  });

  function renderRows() {
    const arr = [...rows].sort((a, b) =>
      compareTrades(a, b, state.sortKey, state.sortDir));
    const tb = hostTable.querySelector("tbody");
    const frag = document.createDocumentFragment();
    for (const row of arr) {
      const tr = document.createElement("tr");
      tr.className = "tw";
      tr.dataset.id = row.id;
      tr.dataset.side = row.type;
      // F-3用・F-2では未配線: activeFilter は applyFilter(F-3 ヒートマップ/抽出)で設定される
      // スキャフォールド。F-2 では常に null のため dim は付かない（CSS は定義済・未発火）。
      const filter = linkage.activeFilter;
      if (filter && !filter.has(row.id)) tr.classList.add("dim");
      // クリック→チャート移動（focusTime）のため entry_time を行に保持する。
      tr.dataset.t = row.open_time;
      // セルは textContent で描画（多層防御: 値経路からの HTML 注入を遮断）。
      for (const c of COLS) {
        const td = document.createElement("td");
        if (c[2] === "side") {
          td.className = "side";
          td.textContent = _cell(row, c[0]);
        } else if (c[2] === "pl") {
          // Profit 列: 試作準拠で損益配色（正=pos/緑・0以下=neg/赤）＋符号付き金額。
          const p = row[c[0]];
          td.className = "pl " + (p > 0 ? "pos" : "neg");
          td.textContent = _money(p);
        } else {
          td.textContent = _cell(row, c[0]);
        }
        tr.appendChild(td);
      }
      frag.appendChild(tr);
    }
    tb.innerHTML = "";
    tb.appendChild(frag);
    // ソートで tbody を全再生成した後も hover 強調を維持する。状態の真実源は linkage。
    const hid = linkage.hoverTradeId;
    if (hid != null) {
      const hrow = hostTable.querySelector(`tr.tw[data-id="${hid}"]`);
      if (hrow) hrow.classList.add("hl");
    }
  }

  // hover の双方向結線（mouseover→setHover / subscribe→hl）を結線（重複登録防止は委譲）。
  _wireHoverHighlight(hostTable, linkage);
  // 行クリック→チャート移動（focusTime）を結線（重複登録防止は委譲）。
  _wireRowClickFocus(hostTable, onFocus);

  renderRows();
}
