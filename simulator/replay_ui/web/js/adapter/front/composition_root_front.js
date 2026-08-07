// composition_root_front.js（フロント側 Composition Root）。
//
// 設計入力: 内部設計書 §2.1（framework/front/composition_root_front.js）、§3.3.5（ComputeHttpClient）、
//   §6.3（/candles）、内部設計_パラメータ設定ダイアログ §9（B方式 params 実反映）。
// 依存配線: catalog / compute / persistence / renderer を IndicatorController に注入する唯一の点。
//   - upstream JS（LightweightCharts）は ChartRenderer の生成にのみ使い、ここでは
//     chart / mainSeries を作って渡す（系列追加系 API 名はここで参照しない）。
//
// 方式切替（B方式を既定に）:
//   - served（http://・https://）: ComputeHttpClient（fetch /compute）を注入し、candles は
//     GET /candles から取得する（same-origin・CORS/バンドル不要）。params が実再計算される。
//   判定は location.protocol（http/https → 'b' / それ以外 → 'a'）。

import { ChartRenderer } from './chart_renderer.js';
import { ChartInteractionController } from './chart_interaction_controller.js';
import { createChartWithMainSeries, makeUpdatePaneHeight } from './chart_bootstrap.js';
import { ScrollToLatestButton } from './scroll_to_latest_button.js';
import { TimeframeMenu, timeframeLabels } from './timeframe_menu.js';
// チャートテンプレート（基本設計_チャートテンプレート v0.1.1 §7.1）: standalone replay_ui を単体起動
//   した場合の同等配線（統合 UI 経由では live root 側の配線が使われる）。実体は indicator_ui 側の
//   単一ソースを symlink 共有する＝両モードの挙動一致を構造的に保証する（E-5）。
import { LocalStorageTemplateGateway } from './local_storage_template_gateway.js';
import { ChartTemplateMenu } from './chart_template_menu.js';
import { ChartTemplateDialogs } from './chart_template_dialogs.js';
import { ChartTemplateController } from './chart_template_controller.js';
import { CrosshairReadoutView } from './crosshair_readout_view.js';
import { PaneLegendView } from './pane_legend_view.js';
import { CurrentPriceView } from './current_price_view.js';
import { ComputeHttpClient } from './compute_http_client.js';
import { LiveUpdater } from './live_updater.js';
import { LocalStorageGateway } from './local_storage_gateway.js';
import { IndicatorCatalogClient } from './catalog_client.js';
import { ReplayIndicatorController } from './replay_indicator_controller.js';
import { TradeMarkersRenderer } from './trade_markers_renderer.js';
import { MarketProfileFormingClient } from './market_profile_forming_client.js';
import { MarketProfileClient } from './market_profile_client.js';
import { MarketProfileHistogramPrimitive } from './market_profile_primitive.js';
import { MarketProfileReplayBar } from './market_profile_replay_bar.js';
import { ReplayMarketProfileActor } from './replay_market_profile_actor.js';
import { TickvolBandsActor } from './tickvol_bands_actor.js';
import { TickvolBandsController } from './tickvol_bands_controller.js';
import { DwellAccumulator } from '../../domain/market_profile_dwell_accumulator.js';

// 既定時間足（1 分足原子からの初期表示足）と直近表示本数（§配信設計: リサンプル＋直近 N 本）。
//   1 分足原子の全期間（数百万点）を直接配信しないため、/candles・/compute を直近 N 本へ制限する。
export const DEFAULT_TIMEFRAME = '1D';
export const RECENT_BARS = 1500;

// GET /candles?datasetRef=&timeframe=&limit= で candles を取得する（B方式）。失敗時は null。
//   timeframe 省略時はサーバが原子（再集計なし）扱い、limit 省略時は全件（後方互換）。
async function fetchCandles(fetchImpl, datasetRef = 'sample', timeframe = null, limit = null) {
  if (typeof fetchImpl !== 'function') {
    return null;
  }
  try {
    let url = `/candles?datasetRef=${encodeURIComponent(datasetRef)}`;
    if (timeframe) {
      url += `&timeframe=${encodeURIComponent(timeframe)}`;
    }
    if (limit) {
      url += `&limit=${encodeURIComponent(limit)}`;
    }
    const resp = await fetchImpl(url);
    if (!resp.ok) {
      return null;
    }
    const payload = await resp.json();
    return payload && payload.ok ? payload.candles : null;
  } catch {
    return null;
  }
}

