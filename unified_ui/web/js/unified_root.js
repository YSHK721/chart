// 統合エントリのブートストラップ（単一 mount + リプレイ層のオン・オフ）。
//
// 役割:
//   - **単一 chart を 1 回だけ生成**する。live root（indicator_ui）の bootstrap を 1 回呼び、
//     リプレイ部品 { ReplayIndicatorController, setupReplay } を **オプション注入**して「リプレイ層」を
//     同一 chart 上へ配線する（controller は ReplayIndicatorController＝untilTime=undefined で live 等価）。
//   - モード切替は chart/ローソク/指標を **dispose も再生成もしない**。live pollers（LiveUpdater/
//     FormingBarUpdater/LiveTickPlayer）の start/stop と、リプレイ層ハンドルの enable/disable、
//     controller.clearRevealCache、body クラス、SW アクティブモード通知だけで切り替える（チラつき・重複ゼロ）。
//   - root 相対 API fetch のアクティブモード prefix（/live・/replay）付与は **アプリ自身が行う**
//     （createRoutedFetch を live root の fetch 注入点へ渡す・ISSUE-362）。Service Worker は
//     注入点を通らない要求のための保険であり、**正しさの前提ではない**（SW 不在でも動く）。
//
// 無波及順守:
//   - live root はリプレイのコードを import しない（統合レイヤが注入する）＝スタンドアロン live は byte 不変。
//   - MP は当面モード別アクター差替が対象外。live MP stack は無改変で維持し、リプレイ層へは marketProfile を
//     渡さない（live root 側で null 注入）。既定ビュー（MP 無効）で単一化・チラつき解消を成立させる。

import { installOpLog } from './op_log.js';
// 版面の縦 2 分割（下部ペイン＋分割線）の器。表示層はここへ挿す（裁定 2026-08-21）。
import { createBottomPaneView } from './bottom_pane_view.js';
import { mountDashboardArea } from './dashboard_area_view.js';
import { wrap as wrapTimers } from './timer_registry.js';
import { scopedStorage } from './mode_storage.js';
// テンプレート束は live スコープの storage を**読み取り専用**で渡す（arch-spec §0 T-2）。
import { readOnlyStorage } from './readonly_storage.js';
import { registerServiceWorker, notifySwMode } from './sw_client.js';
import { createRoutedFetch } from './routed_fetch.js';
// モード集合・巡回・ツールバー構成の単一ソース（基本設計書 §3.5.6・§11.2）。
import {
  DEFAULT_MODE,
  nextMode,
  MODE_TOGGLE_BUTTONS,
  MODES,
  BOTTOM_PANE_HOST_KIND,
  FULL_AREA_HOST_KIND,
} from './mode_table.js';
import {
  MODE,
  loadVendor,
  showModeError,
  clearModeError,
  applyModeUi,
  wireModeSwitchButtons,
} from './mode_ui_view.js';

const DATASET_REF = 'jp225_tick';

// 中身の高さへ足す余白（ISSUE-442 / ISSUE-443）。小数の高さがそのまま切り上がってスクロール
//   バーが出るのを避けるためだけの最小値にする。14px にしていたときは中身の下に目に見える
//   帯が残った（依頼者指摘 2026-08-22「下部余白が存在する」）。
const SIM_PANE_CONTENT_MARGIN_PX = 4;

