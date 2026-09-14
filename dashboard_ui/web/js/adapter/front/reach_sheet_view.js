// reach_sheet_view（adapter/front/reach_sheet_view.js）— 第 1 表＝価格ラダーの版面。
//
// 版面の参照実装は依頼者所有のモック「水準到達シート」（ISSUE-463・アーティファクト 1707bef3）。
//   列は 4 本（距離＋次のターゲット / 価格＋直前行との差 / 時間足 / 水準名）で、到達側は
//   帯（--up-bg）＋左 3px（--up-bar）、現在値行は反転（地 --ink・文字 --bg）。
//
// 設計入力:
//   §4.1 / §4.7: 行 = 水準 1 本（**束ねない**）。現在値は独立行として価格順の位置に入る。
//     「差」= 直前行との価格差。独立列（依頼者指示 2026-08-30。モックの「価格の直下」から変更）。
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
//
// 責務（ISSUE-502 段階 4C・F-2 の是正後）: **DOM の構築と結線だけ**。
//   本モジュールは規則を所有しない。以下はすべて domain の純ロジックが持ち、View は
//   「測る・描く・押されたことを伝える」に徹する（規則は DOM 無しで単体検証できる）:
//     - 更新頻度の統計 …… domain/tick_rate.js（createTickRateMeter）
//     - 残光の予定表 …… domain/next_target_glow.js（createNextTargetGlow）
//     - 表示範囲の状態機械 …… domain/ladder_scope.js（createLadderScope）
//     - 窓の幾何 …… domain/ladder_window.js（sliceWindow / fitRadius）
//     - 距離・差の式と台帳 …… domain/smooth_ladder.js（createSmoothLedger）
//     - 行の論理識別子 …… domain/ladder_row.js（rowKeyOf）
//   分割前は 1,170 行に 6 責務が同居し、変更要求元（依頼者の見た目指示 / 版面の列構成 /
//   時間の規則）が別々の 3 アクター以上あった。同居していると、頻度の窓を変えるだけの
//   要求が「表を描く関数」の改変として現れる（SRP 違反）。

import { colorForP, colorForDensity } from './heat_scale.js';
import { createElementWith } from './dom_element.js';
// 操作子（期間ボタン・時間足ピル）の版面は別モジュールが所有する（F-2: 押す道具と読む表の分離）。
import { createLadderSelectorsView } from './ladder_selectors_view.js';
// 価格表記の唯一源（第 2 表と共有・写しを持たない）。
import { formatPrice, formatReachTimestamp } from './format.js';
// 足別トーン（モックの r0〜r7）の並びは列を出す側と同じ唯一源を使う（写しを持たない）。
import { DASHBOARD_TIMEFRAMES } from './timeframes.js';
// 規則の所有者（上記「責務」参照）。View はこれらを組み立てて使うだけで、式・閾値を持たない。
import { rowKeyOf } from '../../domain/ladder_row.js';
import { createTickRateMeter } from '../../domain/tick_rate.js';
import { createNextTargetGlow } from '../../domain/next_target_glow.js';
import { createLadderScope } from '../../domain/ladder_scope.js';
import { createSmoothLedger } from '../../domain/smooth_ladder.js';
import { WINDOW_RADIUS, fitRadius, sliceWindow } from '../../domain/ladder_window.js';

/** 背景 3 分割の並び（§4.3 の短い順）。値は dashboard_ui/domain/horizon.py の Horizon 値。 */
const HORIZONS = Object.freeze([
  { key: 'short', label: '短期' },
  { key: 'medium', label: '中期' },
  { key: 'long', label: '長期' },
]);

/**
 * MP セルの説明文（依頼者承認 2026-09-06「MP 列＝ライブ MP の借用」・裁定 8(b)）。
 *
 * 窓の本数を名乗るのをやめた理由: MP 列はもうサーバ側の固定窓（gateway の `MP_WINDOW_BARS`）
 * ではなく、live core の `/market_profile` を**ライブチャートと同一のパラメータで**借りて
 * 描いている。窓も設定も更新規則もチャート側が唯一源であり、版面が独立した数を名乗ると
 * その数だけが古くなる。したがって「どこと同じか」を言う。
 */
