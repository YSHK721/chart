// mode_table.js — 統合 UI のモード定義表（単一ソース・葉モジュール）。
//
// なぜ在るか（基本設計書 §3.5.6 / §11.2 — OCP 違反の是正）:
//   モード集合をフロント各ファイルと sw.js の分岐へ散らすと（`mode === 'replay' ? … : 'live'`／
//   body クラスの個別トグル／`path.startsWith('/live/') || path.startsWith('/replay/')` 等）、
//   モードを 1 つ足すたびに全箇所を同時に直すことになり、1 箇所でも取り残すと**無症状で誤動作する**。
//   最悪の形は未知モードを黙って既定へ倒す分岐で、`'sim'` が**エラーにならずライブ core の答えを
//   返す**（§3.5.6 #9）。よってモード集合は本表 1 枚だけが持ち、各分岐は表の走査で書く。
//   第 4 モードの追加は**本表への 1 行追加**で済み、本体コードは変わらない。
//
// 表が持つ 6 つの属性（散っていた定義の集約先）:
//   id        … モード名（API・SW・localStorage スコープで使う正準値）
//   prefix    … ルータの URL prefix（`unified_ui/router.py` の振り分けと 1:1）
//   bodyClass … body へ付けるモード クラス（index.html のモード別 CSS と 1:1）
//   toggleId  … ツールバーのトグルボタン id（既定モードは「オフ」状態＝ボタンを持たない＝null）
//   label     … トグルボタンの表示ラベル（同上）
//   chartApi  … その core がチャート API（/candles・/compute）を持つか。持たない core で
//               時間足や指標を操作すると要求は飛ぶが 404 で、画面には何も起きない（無音の失敗）。
//               UI は本属性を見て操作を閉じる（モード名で分岐しない）
//
// 依存なし（DOM / fetch / SW に非依存の純データ＋純関数）。sw.js からも import される。

/** 既定モード。未知値のフォールバック先であり、トグルの「オフ」状態でもある。 */
export const DEFAULT_MODE = 'live';

/**
 * モード定義表。配列の順序が「表の巡回順」（`nextMode`）を決める。
 * 第 4 モードはここへ 1 行足すだけでよい（本体コードは不変）。
 */
export const MODES = Object.freeze([
  Object.freeze({
    id: 'live',
    prefix: '/live',
    bodyClass: 'um-mode-live',
    toggleId: null,          // 既定モード＝トグルのオフ状態。専用ボタンを持たない。
    label: null,
    buttonTitle: null,
    chartApi: true,          // indicator_ui core が /candles・/compute を持つ。
    bottomPane: false,       // 表示層を持たない（チャートのみ）。
  }),
  Object.freeze({
    id: 'replay',
    prefix: '/replay',
    bodyClass: 'um-mode-replay',
    toggleId: 'enter-replay',
    label: 'リプレイ',
    buttonTitle: 'リプレイ表示のオン・オフ',
    chartApi: true,          // replay_ui core が /candles・/compute を持つ。
    bottomPane: false,       // 表示層を持たない（チャートのみ）。
  }),
  Object.freeze({
    id: 'sim',
    prefix: '/sim',
    bodyClass: 'um-mode-sim',
    toggleId: 'enter-sim',
    label: 'シミュレーション',
    buttonTitle: 'シミュレーション表示のオン・オフ',
    // Phase 1 の sim core は静的配信のみ（simulator/sim_ui/framework/serve_sim.py）。
    //   ジョブ API を持つ Phase 2 以降も /candles・/compute は持たない設計（§6.1）。
    chartApi: false,
    // MT5 のストラテジーテスターと同形: 表示層は**下部ドックペイン**に出し、チャートは
    //   上に残す（縦 2 分割・裁定 2026-08-21）。
    bottomPane: true,
  }),
  Object.freeze({
    id: 'dashboard',
    prefix: '/dashboard',
    bodyClass: 'um-mode-dashboard',
    toggleId: 'enter-dashboard',
    label: 'ダッシュボード',
    buttonTitle: 'ダッシュボード表示のオン・オフ',
    // ISSUE-452 / 設計書 §4.6: 価格ラダー（全時間足の水準を価格軸 1 本に並べる）と各時間足の
    //   チャート一覧の置き場所。チャート画面へは置かない（価格軸整列 2.4px/行・ページ級タブ
    //   不在・併置でチャートが 320px 狭くなる＝3 案とも実測で却下）。
    //   dashboard core（127.0.0.1:8481・arch-spec §3）は自分の面（/reach_sheet）だけを持ち、
    //   `/candles`・`/compute` は持たない。
    chartApi: false,
    // 設計書 §4.6（依頼者裁定 2026-08-29・sim の縦 2 分割裁定より後）: **チャート画面には
    //   置かない**。下部ペインではなく専用の全面ホスト（#um-dashboard-area）を使う（ISSUE-460）。
    bottomPane: false,
  }),
]);