// 単一 mount の live root と、注入するリプレイ部品の URL。名指してよいのは各 core の
//   **公開面**（`/<core>/js/public/*.js`）だけで、内部階層を名指すと core 側の配置換えで
//   統合層が無言で 404 になる（識別子渡しの動的 import は import 走査に現れない・J-4 の実測）。
//   symlink ではなく `/live/` プロキシ経由なのは、router.py:326-331 が realpath 解決後に
//   web_root 外を 404 にするためである（実測 2026-09-01）。
//   合成根が `live_public_api.js` ではなく専用の面なのは重さの境界で、dashboard core が借りる
//   軽い面（期間プリセット・tick 再生）へ live のチャートアプリ一式を巻き込まないためである。
const LIVE_ROOT = '/live/js/public/live_root_api.js';
// 表示対象 ref の解決規則（ISSUE-447・A-3 案 U1）。実装は live core 側の 1 つだけで、統合層は
//   それを参照する（手書き複製の禁止）。
const LIVE_PUBLIC_API = '/live/js/public/live_public_api.js';
// リプレイ層から借りる 4 点（コントローラ・駆動・MP アクター・操作バー）は replay core の
//   公開面 1 本から取る（ISSUE-479 Wave2 J-4b）。内部階層を名指すと replay 側の配置換えで
//   統合層が無言で 404 になる（識別子渡しの動的 import は import 走査に映らない）。
//   操作バーの DOM は replay 層の View が所有する（ISSUE-278 #16: 2 ページ複製をやめた）。
const REPLAY_PUBLIC_API = '/replay/js/public/replay_public_api.js';
// 各モードの表示層（sim: 器・3 窓・取引明細／dashboard: 価格ラダー・各時間足の一覧）の入口 URL は
//   **定義表が持つ**（mode_table.js の displayLayerPath・ISSUE-479 Wave2b J-5）。ここへ定数として
//   並べていた形は、モードを 1 つ足すたびに定数・destructuring・setup 呼出・layers の 4 箇所を
//   同時に直す義務を作り、1 箇所でも取り残すと無症状で誤動作した。層はどれも live root へは
//   注入しない独立した層である（チャート画面は無改変で広く保つ）。

let modeController = null; // createModeController の実体（トグルボタンが参照）。

// vendor ロード / SW 通知 / エラー表示 / モード UI 反映は葉モジュールへ抽出済み:
//   - loadVendor / showModeError / clearModeError / applyModeUi / wireModeSwitchButtons: ./mode_ui_view.js
//   - registerServiceWorker / notifySwMode: ./sw_client.js
//   （MODE も applyModeUi の依存定数として mode_ui_view.js に置き、ここでは import して再 export する）

