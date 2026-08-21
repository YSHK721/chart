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
import { wrap as wrapTimers } from './timer_registry.js';
import { scopedStorage } from './mode_storage.js';
import { registerServiceWorker, notifySwMode } from './sw_client.js';
import { createRoutedFetch } from './routed_fetch.js';
// モード集合・巡回・ツールバー構成の単一ソース（基本設計書 §3.5.6・§11.2）。
import { DEFAULT_MODE, nextMode, MODE_TOGGLE_BUTTONS } from './mode_table.js';
import {
  MODE,
  loadVendor,
  showModeError,
  clearModeError,
  applyModeUi,
  wireModeSwitchButtons,
} from './mode_ui_view.js';

const DATASET_REF = 'jp225_tick';

// 単一 mount の live root と、注入するリプレイ部品の URL（/replay プロキシ経由で取得）。
const LIVE_ROOT = '/live/js/adapter/front/composition_root_front.js';
const REPLAY_CONTROLLER = '/replay/js/adapter/front/replay_indicator_controller.js';
const REPLAY_DRIVER = '/replay/js/replay.js';
const REPLAY_MP_ACTOR = '/replay/js/adapter/front/replay_market_profile_actor.js';
// リプレイ操作バーの DOM は replay 層の View が所有する（ISSUE-278 #16: 2 ページ複製をやめた）。
const REPLAY_BAR_VIEW = '/replay/js/adapter/front/replay_bar_view.js';
// sim 表示層の合成根（器・3 窓・取引明細を所有する。live root へは注入しない＝独立した層）。
const SIM_ROOT = '/sim/js/adapter/front/composition_root_front.js';

let modeController = null; // createModeController の実体（トグルボタンが参照）。

// vendor ロード / SW 通知 / エラー表示 / モード UI 反映は葉モジュールへ抽出済み:
//   - loadVendor / showModeError / clearModeError / applyModeUi / wireModeSwitchButtons: ./mode_ui_view.js
//   - registerServiceWorker / notifySwMode: ./sw_client.js
//   （MODE も applyModeUi の依存定数として mode_ui_view.js に置き、ここでは import して再 export する）

