// reach_sheet_view（adapter/front/reach_sheet_view.js）— 第 1 表＝価格ラダーの版面。
//
// 版面の参照実装は依頼者所有のモック「水準到達シート」（ISSUE-463・アーティファクト 1707bef3）。
//   列は 4 本（距離＋次のターゲット / 価格＋直前行との差 / 時間足 / 水準名）で、到達側は
//   帯（--up-bg）＋左 3px（--up-bar）、現在値行は反転（地 --ink・文字 --bg）。
//
// 設計入力:
//   §4.1 / §4.7: 行 = 水準 1 本（**束ねない**）。現在値は独立行として価格順の位置に入る。
//     「差」= 直前行との価格差で、モックに倣い価格の直下へ小さく置く（列は分けない）。
//   §4.3 / §4.7: 地平 3 段（短期 = すべて / 中期 = 1h 以上 / 長期 = 1D 以上）の直上・直下に
//     「次のターゲット」の印を付ける。印はモックの b.next（地平ごとの色）で出す。
//   §5.5.5 / §5.5.6: 価格セルの背景を地平 3 段で 3 分割し、各地平の `p` を heat_scale で塗る。
//     **数値は表示せず色だけ**。候補が 1 つも無い地平は空にし、色を置かない（無言で 0.5 を
//     埋めない）。モックにこの背景は無いが、§5.5.5 の要件なので保持した上でモックの
//     パレットへ調和させている（heat_scale.js 冒頭の記録）。
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
// 足別トーン（モックの r0〜r7）の並びは列を出す側と同じ唯一源を使う（写しを持たない）。
import { DASHBOARD_TIMEFRAMES } from './timeframes.js';