// ---- モード切替ステートマシン（純ロジック・単一 mount 前提）------------------------
//   chart/controller/pollers/表示層は注入。**chart を dispose も再生成もしない**（本関数は
//   remove を一切呼ばない＝再構築なしの構造的実証）。切替は pollers の start/stop、各層の
//   enable/disable、controller.clearRevealCache、SW モード通知、body クラスのみで行う。
//   - replay ON:  pollers stop → clearRevealCache → SW=replay → replay 層 enable → um-mode-replay
//   - live  ON:   SW=live → clearRevealCache → 層を畳む → pollers start → um-mode-live
//
// 表示層の受け取り方（arch-spec §0 T-4）:
//   従来はモード別の名前付き引数（`simHandle`）で受け、遷移手続きを `TRANSITIONS` へ手で
//   並べていた。この形ではモードを 1 つ足すたびに「引数・遷移関数・遷移表」の 3 箇所を同時に
//   直す義務が生まれ、1 箇所でも取り残すと**無症状で誤動作する**（押しても器が出ない／前の
//   モードの器が残る）。よって層は `layers`（モード名 → {enable, disable}）の 1 枚で受け、
//   遷移はモード定義表（MODES）の走査で組む。第 4 モードの追加は表の 1 行と layers の
//   1 エントリで完結し、本関数は変わらない。
//   `replayHandle` / `simHandle` は T-4 以前の呼び出し形の**別名**として残す（加法的変更）。
//
// 遷移の形はモード名ではなく**表の属性**（既定モードか / chartApi を持つか）から決まる:
//   1. 既定モード（live）           … SW を戻す → 層を畳む → live pollers を起動
//   2. chartApi を持つ層（replay）  … pollers 停止 → 層を畳む → SW を向ける → 自層 enable
//   3. chartApi を持たない層（sim / dashboard）
//                                   … pollers 停止 → 他の層を畳む → SW を**既定モード**へ →
//                                     chart 層を畳む → SW を自モードへ → 自層 enable
//   3 で SW を一度既定モードへ向けるのは、chart 層の `disable()` が出す全長復帰 fetch
//   （`/candles` 再取得）を **`/candles` を持つ core** へ届けるため。chartApi を持たない core
//   （sim・dashboard）へ向くと 404 になり、チャートがライブ全長へ戻らない。
export function createModeController({
  controller,
  replayHandle,
  simHandle,
  layers,
  pollers = [],
  setSwMode = () => Promise.resolve(false),
  applyMode = () => {},
  initialMode = MODE.LIVE,
} = {}) {
  let activeMode = initialMode;
  let switching = false;

  // モード名 → 表示層。`layers` に明示があればそれを使い、無ければ旧来の名前付き引数を充てる。
  const layerOf = new Map(layers instanceof Map ? layers : Object.entries(layers || {}));
  if (replayHandle && MODE.REPLAY && !layerOf.has(MODE.REPLAY)) {
    layerOf.set(MODE.REPLAY, replayHandle);
  }
  if (simHandle && MODE.SIM && !layerOf.has(MODE.SIM)) {
    layerOf.set(MODE.SIM, simHandle);
  }

  // 層の 2 種別を表から導く（モード名を本体へ書かない）。
  //   chart 層 … 単一 chart の上で働き、畳むときに `/candles` を持つ core を必要とする層。
  //   器の層   … 自前の器（iframe 等）を出し入れするだけの層（chartApi を持たない core）。
  const CHART_LAYER_MODES = MODES.filter((m) => m.id !== DEFAULT_MODE && m.chartApi).map((m) => m.id);
  const HOSTED_LAYER_MODES = MODES.filter((m) => !m.chartApi).map((m) => m.id);
  //: 畳む順序。器の層を先に、chart 層を後に畳む（T-4 前の enterLive / enterReplay と同順）。
  const FOLD_ORDER = [...HOSTED_LAYER_MODES, ...CHART_LAYER_MODES];

  const startPollers = () => {
    for (const p of pollers) {
      if (p && typeof p.start === 'function') {
        p.start();
      }
    }
  };
  const stopPollers = () => {
    for (const p of pollers) {
      if (p && typeof p.stop === 'function') {
        p.stop();
      }
    }
  };
  const clearReveal = () => {
    if (controller && typeof controller.clearRevealCache === 'function') {
      controller.clearRevealCache();
    }
  };

  // 層 1 つへの操作（未注入・未実装なら何もしない＝無波及）。**同期関数**であることが要点で、
  //   層が居ないときに await を 1 つも増やさない（切替の進み方＝マイクロタスクの刻みを
  //   T-4 前と一致させる。再入排他の検定はこの刻みを観測している）。
  const callLayer = (id, op) => {
    const layer = layerOf.get(id);
    return layer && typeof layer[op] === 'function' ? layer[op]() : undefined;
  };

  // 目標モード以外の層を畳む。**目標の層は畳まない**（畳んでから開き直す無駄を作らない）。
  //   走査順は定義表の順（畳む順序が実装ごとに揺れないようにする）。
  const foldLayers = async (ids, target) => {
    for (const id of ids) {
      if (id === target) {
        continue;
      }
      const pending = callLayer(id, 'disable');
      if (pending && typeof pending.then === 'function') {
        await pending;
      }
    }
  };

  // 目標モードの行から遷移手続きを組む（手続きの本体はモード名を 1 つも見ない）。
  async function enterMode(row) {
    if (row.id === DEFAULT_MODE) {
      // SW を先に既定モードへ。これで戻せるのは **注入点を通らない要求**（SW だけが prefix を
      //   付ける経路）。disable の全長復帰 fetch に prefix を付けるのは front の routedFetch で
      //   あり、参照するのは activeMode（末尾で更新）なので、行き先は SW ではなく**遷移前
      //   モード**の core になる。replay→live は /replay/candles へ向かい replay core が答える。
      //   sim→live はリプレイ層が未 enable ゆえ disable が早期 return し（replay.js:514）、
      //   要求自体が出ない。（実測: unified_root_restore_fetch_routing.test.js）
      await setSwMode(row.id);
      clearReveal();
      // 器の層 → chart 層の順に畳む（chart 層の disable が reveal トリム解除＋ライブ全長再描画）。
      await foldLayers(FOLD_ORDER, row.id);
      startPollers();
    } else if (row.chartApi) {
      stopPollers();
      clearReveal();
      await foldLayers(FOLD_ORDER, row.id);
      // SW を先に向ける（enable の loadTimeframe が当該 core の /candles・/compute を叩く）。
      await setSwMode(row.id);
      await callLayer(row.id, 'enable');
    } else {
      stopPollers();
      clearReveal();
      await foldLayers(HOSTED_LAYER_MODES, row.id);
      // chart 層を畳む復帰要求が届く先を、必ず /candles を持つ core（既定モード）にしておく。
      await setSwMode(DEFAULT_MODE);
      await foldLayers(CHART_LAYER_MODES, row.id);
      // 復帰が終わってから自モードへ切り替える。
      await setSwMode(row.id);
      // 器はここで出す（SW を自モードへ向けた後＝chart 層の復帰要求と混ざらない）。
      await callLayer(row.id, 'enable');
    }
    activeMode = row.id;
    applyMode(row.id);
  }

  // 目標モード → 遷移手続き。3 値以上では if/else の二分岐で表せないため表で引く。
  //   表そのものを**モード定義表の走査で組む**ので、第 4 モードの追加で本ファイルは変わらない
  //   （旧実装はここへモードごとの遷移関数を 1 行ずつ書き並べていた＝OCP 違反の残骸）。
  const TRANSITIONS = Object.freeze(Object.fromEntries(
    MODES.map((row) => [row.id, () => enterMode(row)]),
  ));

  // 既存の公開 API（名前付きの入口）。一般化しても呼び出し形を壊さない。
  const noTransition = () => Promise.resolve();
  const enterLive = TRANSITIONS[DEFAULT_MODE] || noTransition;
  const enterReplay = TRANSITIONS[MODE.REPLAY] || noTransition;
  const enterSim = TRANSITIONS[MODE.SIM] || noTransition;

  async function toggle(next) {
    // 既定算出（引数省略時）は定義表由来。既定モードに居るなら表の次のモードへ、
    //   そうでなければ既定モードへ戻す＝「オン・オフのトグル」の意味を 3 値以上でも保つ
    //   （モード名を直書きした二値反転では、3 値以上で行き先が定義できない・§3.5.6 #3）。
    const target = next || (activeMode === DEFAULT_MODE ? nextMode(DEFAULT_MODE) : DEFAULT_MODE);
    const transition = TRANSITIONS[target];
    // 表に無いモードは遷移させない（未知値で状態を壊さない＝全域性）。
    if (switching || !transition || target === activeMode) {
      return;
    }
    switching = true;
    try {
      await transition();
    } finally {
      switching = false;
    }
  }

  return {
    toggle,
    // 任意モードの入口（表に無い値は何もしない＝全域性）。第 5 モード以降はこれで足りる。
    enter: (id) => (TRANSITIONS[id] || noTransition)(),
    enterReplay,
    enterLive,
    enterSim,
    startPollers,
    stopPollers,
    getMode: () => activeMode,
  };
}

