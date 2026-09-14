// 日付選択のカレンダー（View・依頼者参照デザイン 2026-09-06 準拠）。
//
// なぜ自前か: ネイティブ `<input type="date">` のポップアップはブラウザ内部描画であり
// CSS が届かない（`color-scheme` で明暗を切り替えられるだけ・実測 2026-09-06）。参照
// デザイン（ダーク面・月送りヘッダ・月曜始まりの曜日行・隣接月の淡色・選択日の強調・
// Cancel / Choose Date の 2 段確定）には自前描画が必要。ライブラリは追加しない。
//
// 責務（SRP）: カレンダーの DOM とひと月分の格子の組み立て・月送り・選択・確定通知だけ。
//   どの `.ini` キーの値かは知らない（呼び出し側が openFor で値と受け口を渡す）。
//   検証もしない——`YYYY.MM.DD` の正否はサーバの `_strict_date`（R10）が単一ソース。
//
// 参照デザインどおりの要素（勝手に足さない・削らない）:
//   ヘッダ（前月◀ / "April 2021" / 翌月▶）・曜日行（Mo〜Su・月曜始まり）・6×7 の格子
//   （隣接月は淡色）・選択日の強調。
//   参照画像の日の下の点（マーカー）は対応する概念が本アプリに無いため対象外。
//   フッタ（Cancel / Choose Date）は依頼者裁定（2026-09-06）で撤去: 日付クリックで
//   **即時確定して閉じる**（確定ボタン不要）。即時確定では Cancel も機能を失う（取り消す
//   選択が存在しない）ため同時に撤去。カレンダーの外をクリックしたら閉じる。
//
// fake DOM 前提: querySelector は使わず、要素参照を JS 側で保持する。

/** 月名（参照デザインの表記＝英語）。 */
const MONTH_NAMES = [
  "January", "February", "March", "April", "May", "June",
  "July", "August", "September", "October", "November", "December",
];
/** 曜日行（参照デザインの表記＝月曜始まり）。 */
const WEEKDAY_LABELS = ["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"];
/** 格子の枡数（6 週 × 7 曜・参照デザインの固定形）。月の長短で増減しない。 */
const GRID_CELLS = 42;

/** `.ini` の日付トークン `YYYY.MM.DD` → {y, m, d}（不正・空なら null）。 */
function parseToken(token) {
  const matched = /^([0-9]{4})\.([0-9]{2})\.([0-9]{2})$/.exec(String(token || ""));
  if (!matched) return null;
  return { y: Number(matched[1]), m: Number(matched[2]) - 1, d: Number(matched[3]) };
}

const pad2 = (n) => String(n).padStart(2, "0");

/** {y, m, d} → `.ini` の日付トークン `YYYY.MM.DD`。 */
function formatToken({ y, m, d }) {
  return `${y}.${pad2(m + 1)}.${pad2(d)}`;
}

/** 表示月 (y, m) の 6×7 格子（月曜始まり・隣接月込み）。純関数（Date 算術は正規化に使う）。 */
function gridOf(y, m) {
  const lead = (new Date(y, m, 1).getDay() + 6) % 7;   // 月曜始まりの先頭余白
  const cells = [];
  for (let i = 0; i < GRID_CELLS; i += 1) {
    const date = new Date(y, m, 1 - lead + i);
    cells.push({
      y: date.getFullYear(), m: date.getMonth(), d: date.getDate(),
      out: date.getMonth() !== m,                       // 隣接月（淡色表示）
    });
  }
  return cells;
}