const MP_CELL_TITLE = 'ライブチャートの MP と同一（設定・窓・更新はチャートに追従）';

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
  // 次のターゲット（地平の印）は独立列・距離より先（依頼者指示 2026-08-30
  //   「距離 · 次のターゲットも各列に分離しろ」→「順番を逆に」）。
  { cell: 'next', head: '次のターゲット', className: 'dash-ladder-head-next' },
  { cell: 'distance', head: '距離', className: 'dash-ladder-head-distance' },
  { cell: 'price', head: '価格', className: 'dash-ladder-head-price' },
  // MP（依頼者承認 2026-09-06「価格と差の間に MP 列」）。1m 確定足の窓
  //   （長さは MP_WINDOW_BARS_LABEL）で作ったプロファイルの TPO 密度を色の濃度＋横バーで
  //   出す（同日指示「色の濃度とグラフで表示しろ」）。数値は出さない。
  { cell: 'mp', head: 'MP', className: 'dash-ladder-head-mp' },
  // 差は独立列（依頼者指示 2026-08-30「価格と直前行の差を分離して各列に」）。
  { cell: 'gap', head: '差', hint: '（直前行と）', className: 'dash-ladder-head-gap' },
  // 到達時間（依頼者指示 2026-08-30: 差と時間足の間・YYYY/MM/DD HH:MM:SS・UTC）。
  { cell: 'reach_time', head: '到達時間', className: 'dash-ladder-head-reach-time' },
  { cell: 'timeframe', head: '時間足', className: 'dash-ladder-head-timeframe' },
  // 水準情報の 4 列（依頼者指示 2026-08-30）。`naming` の宣言が「この列は水準情報か」の
  //   唯一源で、セル数は下でここから数える（列の一覧と別に数字を書かない）。
  { cell: 'name', head: '指標名', className: 'dash-ladder-head-name', naming: true },
  { cell: 'level', head: '水準', className: 'dash-ladder-head-level', naming: true },
  { cell: 'period', head: '期間', hint: '（プリセット）', className: 'dash-ladder-head-period', naming: true },
  { cell: 'source', head: 'ソース', className: 'dash-ladder-head-source', naming: true },
]);

/** 水準情報のセル数（naming を宣言した列の数）。 */
const NAMING_CELLS = COLUMNS.filter((column) => column.naming).length;

/** 現在値行（§4.1）が畳む列数＝価格列の**前**と**後ろ**。COLUMNS から導く。
 *
 *  ここを定数で持つと、列を足すたびに列の一覧と別の場所を手で数え直すことになる
 *  （実際に 2026-08-30 の「差」列・2026-09-06 の「MP」列で 2 度発生した）。数え違いは
 *  版面の列ずれとして現れるので、列の一覧を唯一源にして数え直しの機会そのものを無くす。 */
const PRICE_COLUMN_AT = COLUMNS.findIndex((column) => column.cell === 'price');
const CURRENT_HEAD_CELLS = PRICE_COLUMN_AT;
const CURRENT_TAIL_CELLS = COLUMNS.length - PRICE_COLUMN_AT - 1;

// ティック効果（依頼者指示 2026-08-31: **更新頻度**を方向色の濃度で表現し、2 秒でフェード
//   アウト）・残光の賞味期限・窓の上限・期間グループの切り出しは、いずれも版面ではなく
//   規則である。所有者は domain（tick_rate / next_target_glow / ladder_window / ladder_scope）で、
//   ここには数値を持たない。視覚のフェードの実体は CSS（dash-tick-fade・dash-row-glow）で、
//   クラスの後始末は animationend が行う（タイマーを持たない）。

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
 * @param {?Function} [opts.mpNormOf] (price) => number|null。行の価格に対する MP の密度。
 *   出所は live core から借りたプロファイル（依頼者承認 2026-09-06）。bin の決め方は
 *   domain の mp_bin.js が持ち、合成根が束ねて注入する（写しを持たない・View は描くだけ）。
 *   無ければ MP 列は空欄（借用できない環境で 0 を描かない）。
 * @returns {{mount: Function, render: Function, unmount: Function}}
 */
