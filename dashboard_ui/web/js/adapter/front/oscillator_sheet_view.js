// oscillator_sheet_view（adapter/front/oscillator_sheet_view.js）— 第 2 表の版面。
//
// 設計入力:
//   §5.1: 行 = 指標インスタンス / 列 = 表示時間足 8 列（第 1 表と違い MTF 共通列は無い）。
//   §5.2: セルの内容は 3 つ。
//         - 配色 = 連続量 `p`（§5.3）。**段の名前は表示しない**（依頼者裁定 2026-08-29）。
//         - 現在値 = 必ず数字で併記する（色から絶対量は読めないため）。
//         - 到達時刻 = 帯を出ているときのみ（定義 A・§6.2）。
//         **水準が存在しないセルは隠さない。** 空欄にせず「水準なし」と明示する
//         （§7 の `cvfe` と同じ規約。無言の縮退を作らない）。
//   §5.3.2: GPD が当てはまらない 7 セルは帯外を**単一色**にして目盛りが無いことを示す。
//   §9-5 / arch-spec T-11: 到達時刻の表示粒度は**当日は時刻・過去日は相対表記**。
//   arch-spec §9: 応答のフィールド名をそのまま読む。フロントは数値を再計算しない。
//
// DOM は View が生成し所有する。色は heat_scale.js が唯一源（第 1 表と同じ目盛り・§5.5.7）。
// 時計は注入で受ける（実時計を直接読むと、到達時刻の表記が検定のたびに変わる）。

import { colorForP, tailUnscaledColor } from './heat_scale.js';
import { createElementWith } from './dom_element.js';
// 価格表記の唯一源（第 1 表と共有・写しを持たない）。
import { formatPrice } from './format.js';
// 列（表示時間足 8 本）は束を組む側と同じ並びを使う（写しを持たない）。
import { DASHBOARD_TIMEFRAMES as TIMEFRAMES } from './timeframes.js';


/** 1 日の秒数（到達時刻の相対表記の単位）。 */
const SECONDS_PER_DAY = 86400;

/** 値が無いことの表示（§5.4 の版面の「―」）。 */
const NOT_APPLICABLE = '―';

/** UTC の暦日番号（marketdata の date 列は UTC・MEMORY: marketdata-date-column-utc）。 */
function utcDayIndex(unixSeconds) {
  return Math.floor(unixSeconds / SECONDS_PER_DAY);
}

/** UTC の HH:MM。 */
function clockOf(unixSeconds) {
  const date = new Date(unixSeconds * 1000);
  const hh = String(date.getUTCHours()).padStart(2, '0');
  const mm = String(date.getUTCMinutes()).padStart(2, '0');
  return `${hh}:${mm}`;
}

/**
 * 到達時刻の表記（§9-5 / T-11）。当日は時刻、過去日は相対表記。
 *
 * `truncated` は「連続区間が履歴の先頭で切れている」＝始端が履歴の外かもしれない状態
 * （dashboard_ui/domain/reach.py の ReachState）。断定して日時を出すと、履歴外の到達を
 * その日の到達として誤読させるため「以前」を付けて限定する。
 */
export function formatReachTime(reach, nowUnix) {
  if (!reach || reach.reached !== true || reach.since_time === null || reach.since_time === undefined) {
    return NOT_APPLICABLE;
  }
  const since = Number(reach.since_time);
  const days = utcDayIndex(nowUnix) - utcDayIndex(since);
  let base;
  if (days <= 0) {
    base = clockOf(since);
  } else if (days === 1) {
    base = '昨日';
  } else {
    base = `${days}日前`;
  }
  return reach.truncated === true ? `${base}以前` : base;
}

/** 現在値の表記（単位は指標ごとに違うので、丸めだけ揃える・§5.2）。 */
function formatValue(value) {
  if (value === null || value === undefined || !Number.isFinite(Number(value))) {
    return NOT_APPLICABLE;
  }
  const n = Number(value);
  return Number.isInteger(n) ? String(n) : n.toFixed(2).replace(/\.?0+$/, '');
}

/**
 * 第 2 表の View を作る。
 *
 * @param {object}   opts
 * @param {object}   opts.doc  DOM 実装（注入）
 * @param {Function} opts.now  現在時刻（unix 秒）を返す時計（注入）
 * @returns {{mount: Function, render: Function, unmount: Function}}
 */
