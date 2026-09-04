// 取引明細テーブル（詳細設計 §11.1 table.js / アーキ指針 §3）。
// 試作 prototype_260623-02 の取引明細 12 列に**完全準拠**で trades[] を一次ソースに描画する。
// 列ソートの比較は副作用のない純関数 compareTrades として切り出す（テスト容易化）。
// hover は mouseover delegation で linkage.setHover(id,'table') を呼ぶ（DOM 結線は main.js）。
// 時刻列(time)は fmtT 整形、Profit 列(pl)は td.pl.pos/neg 配色（緑=利益/赤=損失）。

import { fmtMoney, fmtT } from "./format.js";

// 列定義: [key, label, kind]。kind は描画/比較の型ヒント（id/time/num/side/txt/pl）。
// 試作 prototype_260623-02 index.html の COLS（# / Open Time / Order / Type / Vol / Price /
// S/L / T/P / Time(close) / Exit / State / Comment / Profit）と同順・同キー・同ラベルにする。
// 注: 試作の取引明細に Symbol 列は無い（銘柄はヘッダ meta-line に表示）。
export const COLS = [
  ["id", "#", "id"],
  ["entry_time", "Open Time", "time"],
  ["order", "Order", "num"],
  ["side", "Type", "side"],
  ["volume", "Vol", "txt"],
  ["entry_price", "Price", "num"],
  ["sl", "S / L", "txt"],
  ["tp", "T / P", "txt"],
  ["exit_time", "Time(close)", "time"],
  ["exit_price", "Exit", "num"],
  ["comment", "State / Comment", "txt"],
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

// trades[] を試作 12 列キーの行ビューへ射影する（試作はトレード生フィールドを直接描画）。
// 列キー（id/entry_time/order/side/volume/entry_price/sl/tp/exit_time/exit_price/comment/profit）を
// そのまま保持し、hover / marker の単一 id 空間（id=order=i+1）を維持する。
export function projectRow(t) {
  return {
    id: t.id,
    entry_time: t.entry_time,
    order: t.order,
    side: t.side,
    volume: t.volume,
    entry_price: t.entry_price,
    sl: t.sl,
    tp: t.tp,
    exit_time: t.exit_time,
    exit_price: t.exit_price,
    comment: t.comment,
    profit: t.profit,
  };
}

// 射影済み行のセル文字列（time は fmtT 整形・その他は素の文字列／欠落は空）。
function _cell(row, key, kind) {
  const v = row[key];
  if (kind === "time") return fmtT(v);
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

// 行クリック→選択ハイライト＋チャート移動の結線。hover と同じく一度だけ登録し、
// 最新の onFocus を module 参照で保持する（区間切替で再構築されても累積しない・冪等）。
// onFocus(id, entryTime): id=該当 trade（減光/強調の確定用）, entryTime=focusTime のズーム先。
let _focusCb = null;
let _clickWired = false;
function _wireRowClickFocus(hostTable, onFocus) {
  _focusCb = onFocus; // 直近の onFocus（focusTime は同一インスタンスのため全区間を賄える）
  if (_clickWired) return;
  _clickWired = true;
  hostTable.addEventListener("click", (e) => {
    const tr = e.target.closest("tr.tw");
    if (tr && _focusCb) _focusCb(+tr.dataset.id, +tr.dataset.t); // id ＋ entry_time
  });
}

// 明細テーブルを構築（ヘッダ＋初期行）し、linkage と結線する。
// 引数: hostTable=<table id=tradeTable>, segment（trades[]/meta.symbol）, linkage,
//        onFocus(optional)=行クリックで該当 trade の時刻へチャートを移動するコールバック（main 注入）。
export function buildTradeTable(hostTable, segment, linkage, onFocus) {
  const rows = (segment.trades || []).map((t) => projectRow(t)); // 試作 12 列へ射影
  const state = { sortKey: "id", sortDir: 1 }; // 試作既定は id 昇順

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
      tr.dataset.side = row.side;
      // activeFilter は applyFilter（ヒートマップ/抽出）で設定。非該当行を dim 表示する。
      const filter = linkage.activeFilter;
      if (filter && !filter.has(row.id)) tr.classList.add("dim");
      // クリック→チャート移動（focusTime）のため entry_time を行に保持する。
      tr.dataset.t = row.entry_time;
      // セルは textContent で描画（多層防御: 値経路からの HTML 注入を遮断）。
      for (const c of COLS) {
        const td = document.createElement("td");
        if (c[2] === "side") {
          td.className = "side";
          td.textContent = _cell(row, c[0], c[2]);
        } else if (c[2] === "pl") {
          // Profit 列: 試作準拠で損益配色（正=pos/緑・0以下=neg/赤）＋符号付き金額。
          const p = row[c[0]];
          td.className = "pl " + (p > 0 ? "pos" : "neg");
          td.textContent = _money(p);
        } else {
          // State / Comment 列は試作同様に左寄せ（cmt クラス）。
          if (c[0] === "comment") td.className = "cmt";
          td.textContent = _cell(row, c[0], c[2]);
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
