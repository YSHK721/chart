// reach_sheet_view（adapter/front/reach_sheet_view.js）— 第 1 表＝価格ラダーの版面。
//
// 設計入力:
//   §4.1 / §4.7: 行 = 水準 1 本（**束ねない**）。列 = 距離 / 価格 / 差 / 時間足 / 水準ラベル。
//     現在値は独立行として価格順の位置に入る。「差」= 直前行との価格差。
//   §4.3 / §4.7: 地平 3 段（短期 = すべて / 中期 = 1h 以上 / 長期 = 1D 以上）の直上・直下に
//     「次のターゲット」の印を付ける。
//   §5.5.5 / §5.5.6: 価格セルの背景を地平 3 段で 3 分割し、各地平の `p` を heat_scale で塗る。
//     **数値は表示せず色だけ**。候補が 1 つも無い地平は空にし、色を置かない（無言で 0.5 を
//     埋めない）。
//   §7: `cvfe` は増分器が無く段 1 でしか更新されない。更新粒度の差を**隠さず**掲示する。
//   arch-spec §9: 応答のフィールド名をそのまま読む。**フロントは数値を再計算しない**
//     （`p` の算出・並び替え・到達判定はすべてサーバ側が単一ソース）。
//
// DOM は View が生成し所有する（index.html へ表を直書きしない・overlay_host.js 規約）。
// 色は heat_scale.js が唯一源であり、本モジュールは色を作らない。
// 発行（HTTP）も時計も持たない——描くだけ。混ぜると「描くたびに発行する」欠陥が入り込み、
//   出力は正しいまま無駄だけが増える（ISSUE-450 と同型）。

import { colorForP } from './heat_scale.js';
import { createElementWith } from './dom_element.js';

/** 背景 3 分割の並び（§4.3 の短い順）。値は dashboard_ui/domain/horizon.py の Horizon 値。 */
const HORIZONS = Object.freeze([
  { key: 'short', label: '短期' },
  { key: 'medium', label: '中期' },
  { key: 'long', label: '長期' },
]);

/** 地平キーの集合（照合用）。 */
const HORIZON_KEYS = Object.freeze(HORIZONS.map((h) => h.key));

/**
 * 応答が持ち込んだ未知の地平キーを集める。
 *
 * 地平の名前の唯一源は dashboard_ui/domain/horizon.py の `Horizon`（short / medium / long）。
 * arch-spec §9 の例示は `mid` と書いているが、同 §9 自身が「実際の enum 値名は horizon.py を
 * 読んで確定せよ」と定めており、enum が正である。
 *
 * なぜ黙って捨てないか: 背景は**色しか出さない**（§5.5.6）。サーバが `mid` を出すと中期の帯は
 * ただ色が付かないだけになり、「候補が無い地平」（§5.5.5 の正当な空）と版面上で区別できない。
 * 契約のズレが永久に見つからなくなるので、掲示して見えるようにする。
 */
function unknownHorizonKeys(rows) {
  const found = new Set();
  for (const row of rows) {
    for (const key of Object.keys(row && row.horizon_p ? row.horizon_p : {})) {
      if (!HORIZON_KEYS.includes(key)) found.add(key);
    }
  }
  return [...found].sort();
}

/** §4.7 の列見出し。 */
const COLUMNS = Object.freeze([
  { cell: 'distance', head: '距離' },
  { cell: 'price', head: '価格' },
  { cell: 'gap', head: '差' },
  { cell: 'timeframe', head: '時間足' },
  { cell: 'label', head: '水準' },
  { cell: 'marks', head: '' },
]);

/** 価格の表記（§4.7 の版面: 桁区切りあり・小数 1 桁）。 */
function formatPrice(value) {
  return Number(value).toLocaleString('en-US', { minimumFractionDigits: 1, maximumFractionDigits: 1 });
}

/** 距離の表記（符号を必ず付ける＝上下が符号だけで読める）。 */
function formatDistance(value) {
  const n = Number(value);
  return `${n >= 0 ? '+' : '-'}${Math.abs(n).toFixed(1)}`;
}

/** 直前行との差（先頭行は直前行が無いので空）。 */
function formatGap(value) {
  return value === null || value === undefined ? '' : `差 ${Number(value).toFixed(1)}`;
}