// ---- 表示層の読み込み（モード定義表の走査）--------------------------------------
//
// モードごとの差は 2 か所にしか置かない:
//   1. 定義表の行（displayLayerPath / displayLayerExport / hostKind）… core 側の事実
//   2. 本 LAYER_EXTRAS                                              … 統合層側の事実
// これ以外（読み込み・据付・登録の手順）はモードを 1 つも見ない。第 5 モードの追加は表の
// 1 行で完結し、追加注入が要るときだけ本表へ 1 行足す。
//
// なぜ追加注入を core 側へ渡さず統合層が持つのか（DIP・ISSUE-460 / T-2）:
//   `onContentHeight` はペインの高さを決める口である。決めるのは**ペインの所有者**（統合層）で、
//   sim は測って渡すだけ。`templates` は live スコープの storage をどう見せるかの判断であり、
//   これも束の出所とスコープを決める統合層の責務である。core にこの判断を持たせると、
//   統合ページの器の事情が core 側へ漏れる。
//   公開しない: 借り手は `loadDisplayLayers` だけで、外から差し替える口を作る理由が無い
//   （使われない公開面は、消えたことに誰も気付けない依存を育てる）。
const LAYER_EXTRAS = Object.freeze({
  [MODE.SIM]: ({ lwc, bottomPane }) => ({
    lwc,
    // 中身が必要とする高さを受け取り、**既定の高さ**として与える（ISSUE-442・裁定 2026-08-22）。
    //   既定が版面の 45% 固定だと、投入フォームの下に余白が出る一方でチャート側は必要以上に
    //   削られ、指標ペインが狭くなって手で広げる作業が要った。
    //   利用者が一度でも分割線を掴んでいたら**触らない**（ビュー自動介入の禁止・ISSUE-164）。
    onContentHeight: (px) => {
      if (bottomPane && !bottomPane.isUserSized()) {
        bottomPane.setHeightPx(px + SIM_PANE_CONTENT_MARGIN_PX);
      }
    },
  }),
  // テンプレート束（live スコープの chart テンプレート）は**読み取り専用**で渡す。束をどう
  //   消費するかは dashboard 側の責務で、統合層は出所とスコープだけを固定する（arch-spec §0 T-2）。
  //   書ける口を渡すと、第 4 モードの不具合が live の資産を壊す経路になる。
  [MODE.DASHBOARD]: ({ liveStorage }) => ({ templates: readOnlyStorage(liveStorage) }),
});