/** モード名の一覧（表の順）。 */
export const MODE_IDS = Object.freeze(MODES.map((m) => m.id));

/** URL prefix の一覧（表の順）。SW / routed_fetch の「既 prefix 判定」が走査する。 */
export const MODE_PREFIXES = Object.freeze(MODES.map((m) => m.prefix));

/** body のモード クラス一覧（表の順）。applyModeUi / op_log が走査する。 */
export const MODE_BODY_CLASSES = Object.freeze(MODES.map((m) => m.bodyClass));

/**
 * ツールバーへ置くトグルボタンの定義配列（既定モードを除く全モード）。
 * `app_chrome_view.installChartToolbar` の `modeButtons` へそのまま渡せる形。
 */
export const MODE_TOGGLE_BUTTONS = Object.freeze(
  MODES.filter((m) => m.toggleId).map((m) => Object.freeze({
    id: m.toggleId,
    mode: m.id,
    label: m.label,
    title: m.buttonTitle,
  })),
);

/** 表の行を返す（未知は undefined）。 */
export function modeOf(id) {
  return MODES.find((m) => m.id === id);
}

/** 表に載っているモードか（許可集合判定＝誤配の遮断点）。 */
export function isKnownMode(id) {
  return MODES.some((m) => m.id === id);
}

/** モードの URL prefix（未知は既定モードの prefix へ倒す＝全域性）。 */
export function prefixOf(id) {
  const row = modeOf(id);
  return row ? row.prefix : modeOf(DEFAULT_MODE).prefix;
}

/**
 * body へ付ける「この core はチャート API を持つ」状態クラス。
 * CSS はモード名ではなく本クラスの**有無**だけを見る（`body:not(.um-chart-api) …`）。
 * これでモードを増やしても CSS は変わらない（#replay-bar / #live-follow-toggle と同じ反転記法）。
 */
export const CHART_API_BODY_CLASS = 'um-chart-api';

//: 「このモードの表示層は下部ドックペインを使う」を表す body の状態クラス。CSS はモード名
//:   ではなく本クラスの有無だけを見る（モードを増やしても CSS 側は変わらない・ISSUE-460）。
export const BOTTOM_PANE_BODY_CLASS = 'um-bottom-pane-mode';

/** その core がチャート API（/candles・/compute）を持つか。未知は false へ倒す。
 *  （「持つ」と誤認して操作させると無音の 404 になるため、慎重側は「持たない」）。 */
export function hasChartApi(id) {
  const row = modeOf(id);
  return !!(row && row.chartApi);
}

/** そのモードの表示層が下部ドックペインを使うか（表が唯一源）。 */
export function usesBottomPane(id) {
  const row = MODES.find((mode) => mode.id === id);
  return !!(row && row.bottomPane);
}

/** モードの body クラス（未知は null）。 */
export function bodyClassOf(id) {
  const row = modeOf(id);
  return row ? row.bodyClass : null;
}

/** 表を 1 行進める（末尾は先頭へ回る）。未知は既定モードへ倒す。 */
export function nextMode(id) {
  const i = MODES.findIndex((m) => m.id === id);
  if (i < 0) {
    return DEFAULT_MODE;
  }
  return MODES[(i + 1) % MODES.length].id;
}
