// composition_root_front.js（standalone replay の Composition Root）。
//
// 設計入力: 内部設計書 §2.1（framework/front/composition_root_front.js）、§3.3.5（ComputeHttpClient）、
//   §6.3（/candles）、内部設計_パラメータ設定ダイアログ §9（B方式 params 実反映）。
//
// ISSUE-278 #4: 本ファイルはかつて **ライブ合成根の全文フォーク**（338 行）だった。同じ配線が 2 か所に
//   手書きで存在するため、ライブ側の修正がリプレイへ届かず取り残しを繰り返していた（`#rp-mode` の
//   option 欠落 4079461 ／ カテゴリボタン ISSUE-221 ／ 凡例の器 ISSUE-277 ／ `catalog.load` 未呼出）。
//   さらにフォーク側の手書き定数が陳腐化していた（「30m 非対応」の 8 足リスト。実測ではリプレイ core の
//   `/candles?timeframe=30m` も `/compute` も 200 を返す＝制約はとうに消えていた）。
//
//   共有配線は chart_app_wiring.js（ライブ実体を symlink 参照＝単一ソース）が所有する。本 root には
//   **リプレイ固有の差だけ**を残す:
//     - ReplayIndicatorController（untilTime＝リビール T を持つ）と常時成長（mpGrowthResolver）
//     - MP リプレイ表示モード中は縦パンを開始しない（isVerticalPanBlocked）
//     - MP scrub バー（MarketProfileReplayBar）と ReplayMarketProfileActor
//   ライブ 3 ポーラ（LiveUpdater/FormingBarUpdater/LiveTickPlayer）・ライブ追従・tf-period 列は
//   リプレイ core に対応エンドポイントが無いため配線しない（ライブ root が持つ）。

import {
  composeChartShell,
  installSharedUi,
  wireControllerCollaborators,
  createPositionSizingContextItems,
  fetchCandles,
} from './chart_app_wiring.js';
import { LiveUpdater } from './live_updater.js';
import { ReplayIndicatorController } from './replay_indicator_controller.js';
import { MarketProfileFormingClient } from './market_profile_forming_client.js';
import { MarketProfileClient } from './market_profile_client.js';
import { MarketProfileHistogramPrimitive } from './market_profile_primitive.js';
import { MarketProfileReplayBar } from './market_profile_replay_bar.js';
import { ReplayMarketProfileActor } from './replay_market_profile_actor.js';
import { installReplayBar } from './replay_bar_view.js';
import { DwellAccumulator } from '../../domain/market_profile_dwell_accumulator.js';

// 既定時間足（1 分足原子からの初期表示足）と直近表示本数（§配信設計: リサンプル＋直近 N 本）。
//   1 分足原子の全期間（数百万点）を直接配信しないため、/candles・/compute を直近 N 本へ制限する。
export const DEFAULT_TIMEFRAME = '1D';
export const RECENT_BARS = 1500;