export function createReachSheetView({
  doc, periodAnnotator = null, now = null, mpNormOf = null,
} = {}) {
  let root = null;
  let tbody = null;
  let message = null;
  let windowNote = null;
  /** 表示範囲の状態機械（選択中の時間足 ＋ 全期間モード）。規則は domain が持ち、View は
   *  押されたことを伝えて答えを描くだけ。版面を畳んでも選択は残す（unmount で捨てない）。 */
  const scope = createLadderScope({ timeframes: DASHBOARD_TIMEFRAMES });
  /** 切替の再描画用に直近の応答を保つ（切替は**発行を生まない**——描き直すだけ）。 */
  let lastResponse = null;
  /** 操作子の版面（選択が変われば直近の応答を描き直す＝発行は生まない）。 */
  const selectors = createLadderSelectorsView({
    doc,
    scope,
    timeframes: DASHBOARD_TIMEFRAMES,
    onChange: () => { if (lastResponse) render(lastResponse); },
  });
  /** 走査域の実体（fitWindow が高さを実測する対象）。 */
  let scrollBox = null;
  /** 器の実高に適合させた窓の半径（null＝未適合＝WINDOW_RADIUS を使う）。 */
  let fittedRadius = null;
  /** fitWindow の再入ガード（適合の描き直しの中で再適合を測らない）。 */
  let fitting = false;
  /** 直近の現在値（ティックの上下判定用）。 */
  let lastCurrentPrice = null;
  /** 直近に動いた向き（'up' | 'down' | null＝まだ動きを見ていない）。 */
  let currentDirection = null;
  /** ティック効果の濃度（更新頻度 → 濃度の規則は domain/tick_rate.js が持つ）。 */
  const tickRate = createTickRateMeter();
  /** 現在値が最後に変わった時刻（unix 秒・注入時計で観測）。null＝時計なし or 未観測。 */
  let lastUpdateAt = null;
  /** なめらか再生の外部価格（唯一の書き手・依頼者指示 2026-08-31）。null＝未供給
   *  ＝従来どおり応答の current_price を表示。 */
  let externalPrice = null;
  /** なめらか再生の台帳と距離・差の式（domain/smooth_ladder.js が所有）。差の隣接は
   *  サーバの全行順で決まるため、可視外の行も台帳に載る。 */
  const smoothLedger = createSmoothLedger();
  /** 可視行の書き換え先 {fullIndex, priceEl, distanceEl, gapEl}（render で張り直す）。 */
  let levelRowRefs = [];
  /** buildPriceCell が直近に作った価格の文字（buildLevelRow が参照を拾う）。 */
  let builtPriceTextEl = null;
  /** 「次のターゲット」印の移動 → 行の残光の予定表（domain/next_target_glow.js が所有）。 */
  const glow = createNextTargetGlow();
  /** MP 借用の掲示文（null＝異常なし）。書き手は合成根（setMpNote）だけ。 */
  let mpNote = null;
  /** 現在値行のその場書き換え先（毎 tick の表再構築を避ける）。 */
  let currentRowEl = null;
  let currentPriceEl = null;
  let currentLabelCell = null;
  let currentUpdateEl = null;

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
    head.appendChild(el('h2', { className: 'dash-sheet-title', textContent: '価格ラダー' }));
    // リード文（説明の段落）は出さない（依頼者指示 2026-08-31: 削除・第 2 表と同じ）。
    // 期間と時間足の操作子は ladder_selectors_view が組んで所有する（版面の並びは不変）。
    head.appendChild(selectors.build());
    selectors.sync();   // 初期の見た目も選択状態（唯一源）から導く（再 mount でもずれない）。
    panel.appendChild(head);

    const scroll = el('div', { className: 'dash-scroll' });
    scrollBox = scroll;
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
    // 窓の掲示欄。窓の外の水準は建てない（WINDOW_RADIUS）ため、外に何本続いているかを
    //   ここで必ず掲示する（隠れた行が「存在しない」と読める版面にしない）。
    windowNote = el('p', { className: 'dash-ladder-window-note' });
    panel.appendChild(windowNote);
    root.appendChild(panel);

    root.appendChild(buildLegend());

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
    legend.appendChild(item('dash-legend-swatch-pending', '現在値より上（未到達＝抵抗側）'));
    // 差は独立列になり見出し（差・直前行と）が意味を持つため、凡例からは外した
    //   （依頼者指示 2026-08-30・同じ説明を 2 か所に置かない）。
    return legend;
  }

  /** 地平の印（依頼者指示 2026-08-31: [短][中][長] の 1 文字を四角で囲み、上＝赤・下＝緑。
   *  最大 3 段でも 1 行に収まる）。向きは**距離の符号**で決まる。地平の名前はツールチップが
   *  持つ（1 文字表記で情報を落とさない）。 */
  function buildMarks(marks, distance) {
    const holder = el('span', { className: 'dash-ladder-marks', dataset: { cell: 'marks' } });
    const list = Array.isArray(marks) ? marks : [];
    if (list.length === 0) {
      return holder;
    }
    const side = Number(distance) >= 0 ? 'up' : 'down';
    for (const horizon of HORIZONS) {
      if (!list.includes(horizon.key)) continue;
      holder.appendChild(el('b', {
        className: `dash-ladder-next dash-ladder-next-${side}`,
        textContent: horizon.label.charAt(0),
        title: `${horizon.label}の次のターゲット（${side === 'up' ? '上' : '下'}）`,
      }));
    }
    return holder;
  }

  /** 予約済みの行発光のうち、表示時刻に達したものを可視行へ乗せる（render 直後と
   *  なめらか再生の tick 適用時の両方から呼ばれる＝render の合間でも点灯する）。
   *  「いつ・どれを一度だけ」は予定表（domain）が決め、ここは乗せるだけ。 */
  function applyDueRowGlows() {
    if (glow.size() === 0 || levelRowRefs.length === 0) {
      return;
    }
    const clockNow = typeof now === 'function' ? now() : null;
    if (clockNow === null) {
      return;
    }
    for (const due of glow.due(clockNow)) {
      const ref = levelRowRefs.find((r) => r.rowKey === due.owner);
      if (!ref) {
        continue;   // 窓の外＝光らせる先が無い（記録は寿命まで保つ）。
      }
      startRowGlow(ref.tr, due.elapsed);
      glow.markApplied(due.mark);
    }
  }

  /** 行全体の残光を（再）始動する（色は地平によらず一色・依頼者指示 2026-08-31
   *  「グレーは分かりにくい」）。経過を負の delay（--row-glow-delay・セルの ::after へ継承）
   *  で引き継ぐ＝再描画してもフェードは元の残り時間から続く。 */
  function startRowGlow(tr, elapsed) {
    tr.classList.remove('dash-ladder-row-moved');
    if (typeof tr.offsetWidth === 'number') {
      void tr.offsetWidth;   // 実 DOM でアニメを再始動させる（fake DOM では最終状態のみ意味）。
    }
    const delay = `-${Math.max(0, elapsed)}s`;
    if (typeof tr.style.setProperty === 'function') {
      tr.style.setProperty('--row-glow-delay', delay);
    } else {
      tr.style['--row-glow-delay'] = delay;
    }
    tr.classList.add('dash-ladder-row-moved');
  }

  /** 価格セル（3 分割の背景＋価格の文字）。差は独立列（依頼者指示 2026-08-30）。 */
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
    builtPriceTextEl = el('span', {
      className: 'dash-ladder-price-text',
      textContent: formatPrice(row.price),
      dataset: { cell: 'price' },
    });
    cell.appendChild(builtPriceTextEl);
    return cell;
  }

  /**
   * MP セル（依頼者承認 2026-09-06: 価格ラダーの MP 列）。
   *
   * 密度の出所は**注入された `mpNormOf(price)`**（依頼者承認 2026-09-06「MP 列＝ライブ MP の
   * 借用」・裁定 6）。合成根が live core から借りたプロファイルを domain の bin 写像
   * （mp_bin.js）へ通して渡す。View は受けた値を描くだけで、どの bin かも、どこから来たかも
   * 知らない（periodAnnotator と同型）。`/reach_sheet` の `mp` 欄は**読まない**——第 1 段階では
   * サーバ側の供給を温存する（dormant）ため欄は残るが、版面の出所ではない。
   *
   * 中身は**横バー 1 本**だけで、数値は出さない（依頼者指示 2026-09-06「色の濃度とグラフで
   * 表示しろ」）。長さ ∝ norm・色は heat_scale の `colorForDensity`（色の唯一源。ここで色を
   * 作らない）。密度は「量」なので**単調**写像を使う——分位 `p` の双極写像（`colorForP`）へ
   * 載せると濃さが量を表さない（norm ≈ 0.5 が透明・norm < 0.5 は低いほど濃い）。
   * 密度が無い（範囲外・借用なし・非有限）ときは**バーを作らない**——0 幅のバーや無色のバーを
   * 置くと「密度が最小」と読めてしまう（§5.5.5 の正当な空と同じ規律）。
   * 更新粒度は 1m バー確定なので、なめらか再生（refreshSmoothNumbers）はここを書き換えない。
   */
  function buildMpCell(row) {
    const cell = el('td', {
      className: 'dash-ladder-mp',
      dataset: { cell: 'mp' },
      title: MP_CELL_TITLE,
    });
    if (typeof mpNormOf !== 'function') {
      return cell;   // 借用の口が無い環境（単体起動）は空欄のまま（無言の 0 を描かない）。
    }
    // `null` を Number へ通すと 0 になり、「密度なし」が「密度 0」として最小のバーで
    //   描かれてしまう（読み手には最も薄い密度に見える）。先に不在を弾く。
    const supplied = mpNormOf(row.price);
    if (supplied === null || supplied === undefined) {
      return cell;
    }
    const norm = Number(supplied);
    if (!Number.isFinite(norm)) {
      return cell;
    }
    const bar = el('span', { className: 'dash-ladder-mp-bar' });
    bar.style.width = `${norm * 100}%`;
    bar.style.backgroundColor = colorForDensity(norm);
    cell.appendChild(bar);
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

    // 次のターゲット（地平の印）→ 距離 の順（依頼者指示 2026-08-30「順番を逆に」）。
    const nextCell = el('td', { className: 'dash-ladder-next-cell' });
    nextCell.appendChild(buildMarks(row.horizon_marks, row.distance));
    tr.appendChild(nextCell);

    const distanceCell = el('th', { className: 'dash-ladder-distance-cell', scope: 'row' });
    const distanceTextEl = el('span', {
      className: 'dash-ladder-distance',
      textContent: formatDistance(row.distance),
      dataset: { cell: 'distance' },
    });
    distanceCell.appendChild(distanceTextEl);
    tr.appendChild(distanceCell);

    tr.appendChild(buildPriceCell(row));
    tr.appendChild(buildMpCell(row));
    const gapCell = el('td', {
      className: 'dash-ladder-gap',
      textContent: formatGap(row.gap_to_previous),
      dataset: { cell: 'gap' },
    });
    tr.appendChild(gapCell);
    // なめらか再生の書き換え先（依頼者指示 2026-08-31: 距離・価格・差もライブチャート粒度）。
    //   distance の文字は下の distanceCell 内 span（既に作成済み）を使う。
    const fullIndex = smoothLedger.indexOf(rowKeyOf(row));
    if (fullIndex !== undefined) {
      levelRowRefs.push({
        fullIndex,
        rowKey: rowKeyOf(row),
        tr,
        priceEl: builtPriceTextEl,
        distanceEl: distanceTextEl,
        gapEl: gapCell,
      });
    }
    // 到達時間（定義 C＝最初の接点の時刻・§6.2）。未到達は空欄。履歴の先頭で
    //   切れているとき（truncated）は断定を避ける限定を title へ持つ（§9-5 の規約を保つ）。
    const reach = row.reach ?? null;
    const reached = !!(reach && reach.reached === true
      && reach.since_time !== null && reach.since_time !== undefined);
    const reachCell = el('td', {
      className: 'dash-ladder-reach-time',
      textContent: reached ? formatReachTimestamp(reach.since_time) : '',
      dataset: { cell: 'reach_time' },
    });
    if (reached && reach.truncated === true) {
      reachCell.title = '履歴の先頭で切れているため、実際にはこれ以前から到達している可能性があります';
    }
    tr.appendChild(reachCell);
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
    if (naming.level_note) {
      // σ 帯の宣言分位は正規換算（唯一の仮定）。仮定を無言にしない（title へ明記）。
      levelCell.title = String(naming.level_note);
    }
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

  /** 現在値の独立行（§4.1）。
   *
   *  「全時間足で同一の 1 点」の説明文は出さない（依頼者指示 2026-08-30: ラベル削除）。
   *  地色は直近に動いた向き（依頼者指示 2026-08-31: 上＝緑・下＝赤・**中間色はなし**）。
   *  色の実体は dashboard.css（--tick-up-bg / --tick-down-bg）。向きをまだ見ていない
   *  起動直後だけ従来の反転帯（--ink）＝向きを発明しない。 */
  function buildCurrentRow(currentPrice) {
    // なめらか再生が有効なら外部価格が唯一の書き手（参照実装 LiveTickPlayer の
    //   suppressPriceUpdate と同じ規約・依頼者指示 2026-08-31）。
    const shown = externalPrice !== null ? externalPrice : currentPrice;
    const direction = currentDirection === null || tickRate.strength() <= 0
      ? '' : ` dash-ladder-current-${currentDirection}`;
    // 現在値の文字色は直近の向きに追従して**残る**（依頼者指示 2026-08-31。フェードで消える
    //   発光クラスとは別の恒常クラス＝animationend では外さない）。
    const dirClass = currentDirection === null ? '' : ` dash-ladder-current-dir-${currentDirection}`;
    const tr = el('tr', { className: `dash-ladder-row dash-ladder-current${direction}${dirClass}` });
    if (direction) {
      setTickStrengthOn(tr);
    }
    // フェード完了（2s・CSS の dash-tick-fade）で効果のクラスを外す。タイマーを持たずに
    //   CSS の時間へ正確に同期する（外し損ねたクラスは次の再構築で発光を再生してしまう）。
    if (typeof tr.addEventListener === 'function') {
      tr.addEventListener('animationend', () => {
        tickRate.fade();
        tr.classList.remove('dash-ladder-current-up');
        tr.classList.remove('dash-ladder-current-down');
      });
    }
    // 現在値行は価格より前の列（次のターゲット・距離）をまとめる（列の分離・依頼者指示
    //   2026-08-30）。数は COLUMNS から導く（CURRENT_HEAD_CELLS）。
    tr.appendChild(el('th', {
      scope: 'row', colSpan: CURRENT_HEAD_CELLS, textContent: '現在値',
      dataset: { cell: 'distance' },
    }));
    const priceCell = el('td', { dataset: { cell: 'price' } });
    currentPriceEl = el('b', {
      className: 'dash-ladder-current-price',
      textContent: formatPrice(shown),
    });
    priceCell.appendChild(currentPriceEl);
    tr.appendChild(priceCell);
    const labelCell = el('td', {
      // 価格より後ろの列（MP・差・到達時間・時間足＋水準情報）をまとめて 1 セルに。
      //   数は COLUMNS から導く（CURRENT_TAIL_CELLS）＝列を足しても数え直さない。
      //   ラベル文は置かない（上記）。
      colSpan: CURRENT_TAIL_CELLS,
      dataset: { cell: 'label' },
    });
    currentUpdateEl = null;
    if (lastUpdateAt !== null) {
      // 最終更新日時（依頼者指示 2026-08-31）。表記は到達時間と同じ唯一源（UTC）。
      currentUpdateEl = el('span', {
        className: 'dash-ladder-current-update',
        textContent: `UPDATE:${formatReachTimestamp(lastUpdateAt)}`,
        dataset: { cell: 'update' },
      });
      labelCell.appendChild(currentUpdateEl);
    }
    currentLabelCell = labelCell;
    tr.appendChild(labelCell);
    currentRowEl = tr;
    return tr;
  }

  /**
   * なめらか再生の 1 tick を現在値行へ適用する（依頼者指示 2026-08-31: ライブチャート仕様
   * ＝LiveTickPlayer の 12 秒固定遅延・100ms 粒度再生に合わせる）。
   *
   * 呼び手は composition root（player の renderer 注入）。以後この価格が現在値表示の
   * **唯一の書き手**になり、1s の応答描画は行の構成（並び・距離）だけを更新する
   * （数値の再計算はしない——距離・並びはサーバの値のまま。arch-spec §9）。
   *
   * 同値の tick は何も作らない（作ってから捨てる仕事を発生させない・絶対命令 §4.1）。
   * 更新は現在値行の**その場書き換え**（文字と効果クラスのみ）で、表は再構築しない。
   */
  function updateCurrentPrice(price) {
    const value = Number(price);
    if (!Number.isFinite(value)) {
      return;
    }
    if (externalPrice === value) {
      return;   // 変化なし＝DOM もタイムスタンプも触らない。
    }
    const previous = externalPrice;   // 外部価格どうしでのみ比較（12 秒遅延の系列内で閉じる）。
    externalPrice = value;
    lastUpdateAt = typeof now === 'function' ? now() : null;
    if (previous !== null) {
      currentDirection = value > previous ? 'up' : 'down';
      registerTickEffect();
    }
    if (!currentRowEl) {
      return;   // まだ版面が無い（初回応答前）。値は次の描画が拾う。
    }
    currentPriceEl.textContent = formatPrice(value);
    if (lastUpdateAt !== null) {
      if (!currentUpdateEl) {
        currentUpdateEl = el('span', {
          className: 'dash-ladder-current-update', dataset: { cell: 'update' },
        });
        currentLabelCell.appendChild(currentUpdateEl);
      }
      currentUpdateEl.textContent = `UPDATE:${formatReachTimestamp(lastUpdateAt)}`;
    }
    if (previous !== null && currentDirection !== null && tickRate.strength() > 0) {
      // 現在値の文字色の恒常クラス（向きが変わったときだけ付け替える）。
      const otherDir = currentDirection === 'up' ? 'down' : 'up';
      currentRowEl.classList.remove(`dash-ladder-current-dir-${otherDir}`);
      currentRowEl.classList.add(`dash-ladder-current-dir-${currentDirection}`);
      // 更新の瞬間に方向色を頻度の濃度で乗せ、CSS の 2s フェードを再始動する
      //   （クラスを外す → reflow → 濃度 → 付け直す。fake DOM では最終状態のみ意味）。
      currentRowEl.classList.remove('dash-ladder-current-up');
      currentRowEl.classList.remove('dash-ladder-current-down');
      if (typeof currentRowEl.offsetWidth === 'number') {
        void currentRowEl.offsetWidth;
      }
      setTickStrengthOn(currentRowEl);
      currentRowEl.classList.add(`dash-ladder-current-${currentDirection}`);
    }
    // 現在値が動けば全行の距離も動く（距離 = 水準価格 − 現在値・依頼者指示 2026-08-31）。
    refreshSmoothNumbers();
  }

  /** 更新 1 回を頻度の計器へ入れる（規則は domain/tick_rate.js・時計は注入のまま）。 */
  function registerTickEffect() {
    tickRate.register(typeof now === 'function' ? now() : null);
  }

  /** 濃度をカスタムプロパティで渡す（色の値そのものは書かない＝色の唯一源を侵さない）。 */
  function setTickStrengthOn(row) {
    const value = String(Math.round(tickRate.strength()));
    if (typeof row.style.setProperty === 'function') {
      row.style.setProperty('--tick-strength', value);
    } else {
      row.style['--tick-strength'] = value;
    }
  }

  /** 変わった文字だけ書く（同値は DOM に触らない＝作ってから捨てる仕事を生まない）。 */
  function setTextIfChanged(target, text) {
    if (target && target.textContent !== text) {
      target.textContent = text;
    }
  }

  /**
   * なめらか再生の数値（価格・距離・差）を可視行へ書き直す（依頼者指示 2026-08-31）。
   *
   * **式は持たない**——距離・差の定義（サーバの参照定義 domain/price_ladder.py と同じ）は
   * domain/smooth_ladder.js が唯一源で、ここは受け取った数を文字にして書くだけである。
   */
  function refreshSmoothNumbers() {
    // 発光の表示時刻は render の合間に来ることが多い。tick 適用（100ms 粒度）を契機に予約を
    //   確認する＝発光が次の内容変化を待たされない。
    applyDueRowGlows();
    if (externalPrice === null) {
      return;
    }
    for (const ref of levelRowRefs) {
      const numbers = smoothLedger.numbersAt(ref.fullIndex, externalPrice);
      if (numbers === null) {
        continue;
      }
      setTextIfChanged(ref.priceEl, formatPrice(numbers.price));
      setTextIfChanged(ref.distanceEl, formatDistance(numbers.distance));
      setTextIfChanged(ref.gapEl, numbers.gap === null ? '' : formatGap(numbers.gap));
    }
  }

  /**
   * なめらか再生の 1 tick ぶんの水準価格を流す（依頼者指示 2026-08-31: 距離・価格・差も
   * ライブチャートと同じ更新粒度）。
   *
   * @param {Function} lookup (instance_key 配列, series 名) => 末尾値 | undefined。
   *   値の実体は `/live_ticks` の tails（サーバ計算）で、合成根が閉じ込めて渡す
   *   （View は tails のキー構造を知らない）。
   */
  function updateLevelValues(lookup) {
    if (externalPrice === null || typeof lookup !== 'function' || smoothLedger.size() === 0) {
      return;
    }
    smoothLedger.applyTails(lookup);
    refreshSmoothNumbers();
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
    lastResponse = response;   // 範囲の切替は直近の応答を描き直す（発行を生まない）。
    while (tbody.children.length > 0) {
      tbody.removeChild(tbody.children[0]);
    }
    if (!response || response.ok !== true) {
      const reason = response && response.error && response.error.message
        ? response.error.message
        : 'シートを取得できませんでした';
      message.textContent = reason;
      return;
    }
    // ティック方向（依頼者指示 2026-08-31: 上＝緑・下＝赤の地色・中間色なし）。向きは直近に
    //   動いた向きのまま保つ（更新の無い描画周期でも落とさない＝地色は状態）。なめらか再生が
    //   有効（externalPrice あり）のときは updateCurrentPrice が向きの唯一の書き手であり、
    //   ここでは応答の価格と比較しない（応答は遅延なし・再生は 12 秒遅延の別系列。比較すると
    //   毎描画が偽の更新になる）。
    const currentPrice = Number(response.current_price);
    if (externalPrice === null) {
      if (Number.isFinite(currentPrice) && lastCurrentPrice !== null
          && currentPrice !== lastCurrentPrice) {
        currentDirection = currentPrice > lastCurrentPrice ? 'up' : 'down';
        registerTickEffect();
      } else {
        // 更新の無い描画周期では向きの状態だけ落とす（視覚フェードは CSS の 2 秒が完結させる）。
        tickRate.fade();
      }
      if (Number.isFinite(currentPrice)) {
        if (lastCurrentPrice === null || currentPrice !== lastCurrentPrice) {
          // 最終更新日時（依頼者指示 2026-08-31: 現在値行へ UPDATE:… を追記）。時計は注入
          //   （View は時計を持たない規約のまま）。初回の観測も「更新」として記録する。
          lastUpdateAt = typeof now === 'function' ? now() : null;
        }
        lastCurrentPrice = currentPrice;
      }
    }
    const allRows = Array.isArray(response.rows) ? response.rows : [];
    // 次のターゲット印の移動検出（依頼者承認 2026-08-31）。突合は表の構築前に 1 回、全行で
    //   （移動先が窓の外なら光らないだけ）。予定表は domain が持つ＝時計は注入のまま渡す。
    glow.track(allRows, typeof now === 'function' ? now() : null);
    // なめらか再生の水準台帳（依頼者指示 2026-08-31: 距離・価格・差もライブチャート粒度）。
    //   種はサーバ価格。並びはサーバの全行順（差の隣接はこの順で決まる・絞り込みと無関係）。
    smoothLedger.reset(allRows);
    levelRowRefs = [];
    // 契約のズレ（未知の地平キー）は色の不在として紛れるので、必ず文字で掲示する。
    const unknown = unknownHorizonKeys(allRows);
    // 掲示は 1 か所へまとめる（欄を増やすと読み手が見る場所が散る）。MP の借用失敗も
    //   ここへ載せる——列が空のとき「密度が無い相場」と「借りられなかった」は版面で
    //   区別が付かないため、無言で空にしない（設計書 §5.2 / §7 の無言縮退の禁止）。
    const notes = [];
    if (unknown.length > 0) {
      notes.push(`未知の地平キーが応答に含まれています: ${unknown.join(', ')}（対象は ${HORIZON_KEYS.join(' / ')}）`);
    }
    if (mpNote) {
      notes.push(mpNote);
    }
    message.textContent = notes.join(' / ');

    // 絞り込みと現在値行の位置は状態機械（domain/ladder_scope.js）が決める。並びはサーバの
    //   まま（順序を再計算しない）。全選択のときはフィルタを通さない（未知の足も従来どおり）。
    const rows = scope.filter(allRows);
    const at = scope.currentIndexOf(rows, response.current_index);
    // 現在値を中心とした窓だけを建てる（縦スクロールを不要にする）。半径は器の実高への
    //   適合値（fitWindow）を優先し、未適合は上限 WINDOW_RADIUS。窓の外の行はここで
    //   **建てない**——建ててから隠すと捨てる色計算が毎描画発生する。
    //   全期間は窓なし（全量。従来の表示に戻す選択肢）。
    const radius = fittedRadius ?? WINDOW_RADIUS;
    const { start, end } = sliceWindow({
      total: rows.length, at, radius, windowless: scope.isWindowless(),
    });
    const visible = rows.slice(start, end);
    const currentAt = at - start;
    visible.forEach((row, index) => {
      if (index === currentAt) {
        tbody.appendChild(buildCurrentRow(response.current_price));
      }
      tbody.appendChild(buildLevelRow(row));
    });
    if (currentAt >= visible.length) {
      tbody.appendChild(buildCurrentRow(response.current_price));
    }
    applyDueRowGlows();   // 予約済みの行発光を新しい版面へ乗せ直す（負 delay で継続）。
    renderWindowNote(rows.length, radius, start, rows.length - end);
    // 絞り込みで 1 本も残らないことは正当な状態だが、無言の空にはしない（掲示する）。
    if (rows.length === 0 && allRows.length > 0) {
      windowNote.textContent = '選択中の範囲・時間足に表示できる水準がありません';
    }
    fitWindow();
  }

  /**
   * 窓の半径を器の実高へ適合させる（依頼者指示「縦スクロールは必要なし」を画面の高さに
   * 依らず成立させる）。初回描画で溢れていたときだけ、行の実高から収まる本数を計算して
   * **一度だけ**描き直す。以後の周期描画は適合済みの半径で建てるため、描き直しは
   * 繰り返されない（縮める方向にしか動かない・再入ガードつき）。
   * 実高を測れない環境（テストダブル）は何もしない＝WINDOW_RADIUS のまま（検定は決定的）。
   *
   * ここが持つのは**測定**だけである（clientHeight / scrollHeight / 行の実高）。測った数から
   * 半径を決める算術は domain/ladder_window.js の `fitRadius` が唯一源で、View は数を渡して
   * 答えを受け取る。
   */
  function fitWindow() {
    if (fitting || scope.isWindowless() || !scrollBox || !tbody) {
      return;
    }
    const first = tbody.children[0];
    const next = fitRadius({
      boxHeight: scrollBox.clientHeight,
      contentHeight: scrollBox.scrollHeight,
      rowHeight: first && typeof first.getBoundingClientRect === 'function'
        ? first.getBoundingClientRect().height : 0,
      rowCount: tbody.children.length,
      currentRadius: fittedRadius ?? WINDOW_RADIUS,
    });
    if (next === null) {
      return;   // 溢れていない・測れない・縮まらない（拡縮の往復で毎描画作り直さない）。
    }
    fittedRadius = next;
    if (lastResponse) {
      fitting = true;
      try {
        render(lastResponse);
      } finally {
        fitting = false;
      }
    }
  }

  /** 窓の掲示（上下に何本続いているか）。全量が窓に収まるときは何も出さない。 */
  function renderWindowNote(total, radius, hiddenAbove, hiddenBelow) {
    if (hiddenAbove === 0 && hiddenBelow === 0) {
      windowNote.textContent = '';
      return;
    }
    windowNote.textContent = `全 ${total} 本中、現在値の前後 ${radius} 本を表示`
      + `（この上に ${hiddenAbove} 本・下に ${hiddenBelow} 本）`;
  }

  /** 版面を畳む（共有の器へ何も残さない）。 */
  function unmount() {
    if (root && root.parentNode && typeof root.parentNode.removeChild === 'function') {
      root.parentNode.removeChild(root);
    }
    root = null;
    tbody = null;
    message = null;
    windowNote = null;
    selectors.reset();
    lastResponse = null;
    scrollBox = null;
    fittedRadius = null;
    lastCurrentPrice = null;
    currentDirection = null;
    tickRate.reset();
    lastUpdateAt = null;
    externalPrice = null;
    glow.clear();
    smoothLedger.clear();
    levelRowRefs = [];
    builtPriceTextEl = null;
    currentRowEl = null;
    currentPriceEl = null;
    currentLabelCell = null;
    currentUpdateEl = null;
  }

  /**
   * MP 借用の掲示文を差し替える（null で解除）。次の描画から効く。
   *
   * 呼び手は合成根だけ（借用の成否を知っているのはそこだけである）。View は文言を
   * 組み立てない——理由の文言は失敗を観測した層が持つ（mp_profile_client の error.message）。
   */
  function setMpNote(text) {
    mpNote = text || null;
  }

  return { mount, render, unmount, updateCurrentPrice, updateLevelValues, setMpNote };
}
