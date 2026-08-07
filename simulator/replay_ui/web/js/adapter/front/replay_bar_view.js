// ReplayBarView（adapter/front/replay_bar_view.js）— リプレイ操作バー DOM の**所有規約**（ISSUE-278 #16）。
//
// 解決する問題（実測）: 同じバーの markup が replay core と統合 UI の 2 ページへ手書き複製されており、
//   実際にドリフトしていた。`rp-speed` の title が :8000 だけ「リアルタイム＝実時間」の説明を欠く
//   （replay core: 「1.00=最速・0.00=一時停止・リアルタイム＝実時間」／統合 UI:「…0.00=一時停止」）。
//   実時間テンポは `data-speed="realtime"` の番兵値で実装済みなのに、実際に配信されるページの
//   ツールチップだけがその存在を説明していなかった＝複製の取り残し。
//
// 規約は app_chrome_view / overlay_host と同一: バーを配線する側（replay 層）が DOM を所有し、
//   ページが持つのはアンカーだけ。要素の意味は css/replay_bar.css の冒頭コメントを参照。
//
// ページ差（正当な差）: 「リプレイ終了（✕）」は統合 UI だけが持つ（standalone replay には戻り先の
//   ライブが無い）。差は withClose 引数 1 つで表し、markup そのものは複製しない。

export const REPLAY_BAR_ANCHOR_SELECTOR = '#app';

// 再生速度の選択肢と意味（title）は実装（replay/timing.js の clampSpeed / REALTIME 番兵）に対応する。
//   ここが唯一の説明文＝ページ間でドリフトしない。
const SPEED_TITLE = '再生速度（1.00=最速・0.00=一時停止・リアルタイム＝実時間）';

// 足内更新の方法（MT5 モデリング相当）。option の集合はサーバ /intraday の mode と対応する。
const INTRABAR_MODES = [
  ['real_ticks', '実ティック'],
  ['every_tick', '全ティック合成'],
  ['ohlc_1min', '1分OHLC'],
  ['open_only', '始値のみ'],
  ['math', '数学計算(終値)'],
];
const INTRABAR_DEFAULT = 'ohlc_1min';

/**
 * リプレイ操作バーをアンカー配下へ生成する（既にあれば再利用）。
 *
 * @param {object|null} doc      DOM 実装（注入）。DOM 非対応環境は null 可＝縮退（生成しない）。
 * @param {object} opts
 * @param {object} [opts.anchor] アンカー要素の直接注入（既定は #app を querySelector）。
 * @param {boolean} [opts.withClose] 「リプレイ終了（✕）」を置くか（統合 UI のみ true）。
 * @returns {object|null} バー要素（DOM 非対応環境では null）。
 * @throws {Error} DOM はあるがアンカーが無い場合（契約違反・フェイルクローズ）。
 */
export function installReplayBar(doc, { anchor = null, withClose = false } = {}) {
  if (!doc || typeof doc.createElement !== 'function') {
    return null;
  }
  if (!anchor && typeof doc.querySelector !== 'function') {
    return null;
  }
  const root = anchor ?? doc.querySelector(REPLAY_BAR_ANCHOR_SELECTOR);
  if (!root) {
    throw new Error(`replay_bar_view: アンカー ${REPLAY_BAR_ANCHOR_SELECTOR} がページに無い`);
  }
  const existing = typeof root.querySelector === 'function' ? root.querySelector('#replay-bar') : null;
  if (existing) {
    return existing;   // 再入で増やさない。
  }
  const options = INTRABAR_MODES
    .map(([v, label]) => `<option value="${v}"${v === INTRABAR_DEFAULT ? ' selected' : ''}>${label}</option>`)
    .join('');
  const bar = doc.createElement('div');
  bar.className = 'replay-bar';
  bar.id = 'replay-bar';
  bar.innerHTML = [
    '<div class="rp-group">',
    '<button id="rp-range" type="button" title="表示期間 / 再生開始日">全期間</button>',
    '<button id="rp-range-caret" type="button" aria-haspopup="true" title="カレンダー / 期間プリセット">⌄</button>',
    '<span class="rp-sep"></span>',
    '<button id="rp-view-left" class="rp-icon" type="button" title="チャート表示を左端へスクロール（再生位置は変えない）">‖◁</button>',
    '<button id="rp-prev" class="rp-icon" type="button" title="1足戻る">|◁</button>',
    '<button id="rp-play" class="rp-icon" type="button" title="再生 / 一時停止（再生中は再生位置に追随）">▷</button>',
    '<button id="rp-next" class="rp-icon" type="button" title="1足進む">▷|</button>',
    '<button id="rp-view-right" class="rp-icon" type="button" title="チャート表示を右端へスクロール（再生位置は変えない）">▷‖</button>',
    '<span class="rp-sep"></span>',
    `<button id="rp-speed" type="button" data-speed="1" aria-haspopup="true" title="${SPEED_TITLE}">x1.00</button>`,
    `<select id="rp-mode" class="rp-select" title="最新足を足内で更新する方法（MT5 モデリング相当）">${options}</select>`,
    '<span id="rp-eta" class="rp-eta" title="完了予想時間（残り足 × 実測の1足あたり所要）">完了予想 —</span>',
    '</div>',
    // リプレイ終了（= ツールバーの「リプレイ」トグル OFF と同一動作＝ライブへ戻る）。
    withClose ? '<button id="rp-close" type="button" title="リプレイ終了">✕</button>' : '',
  ].join('');
  root.appendChild(bar);
  return bar;
}