// グローバル LightweightCharts（bundled JS が window へ公開）を引数で受け取り、
// チャート + ローソク系列を生成して ChartRenderer に渡す。
export async function bootstrap({
  lwc,
  container,
  doc = (typeof document !== 'undefined' ? document : null),
  storage,
  // ネイティブ fetch は this===window/globalThis を要求する。detached のまま
  // this._fetch(...) で呼ぶと "Illegal invocation" になるため globalThis へ束縛する。
  fetch = (typeof globalThis !== 'undefined' && globalThis.fetch
    ? globalThis.fetch.bind(globalThis) : undefined),
  // B方式の対象データセット（/candles・/compute）。既定 'sample'（既存挙動・テスト互換）。
  datasetRef = 'sample',
  // 既定時間足・直近表示本数（§配信設計）。テスト・入口で差し替え可能。
  timeframe = DEFAULT_TIMEFRAME,
  recentBars = RECENT_BARS,
  // ライブ更新のタイマー実装（注入・テストでフェイク化）。合成根自身は setInterval を呼ばない。
  setInterval: setIntervalImpl = (typeof globalThis !== 'undefined' ? globalThis.setInterval.bind(globalThis) : undefined),
  clearInterval: clearIntervalImpl = (typeof globalThis !== 'undefined' ? globalThis.clearInterval.bind(globalThis) : undefined),
  // ライブ更新間隔（ms・既定 60 秒）。テストで差し替え可能。
  liveIntervalMs = 60000,
} = {}) {
  // controller 以前の組み立て（チャート・描画・永続化・catalog）は両 root 共有の単一ソースへ委譲する。
  //   catalog.load が param 既定値と variant ごとの受理 param を overlay する（ISSUE-278 #8）。
  const {
    chart, mainSeries, compute, paneLegendView, currentPriceView, renderer,
    updatePaneHeight, persistence, templateStore, catalog, loadCandles,
    // 指標カラーテーマ（段階 3）。ライブと同一配線＝テーマ集合・選択中テーマはモード間で共有される
    //   （storage 名前空間が同一実体・E-17）。
    themeStore, themeState, chromeThemeApplier,
    // 銘柄仕様（呼び値・表示桁）。ライブと同一配線＝解決は共有配線の 1 回だけで、
    //   root は識別子を通すだけ（themeStore / themeState と同一の受け渡し規約）。
    symbolSpec,
  } = await composeChartShell({ lwc, container, doc, storage, fetch, datasetRef, recentBars });

  let controller;
  let chartTemplates = null;
  let colorThemes = null;
  // ISSUE-368 スライス 7: 計算機の協働子は wireControllerCollaborators で生成される（遅延参照）。
  let positionSizing = null;
  // controller に依存しない UI 部品（操作・スクロール・時間足メニュー・テンプレートメニュー）。
  //   リプレイ固有の差は「MP リプレイ表示モード中は縦パンを開始しない」ゲートのみ（旧フォークの
  //   _isReplayOn を注入で維持）。controller / 協働子は遅延参照で渡す（生成はこの後）。
  const {
    chartTemplateMenu, chartTemplateDialogs, colorThemeMenu, colorThemeDialogs,
    positionSizingDialog, chartToast, registerVerticalPanBlocker,
  } = installSharedUi({
    container,
    renderer,
    doc,
    getController: () => controller,
    updatePaneHeight,
    isVerticalPanBlocked: () => !!(controller && controller._marketProfile
      && typeof controller._marketProfile.isReplay === 'function'
      && controller._marketProfile.isReplay()),
    getTemplates: () => chartTemplates,
    getColorThemes: () => colorThemes,
    getPositionSizing: () => positionSizing,
    // 右クリックの価格設定 3 項目（R-P3）。確定要件（ISSUE.md:6927「ライブ＋リプレイ両方に載せる」）
    //   によりライブと**対称**に注入する。共有配線が無条件に足す形にはしない（注入機構は維持）＝
    //   計算機を載せないページが将来増えても、そのページだけ項目が出ない状態を作れる。
    contextMenuItems: createPositionSizingContextItems({
      renderer,
      getPositionSizing: () => positionSizing,
      // 告知先（下段ペインの案内・裁定 2026-08-20）はライブと同一で遅延参照。共有トーストは
      //   installSharedUi の内側で生成されるため、この引数を作る時点では未生成である。
      getToast: () => chartToast,
    }),
    // standalone replay のツールバーはライブ追従トグルもリプレイトグルも持たない
    //   （ライブ更新が無く、切替先のライブも無い）。
    toolbar: { liveFollow: false, enterReplay: false },
    // 足情報のコピーが画面の読み取り欄と同じ桁で書くための転送（工程 5 是正 A）。
    //   ライブ root と**対称**に渡す。
    symbolSpec,
  });
  // リプレイ操作バー（ISSUE-278 #16: markup は View が所有＝2 ページ複製をやめた）。
  //   「リプレイ終了（✕）」は統合 UI だけが持つ（standalone には戻り先のライブが無い）。
  installReplayBar(doc, { withClose: false });

  controller = new ReplayIndicatorController({
    catalog, compute, persistence, renderer, document: doc, datasetRef,
    timeframe, recentBars, loadCandles,
  });
  // ペイン別凡例へ行（ラベル・可視・操作）を供給する（ライブと同一配線・ISSUE-276）。
  controller.setPaneLegendView(paneLegendView);

  // Market Profile 全モード（MP DI 集約点）。共有 present MarketProfileActor を extends した
  //   ReplayMarketProfileActor を組み立て、戻り値に marketProfile として公開する（replay.js/setupReplay は
  //   new せず受け取るだけ＝DI 集約）。基底の normal/sessions/replay 駆動を再利用し、reveal 差（因果 as-of /
  //   push 駆動 ticklive）は subclass の override/追加で吸収する（present 無改変）。
  //   バーのホストは index.html の #mp-replay-bar-host（チャート下部・sibling）を優先し、不在時は container。
  const mpReplayHost = (doc && typeof doc.getElementById === 'function'
    ? doc.getElementById('mp-replay-bar-host') : null) || container;
  const replayBar = new MarketProfileReplayBar({
    document: doc,
    container: mpReplayHost,
    onScrub: (time) => { if (controller && controller._marketProfile) { controller._marketProfile.setReplayCursor(time); } },
    onChange: () => { if (controller && controller._marketProfile) { controller._marketProfile.onReplayControlsChange(); } },
  });
  const marketProfile = new ReplayMarketProfileActor({
    client: new MarketProfileClient({ fetch }),
    formingClient: new MarketProfileFormingClient({ fetch }),
    makeAccumulator: () => new DwellAccumulator(),
    primitive: new MarketProfileHistogramPrimitive(),
    mainSeries,
    replayBar,
    renderer,
    getCandles: () => renderer.getCandles(),
    // to（=controller._untilTime＝リビール T）を遅延読み取りし、全モードで as-seen-at-t（因果）を成立させる。
    getContext: () => ({ datasetRef, timeframe: controller._timeframe, to: controller._untilTime }),
  });
  // 同一 actor を controller へも渡す（メニュー一本化）。controller.applyIndicator('market_profile')
  //   が本 actor を有効化（setEnabled）し、setupReplay 側の駆動フック（render→enterBar / animateForming→
  //   feedTick）が isEnabled()=true を観測して育てる。
  //
  //   ISSUE-479 Wave2b J-1 OCP-5 S3: 本代入は **reveal 固有の経路**（ReplayIndicatorController の
  //   _recomputeMarketProfile が enterBar / refresh を直に駆動する）のためだけに残る。MP 協働子への
  //   供給は下の wireControllerCollaborators が担う（協働子は host のフィールド名を見ない）。
  //   actor の生成は controller の直後へ前倒しした（getContext は controller を遅延参照するため
  //   生成順の制約は無い）。これでライブ root と同じく「共有配線へ実体を渡す」形に揃う。
  controller._marketProfile = marketProfile;

  // controller 生成後の協働子（テンプレート・取引密度帯・売買マーカー・現在値）は共有配線が結ぶ。
  const {
    chartTemplates: templates, tickvolBands, tradeMarkers, colorThemes: themes,
    positionSizing: sizing,
  } = wireControllerCollaborators({
    controller, renderer, doc, fetch, datasetRef, timeframe, recentBars,
    templateStore, chartTemplateMenu, chartTemplateDialogs,
    themeStore, themeState, chromeThemeApplier, colorThemeMenu, colorThemeDialogs,
    positionSizingDialog, registerVerticalPanBlocker, chartToast,
    lwc, mainSeries, chart, container, currentPriceView,
    // ISSUE-479 Wave2 J-1 OCP-5 S2/S3: MP のアクターと成長解決役を共通登録口へ渡す（唯一の供給経路）。
    //   Phase5（統一成長）: reveal は常に成長状態＝growing=true。旧 'ticklive' 表示モードが担っていた
    //   成長活性化を成長軸へ移行し、常時 growing を注入する（setParams 後に applyMpGrowth が適用）。
    //   normal/replay+growing は push 成長（enterBar/growTo/feedTick）、sessions+growing は
    //   refresh(to) 成長（機構A）。mode 解決役は渡さない＝gear 選択モードをそのまま維持（present と同型）。
    marketProfile,
    mpGrowthResolver: () => true,
  });
  chartTemplates = templates;
  colorThemes = themes;
  // 遅延参照の解決（ここで初めてメニュー・モーダル・右クリック項目が生きる）。
  positionSizing = sizing ? sizing.controller : null;

  // B方式は /candles から実 OHLCV を取得し、メイン系列を差し替える（/compute と時間軸を揃える）。
  //   初期は既定時間足・直近 recentBars 本。取得失敗時は SAMPLE_DATA のまま（フォールバック）。
  const ready = fetchCandles(fetch, datasetRef, timeframe, recentBars).then((candles) => {
    if (candles && candles.length > 0) {
      // renderer.setCandles 経由で _lastBar も立てる（読み取り欄の hover 解除フォールバック）。
      renderer.setCandles(candles);
    }
  });

  // ライブ更新（1 分間隔）の組み立て。start は入口が呼ぶ（リプレイ入口は再生ドライバを起動するため
  //   呼ばない＝構築のみ・戻り値として公開する）。
  const liveUpdater = new LiveUpdater({
    controller,
    renderer,
    loadCandles: (ref, tf) => fetchCandles(fetch, ref, tf, recentBars),
    datasetRef,
    getTimeframe: () => controller._timeframe,
    setInterval: setIntervalImpl,
    clearInterval: clearIntervalImpl,
    intervalMs: liveIntervalMs,
  });


  return {
    chart, mainSeries, renderer, controller, ready, tickvolBands, liveUpdater,
    tradeMarkers, marketProfile, replayBar, chartTemplates, chartTemplateMenu, chartTemplateDialogs,
  };
}
