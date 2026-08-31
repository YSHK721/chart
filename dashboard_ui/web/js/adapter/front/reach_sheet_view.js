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

import { colorForP } from './heat_scale.js';
import { createElementWith } from './dom_element.js';
// 価格表記の唯一源（第 2 表と共有・写しを持たない）。
import { formatPrice, formatReachTimestamp } from './format.js';
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
  // 次のターゲット（地平の印）は独立列・距離より先（依頼者指示 2026-08-30
  //   「距離 · 次のターゲットも各列に分離しろ」→「順番を逆に」）。
  { cell: 'next', head: '次のターゲット', className: 'dash-ladder-head-next' },
  { cell: 'distance', head: '距離', className: 'dash-ladder-head-distance' },
  { cell: 'price', head: '価格', className: 'dash-ladder-head-price' },
  // 差は独立列（依頼者指示 2026-08-30「価格と直前行の差を分離して各列に」）。
  { cell: 'gap', head: '差', hint: '（直前行と）', className: 'dash-ladder-head-gap' },
  // 到達時間（依頼者指示 2026-08-30: 差と時間足の間・YYYY/MM/DD HH:MM:SS・UTC）。
  { cell: 'reach_time', head: '到達時間', className: 'dash-ladder-head-reach-time' },
  { cell: 'timeframe', head: '時間足', className: 'dash-ladder-head-timeframe' },
  { cell: 'name', head: '指標名', className: 'dash-ladder-head-name' },
  { cell: 'level', head: '水準', className: 'dash-ladder-head-level' },
  { cell: 'period', head: '期間', hint: '（プリセット）', className: 'dash-ladder-head-period' },
  { cell: 'source', head: 'ソース', className: 'dash-ladder-head-source' },
]);

/** 水準情報のセル数（現在値行の colSpan が数え直しを忘れないための唯一源）。 */
const NAMING_CELLS = 4;

/** ティック効果（依頼者指示 2026-08-31: **更新頻度**を方向色の濃度で表現し、2 秒で
 *  フェードアウト（同日指示で 1 秒 → 2 秒）。先の「単色・中間色なし」を本指示が置換）。
 *
 *  濃度 = clamp(直近 TICK_RATE_WINDOW_SECONDS 秒の更新回数/秒 ÷ TICK_FULL_RATE, 最小, 100)%。
 *  TICK_FULL_RATE は「これ以上で最濃」となる更新頻度。再生粒度は 100ms＝最大 10 回/秒で、
 *  その半分（5 回/秒）を最濃に採った。下限は「動いたことが見える」最小濃度。
 *  フェードの時間の唯一源は CSS（dash-tick-fade・2s）。クラスの後始末は animationend。 */
const TICK_RATE_WINDOW_SECONDS = 2;
const TICK_FULL_RATE = 5;
const TICK_MIN_STRENGTH = 25;

/** 次のターゲット印の移動先の残光（依頼者承認 2026-08-31 →「移動した価格帯の**行全体**に
 *  色を乗せてフェードアウトせよ」で行全体へ変更）。印（horizon_marks）の持ち主行が前回
 *  応答から変わったとき、**移動先の行全体**へ地平色を乗せ、この秒数かけてフェードアウト
 *  する。視覚のフェードの実体は CSS（dash-row-glow・同じ 8s）で、本定数は
 *  「効果がもう終わった印」を再適用しないための賞味期限。再描画では負の animation-delay
 *  （--row-glow-delay）で経過を引き継ぐ（途切れ・再点滅を作らない）。 */
const NEXT_MOVE_FADE_SECONDS = 8;

/** 発光の適用を遅らせる秒数。印の移動はサーバの**無遅延**価格で検出されるが、版面の表示
 *  （現在値・水準・チャート）は LiveTickPlayer の **12 秒固定遅延**の再生系列に統一されて
 *  いる（依頼者指示 2026-08-31）。同期させないと「画面上で価格が跨ぐ 12 秒前」に点灯して
 *  跨ぐ頃には消えている（実測 2026-08-31: 体感できない）。値の出所は参照実装
 *  live_tick_player.js の DELAY_MS（12000ms・非公開 const のため写す。変えるときは両方）。 */