/**
 * 定義表が宣言した表示層をすべて読み込み、据え付けて「モード名 → ハンドル」の 1 枚にする。
 *
 * 読み込みは**宣言した行のぶんだけ**発行する（作って捨てる読込を 1 件も出さない）。切替では
 * 一切読み直さない——ハンドルは起動時の 1 回だけ作り、以降は enable/disable するだけである。
 *
 * @param {object}   [opts]
 * @param {Function} [opts.importModule] module の読込（既定は動的 import。検定は spy を注入）
 * @param {object}   [opts.context]      層へ渡す材料（`doc` / `hosts`（hostKind → 器）/ `lwc` /
 *                                       `bottomPane` / `liveStorage`）。器の所有者は統合層であり、
 *                                       core は統合ページの id を知らない。
 * @returns {Promise<Map<string, {enable: Function, disable: Function}>>}
 */
export async function loadDisplayLayers({
  importModule = (url) => import(url),
  context = {},
} = {}) {
  const layers = new Map();
  const hosts = context.hosts || {};
  for (const row of MODES) {
    if (!row.displayLayerPath) {
      continue; // 単一 chart の上で働く core（chartApi あり）は別 module を読まない。
    }
    const mod = await importModule(row.displayLayerPath);
    const setup = mod[row.displayLayerExport];
    if (typeof setup !== 'function') {
      // 黙って層無しで起動しない。公開面が名前を変えた状態は、押しても器が出ない無音の失敗になる。
      throw new Error(
        `${row.displayLayerPath} が ${row.displayLayerExport} を公開していません`,
      );
    }
    const extrasOf = LAYER_EXTRAS[row.id];
    const extras = typeof extrasOf === 'function' ? extrasOf(context) : {};
    layers.set(row.id, await setup({ doc: context.doc, host: hosts[row.hostKind], ...extras }));
  }
  return layers;
}