/**
 * 地平の印（§4.7 の「← 長期・上」「← 中期・下／長期・下」）。
 * 向きは**距離の符号**で決まる（サーバが並びと符号の単一ソース）。
 */
function formatMarks(marks, distance) {
  const list = Array.isArray(marks) ? marks : [];
  if (list.length === 0) {
    return '';
  }
  const direction = Number(distance) >= 0 ? '上' : '下';
  const names = HORIZONS.filter((h) => list.includes(h.key)).map((h) => `${h.label}・${direction}`);
  return names.length === 0 ? '' : `← ${names.join('／')}`;
}

/**
 * 第 1 表の View を作る。
 *
 * @param {object} opts
 * @param {object} opts.doc DOM 実装（注入）
 * @returns {{mount: Function, render: Function, unmount: Function}}
 */
export function createReachSheetView({ doc } = {}) {
  let root = null;
  let tbody = null;
  let notice = null;
  let message = null;

  const el = (tag, props = {}) => createElementWith(doc, tag, props);

  /** 版面（見出し・本体・掲示欄）を組んでホストへ挿す。 */
  function mount(host) {
    if (!doc || typeof doc.createElement !== 'function') {
      return null;
    }
    if (!host || typeof host.appendChild !== 'function') {
      throw new Error('reach_sheet_view: ホストが渡されていないため版面を配置できない');
    }
    root = el('section', { className: 'dash-ladder' });
    root.appendChild(el('h2', { className: 'dash-sheet-title', textContent: '価格ラダー' }));

    message = el('p', { className: 'dash-sheet-message' });
    root.appendChild(message);

    const table = el('table', { className: 'dash-ladder-table' });
    const thead = el('thead');
    const headRow = el('tr');
    for (const column of COLUMNS) {
      headRow.appendChild(el('th', { textContent: column.head, dataset: { cell: column.cell } }));
    }
    thead.appendChild(headRow);
    table.appendChild(thead);
    tbody = el('tbody');
    table.appendChild(tbody);
    root.appendChild(table);

    notice = el('p', { className: 'dash-granularity-notice' });
    host.appendChild(root);
    return root;
  }

  /** 価格セル（3 分割の背景＋価格の文字）。 */
  function buildPriceCell(row) {
    const cell = el('td', { className: 'dash-ladder-price', dataset: { cell: 'price' } });
    const bands = el('span', { className: 'dash-ladder-bands' });
    const horizonP = row.horizon_p ?? {};
    for (const horizon of HORIZONS) {
      const value = Object.prototype.hasOwnProperty.call(horizonP, horizon.key) ? horizonP[horizon.key] : null;
      const band = el('span', {
        className: 'dash-ladder-band',
        // 段名も数値も出さない（§5.3 / §5.5.6）。読み取れるのは濃さだけ。
        title: `${horizon.label}の分位`,
        dataset: { horizon: horizon.key },
      });
      band.style.backgroundColor = colorForP(value);
      bands.appendChild(band);
    }
    cell.appendChild(bands);
    // 価格の文字は**子要素**として足す。`cell.textContent = ...` と書くと実 DOM では
    //   直前に足した 3 分割の背景が丸ごと消える（textContent の代入は子を捨てる）。
    cell.appendChild(el('span', { className: 'dash-ladder-price-text', textContent: formatPrice(row.price) }));
    return cell;
  }

  /** 水準 1 行。 */
  function buildLevelRow(row) {
    const tr = el('tr', { className: 'dash-ladder-row' });
    tr.appendChild(el('td', { className: 'dash-ladder-distance', textContent: formatDistance(row.distance), dataset: { cell: 'distance' } }));
    tr.appendChild(buildPriceCell(row));
    tr.appendChild(el('td', { textContent: formatGap(row.gap_to_previous), dataset: { cell: 'gap' } }));
    tr.appendChild(el('td', { textContent: String(row.timeframe ?? ''), dataset: { cell: 'timeframe' } }));
    tr.appendChild(el('td', { textContent: String(row.label ?? ''), dataset: { cell: 'label' } }));
    tr.appendChild(el('td', { className: 'dash-ladder-marks', textContent: formatMarks(row.horizon_marks, row.distance), dataset: { cell: 'marks' } }));
    return tr;
  }

  /** 現在値の独立行（§4.1）。 */
  function buildCurrentRow(currentPrice) {
    const tr = el('tr', { className: 'dash-ladder-row dash-ladder-current' });
    tr.appendChild(el('td', { textContent: '', dataset: { cell: 'distance' } }));
    tr.appendChild(el('td', { textContent: formatPrice(currentPrice), dataset: { cell: 'price' } }));
    tr.appendChild(el('td', { textContent: '', dataset: { cell: 'gap' } }));
    tr.appendChild(el('td', { textContent: '', dataset: { cell: 'timeframe' } }));
    tr.appendChild(el('td', { textContent: '現在値', dataset: { cell: 'label' } }));
    tr.appendChild(el('td', { textContent: '', dataset: { cell: 'marks' } }));
    return tr;
  }

  /**
   * §7 の更新粒度の掲示。
   *
   * 掲示は `degradations` が持つ情報（指標 / 時間足 / 粒度 / 理由）だけで組む。応答の
   * `rows` は `label` と `timeframe` しか持たず **`indicator_id` を持たない**ため、行と
   * instance を突き合わせる手掛かりが契約に無い。ラベルの綴りから推測して行へ印を置くと、
   * ラベルの書式が変わった瞬間に無言で外れる（＝推測に基づく実装）。よってここでは
   * 契約が実際に与える単位（instance）で掲示する。
   */
  function renderNotice(degradations) {
    const list = Array.isArray(degradations) ? degradations : [];
    const barClose = list.filter((d) => d && d.granularity === 'bar_close');
    if (notice.parentNode) {
      notice.parentNode.removeChild(notice);
    }
    notice.textContent = '';
    if (barClose.length === 0) {
      return;
    }
    const named = barClose.map((d) => {
      const key = Array.isArray(d.instance_key) ? d.instance_key : [];
      return `${key[0] ?? '?'}（${key[3] ?? '?'}）`;
    });
    notice.textContent = `更新粒度がバー確定のもの: ${named.join('・')}。ティックでは更新されません。`;
    root.appendChild(notice);
  }

  /**
   * 応答 1 件を描く（段 1・段 2 とも同じ経路。毎回組み直すので積み上がらない）。
   *
   * @param {object} response arch-spec §9 の応答
   */
  function render(response) {
    if (!root || !tbody) {
      throw new Error('reach_sheet_view: mount より先に render は呼べない');
    }
    while (tbody.children.length > 0) {
      tbody.removeChild(tbody.children[0]);
    }
    if (!response || response.ok !== true) {
      const reason = response && response.error && response.error.message
        ? response.error.message
        : 'シートを取得できませんでした';
      message.textContent = reason;
      renderNotice([]);
      return;
    }
    const rows = Array.isArray(response.rows) ? response.rows : [];
    // 契約のズレ（未知の地平キー）は色の不在として紛れるので、必ず文字で掲示する。
    const unknown = unknownHorizonKeys(rows);
    message.textContent = unknown.length === 0
      ? ''
      : `未知の地平キーが応答に含まれています: ${unknown.join(', ')}（対象は ${HORIZON_KEYS.join(' / ')}）`;

    // 現在値行の位置はサーバが決める（並びの単一ソース）。範囲外の指定は端へ倒す。
    const at = Math.max(0, Math.min(Number(response.current_index) || 0, rows.length));
    rows.forEach((row, index) => {
      if (index === at) {
        tbody.appendChild(buildCurrentRow(response.current_price));
      }
      tbody.appendChild(buildLevelRow(row));
    });
    if (at >= rows.length) {
      tbody.appendChild(buildCurrentRow(response.current_price));
    }
    renderNotice(response.degradations);
  }

  /** 版面を畳む（共有の器へ何も残さない）。 */
  function unmount() {
    if (root && root.parentNode && typeof root.parentNode.removeChild === 'function') {
      root.parentNode.removeChild(root);
    }
    root = null;
    tbody = null;
    notice = null;
    message = null;
  }

  return { mount, render, unmount };
}