// ---- モード切替ステートマシン（純ロジック・単一 mount 前提）------------------------
//   chart/controller/pollers/replayHandle は注入。**chart を dispose も再生成もしない**（本関数は
//   remove を一切呼ばない＝再構築なしの構造的実証）。切替は pollers の start/stop、replayHandle の
//   enable/disable、controller.clearRevealCache、SW モード通知、body クラスのみで行う。
//   - replay ON:  pollers stop → clearRevealCache → SW=replay → replayHandle.enable → um-mode-replay
//   - live  ON:   replayHandle.disable → clearRevealCache → SW=live → pollers start → um-mode-live
export function createModeController({
  controller,
  replayHandle,
  simHandle,
  pollers = [],
  setSwMode = () => Promise.resolve(false),
  applyMode = () => {},
  initialMode = MODE.LIVE,
} = {}) {
  let activeMode = initialMode;
  let switching = false;

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

  // sim 表示層の器は sim モードでだけ出す。live/replay へ出るときは必ず畳む（器・共有 CSS を
  //   統合ページへ残さない）。未注入（standalone・既存検定）では何も起きない＝無波及。
  const disableSim = async () => {
    if (simHandle && typeof simHandle.disable === 'function') {
      await simHandle.disable();
    }
  };

  async function enterReplay() {
    stopPollers();
    clearReveal();
    await disableSim();
    // SW を先に replay へ（enable の loadTimeframe が /replay/candles・/replay/compute を叩く）。
    await setSwMode(MODE.REPLAY);
    if (replayHandle && typeof replayHandle.enable === 'function') {
      await replayHandle.enable();
    }
    activeMode = MODE.REPLAY;
    applyMode(MODE.REPLAY);
  }

  // sim（シミュレーション）は「再生」ではないのでリプレイ層は畳む。手順は replay と同型だが、
  //   **SW の向け先だけが異なる**。
  //
  //   不変条件: `replayHandle.disable()` の全長復帰 fetch（replay.js:532 catchUpToLiveTail の
  //   `/candles` 再取得）は、必ず **/candles を持つ core** へ向ける。Phase 1 の sim core は静的配信
  //   しか持たない（`simulator/sim_ui/framework/serve_sim.py`）ので、そこへ向くと 404 になり
  //   チャートがライブ全長へ戻らない。front 側は activeMode を末尾まで sim にしないことで
  //   （＝遷移前モードへ向かう）、SW 側は **disable が終わるまで sim にしない**ことで、これを守る。
  async function enterSim() {
    stopPollers();
    clearReveal();
    // disable の復帰要求が届く先を、必ず /candles を持つ core（既定モード）にしておく。
    await setSwMode(DEFAULT_MODE);
    if (replayHandle && typeof replayHandle.disable === 'function') {
      await replayHandle.disable();
    }
    // 復帰が終わってから sim へ切り替える。
    await setSwMode(MODE.SIM);
    // 表示層はここで出す（SW を sim へ向けた後＝リプレイ層の復帰要求と混ざらない）。
    if (simHandle && typeof simHandle.enable === 'function') {
      await simHandle.enable();
    }
    activeMode = MODE.SIM;
    applyMode(MODE.SIM);
  }

  async function enterLive() {
    // SW を先に live へ。これで戻せるのは **注入点を通らない要求**（SW だけが prefix を付ける経路）。
    //   disable の全長復帰 fetch に prefix を付けるのは front の routedFetch であり、参照するのは
    //   activeMode（末尾で更新）なので、行き先は SW ではなく**遷移前モード**の core になる。
    //   replay→live は /replay/candles へ向かい replay core が答える。sim→live はリプレイ層が
    //   未 enable ゆえ disable が早期 return し（replay.js:514）、要求自体が出ない。
    //   （実測: unified_root_restore_fetch_routing.test.js）
    await setSwMode(MODE.LIVE);
    clearReveal();
    await disableSim();
    if (replayHandle && typeof replayHandle.disable === 'function') {
      await replayHandle.disable(); // reveal トリム解除＋ライブ全長再描画（chart 再構築なし）。
    }
    startPollers();
    activeMode = MODE.LIVE;
    applyMode(MODE.LIVE);
  }

  // 目標モード → 遷移手続き。3 値以上では if/else の二分岐で表せないため表で引く
  //   （第 4 モードの追加は本表への 1 行追加で済み、toggle 本体は変わらない）。
  const TRANSITIONS = {
    [MODE.LIVE]: enterLive,
    [MODE.REPLAY]: enterReplay,
    [MODE.SIM]: enterSim,
  };

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
    enterReplay,
    enterLive,
    enterSim,
    startPollers,
    stopPollers,
    getMode: () => activeMode,
  };
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
  let setupSimDisplay;
  try {
    ({ bootstrap } = await import(LIVE_ROOT));
    ({ ReplayIndicatorController } = await import(REPLAY_CONTROLLER));
    ({ setupReplay } = await import(REPLAY_DRIVER));
    ({ ReplayMarketProfileActor } = await import(REPLAY_MP_ACTOR));
    ({ installReplayBar } = await import(REPLAY_BAR_VIEW));
    ({ setupSimDisplay } = await import(SIM_ROOT));
  } catch (err) {
    showModeError(`モジュール読込に失敗しました: ${err && err.message ? err.message : err}`);
    return;
  }

  // 既存 bootstrap の setInterval/clearInterval 注入口へラップ済みタイマを渡す（一括停止の受け皿）。
  const registry = wrapTimers({
    setInterval: globalThis.setInterval.bind(globalThis),
    clearInterval: globalThis.clearInterval.bind(globalThis),
  });

  // ★ 単一 mount: chart/mainSeries/renderer/controller(=ReplayIndicatorController)/live pollers/
  //   リプレイ層ハンドルを 1 回だけ生成する（以降 toggle で再生成しない）。
  let boot;
  try {
    boot = await bootstrap({
      lwc: window.LightweightCharts,
      container: document.getElementById('chart'),
      doc: document,
      storage: scopedStorage(globalThis.localStorage, MODE.LIVE),
      // ISSUE-362: 配下の全 API クライアントはこの 1 つの fetch を使う（this._fetch）。
      //   ここで prefix 付与を担保するので、ルーティングが SW の可用性に依存しない。
      fetch: routedFetch,
      datasetRef: DATASET_REF,
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

  // sim 表示層のハンドル（enable/disable のみ）。器・CSS・3 窓・明細は sim 側が所有する。
  //   置き場所は**統合層が決める**（sim 側は渡された host へ挿すだけ＝統合ページの id を知らない）。
  //   job_id は `?job=<id>` から sim 側が読む（統合層は選ばない＝ビュー自動介入の禁止）。
  const simHandle = await setupSimDisplay({
    doc: document,
    lwc: window.LightweightCharts,
    host: bottomPane.host(),
  });

  modeController = createModeController({
    controller: boot.controller,
    replayHandle: boot.replayHandle,
    simHandle,
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