// ---- 単一 mount 起動 ----------------------------------------------------------
async function main() {
  // [ISSUE-298] 操作ログを**最初に**仕掛ける（以降の動的 import・fetch・クリックをすべて記録する）。
  //   受動監視のみ（クリック購読・body クラス観測・fetch/console.error の透過ラップ）で挙動は変えない。
  //   取り出しは `__opsDump()`（現セッション）／`__opsPrev()`（リロード前のセッション）。
  installOpLog();

  // ISSUE-362: ルーティングはアプリの責務。prefix 付与は下の routedFetch（bootstrap へ注入）が
  //   必ず行うため、SW が無くても・迂回されていても API は正しい core へ届く。SW は注入点を
  //   通らない要求のための保険にすぎないので、登録に失敗しても起動を止めない
  //   （旧実装はここでフェイルクローズしていた＝SW を正しさの前提に置いていた）。
  //   なお SW と front の規則は同一の rewritePath で、冪等ゆえ二重付与は起こらない。
  await registerServiceWorker();

  // 初期 live: SW 側のモードも合わせる（SW が居ない環境では no-op）。
  await notifySwMode(MODE.LIVE);

  // API 要求へアクティブモードの prefix を付ける fetch。モードは呼び出しごとに読むため、
  //   live↔replay の切替後も同じ実体のまま正しい core へ回る。
  const routedFetch = createRoutedFetch({
    baseFetch: globalThis.fetch.bind(globalThis),
    getMode: () => (modeController ? modeController.getMode() : MODE.LIVE),
  });

  const vendorOk = await loadVendor(MODE.LIVE);
  if (!vendorOk) {
    showModeError('live core を起動できません（vendor 未取得・live core 停止の可能性）。');
    return;
  }

  // live root（bootstrap）と、注入するリプレイ部品を動的ロードする。
  let bootstrap;
  let ReplayIndicatorController;
  let setupReplay;
  let ReplayMarketProfileActor;
  let installReplayBar;
  let resolveDatasetRef;
  try {
    ({ bootstrap } = await import(LIVE_ROOT));
    ({ resolveDatasetRef } = await import(LIVE_PUBLIC_API));
    ({ ReplayIndicatorController, setupReplay, ReplayMarketProfileActor, installReplayBar } =
      await import(REPLAY_PUBLIC_API));
  } catch (err) {
    showModeError(`モジュール読込に失敗しました: ${err && err.message ? err.message : err}`);
    return;
  }

  // 既存 bootstrap の setInterval/clearInterval 注入口へラップ済みタイマを渡す（一括停止の受け皿）。
  const registry = wrapTimers({
    setInterval: globalThis.setInterval.bind(globalThis),
    clearInterval: globalThis.clearInterval.bind(globalThis),
  });

  // live スコープの storage（チャートテンプレート等の既存資産の置き場所）。live root へは
  //   そのまま渡し、dashboard へは読み取り専用にして渡す（arch-spec §0 T-2: どのスコープを
  //   読むかを決めるのは合成根であり、View は自分でスコープを選ばない）。
  const liveStorage = scopedStorage(globalThis.localStorage, MODE.LIVE);

  // ★ 単一 mount: chart/mainSeries/renderer/controller(=ReplayIndicatorController)/live pollers/
  //   リプレイ層ハンドルを 1 回だけ生成する（以降 toggle で再生成しない）。
  let boot;
  try {
    boot = await bootstrap({
      lwc: window.LightweightCharts,
      container: document.getElementById('chart'),
      doc: document,
      storage: liveStorage,
      // ISSUE-362: 配下の全 API クライアントはこの 1 つの fetch を使う（this._fetch）。
      //   ここで prefix 付与を担保するので、ルーティングが SW の可用性に依存しない。
      fetch: routedFetch,
      // `?dataset=<ref>` を付けたときだけ上書きする（ISSUE-447 A-3 案 U1・承認済み）。
      //   クエリ無し＝従来どおり DATASET_REF（既定表示は不変）。解決は起動時のこの 1 回だけ。
      datasetRef: resolveDatasetRef(location.search, DATASET_REF),
      setInterval: registry.setInterval,
      clearInterval: registry.clearInterval,
      // ツールバー構成の注入（§11.1 裁定 3 = L-1）。モードの集合を知っているのは統合層だけなので、
      //   ライブ core の合成根には**構成を渡す**。定義表から作るので、第 4 モードを表へ足せば
      //   ここも本体不変のままボタンが増える（ライブ core は統合層を import しない＝依存方向を維持）。
      toolbar: { liveFollow: true, modeButtons: MODE_TOGGLE_BUTTONS },
      // リプレイ層のオプション注入（live root はこの注入時のみリプレイを配線する）。
      // リプレイ層のオプション注入（live root はこの注入時のみリプレイを配線する）。MP 単一化:
      //   ReplayMarketProfileActor を単一 MP アクターとして注入し、isLiveMode で 3状態 to を切替える
      //   （live→MP_TO_LATEST＝base byte 等価／replay→controller._untilTime＝pull-at-T）。アクター/chart は
      //   1 回生成のみ＝toggle で getMpTo の返す to だけが live↔replay で切り替わる（再構築なし）。
      replay: {
        ReplayIndicatorController,
        setupReplay,
        ReplayMarketProfileActor,
        // バー DOM の生成器を注入する（live root はリプレイのコードを import しない＝注入のみ）。
        installReplayBar,
        isLiveMode: () => (modeController ? modeController.getMode() === MODE.LIVE : true),
      },
    });
  } catch (err) {
    showModeError(`初期化に失敗しました: ${err && err.message ? err.message : err}`);
    return;
  }

  // mount シーケンス（standalone live と同順＋初期 disable でリプレイ層を live 等価へ倒す）。
  boot.controller.bind();
  if (boot.replayHandle && typeof boot.replayHandle.disable === 'function') {
    // 初期 live: playing=false・untilTime=undefined・_recentBars 復帰（wasEnabled=false＝軽量停止のみ・
    //   全長再取得はしない＝下の ready+restore が live 全長を描く）。
    await boot.replayHandle.disable();
  }
  if (typeof boot.controller.setUntilTime === 'function') {
    boot.controller.setUntilTime(undefined); // 冗長だが明示（null でなく undefined＝live 等価 gate）。
  }
  await boot.ready;
  boot.controller.restore(); // 復元・再計算は untilTime=undefined＝live 経路で走る。
  if (typeof boot.controller.clearRevealCache === 'function') {
    boot.controller.clearRevealCache(); // 初期 setup の一括リビール基底を破棄（live に不要）。
  }

  // 下部ペイン（分割線＋ペイン）を版面へ足す。チャートは畳まず、表示層はこのペインへ出す
  //   （裁定 2026-08-21・参照実装 MT5 のストラテジーテスター＝下部ドックペイン）。
  //   出し入れは CSS（body:not(.um-chart-api)）が持つので、ここでは器を置くだけ。
  //   `above` は分割線の上で高さを譲る要素＝価格チャートの版面。可動域の上限はこの要素の
  //   下限から決まる（版面 #app にはツールバー等の分割に与らない兄弟が居るので #app 高では
  //   計算できない・実測 2026-08-21）。
  const bottomPane = createBottomPaneView({ doc: document });
  bottomPane.mount(document.getElementById('app'), { above: document.querySelector('.chart-wrap') });

  // 表示層の器を用意する。**置き場所を決めるのは統合層**（器の所有者）であり、各 core は
  //   渡された host へ挿すだけで統合ページの id を知らない（DIP）。
  //   - 下部ドックペイン（sim）… チャートは上に残す縦 2 分割（裁定 2026-08-21・MT5 と同形）。
  //   - 専用の全面ホスト（dashboard）… **チャート画面ではない**（設計書 §4.6 依頼者裁定・
  //     ISSUE-460）。表示・非表示はモード CSS（index.html）が body クラスで切り替えるので、
  //     enable/disable はスタイルを触らない。
  const displayHosts = {
    [BOTTOM_PANE_HOST_KIND]: bottomPane.host(),
    [FULL_AREA_HOST_KIND]: mountDashboardArea(document),
  };

  // 表示層のハンドル（enable/disable のみ）。どの層を読み・どの器へ挿すかは定義表が持つので、
  //   ここはモード名を 1 つも見ない。器・CSS・中身は各 core が所有する（ISSUE-452 禁止事項）。
  //   sim の job_id は `?job=<id>` から sim 側が読む（統合層は選ばない＝ビュー自動介入の禁止）。
  let layers;
  try {
    layers = await loadDisplayLayers({
      context: {
        doc: document,
        hosts: displayHosts,
        lwc: window.LightweightCharts,
        bottomPane,
        liveStorage,
      },
    });
  } catch (err) {
    showModeError(`表示層の読込に失敗しました: ${err && err.message ? err.message : err}`);
    return;
  }

  modeController = createModeController({
    controller: boot.controller,
    replayHandle: boot.replayHandle,
    // 表示層は「モード名 → {enable, disable}」の 1 枚で渡す（arch-spec §0 T-4）。モードごとの
    //   名前付き引数を足していく形だと、増えるたびに本呼び出しと controller の両方を直す義務が
    //   残り、取り残しが無音の失敗になる。
    layers,
    pollers: [boot.liveUpdater, boot.formingBarUpdater, boot.liveTickPlayer],
    setSwMode: notifySwMode,
    applyMode: applyModeUi,
    initialMode: MODE.LIVE,
  });

  // 初期 live: pollers を明示 start（standalone live と同じ・start は冪等）。
  modeController.startPollers();
  if (boot.tradeMarkers && typeof boot.tradeMarkers.load === 'function') {
    boot.tradeMarkers.load('/live/data/trade_markers.json');
  }
  applyModeUi(MODE.LIVE);
  wireModeSwitchButtons(modeController);
  clearModeError();
}

// ブラウザ（DOM/SW あり）でのみ起動する。node（vitest）import 時は起動しない＝
//   createModeController を純ロジックとして単体検証できる。
if (typeof document !== 'undefined' && typeof window !== 'undefined') {
  main().catch((err) => {
    showModeError(`初期化に失敗しました: ${err && err.message ? err.message : err}`);
  });
}

// テスト・診断用途に公開。
export { MODE };
