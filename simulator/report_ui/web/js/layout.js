// レイアウト状態機械（試作 index.html:323-373 準拠・パリティ点 5,6,10,11）。
// 責務（SRP）: チャート/下部の 3 状態レイアウト（normal/chart/detail）と高さ可変リサイザ
//   （rz0/rz1/rz2）を main から分離する。DOM 操作のみで report.json を消費しない。
//
// 純ロジック（DOM 非依存・node:test 被覆）: nextLayoutMode（最大化ボタンのトグル状態遷移）。
//   状態遷移: normal --maxChart--> chart / normal --maxDetail--> detail /
//             chart --maxChart--> normal / detail --maxDetail--> normal。

// 最大化トグルの次状態を返す（試作 wireMaximize の三項分岐を純化）。
//   btn="chart"|"detail"。現在 cur と一致するなら normal へ復元、違えば btn へ最大化。
export function nextLayoutMode(cur, btn) {
  return cur === btn ? "normal" : btn;
}

// --- DOM（layout state machine・e2e 被覆） --------------------------------------

let _mode = "normal";
let _savedCwPx = 0;
let _onResize = null; // レイアウト変更後に chart/cmpCharts を resize するコールバック注入

export function currentLayoutMode() { return _mode; }

// レイアウトを適用する（試作 applyLayout・点6 chart / 点10 detail / 点11 3 状態）。
//   normal→最大化の遷移時のみ chartWrap 高さ(px)を退避し、復元で保持する（%依存を避ける）。
export function applyLayout(mode) {
  const cw = document.getElementById("chartWrap");
  const bo = document.getElementById("bottom");
  const rz0 = document.getElementById("rz0");
  if (!cw || !bo) return;
  if (_mode === "normal" && mode !== "normal") {
    _savedCwPx = Math.round(cw.getBoundingClientRect().height);
  }
  _mode = mode;
  if (mode === "normal") {
    cw.style.display = ""; bo.style.display = ""; if (rz0) rz0.style.display = "";
    cw.style.flex = "0 0 " + _savedCwPx + "px"; bo.style.flex = "1 1 auto";
  } else if (mode === "chart") {
    cw.style.display = ""; cw.style.flex = "1 1 auto"; bo.style.display = "none"; if (rz0) rz0.style.display = "none";
  } else { // detail
    bo.style.display = ""; bo.style.flex = "1 1 auto"; cw.style.display = "none"; if (rz0) rz0.style.display = "none";
  }
  const mc = document.getElementById("maxChart"), md = document.getElementById("maxDetail");
  if (mc) { mc.classList.toggle("on", mode === "chart"); mc.textContent = mode === "chart" ? "↙ 復元" : "⛶ チャート最大化"; }
  if (md) { md.classList.toggle("on", mode === "detail"); md.textContent = mode === "detail" ? "↙ 復元" : "⛶ 明細最大化"; }
  // 明細最大化時のみグラフを 100% 充填（点10・試作 gfill）。
  const pg = document.getElementById("graphHost");
  if (pg) pg.classList.toggle("gfill", mode === "detail");
  setTimeout(() => { if (_onResize) _onResize(); }, 40);
}

// 下部（明細/タブ領域）の最大化トグル（正常⇄拡大）。タブのダブルクリックから呼ぶ。
//   現在 detail なら normal へ復元、それ以外（normal/chart）なら detail へ最大化する。
export function toggleDetailMax() {
  applyLayout(nextLayoutMode(_mode, "detail"));
}

// 最大化トグル（点6/点10）を結線する（試作 wireMaximize）。
export function wireMaximize(onResize) {
  _onResize = onResize || null;
  const mc = document.getElementById("maxChart"), md = document.getElementById("maxDetail");
  if (mc) mc.onclick = () => applyLayout(nextLayoutMode(_mode, "chart"));
  if (md) md.onclick = () => applyLayout(nextLayoutMode(_mode, "detail"));
}

// 隣接 2 要素の高さをドラッグで調整するリサイザ（試作 makeResizer・点5/点11）。
function _makeResizer(rz, prevEl, nextEl) {
  if (!rz || !prevEl || !nextEl) return;
  rz.addEventListener("mousedown", (e) => {
    e.preventDefault();
    const startY = e.clientY;
    const pH = prevEl.getBoundingClientRect().height;
    const nH = nextEl.getBoundingClientRect().height;
    rz.classList.add("drag");
    document.body.style.userSelect = "none";
    const move = (ev) => {
      const dy = ev.clientY - startY;
      prevEl.style.flex = "0 0 " + Math.max(40, pH + dy) + "px";
      nextEl.style.flex = "0 0 " + Math.max(40, nH - dy) + "px";
      if (_onResize) _onResize();
    };
    const up = () => {
      rz.classList.remove("drag");
      document.body.style.userSelect = "";
      document.removeEventListener("mousemove", move);
      document.removeEventListener("mouseup", up);
    };
    document.addEventListener("mousemove", move);
    document.addEventListener("mouseup", up);
  });
}

// 高さ可変リサイザ rz1/rz2/rz0 を結線する（試作 wireResizers・点5/点11）。
export function wireResizers(onResize) {
  if (onResize) _onResize = onResize;
  const $ = (id) => document.getElementById(id);
  _makeResizer($("rz1"), $("price-chart"), $("paneBal"));
  _makeResizer($("rz2"), $("paneBal"), $("paneDD"));
  _makeResizer($("rz0"), $("chartWrap"), $("bottom"));
}