// グローバル LightweightCharts（bundled JS が window へ公開）を引数で受け取り、
// チャート + ローソク系列を生成して ChartRenderer に渡す。
export async function bootstrap({
  lwc,
  container,
  doc = (typeof document !== 'undefined' ? document : null),
  storage,
  // served 判定・/candles 取得・/compute 用の注入（テスト・SSR で差し替え可能）。
  protocol = (typeof location !== 'undefined' ? location.protocol : 'file:'),
  // ネイティブ fetch は this===window/globalThis を要求する。detached のまま
  // this._fetch(...) で呼ぶと "Illegal invocation" になるため globalThis へ束縛する。
  fetch = (typeof globalThis !== 'undefined' && globalThis.fetch
    ? globalThis.fetch.bind(globalThis) : undefined),
  // B方式の対象データセット（/candles・/compute）。既定 'sample'（既存挙動・テスト互換）。
  // アプリ入口（index.html）が 'jp225_m1' を渡すと B方式は 1 分足原子をライブ計算する。
  datasetRef = 'sample',
  // 既定時間足・直近表示本数（§配信設計）。テスト・入口で差し替え可能。
  timeframe = DEFAULT_TIMEFRAME,
  recentBars = RECENT_BARS,
  // ライブ更新のタイマー実装（注入・テストでフェイク化）。合成根自身は setInterval を
  //   呼ばず、LiveUpdater へ実装を渡すだけにする（タイマー依存を合成根に置かない）。
  setInterval: setIntervalImpl = (typeof globalThis !== 'undefined' ? globalThis.setInterval.bind(globalThis) : undefined),
  clearInterval: clearIntervalImpl = (typeof globalThis !== 'undefined' ? globalThis.clearInterval.bind(globalThis) : undefined),
  // ライブ更新間隔（ms・既定 60 秒）。テストで差し替え可能。
  liveIntervalMs = 60000,
} = {}) {

  // チャート生成（組み立て点）。生成オプション・メイン系列は共有ヘルパ chart_bootstrap（ISSUE-123・
  //   present と単一ソース＝クロスヘア Normal・現在値ライン橙 ISSUE-084 も replay へ自動伝播）。
  const { chart, mainSeries } = createChartWithMainSeries({ lwc, container });
  // ポート実装: ComputeHttpClient（fetch /compute）。candles は /candles から取得する。
  const compute = new ComputeHttpClient({ fetch });

  // クロスヘア価格読み取り欄（左上固定オーバーレイ）のビュー。ChartRenderer の onCrosshairReadout
  //   に (dto) => view.render(dto) を注入する（#legend の指標管理行とは別物として分離・相乗りしない）。
  //   doc 不在（SSR/テスト）でも render は防御的に no-op（要素不在で安全）。
  const readoutView = new CrosshairReadoutView({ document: doc, elementId: 'crosshair-readout' });

  // ChartRenderer は upstream API の唯一の隔離点。v5 シリーズ定義（LineSeries/HistogramSeries）と
  // createTextWatermark を lwc 名前空間ごと渡す（系列追加系 API 名の参照を本所外へ漏らさない）。
  // ペイン別凡例（ISSUE-276）。ライブと同一実装（symlink 参照・コード複製なし）。描画先の器は
  //   View 自身が版面（.chart-wrap）配下へ生成する＝HTML への直書きと 3 ページ同期を持ち込まない。
  const paneLegendView = new PaneLegendView({ document: doc });

  const renderer = new ChartRenderer({
    chart, mainSeries, lwc, onCrosshairReadout: (dto) => readoutView.render(dto),
    onPaneLegend: (model) => paneLegendView.update(model),
  });

  // 価格軸ホイールズームの座標→価格変換に使う pane 高（container 高 - timeScale 高）を供給する。
  //   coordinateToPrice(paneHeight) で価格レンジ下端を読むために必要。container/timeScale 非対応
  //   （SSR/テスト）では設定できないため no-op（handlePriceWheel は pane 高未供給時に安全に false）。
  //   リサイズで container 高が変わるため、autoSize 変化に追随できるよう wheel 発火時にも再計算する。
  //   実体は共有ヘルパ chart_bootstrap.makeUpdatePaneHeight（ISSUE-123 単一ソース・旧忠実移植コピーを廃止）。
  const updatePaneHeight = makeUpdatePaneHeight({ container, chart, renderer });
  updatePaneHeight();
  const persistence = new LocalStorageGateway(storage);
  // テンプレート永続化（§4.2 の 3 キー）。既存 LocalStorageGateway は無改変（ISP）。
  const templateStore = new LocalStorageTemplateGateway(storage);
  const catalog = new IndicatorCatalogClient();
  // param 既定値と variant ごとの受理 param を GET /catalog で解決する（ライブ root と同一・
  //   ISSUE-092 ③ / ISSUE-278 #8）。呼ばないと front は variant が受理しない param を送り、
  //   back のフェイルクローズで validation エラーになる（standalone replay だけの取り残しだった）。
  //   overlay は controller 生成前に完了させる。load は例外を投げない（内部で吸収）。
  await catalog.load(fetch);

  // 時間足切替で candles を再取得するためのローダ（B方式のみ）。A方式（SAMPLE_DATA・再集計不可）は null。
  //   controller.setTimeframe が (datasetRef, timeframe) で呼び、直近 recentBars 本へ制限して取得する。
  const loadCandles = (ref, tf) => fetchCandles(fetch, ref, tf, recentBars);

  const controller = new ReplayIndicatorController({
    catalog, compute, persistence, renderer, document: doc, datasetRef,
    timeframe, recentBars, loadCandles,
    // Phase5（統一成長）: reveal は常に成長状態＝growing=true。旧 'ticklive' 表示モードが担っていた成長
    //   活性化を成長軸へ移行し、mpGrowthResolver で常時 growing を注入する（setParams 後に _applyMpGrowth が
    //   適用）。normal/replay+growing は push 成長（enterBar/growTo/feedTick）、sessions+growing は
    //   refresh(to) 成長（機構A）。mode 解決役は注入しない＝gear 選択モードをそのまま維持（present と同型）。
    mpGrowthResolver: () => true,
  });
  // ペイン別凡例へ行（ラベル・可視・操作）を供給する（ライブと同一配線・ISSUE-276）。
  controller.setPaneLegendView(paneLegendView);

  // テンプレート協働子（§7.1）。有効時間足集合は composition root から注入する（U1・replay は
  //   下の TimeframeMenu へ注入する 8 足＝サーバ TIMEFRAME_RULES と一致・30m 非対応）。
  //   メニュー・ダイアログは下（共有 UI 部品の配線位置）で生成し attachUi で結ぶ。
  const chartTemplates = new ChartTemplateController(controller, {
    gateway: templateStore,
    validTimeframes: ['1m', '5m', '15m', '1h', '4h', '1D', '1W', '1M'],
    // 保存ダイアログの文言用ラベル写像（§6.2）。単一情報源は timeframe_menu.js の groups
    //   （replay の 8 足は既定 groups の部分集合でラベル語彙は同一）。
    timeframeLabels: timeframeLabels(),
  });
  // 時間足切替への介入（§7.2）: 購読スロット（setTimeframeObserver）は単数かつ売買マーカーで
  //   使用済み（E-7）のため使わず、own property での差し替え 1 行で行う。順序（除去 → 切替 →
  //   適用）と再入防止は協働子が所有する。
  const proceedSetTimeframe = controller.setTimeframe.bind(controller);
  const proceedTemplateTimeframe = (tf) => chartTemplates.onTimeframeChange(tf, proceedSetTimeframe);

  // 取引密度帯（時刻帯の背景色・1 時間足以下）。standalone replay（8280）用の配線。統合 UI では
  //   live root だけが実行されるため、そちらの同名配線が効く（本 root は単体起動時のみ通る）。
  //   getUntil: リビール T（controller._untilTime）＝当日を集計に含めない因果窓の基準。
  const tickvolBands = new TickvolBandsActor({
    fetch, datasetRef, renderer,
    getTimeframe: () => controller._timeframe,
    getUntil: () => (controller._untilTime != null ? controller._untilTime : null),
  });
  controller.registerActorController('tickvol_bands', new TickvolBandsController(controller, tickvolBands));
  // 時間足切替: 帯は時間足に依存しないので再取得せず、塗る足だけ引き直す（テンプレート介入の内側へチェーン）。
  controller.setTimeframe = (tf) => {
    const done = proceedTemplateTimeframe(tf);
    tickvolBands.onTimeframeChange();
    return done;
  };
  // リプレイ時計の前進: セッション日が変わったときだけ再取得する（日内は応答不変＝当日非参照）。
  const proceedUntil = controller.setUntilTime.bind(controller);
  controller.setUntilTime = (t) => {
    const done = proceedUntil(t);
    tickvolBands.onClock();
    return done;
  };

  // チャート操作（価格軸 wheel ズーム・dblclick 自動スケール復帰・本体縦パン）の配線。
  //   ISSUE-123: 旧・独立コピーを廃止し present と同一実体（symlink 単一ソース）を参照する。
  //   旧コピー固有だった「MP リプレイモード中は縦パンを開始しない」ゲート（_isReplayOn）は
  //   isVerticalPanBlocked オプション注入で維持する（挙動保存・controller は遅延参照）。
  new ChartInteractionController({
    container,
    renderer,
    getController: () => controller,
    updatePaneHeight,
    isVerticalPanBlocked: () => !!(controller && controller._marketProfile
      && typeof controller._marketProfile.isReplay === 'function'
      && controller._marketProfile.isReplay()),
  }).install();

  // ISSUE-122（第1段・UI 共有化）: present と同一の共有 UI 部品（symlink 単一ソース）を replay へも配線。
  //   ・ScrollToLatestButton: 右下ホットゾーンホバーで » 表示→クリックで最新（revealed 末尾）足へ復帰。
  //   ・TimeframeMenu: 時間足ドロップダウンの開閉制御（選択・active/ラベル同期は既存 [data-timeframe] 配線）。
  //   いずれも DOM 不在は install 内防御で no-op。
  new ScrollToLatestButton({ container, renderer, document: doc }).install();
  // replay の対応時間足（サーバ TIMEFRAME_RULES と一致・30m 非対応）。DOM は共有コンポーネントが生成。
  new TimeframeMenu({
    document: doc,
    groups: [
      { cat: '分', items: [['1m', '1分'], ['5m', '5分'], ['15m', '15分']] },
      { cat: '時間', items: [['1h', '1時間'], ['4h', '4時間']] },
      { cat: '日', items: [['1D', '日'], ['1W', '週'], ['1M', '月']] },
    ],
  }).install();
  // チャートテンプレートのメニュー・ダイアログ（§6.1・§6.2）。項目 DOM は共有 JS が生成し、
  //   index.html には空マウント（#tpl-menu）のみを置く。メニューは協働子を import せず
  //   コールバック注入で結ぶ（DIP）。
  const chartTemplateDialogs = new ChartTemplateDialogs({ document: doc });
  const chartTemplateMenu = new ChartTemplateMenu({
    document: doc,
    // U6: 開くたびに最新のビューモデルで再描画する（restore() との順序依存を作らない）。
    provide: () => chartTemplates.viewModel(),
    onSelect: (templateId) => chartTemplates.applyTemplate(templateId),
    onSave: () => chartTemplates.openSaveDialog(),
    onBind: (templateId) => chartTemplates.bindCurrentTimeframe(templateId),
    onManage: () => chartTemplates.openManageDialog(),
  });
  chartTemplateMenu.install();
  chartTemplates.attachUi({ menu: chartTemplateMenu, dialogs: chartTemplateDialogs });

  // B方式は /candles から実 OHLCV を取得し、メイン系列を差し替える（/compute と時間軸を揃える）。
  //   初期は既定時間足・直近 recentBars 本。取得失敗時は SAMPLE_DATA のまま（フォールバック）。
  const ready = fetchCandles(fetch, datasetRef, timeframe, recentBars).then((candles) => {
        if (candles && candles.length > 0) {
          // renderer.setCandles 経由で _lastBar も立てる（読み取り欄の hover 解除フォールバック）。
          renderer.setCandles(candles);
        }
      });

  // ライブ更新（1 分間隔）の組み立て。served（B方式）のみ。tick は controller 経由の再計算
  //   ＋ /candles 再取得 → 最新足を renderer.updateLastCandle で差分反映する。start は入口
  //   （index.html）が served 時のみ呼ぶ。A方式（file://）は null（更新を配線しない）。
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

  // Trade Markers renderer（売買マーカー重畳）の組み立て。副作用 fetch は増やさず renderer を
  //   返すのみ（load トリガは入口 index.html が ready 後に呼ぶ＝既存 candles 経路に非干渉）。
  //   chart も渡し、可視時間範囲を購読して範囲内マーカーのみ描画する（§9 Fix v3・左端クランプ列の除去）。
  //   購読 API 非提供時は全件描画フォールバック（後方互換）。
  //   v6（§12）: renderer（ChartRenderer）を渡し、hover 中ペア外のローソク足を per-bar 減光させる
  //   （減光/復元は ChartRenderer に閉じる＝upstream 隔離維持）。renderer 生成は tradeMarkers より先のため、
  //   candle 変更 observer は setCandleObserver で後据えする（ChartRenderer 起点同期・必須条件1/2）。
  //   ISSUE-026: document / container を注入し、ポップアップ配置の基準を bootstrap が受け取った
  //   container（チャート要素）に固定する（getElementById('chart') リテラルフォールバックを回避）。
  const tradeMarkers = new TradeMarkersRenderer({ lwc, mainSeries, chart, chartRenderer: renderer, document: doc, container });
  // 現在値の大型表示（#current-price・サイズは CSS 規定・ライブと同一設計）。candle 変更 observer は
  //   単一スロットのため tradeMarkers への通知と同一コールバック内で現在値ビューも更新する。
  const currentPriceView = new CurrentPriceView({ document: doc, elementId: 'current-price' });
  renderer.setCandleObserver(() => {
    tradeMarkers.onCandlesChanged();
    currentPriceView.render(renderer.lastClose());
    // 足の差し替え（時間足・期間プリセット・カレンダー・リビール）で塗る足を引き直す。
    //   帯そのものは時間足・足集合に依存しないため再取得は起きない（写像のやり直しのみ）。
    tickvolBands.onCandlesChanged();
  });

  // 指標の追加・削除で pane（と pane 内の系列）が作り直されるため、背景プリミティブを張り直す。
  //   購読スロットは単数で、統合レイヤでは後から replay.js が自分の購読を入れる。上書きで本フックが
  //   消えないよう setAppliedObserver 自体を合成する（後続購読者の挙動は不変・解除も従来どおり）。
  const proceedSetAppliedObserver = controller.setAppliedObserver.bind(controller);
  proceedSetAppliedObserver(() => tickvolBands.onPanesChanged());
  controller.setAppliedObserver = (observer) => proceedSetAppliedObserver(() => {
    if (typeof observer === 'function') {
      observer();
    }
    tickvolBands.onPanesChanged();
  });

  // Market Profile 全モード（MP DI 集約点）。共有 present MarketProfileActor を extends した
  //   ReplayMarketProfileActor を組み立て、戻り値に marketProfile として公開する（replay.js/setupReplay は
  //   new せず受け取るだけ＝DI 集約）。基底の normal/sessions/replay 駆動を再利用し、reveal 差（因果 as-of /
  //   push 駆動 ticklive）は subclass の override/追加で吸収する（present 無改変）。
  //   - client（共有 MarketProfileClient）: /market_profile を取得（normal/sessions/replay の refresh/_fetchAt）。
  //   - formingClient / makeAccumulator: ticklive の push 成長（enterBar/feedTick）。
  //   - renderer / getCandles: sessions（ローソク透明化 / OHLC 突合）・profile margin・snapshot trim。
  //   - replayBar: replay モードの scrub バー（onScrub→setReplayCursor / onChange→onReplayControlsChange）。
  //   - getContext.to（=controller._untilTime＝リビール T）: 全モードで as-seen-at-t を成立させる因果カーソル。
  //   既定は setEnabled(false)＝OFF（indicator メニューの market_profile 追加で ON・既存 replay へ非干渉）。
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
  // 同一 actor を controller へも注入する（メニュー一本化）。controller.applyIndicator('market_profile')
  //   が本 actor を有効化（setEnabled）し、setupReplay 側の駆動フック（render→enterBar / animateForming→
  //   feedTick）が isEnabled()=true を観測して育てる。controller は getContext で controller._timeframe を
  //   遅延参照するため marketProfile を後に生成しており、ここで後注入する（同一実体の共有）。
  controller._marketProfile = marketProfile;

  // 時間足変更を売買マーカーへ通知し、該当時間足（建玉の時間足）以外は非表示にする。
  //   初期時間足を反映し、以降は controller の時間足購読で連動する。
  tradeMarkers.setCurrentTimeframe(timeframe);
  controller.setTimeframeObserver((tf) => tradeMarkers.setCurrentTimeframe(tf));

  return { chart, mainSeries, renderer, controller, ready, tickvolBands, liveUpdater, tradeMarkers, marketProfile, replayBar, chartTemplates, chartTemplateMenu, chartTemplateDialogs };
}