/** 背景 3 分割の並び（§4.3 の短い順）。値は dashboard_ui/domain/horizon.py の Horizon 値。 */
const HORIZONS = Object.freeze([
  { key: 'short', label: '短期', badge: 'dash-ladder-next-h1' },
  { key: 'medium', label: '中期', badge: 'dash-ladder-next-h2' },
  { key: 'long', label: '長期', badge: 'dash-ladder-next-h3' },
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

/** 列見出し。水準列はモックの 1 列から指標名 / 期間 / ソースの 3 列へ分割
 *  （依頼者指示 2026-08-30。行の識別は従来どおりサーバの `label` が担う）。 */
const COLUMNS = Object.freeze([
  { cell: 'distance', head: '距離 · 次のターゲット', className: 'dash-ladder-head-distance' },
  { cell: 'price', head: '価格', hint: '（下は直前行との差）', className: 'dash-ladder-head-price' },
  { cell: 'timeframe', head: '時間足', className: 'dash-ladder-head-timeframe' },
  { cell: 'name', head: '指標名', className: 'dash-ladder-head-name' },
  { cell: 'level', head: '水準', className: 'dash-ladder-head-level' },
  { cell: 'period', head: '期間', hint: '（プリセット）', className: 'dash-ladder-head-period' },
  { cell: 'source', head: 'ソース', className: 'dash-ladder-head-source' },
]);

/** 水準情報のセル数（現在値行の colSpan が数え直しを忘れないための唯一源）。 */
const NAMING_CELLS = 4;

/** 価格の表記（§4.7 の版面: 桁区切りあり・小数 1 桁）。 */
function formatPrice(value) {
  return Number(value).toLocaleString('en-US', { minimumFractionDigits: 1, maximumFractionDigits: 1 });
}

/** 距離の表記（符号を必ず付ける＝上下が符号だけで読める）。 */
function formatDistance(value) {
  const n = Number(value);
  return `${n >= 0 ? '+' : '-'}${Math.abs(n).toFixed(1)}`;
}

/**
 * 直前行との差（先頭行は直前行が無いので空）。
 * 「差」の語は列見出しの補足が担うので、欄には数値だけを置く（モックの i.gap と同じ）。
 */
function formatGap(value) {
  return value === null || value === undefined ? '' : Number(value).toFixed(1);
}

/**
 * 足別トーンの番号（モックの r0〜r7）。表示順＝短い順なので添字がそのままトーンになる。
 * 知らない時間足には番号を与えない（勝手に近い足へ寄せない＝無言の取り違えを作らない）。
 */
function toneIndexOf(timeframe) {
  const at = DASHBOARD_TIMEFRAMES.indexOf(String(timeframe));
  return at < 0 ? null : at;
}

/**
 * 到達側か（モックの tr.lad.hit）。
 *
 * 判定はサーバが与えた**距離の符号**だけで行う。モックの凡例が「現在値より下＝到達済み＝
 * 支持側」と定義しており、その定義そのものが距離の符号である。行の `reach` と or を取ると
 * 版面の意味が 2 つになるので取らない（到達時刻は第 2 表の担当）。
 */
function isReached(distance) {
  return Number(distance) < 0;
}

/**
 * 第 1 表の View を作る。
 *
 * @param {object} opts
 * @param {object} opts.doc DOM 実装（注入）
 * @param {?Function} [opts.periodAnnotator] (timeframe, bars) => string|null。
 *   期間（バー本数）に対応する暦期間プリセット表記（例 '1週'）。唯一源は
 *   indicator_ui の period_presets.js で、composition root が実行時 import して注入する
 *   （写しを持たない）。無ければ本数だけを出す（注記の欠落で版面は壊さない）。
 * @returns {{mount: Function, render: Function, unmount: Function}}
 */
export function createReachSheetView({ doc, periodAnnotator = null } = {}) {
  let root = null;
  let tbody = null;
  let notice = null;
  let message = null;

  const el = (tag, props = {}) => createElementWith(doc, tag, props);

  /** 版面（枠・見出し・本体・掲示欄）を組んでホストへ挿す。 */
  function mount(host) {
    if (!doc || typeof doc.createElement !== 'function') {
      return null;
    }
    if (!host || typeof host.appendChild !== 'function') {
      throw new Error('reach_sheet_view: ホストが渡されていないため版面を配置できない');
    }
    root = el('section', { className: 'dash-ladder' });

    message = el('p', { className: 'dash-sheet-message' });
    root.appendChild(message);

    const panel = el('div', { className: 'dash-panel' });
    const head = el('div', { className: 'dash-panel-head' });
    head.appendChild(el('span', { className: 'dash-panel-stamp', textContent: '水準 1 本 = 1 行' }));
    head.appendChild(el('h2', { className: 'dash-sheet-title', textContent: '価格ラダー' }));
    head.appendChild(el('p', {
      className: 'dash-panel-lead',
      textContent: '水準を束ねず 1 本 1 行で価格降順に並べる。時間足は比較の軸ではなく各行の属性。',
    }));
    panel.appendChild(head);

    const scroll = el('div', { className: 'dash-scroll' });
    const table = el('table', { className: 'dash-ladder-table' });
    const thead = el('thead');
    const headRow = el('tr');
    for (const column of COLUMNS) {
      const th = el('th', { className: column.className, dataset: { cell: column.cell } });
      th.appendChild(el('span', { textContent: column.head }));
      if (column.hint) {
        th.appendChild(el('span', { className: 'dash-sheet-hint', textContent: column.hint }));
      }
      headRow.appendChild(th);
    }
    thead.appendChild(headRow);
    table.appendChild(thead);
    tbody = el('tbody');
    table.appendChild(tbody);
    scroll.appendChild(table);
    panel.appendChild(scroll);
    root.appendChild(panel);

    root.appendChild(buildLegend());

    notice = el('p', { className: 'dash-granularity-notice' });
    host.appendChild(root);
    return root;
  }

  /** 凡例（モックの .legend）。読み方を版面の外へ持ち出させない。 */
  function buildLegend() {
    const legend = el('div', { className: 'dash-legend' });
    const item = (swatchClass, text) => {
      const span = el('span', { className: 'dash-legend-item' });
      span.appendChild(el('i', { className: `dash-legend-swatch ${swatchClass}`.trim() }));
      span.appendChild(el('span', { textContent: text }));
      return span;
    };
    legend.appendChild(item('dash-legend-swatch-hit', '現在値より下（到達済み＝支持側）'));
    legend.appendChild(item('', '現在値より上（未到達＝抵抗側）'));
    legend.appendChild(item('dash-legend-swatch-gap', '価格の下の小さな数字＝直前行との差'));
    return legend;
  }

  /** 地平の印（モックの b.next）。向きは**距離の符号**で決まる。 */
  function buildMarks(marks, distance) {
    const holder = el('span', { className: 'dash-ladder-marks', dataset: { cell: 'marks' } });
    const list = Array.isArray(marks) ? marks : [];
    if (list.length === 0) {
      return holder;
    }
    const direction = Number(distance) >= 0 ? '上' : '下';
    for (const horizon of HORIZONS) {
      if (!list.includes(horizon.key)) continue;
      holder.appendChild(el('b', {
        className: `dash-ladder-next ${horizon.badge}`,
        textContent: `${horizon.label} · ${direction}`,
      }));
    }
    return holder;
  }

  /** 価格セル（3 分割の背景＋価格の文字＋直前行との差）。 */
  function buildPriceCell(row) {
    const cell = el('td', { className: 'dash-ladder-price' });
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
    cell.appendChild(el('span', {
      className: 'dash-ladder-price-text',
      textContent: formatPrice(row.price),
      dataset: { cell: 'price' },
    }));
    cell.appendChild(el('i', {
      className: 'dash-ladder-gap',
      textContent: formatGap(row.gap_to_previous),
      dataset: { cell: 'gap' },
    }));
    return cell;
  }

  /** 時間足セル（モックのピル）。 */
  function buildTimeframeCell(timeframe, tone) {
    const cell = el('td', { className: 'dash-ladder-timeframe', dataset: { cell: 'timeframe' } });
    const pill = el('u', {
      className: tone === null ? 'dash-tf-pill' : `dash-tf-pill dash-tf-r${tone}`,
      textContent: String(timeframe ?? ''),
    });
    cell.appendChild(pill);
    return cell;
  }

  /** 水準 1 行。 */
  function buildLevelRow(row) {
    const tone = toneIndexOf(row.timeframe);
    const state = isReached(row.distance) ? 'dash-ladder-hit' : 'dash-ladder-pending';
    const toneClass = tone === null ? '' : ` dash-ladder-row-r${tone}`;
    const tr = el('tr', { className: `dash-ladder-row ${state}${toneClass}` });

    const distanceCell = el('th', { className: 'dash-ladder-distance-cell', scope: 'row' });
    // `data-cell` は**文字を持つ葉**に置く（欄そのものへ置くと、同じ欄へ同居する
    //   地平バッジの文字まで距離として読めてしまう）。
    distanceCell.appendChild(el('span', {
      className: 'dash-ladder-distance',
      textContent: formatDistance(row.distance),
      dataset: { cell: 'distance' },
    }));
    distanceCell.appendChild(buildMarks(row.horizon_marks, row.distance));
    tr.appendChild(distanceCell);

    tr.appendChild(buildPriceCell(row));
    tr.appendChild(buildTimeframeCell(row.timeframe, tone));
    appendNamingCells(tr, row);
    return tr;
  }

  /**
   * 水準情報の 3 セル（指標名 / 期間 / ソース・依頼者指示 2026-08-30）。
   *
   * サーバの `naming`（構造化）だけを読む。`label` の文字列を刻み直すと綴りの写しになり、
   * サーバ側の命名変更で無言にずれる。naming を欠く応答（旧サーバ）では label を
   * 指標名セルへそのまま出す（情報を落とさない後方互換）。
   */
  function appendNamingCells(tr, row) {
    const naming = row.naming ?? null;
    if (!naming) {
      tr.appendChild(el('td', {
        className: 'dash-ladder-name', colSpan: NAMING_CELLS,
        textContent: String(row.label ?? ''), dataset: { cell: 'name' },
      }));
      return;
    }
    // extra（水準の定義に効く残りの非既定設定）は本文に並べない（依頼者指摘 2026-08-30:
    //   k=v の羅列は伝わらない）。「+N」の印とツールチップへ退避し、版面は指標名だけにする。
    const nameCell = el('td', { className: 'dash-ladder-name', dataset: { cell: 'name' } });
    nameCell.appendChild(el('span', { textContent: String(naming.name ?? '') }));
    if (naming.extra) {
      const count = String(naming.extra).split(' ').filter(Boolean).length;
      nameCell.appendChild(el('i', {
        className: 'dash-ladder-extra-mark',
        textContent: `+${count}`,
        title: `既定と異なる詳細設定: ${naming.extra}`,
      }));
    }
    tr.appendChild(nameCell);
    // 水準セルの背景 = 定義分位 p（依頼者裁定 2026-08-30）。q{pct} 系だけが p を持ち、
    //   σ 帯・mean は p 目盛りに載らないため無色（level_p=null → colorForP が色を置かない）。
    //   色の唯一源は heat_scale（§5.5.7・価格セルの 3 分割と同じ目盛り）。
    const levelCell = el('td', {
      className: 'dash-ladder-level',
      textContent: naming.level === null || naming.level === undefined ? '' : String(naming.level),
      dataset: { cell: 'level' },
    });
    levelCell.style.backgroundColor = colorForP(
      naming.level_p === undefined ? null : naming.level_p,
    );
    tr.appendChild(levelCell);
    const periodCell = el('td', { className: 'dash-ladder-period', dataset: { cell: 'period' } });
    if (naming.period !== null && naming.period !== undefined) {
      periodCell.appendChild(el('span', { textContent: String(naming.period) }));
      const preset = typeof periodAnnotator === 'function'
        ? periodAnnotator(row.timeframe, Number(naming.period)) : null;
      if (preset) {
        periodCell.appendChild(el('i', {
          className: 'dash-ladder-period-preset', textContent: preset,
        }));
      }
    }
    tr.appendChild(periodCell);
    tr.appendChild(el('td', {
      className: 'dash-ladder-source',
      textContent: naming.source === null || naming.source === undefined ? '' : String(naming.source),
      dataset: { cell: 'source' },
    }));
  }

  /** 現在値の独立行（§4.1・モックの tr.now＝反転帯）。 */
  function buildCurrentRow(currentPrice) {
    const tr = el('tr', { className: 'dash-ladder-row dash-ladder-current' });
    tr.appendChild(el('th', { scope: 'row', textContent: '現在値', dataset: { cell: 'distance' } }));
    const priceCell = el('td', { dataset: { cell: 'price' } });
    priceCell.appendChild(el('b', {
      className: 'dash-ladder-current-price',
      textContent: formatPrice(currentPrice),
    }));
    tr.appendChild(priceCell);
    tr.appendChild(el('td', {
      colSpan: 1 + NAMING_CELLS,
      textContent: '全時間足で同一の 1 点',
      dataset: { cell: 'label' },
    }));
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
   *
   * ISSUE-466: 実テンプレートでは同じ理由の縮退が 24 件（8 足 × 3 本）まとめて出る。
   *   1 件 1 文で並べると内部エラーの原文が 24 回繰り返され、読む側は「何が起きたか」を
   *   取り出せない（認知負荷の最小化）。**隠さない**性質は保ったまま、同一の
   *   (指標, 理由種別) を 1 行へ畳み、件数を出し、原文は `title` へ退避する。
   */
  function renderNotice(degradations) {
    const list = Array.isArray(degradations) ? degradations : [];
    const barClose = groupDegradations(list.filter((d) => d && d.granularity === 'bar_close'));
    // `none` は「バー確定でも回復しない」＝その instance の背景が塗られない（レビュー 🟡-2）。
    //   bar_close だけを掲示すると、サーバが理由を送っていても画面上は無言の欠落になる。
    const unprojectable = groupDegradations(list.filter((d) => d && d.granularity === 'none'));
    if (notice.parentNode) {
      notice.parentNode.removeChild(notice);
    }
    notice.textContent = '';
    if (barClose.length === 0 && unprojectable.length === 0) {
      return;
    }
    if (barClose.length > 0) {
      appendSentence('更新粒度がバー確定のもの: ', barClose, '。ティックでは更新されません。');
    }
    if (unprojectable.length > 0) {
      appendSentence('背景を塗れないもの: ', unprojectable, '。');
    }
    root.appendChild(notice);
  }

  /** 導入句 ＋ 集約した項目（原文を持つ）＋ 締めの句 を掲示欄へ足す。 */
  function appendSentence(lead, groups, tail) {
    if (notice.children.length > 0) {
      notice.appendChild(el('span', { textContent: ' ' }));
    }
    notice.appendChild(el('span', { textContent: lead }));
    groups.forEach((group, index) => {
      if (index > 0) {
        notice.appendChild(el('span', { textContent: '・' }));
      }
      notice.appendChild(el('span', {
        className: 'dash-notice-item',
        textContent: `${group.indicatorId}: ${scopeTextOf(group)} — ${group.summary}`,
        // 原文は捨てず title へ退避する（本文は人間向け 1 行・原因は失わない）。
        title: [...group.reasons].join('\n'),
      }));
    });
    notice.appendChild(el('span', { textContent: tail }));
  }

  /** 何本・何足が該当したか（1 本だけなら時間足そのものを出す＝件数より具体が読める）。 */
  function scopeTextOf(group) {
    return group.timeframes.size === 1 && group.count === 1
      ? [...group.timeframes][0]
      : `${group.timeframes.size} 足 ${group.count} 本`;
  }

  /**
   * 内部エラーの原文 → (理由種別, 人間向け 1 行)。
   *
   * 表で持つ（分岐を書かない）。新しい原文が増えたら**行を足す**だけで済む（§8 OCP）。
   * どの規則にも当たらない原文は本文へそのまま出す——読めない文でも、消すよりはよい
   * （無言の縮退禁止）。その場合は原文そのものが理由種別になるので、別々に並ぶ。
   */
  const REASON_RULES = Object.freeze([
    {
      kind: 'unaccepted_params',
      pattern: /受理しない param が渡されました:\s*\[([^\]]*)\]/,
      summarise: (m) => `受理されないパラメータ ${m[1].replace(/['"]/g, '')}`,
    },
    {
      kind: 'insufficient_bars',
      pattern: /E01_INSUFFICIENT_BARS/,
      summarise: () => '素材の本数が指標の必要本数に足りない',
    },
    {
      kind: 'not_bound',
      pattern: /ライブ core に束縛がありません/,
      summarise: () => 'ダッシュボードの計算に対応していない指標',
    },
  ]);

  /** 理由 1 件を (種別, 人間向け 1 行) へ写す。 */
  function readReason(reason) {
    const text = String(reason ?? '理由の記載なし');
    for (const rule of REASON_RULES) {
      const matched = rule.pattern.exec(text);
      if (matched) {
        return { kind: rule.kind, summary: rule.summarise(matched) };
      }
    }
    return { kind: `raw:${text}`, summary: text };
  }

  /** 同一 (指標, 理由種別) を 1 件へ畳む（掲示の単位）。 */
  function groupDegradations(degradations) {
    const groups = new Map();
    for (const degradation of degradations) {
      const key = Array.isArray(degradation.instance_key) ? degradation.instance_key : [];
      const indicatorId = key[0] ?? '?';
      const { kind, summary } = readReason(degradation.reason);
      const id = `${indicatorId} ${kind}`;
      if (!groups.has(id)) {
        groups.set(id, { indicatorId, summary, count: 0, timeframes: new Set(), reasons: new Set() });
      }
      const group = groups.get(id);
      group.count += 1;
      group.timeframes.add(String(key[3] ?? '?'));
      group.reasons.add(String(degradation.reason ?? '理由の記載なし'));
    }
    return [...groups.values()];
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