const NEXT_MOVE_DELAY_SECONDS = 12;

/** 現在値を中心に表示する水準の本数（片側・依頼者指示 2026-08-30「表示本数が多いので調整。
 *  縦スクロールは必要なし。現在を中心に」）の**上限**。実際の半径は初回描画後に器の実高から
 *  適合させる（fitWindow・縦スクロールが出ない本数まで縮める）。窓の外は**建てない**
 *  （建ててから隠すと「作ってから捨てる」色計算が毎描画発生する・絶対命令 §4.1）。
 *  窓の外の存在は window-note が掲示する（無言の縮退禁止）。 */
const WINDOW_RADIUS = 15;

/** 期間グループ（依頼者指示 2026-08-30「切り替えできるようにしろ。短期・中期・長期・オール」→
 *  「オール」は「全期間」へ改称 → 「期間も含めて、時間足も**複数選択**できるように」）。
 *
 *  複数選択の区分として意味を持つよう、§4.3 の閾値（1h・1D）で**互いに素な時間足の帯**に
 *  区切る（短期＝1h 未満 / 中期＝1h 以上 1D 未満 / 長期＝1D 以上）。§4.3 の**地平**
 *  （短期＝すべて…・行の「次のターゲット」印）は別概念のまま変えない——地平は累積の候補集合、
 *  こちらは表示フィルタの区分である。期間ボタンはそのグループの時間足をまとめてトグルし、
 *  時間足ピルと**同一の選択集合**を操作する（別のフィルタ軸を作らない）。
 *  グループの中身は DASHBOARD_TIMEFRAMES（唯一源）から切り出す（写しを持たない）。 */
