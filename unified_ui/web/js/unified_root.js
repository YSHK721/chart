// 統合エントリのブートストラップ（単一 mount + リプレイ層のオン・オフ）。
//
// 役割:
//   - **単一 chart を 1 回だけ生成**する。live root（indicator_ui）の bootstrap を 1 回呼び、
//     リプレイ部品 { ReplayIndicatorController, setupReplay } を **オプション注入**して「リプレイ層」を
//     同一 chart 上へ配線する（controller は ReplayIndicatorController＝untilTime=undefined で live 等価）。
//   - モード切替は chart/ローソク/指標を **dispose も再生成もしない**。live pollers（LiveUpdater/
//     FormingBarUpdater/LiveTickPlayer）の start/stop と、リプレイ層ハンドルの enable/disable、
//     controller.clearRevealCache、body クラス、SW アクティブモード通知だけで切り替える（チラつき・重複ゼロ）。
//   - Service Worker（/sw.js）を登録し、root 相対 API fetch をアクティブモード prefix（/live・/replay）へ
//     リライトさせる。SW が制御に入れない環境は **フェイルクローズ**（明示エラー・mount しない）。
//
// 無波及順守:
//   - live root はリプレイのコードを import しない（統合レイヤが注入する）＝スタンドアロン live は byte 不変。
//   - MP は当面モード別アクター差替が対象外。live MP stack は無改変で維持し、リプレイ層へは marketProfile を
//     渡さない（live root 側で null 注入）。既定ビュー（MP 無効）で単一化・チラつき解消を成立させる。

import { wrap as wrapTimers } from './timer_registry.js';
import { scopedStorage } from './mode_storage.js';
import { registerServiceWorker, notifySwMode } from './sw_client.js';
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

  async function enterReplay() {
    stopPollers();
    clearReveal();
    // SW を先に replay へ（enable の loadTimeframe が /replay/candles・/replay/compute を叩く）。
    await setSwMode(MODE.REPLAY);
    if (replayHandle && typeof replayHandle.enable === 'function') {
      await replayHandle.enable();
    }
    activeMode = MODE.REPLAY;
    applyMode(MODE.REPLAY);
  }

  async function enterLive() {
    // SW を先に live へ（disable の全長復帰 fetch が /live/candles を叩く＝ライブ末尾へ確実に戻す）。
    await setSwMode(MODE.LIVE);
    clearReveal();
    if (replayHandle && typeof replayHandle.disable === 'function') {
      await replayHandle.disable(); // reveal トリム解除＋ライブ全長再描画（chart 再構築なし）。
    }
    startPollers();
    activeMode = MODE.LIVE;
    applyMode(MODE.LIVE);
  }

  async function toggle(next) {
    const target = next || (activeMode === MODE.REPLAY ? MODE.LIVE : MODE.REPLAY);
    if (switching || target === activeMode) {
      return;
    }
    switching = true;
    try {
      if (target === MODE.REPLAY) {
        await enterReplay();
      } else {
        await enterLive();
      }
    } finally {
      switching = false;
    }
  }

  return {
    toggle,
    enterReplay,
    enterLive,
    startPollers,
    stopPollers,
    getMode: () => activeMode,
  };
}

// ---- 単一 mount 起動 ----------------------------------------------------------
async function main() {
  // SW が制御下に入れないと root 相対 API fetch がリライトされず router が 404 する→フェイルクローズ。
  const swControlled = await registerServiceWorker();
  if (!swControlled) {
    showModeError(
      'Service Worker を有効化できないため起動を中止しました。ページをリロードするか、'
        + 'ES Modules Service Worker 対応ブラウザで開いてください'
        + '（未対応だと API 要求がルーティングされず動作しません）。',
    );
    return;
  }

  // 初期 live: 以降の API fetch を /live へ回す（vendor/core/candles すべて live prefix）。
  await notifySwMode(MODE.LIVE);

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
  try {
    ({ bootstrap } = await import(LIVE_ROOT));
    ({ ReplayIndicatorController } = await import(REPLAY_CONTROLLER));
    ({ setupReplay } = await import(REPLAY_DRIVER));
    ({ ReplayMarketProfileActor } = await import(REPLAY_MP_ACTOR));
    ({ installReplayBar } = await import(REPLAY_BAR_VIEW));
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
      datasetRef: DATASET_REF,
      setInterval: registry.setInterval,
      clearInterval: registry.clearInterval,
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

  modeController = createModeController({
    controller: boot.controller,
    replayHandle: boot.replayHandle,
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