export function createOscillatorSheetView({ doc, now } = {}) {
  if (typeof now !== 'function') {
    throw new TypeError('createOscillatorSheetView: now（時計）の注入は必須');
  }
  let root = null;
  let tbody = null;
  let message = null;

  const el = (tag, props = {}) => createElementWith(doc, tag, props);

  function mount(host) {
    if (!doc || typeof doc.createElement !== 'function') {
      return null;
    }
    if (!host || typeof host.appendChild !== 'function') {
      throw new Error('oscillator_sheet_view: ホストが渡されていないため版面を配置できない');
    }
    root = el('section', { className: 'dash-osc' });
    message = el('p', { className: 'dash-sheet-message' });
    root.appendChild(message);

    // 枠・見出し・走査域はモックの .prop / .hd / .scroll と同じ流儀（第 1 表と揃える）。
    const panel = el('div', { className: 'dash-panel' });
    const head = el('div', { className: 'dash-panel-head' });
    head.appendChild(el('h2', { className: 'dash-sheet-title', textContent: 'オシレータ水準到達表' }));
    // リード文（説明の段落）は出さない（依頼者指示 2026-08-30: 削除）。
    panel.appendChild(head);

    const scroll = el('div', { className: 'dash-scroll' });
    const table = el('table', { className: 'dash-osc-table' });
    const thead = el('thead');
    const headRow = el('tr');
    headRow.appendChild(el('th', { textContent: '指標' }));
    for (const timeframe of TIMEFRAMES) {
      headRow.appendChild(el('th', { textContent: timeframe, dataset: { timeframe } }));
    }
    thead.appendChild(headRow);
    table.appendChild(thead);
    tbody = el('tbody');
    table.appendChild(tbody);
    scroll.appendChild(table);
    panel.appendChild(scroll);
    root.appendChild(panel);
    host.appendChild(root);
    return root;
  }

  /** 1 セル（配色・現在値・到達時刻）。 */
  function buildCell(cell, indicatorId, timeframe, nowUnix) {
    const td = el('td', {
      className: 'dash-osc-cell',
      dataset: { indicator: indicatorId, timeframe },
    });
    // 水準が無い（そのセル自体が無い・`p` が出せない）ことを**隠さない**（§5.2）。
    const hasLevel = cell && cell.p !== null && cell.p !== undefined;
    if (!hasLevel) {
      // 色を置かないことを**明示**する（無言で 0.5 を埋めない・§5.5.5 と同じ規約）。
      td.style.backgroundColor = colorForP(null);
      td.title = cell && cell.unavailable_reason ? String(cell.unavailable_reason) : '水準なし';
      td.appendChild(el('span', { className: 'dash-osc-value', textContent: cell ? formatValue(cell.value) : NOT_APPLICABLE }));
      td.appendChild(el('span', { className: 'dash-osc-no-level', textContent: '水準なし' }));
      return td;
    }
    // §5.3.2: 目盛りが当てはまらないセルは単一色（`p` の濃さとして読ませない）。
    if (cell.tail_unscaled === true) {
      td.classList.add('dash-osc-tail-unscaled');
      td.style.backgroundColor = tailUnscaledColor();
      td.title = '帯外は目盛りが無い（本数不足で当てはめ不能）';
    } else {
      td.style.backgroundColor = colorForP(cell.p);
    }
    td.appendChild(el('span', { className: 'dash-osc-value', textContent: formatValue(cell.value) }));
    // 分位水準に達したときの価格（依頼者指示 2026-08-30・上下 2 値は同日承認。
    //   §5.5 の閉形式逆写像＋往復検証）。逆算できない側は null＝出さない（発明しない）。
    //   ↑＝上帯（q_high）・↓＝下帯（q_low）。上（高い価格）から並べる（ラダーと同じ降順）。
    const prices = cell.level_prices || {};
    if (prices.q_high !== null && prices.q_high !== undefined) {
      td.appendChild(el('span', {
        className: 'dash-osc-level-price',
        textContent: `↑ ${formatPrice(prices.q_high)}`,
        title: '上帯（q_high の分位水準）に達したときの価格',
      }));
    }
    if (prices.q_low !== null && prices.q_low !== undefined) {
      td.appendChild(el('span', {
        className: 'dash-osc-level-price',
        textContent: `↓ ${formatPrice(prices.q_low)}`,
        title: '下帯（q_low の分位水準）に達したときの価格',
      }));
    }
    td.appendChild(el('span', { className: 'dash-osc-reach', textContent: formatReachTime(cell.reach, nowUnix) }));
    return td;
  }

  /**
   * 応答 1 件を描く。
   *
   * @param {object} response arch-spec §9 の応答
   */
  function render(response) {
    if (!root || !tbody) {
      throw new Error('oscillator_sheet_view: mount より先に render は呼べない');
    }
    while (tbody.children.length > 0) {
      tbody.removeChild(tbody.children[0]);
    }
    if (!response || response.ok !== true) {
      message.textContent = response && response.error && response.error.message
        ? response.error.message
        : 'シートを取得できませんでした';
      return;
    }
    message.textContent = '';

    const cells = Array.isArray(response.cells) ? response.cells : [];
    // 行の並びは**応答での初出順**（サーバ側が単一ソース。表示側で並べ替えない）。
    const indicators = [];
    const byKey = new Map();
    for (const cell of cells) {
      if (!cell || !cell.indicator_id) continue;
      if (!indicators.includes(cell.indicator_id)) indicators.push(cell.indicator_id);
      byKey.set(`${cell.indicator_id}\u0000${cell.timeframe}`, cell);
    }

    const nowUnix = Number(now());
    for (const indicatorId of indicators) {
      const tr = el('tr', { className: 'dash-osc-row', dataset: { indicator: indicatorId } });
      tr.appendChild(el('th', { className: 'dash-osc-name', scope: 'row', textContent: indicatorId }));
      for (const timeframe of TIMEFRAMES) {
        tr.appendChild(buildCell(byKey.get(`${indicatorId}\u0000${timeframe}`), indicatorId, timeframe, nowUnix));
      }
      tbody.appendChild(tr);
    }
  }

  function unmount() {
    if (root && root.parentNode && typeof root.parentNode.removeChild === 'function') {
      root.parentNode.removeChild(root);
    }
    root = null;
    tbody = null;
    message = null;
  }

  return { mount, render, unmount };
}