const TF_GROUPS = (() => {
  const mediumAt = DASHBOARD_TIMEFRAMES.indexOf('1h');
  const longAt = DASHBOARD_TIMEFRAMES.indexOf('1D');
  return Object.freeze([
    { key: 'short', label: '短期', tfs: DASHBOARD_TIMEFRAMES.slice(0, mediumAt) },
    { key: 'medium', label: '中期', tfs: DASHBOARD_TIMEFRAMES.slice(mediumAt, longAt) },
    { key: 'long', label: '長期', tfs: DASHBOARD_TIMEFRAMES.slice(longAt) },
  ]);
})();

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
export function createReachSheetView({ doc, periodAnnotator = null, now = null } = {}) {
  let root = null;
  let tbody = null;
  let message = null;
  let windowNote = null;
  /** 選択中の時間足（唯一の選択状態。期間ボタンも時間足ピルもこの集合を操作する）。
   *  既定は全選択。全選択のときはフィルタ自体を通さない＝未知の時間足の行も従来どおり出る
   *  （絞ったときだけ、選んだ足の行に限定する）。 */
  let selectedTfs = new Set(DASHBOARD_TIMEFRAMES);
  /** 全期間（窓なし全量）モード。選択を操作した瞬間に解除される。 */
  let windowless = false;
  let tfButtons = [];
  /** 切替の再描画用に直近の応答を保つ（切替は**発行を生まない**——描き直すだけ）。 */
  let lastResponse = null;
  let scopeButtons = [];
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
  /** ティック効果の濃度（0〜100・更新頻度から算出）。0＝無色（反転帯のまま）。 */
  let tickStrength = 0;
  /** 直近の更新時刻（unix 秒・頻度の観測窓）。 */
  let tickTimes = [];
  /** 現在値が最後に変わった時刻（unix 秒・注入時計で観測）。null＝時計なし or 未観測。 */
  let lastUpdateAt = null;
  /** なめらか再生の外部価格（唯一の書き手・依頼者指示 2026-08-31）。null＝未供給
   *  ＝従来どおり応答の current_price を表示。 */
  let externalPrice = null;
  /** なめらか再生の水準価格（全行・サーバ並び順。差の計算は可視外の隣接行も要る）。
   *  各要素 {key, instanceKey, series, smooth}。smooth はサーバ価格を種に tails で上書き。 */
  let smoothRowsAll = [];
  /** rowKey → smoothRowsAll の添字。 */
  const fullIndexByKey = new Map();
  /** 可視行の書き換え先 {fullIndex, priceEl, distanceEl, gapEl}（render で張り直す）。 */
  let levelRowRefs = [];
  /** buildPriceCell が直近に作った価格の文字（buildLevelRow が参照を拾う）。 */
  let builtPriceTextEl = null;
  /** 次のターゲット印の前回の持ち主（markKey `horizon:side` → rowKey）。null＝初回。 */
  let lastMarkOwners = null;
  /** 移動した印の記録（markKey → {at: 発光を**表示する**時刻 unix 秒＝検出＋12s,
   *  owner: 移動先 rowKey, applied: 現在の版面へ適用済みか}）。 */
  const markMovedAt = new Map();
  /** 描画時点の時計（unix 秒・render の冒頭で 1 回だけ取る）。null＝時計なし。 */
  let renderNowSec = null;
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
    // 期間と時間足は 1 行に並べる（依頼者指示 2026-08-30。境界の余白は CSS の
    //   .dash-ladder-selectors の gap が持つ＝両グループの内側の間隔より一段広い）。
    const selectors = el('div', { className: 'dash-ladder-selectors' });
    selectors.appendChild(buildScopeBar());
    selectors.appendChild(buildTfBar());
    head.appendChild(selectors);
    syncSelectors();   // 初期の見た目も選択状態（唯一源）から導く（再 mount でもずれない）。
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

  /** 期間の複数選択バー（短期 / 中期 / 長期 ＋ 全期間）。
   *  期間ボタンは自分のグループの時間足を selectedTfs へまとめてトグルする（時間足ピルと
   *  同一の選択集合＝フィルタ軸を 2 本にしない）。全期間は窓なし全量のモード。
   *  切替は**描き直すだけ**で発行を生まない（発行判定は sheet_poller の唯一責務のまま）。 */
  function buildScopeBar() {
    const bar = el('div', { className: 'dash-ladder-scope', role: 'group' });
    scopeButtons = TF_GROUPS.map((group) => {
      const button = el('button', {
        className: 'dash-ladder-scope-btn',
        type: 'button',
        textContent: group.label,
        dataset: { scope: group.key },
      });
      button.addEventListener('click', () => {
        windowless = false;
        const allOn = group.tfs.every((tf) => selectedTfs.has(tf));
        for (const tf of group.tfs) {
          if (allOn) selectedTfs.delete(tf); else selectedTfs.add(tf);
        }
        syncSelectors();
        if (lastResponse) render(lastResponse);
      });
      bar.appendChild(button);
      return button;
    });
    const allButton = el('button', {
      className: 'dash-ladder-scope-btn',
      type: 'button',
      textContent: '全期間',
      dataset: { scope: 'all' },
    });
    allButton.addEventListener('click', () => {
      // 全期間 = 全選択＋窓なし。もう一度押すと窓ありへ戻る（選択は全選択のまま）。
      windowless = !windowless;
      if (windowless) selectedTfs = new Set(DASHBOARD_TIMEFRAMES);
      syncSelectors();
      if (lastResponse) render(lastResponse);
    });
    bar.appendChild(allButton);
    scopeButtons.push(allButton);
    return bar;
  }

  /** 期間ボタン・全期間・時間足ピルの見た目を選択状態（唯一源）から導き直す。 */
  function syncSelectors() {
    for (const button of scopeButtons) {
      const group = TF_GROUPS.find((g) => g.key === button.dataset.scope);
      const active = group
        ? group.tfs.every((tf) => selectedTfs.has(tf))
        : windowless;   // 全期間ボタン。
      button.setAttribute?.('aria-pressed', String(active));
      if (active) button.classList.add('is-active'); else button.classList.remove('is-active');
    }
    for (const button of tfButtons) {
      const timeframe = button.dataset.timeframe;
      const tone = DASHBOARD_TIMEFRAMES.indexOf(timeframe);
      const on = selectedTfs.has(timeframe);
      button.setAttribute?.('aria-pressed', String(on));
      const pill = button.children[0];
      if (pill) {
        if (on) pill.classList.add(`dash-tf-r${tone}`);
        else pill.classList.remove(`dash-tf-r${tone}`);
      }
    }
  }

  /** 時間足の選択バー（依頼者指示 2026-08-30「時間足も選択できるように」。トグル・既定は
   *  全選択）。見た目は行の時間足ピル（dash-tf-pill・足別トーン）と同じ語彙で、外した足は
   *  トーンを外した無彩のピルにする（薄さで階層を作らない・規約 4）。切替は描き直すだけで
   *  発行を生まない。 */
  function buildTfBar() {
    const bar = el('div', { className: 'dash-ladder-tf-bar', role: 'group' });
    tfButtons = DASHBOARD_TIMEFRAMES.map((timeframe, tone) => {
      const button = el('button', {
        className: 'dash-ladder-tf-btn',
        type: 'button',
        dataset: { timeframe },
      });
      // 初期状態は selectedTfs から導く（再 mount しても選択が版面とずれない）。
      const initiallyOn = selectedTfs.has(timeframe);
      const pill = el('u', {
        className: initiallyOn ? `dash-tf-pill dash-tf-r${tone}` : 'dash-tf-pill',
        textContent: timeframe,
      });
      button.appendChild(pill);
      button.setAttribute?.('aria-pressed', String(initiallyOn));
      button.addEventListener('click', () => {
        windowless = false;
        if (selectedTfs.has(timeframe)) selectedTfs.delete(timeframe);
        else selectedTfs.add(timeframe);
        syncSelectors();
        if (lastResponse) render(lastResponse);
      });
      bar.appendChild(button);
      return button;
    });
    return bar;
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
    // 差は独立列になり見出し（差・直前行と）が意味を持つため、凡例からは外した
    //   （依頼者指示 2026-08-30・同じ説明を 2 か所に置かない）。
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

  /** 行の論理識別子（印の持ち主の同定用）。label は同名でも時間足で別行になりうるので併記。
   *  価格は含めない——水準の値が動いただけの行を「移動」と誤認しない。 */
  function rowKeyOf(row) {
    return `${row.timeframe}|${row.label}`;
  }

  /** 印（horizon:side）→ 持ち主行の対応表。側は距離の符号（buildMarks と同じ定義）。 */
  function markOwnersOf(rows) {
    const owners = new Map();
    for (const row of rows) {
      const side = Number(row.distance) >= 0 ? 'up' : 'down';
      for (const key of (Array.isArray(row.horizon_marks) ? row.horizon_marks : [])) {
        owners.set(`${key}:${side}`, rowKeyOf(row));
      }
    }
    return owners;
  }

  /** 印の移動を検出して記録する（依頼者承認 2026-08-31）。全行（窓の外も含む）で突合する
   *  ——移動先が窓の外なら何も光らないだけで、記録の意味は変わらない。時計が無い環境では
   *  効果ごと出さない（経過を測れないまま光らせると消えない残光を発明する）。 */
  function trackNextTargetMoves(rows) {
    renderNowSec = typeof now === 'function' ? now() : null;
    const owners = markOwnersOf(rows);
    if (renderNowSec !== null && lastMarkOwners !== null) {
      for (const [mark, owner] of owners) {
        const before = lastMarkOwners.get(mark);
        if (before !== undefined && before !== owner) {
          // 表示は 12 秒遅延の再生系列なので、発光も同じだけ遅らせて予約する（同期）。
          markMovedAt.set(mark, {
            at: renderNowSec + NEXT_MOVE_DELAY_SECONDS, owner, applied: false,
          });
        }
      }
    }
    lastMarkOwners = owners;
    // 表を作り直すと発光のクラスも消えるので、表示中の予約は張り直し対象へ戻す
    //   （負の delay で残り時間から続く＝再点滅にはならない）。
    for (const entry of markMovedAt.values()) {
      entry.applied = false;
    }
    if (renderNowSec === null) {
      markMovedAt.clear();   // 時計が無い環境では効果ごと出さない（消えない残光を発明しない）。
    }
  }

  /** 予約済みの行発光のうち、表示時刻に達したものを可視行へ乗せる（render 直後と
   *  なめらか再生の tick 適用時の両方から呼ばれる＝render の合間でも点灯する）。 */
  function applyDueRowGlows() {
    if (markMovedAt.size === 0 || levelRowRefs.length === 0) {
      return;
    }
    const clockNow = typeof now === 'function' ? now() : null;
    if (clockNow === null) {
      return;
    }
    for (const [mark, entry] of markMovedAt) {
      if (clockNow - entry.at >= NEXT_MOVE_FADE_SECONDS) {
        markMovedAt.delete(mark);   // 終わった効果は再適用しない（賞味期限）。
        continue;
      }
      if (entry.applied || clockNow < entry.at) {
        continue;   // 適用済み・またはまだ表示時刻（検出＋12s）に達していない。
      }
      const ref = levelRowRefs.find((r) => r.rowKey === entry.owner);
      if (!ref) {
        continue;   // 窓の外＝光らせる先が無い（記録は寿命まで保つ）。
      }
      startRowGlow(ref.tr, clockNow - entry.at);
      entry.applied = true;
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
    const gapCell = el('td', {
      className: 'dash-ladder-gap',
      textContent: formatGap(row.gap_to_previous),
      dataset: { cell: 'gap' },
    });
    tr.appendChild(gapCell);
    // なめらか再生の書き換え先（依頼者指示 2026-08-31: 距離・価格・差もライブチャート粒度）。
    //   distance の文字は下の distanceCell 内 span（既に作成済み）を使う。
    const fullIndex = fullIndexByKey.get(rowKeyOf(row));
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
    const direction = currentDirection === null || tickStrength <= 0
      ? '' : ` dash-ladder-current-${currentDirection}`;
    const tr = el('tr', { className: `dash-ladder-row dash-ladder-current${direction}` });
    if (direction) {
      setTickStrengthOn(tr);
    }
    // フェード完了（2s・CSS の dash-tick-fade）で効果のクラスを外す。タイマーを持たずに
    //   CSS の時間へ正確に同期する（外し損ねたクラスは次の再構築で発光を再生してしまう）。
    if (typeof tr.addEventListener === 'function') {
      tr.addEventListener('animationend', () => {
        tickStrength = 0;
        tr.classList.remove('dash-ladder-current-up');
        tr.classList.remove('dash-ladder-current-down');
      });
    }
    // 現在値行は距離＋次のターゲットの 2 列ぶんをまとめる（列の分離・依頼者指示 2026-08-30）。
    tr.appendChild(el('th', {
      scope: 'row', colSpan: 2, textContent: '現在値', dataset: { cell: 'distance' },
    }));
    const priceCell = el('td', { dataset: { cell: 'price' } });
    currentPriceEl = el('b', {
      className: 'dash-ladder-current-price',
      textContent: formatPrice(shown),
    });
    priceCell.appendChild(currentPriceEl);
    tr.appendChild(priceCell);
    const labelCell = el('td', {
      // 差＋到達時間＋時間足＋水準情報のぶんをまとめて 1 セルに（列を足したら NAMING_CELLS
      //   側とここの定数 3（差・到達時間・時間足）を数え直す）。ラベル文は置かない（上記）。
      colSpan: 3 + NAMING_CELLS,
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
    if (previous !== null && currentDirection !== null && tickStrength > 0) {
      // 更新の瞬間に方向色を頻度の濃度で乗せ、CSS の 1s フェードを再始動する
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

  /** 更新 1 回を頻度の観測窓へ入れ、濃度を出す（時計が無い環境は最小濃度＝発明しない）。 */
  function registerTickEffect() {
    const t = typeof now === 'function' ? now() : null;
    if (t === null) {
      tickStrength = TICK_MIN_STRENGTH;
      return;
    }
    tickTimes.push(t);
    tickTimes = tickTimes.filter((at) => t - at < TICK_RATE_WINDOW_SECONDS);
    const perSecond = tickTimes.length / TICK_RATE_WINDOW_SECONDS;
    tickStrength = Math.min(100,
      Math.max(TICK_MIN_STRENGTH, (perSecond / TICK_FULL_RATE) * 100));
  }

  /** 濃度をカスタムプロパティで渡す（色の値そのものは書かない＝色の唯一源を侵さない）。 */
  function setTickStrengthOn(row) {
    if (typeof row.style.setProperty === 'function') {
      row.style.setProperty('--tick-strength', String(Math.round(tickStrength)));
    } else {
      row.style['--tick-strength'] = String(Math.round(tickStrength));
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
   * 式はサーバの参照定義（domain/price_ladder.py）そのもの:
   *   距離 = 水準価格 − 現在値 / 差 = 直前行（サーバ全行順）の水準価格 − 自行の水準価格。
   * ここで使う材料（水準価格＝tails・現在値＝再生価格）はどちらもサーバ計算の値であり、
   * フロントが統計や並びを再計算するわけではない（並び・地平・p は 1s の応答描画が持ち主）。
   */
  function refreshSmoothNumbers() {
    // 発光の表示時刻（検出＋12s）は render の合間に来ることが多い。tick 適用（100ms 粒度）を
    //   契機に予約を確認する＝発光が次の内容変化を待たされない。
    applyDueRowGlows();
    if (externalPrice === null) {
      return;
    }
    for (const ref of levelRowRefs) {
      const entry = smoothRowsAll[ref.fullIndex];
      if (!entry || !Number.isFinite(entry.smooth)) {
        continue;
      }
      const prev = ref.fullIndex === 0 ? null : smoothRowsAll[ref.fullIndex - 1];
      setTextIfChanged(ref.priceEl, formatPrice(entry.smooth));
      setTextIfChanged(ref.distanceEl, formatDistance(entry.smooth - externalPrice));
      setTextIfChanged(ref.gapEl, prev === null || !Number.isFinite(prev.smooth)
        ? '' : formatGap(prev.smooth - entry.smooth));
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
    if (externalPrice === null || typeof lookup !== 'function' || smoothRowsAll.length === 0) {
      return;
    }
    for (const entry of smoothRowsAll) {
      if (!entry.instanceKey || !entry.series) {
        continue;
      }
      const value = lookup(entry.instanceKey, entry.series);
      if (typeof value === 'number' && Number.isFinite(value)) {
        entry.smooth = value;
      }
    }
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
        tickStrength = 0;
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
    // 次のターゲット印の移動検出（依頼者承認 2026-08-31）。突合は表の構築前に 1 回。
    trackNextTargetMoves(allRows);
    // なめらか再生の水準台帳（依頼者指示 2026-08-31: 距離・価格・差もライブチャート粒度）。
    //   種はサーバ価格。並びはサーバの全行順（差の隣接はこの順で決まる・絞り込みと無関係）。
    smoothRowsAll = allRows.map((row) => ({
      key: rowKeyOf(row),
      instanceKey: Array.isArray(row.instance_key) ? row.instance_key : null,
      series: typeof row.series === 'string' && row.series ? row.series : null,
      smooth: Number(row.price),
    }));
    fullIndexByKey.clear();
    smoothRowsAll.forEach((entry, index) => fullIndexByKey.set(entry.key, index));
    levelRowRefs = [];
    // 契約のズレ（未知の地平キー）は色の不在として紛れるので、必ず文字で掲示する。
    const unknown = unknownHorizonKeys(allRows);
    message.textContent = unknown.length === 0
      ? ''
      : `未知の地平キーが応答に含まれています: ${unknown.join(', ')}（対象は ${HORIZON_KEYS.join(' / ')}）`;

    // 絞り込み: 選択中の時間足（期間ボタンとピルが操作する唯一の集合）。並びはサーバのまま
    //   （順序を再計算しない）。全選択のときはフィルタを通さない（未知の足も従来どおり）。
    const tfNarrowed = !windowless && selectedTfs.size !== DASHBOARD_TIMEFRAMES.length;
    const rows = tfNarrowed
      ? allRows.filter((row) => selectedTfs.has(String(row.timeframe)))
      : allRows;
    // 現在値行の位置: 全量ではサーバの current_index が唯一源（範囲外の指定は端へ倒す）。
    //   絞った範囲では行が抜けるため、同じ定義（現在値より上＝距離が正の行数）で**数え直す**
    //   （数値の再計算ではない。距離の符号はサーバの値そのもの）。
    const at = tfNarrowed
      ? rows.filter((row) => Number(row.distance) >= 0).length
      : Math.max(0, Math.min(Number(response.current_index) || 0, rows.length));
    // 現在値を中心とした窓だけを建てる（縦スクロールを不要にする）。半径は器の実高への
    //   適合値（fitWindow）を優先し、未適合は上限 WINDOW_RADIUS。窓の外の行はここで
    //   **建てない**——建ててから隠すと捨てる色計算が毎描画発生する。
    //   全期間は窓なし（全量。従来の表示に戻す選択肢）。
    const radius = fittedRadius ?? WINDOW_RADIUS;
    const start = windowless ? 0 : Math.max(0, at - radius);
    const end = windowless ? rows.length : Math.min(rows.length, at + radius);
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
   */
  function fitWindow() {
    if (fitting || windowless || !scrollBox || !tbody) {
      return;
    }
    const boxH = scrollBox.clientHeight;
    const contentH = scrollBox.scrollHeight;
    if (typeof boxH !== 'number' || typeof contentH !== 'number' || boxH <= 0 || contentH <= boxH) {
      return;
    }
    const first = tbody.children[0];
    const rowH = first && typeof first.getBoundingClientRect === 'function'
      ? first.getBoundingClientRect().height : 0;
    if (!rowH) {
      return;
    }
    const headerH = contentH - tbody.children.length * rowH;
    const capacity = Math.floor((boxH - headerH) / rowH);
    const next = Math.max(1, Math.floor((capacity - 1) / 2));
    if (next >= (fittedRadius ?? WINDOW_RADIUS)) {
      return;   // 縮める方向にしか動かない（拡縮の往復で毎描画作り直さない）。
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
    scopeButtons = [];
    tfButtons = [];
    lastResponse = null;
    scrollBox = null;
    fittedRadius = null;
    lastCurrentPrice = null;
    currentDirection = null;
    tickStrength = 0;
    tickTimes = [];
    lastUpdateAt = null;
    externalPrice = null;
    lastMarkOwners = null;
    markMovedAt.clear();
    renderNowSec = null;
    smoothRowsAll = [];
    fullIndexByKey.clear();
    levelRowRefs = [];
    builtPriceTextEl = null;
    currentRowEl = null;
    currentPriceEl = null;
    currentLabelCell = null;
    currentUpdateEl = null;
  }

  return { mount, render, unmount, updateCurrentPrice, updateLevelValues };
}