export function createSimDatePickerView({ doc, today } = {}) {
  const now = today || (() => new Date());
  let pop = null;          // 開いている popup（null = 閉）
  let titleNode = null;
  let gridNode = null;
  let anchorNode = null;   // 開いた欄の箱（この中のクリックでは閉じない＝トグルを壊さない）
  let viewY = 0;           // 表示中の年月
  let viewM = 0;
  let selected = null;     // {y, m, d} | null
  let commitCb = null;

  const el = (tag, props) => {
    const node = doc.createElement(tag);
    for (const [k, v] of Object.entries(props || {})) {
      if (k === "dataset") Object.assign(node.dataset, v);
      else node[k] = v;
    }
    return node;
  };

  const sameDay = (a, b) => !!a && !!b && a.y === b.y && a.m === b.m && a.d === b.d;

  /** 格子を**表示中の月のぶんだけ**組み直す（月送り・選択のたびに 1 回）。 */
  function renderGrid() {
    titleNode.textContent = `${MONTH_NAMES[viewM]} ${viewY}`;
    for (const child of Array.from(gridNode.children || [])) gridNode.removeChild(child);
    for (const cell of gridOf(viewY, viewM)) {
      const day = el("button", {
        type: "button",
        className: "cal-day" + (cell.out ? " cal-out" : "") + (sameDay(cell, selected) ? " cal-sel" : ""),
        textContent: String(cell.d),
        dataset: { token: formatToken(cell) },
      });
      // 日付クリックで**即時確定して閉じる**（確定ボタンは置かない・依頼者裁定 2026-09-06）
      day.addEventListener("click", () => {
        selected = { y: cell.y, m: cell.m, d: cell.d };
        commit();
      });
      gridNode.appendChild(day);
    }
  }

  function moveMonth(delta) {
    const date = new Date(viewY, viewM + delta, 1);
    viewY = date.getFullYear();
    viewM = date.getMonth();
    renderGrid();
  }

  /** node が root の部分木に属するか（親参照は parentNode を正とする）。 */
  function within(node, root) {
    for (let n = node; n; n = n.parentNode) if (n === root) return true;
    return false;
  }

  /** カレンダーの外のクリックで閉じる（依頼者裁定 2026-09-06）。開いた欄の箱の中は除く
   *  （欄の▾ボタンのトグル・欄への手入力を、閉→開の再発火で壊さないため）。
   *  mousedown で判定する: click だと「開いたその 1 クリック」が doc まで浮上して
   *  即座に閉じてしまう（mousedown はリスナ登録**前**に終わっている）。 */
  function onOutsidePointerDown(event) {
    const target = event && event.target;
    if (within(target, pop) || within(target, anchorNode)) return;
    close();
  }

  function close() {
    if (pop && pop.parentNode) pop.parentNode.removeChild(pop);
    if (pop && doc.removeEventListener) doc.removeEventListener("mousedown", onOutsidePointerDown);
    pop = null;
    titleNode = null;
    gridNode = null;
    anchorNode = null;
    selected = null;
    commitCb = null;
  }

  function commit() {
    if (!selected) return;
    const cb = commitCb;
    const token = formatToken(selected);
    close();
    if (cb) cb(token);
  }

  /** anchor（配置先要素）へカレンダーを開く。value は現在の `.ini` トークン（空可）。
   *  確定（Choose Date）で onCommit(token) を呼ぶ。既に開いていれば閉じて開き直す。 */
  function openFor({ anchor, value, onCommit }) {
    close();
    commitCb = onCommit || null;
    selected = parseToken(value);
    const base = selected || (() => { const t = now(); return { y: t.getFullYear(), m: t.getMonth() }; })();
    viewY = base.y;
    viewM = base.m;

    pop = el("div", { className: "cal-pop", dataset: { mt5: "ui:date-picker" } });
    const head = el("div", { className: "cal-head" });
    const prev = el("button", { type: "button", className: "cal-nav cal-prev", textContent: "◀" });
    const next = el("button", { type: "button", className: "cal-nav cal-next", textContent: "▶" });
    prev.addEventListener("click", () => moveMonth(-1));
    next.addEventListener("click", () => moveMonth(1));
    titleNode = el("div", { className: "cal-title" });
    head.appendChild(prev);
    head.appendChild(titleNode);
    head.appendChild(next);
    pop.appendChild(head);

    const week = el("div", { className: "cal-week" });
    for (const label of WEEKDAY_LABELS) {
      week.appendChild(el("span", { className: "cal-weekday", textContent: label }));
    }
    pop.appendChild(week);

    gridNode = el("div", { className: "cal-grid" });
    pop.appendChild(gridNode);

    renderGrid();
    anchorNode = anchor;
    anchor.appendChild(pop);
    positionNear(anchor);
    if (doc.addEventListener) doc.addEventListener("mousedown", onOutsidePointerDown);
    return pop;
  }

  /** 実ブラウザでは fixed 配置で祖先の overflow クリップから脱出させる（実測 2026-09-06:
   *  投入フォーム面は下側ペインで縦が足りず、absolute のままだと 6 週目とフッタが切れる）。
   *  下に収まらなければ上へ開く。rect API の無い fake DOM では何もしない（配置は CSS の
   *  absolute フォールバックが持つ・格子と操作の検定に座標は関与しない）。 */
  function positionNear(anchor) {
    if (!anchor.getBoundingClientRect || !pop.getBoundingClientRect) return;
    const a = anchor.getBoundingClientRect();
    const size = pop.getBoundingClientRect();
    const view = doc.defaultView || {};
    pop.style.position = "fixed";
    const below = a.bottom + 4;
    const above = a.top - size.height - 4;
    const innerH = view.innerHeight || (below + size.height);
    // 下に収まればま下・だめなら上・上も無理なら画面内へクランプ（切れて操作不能が最悪）。
    let top = below;
    if (below + size.height > innerH) top = above >= 8 ? above : Math.max(8, innerH - size.height - 8);
    pop.style.top = `${top}px`;
    let left = a.left;
    const innerW = view.innerWidth || (left + size.width);
    if (left + size.width > innerW) left = Math.max(0, innerW - size.width - 8);
    pop.style.left = `${left}px`;
  }

  return {
    openFor,
    close,
    isOpen: () => !!pop,
  };
}
