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
//   - file:// 単体時: 従来 EmbeddedComputeGateway + SAMPLE_DATA にフォールバック（A方式）。
//   判定は location.protocol（http/https → 'b' / それ以外 → 'a'）。

// SAMPLE_DATA（埋め込み 635KB）は A方式（file://）でのみ動的 import する。B方式（served）は
// candles を /candles から取得するため読み込まない（不要な 635KB の単一障害点を排除）。
import { ChartRenderer } from './chart_renderer.js';
import { CrosshairReadoutView } from './crosshair_readout_view.js';
import { CurrentPriceView } from './current_price_view.js';
import { ComputeHttpClient } from './compute_http_client.js';
import { LiveUpdater } from './live_updater.js';
import { LiveFollowController } from './live_follow_controller.js';
import { FormingBarUpdater } from './forming_bar_updater.js';
import { LiveTickPlayer, isPlayerTimeframe } from './live_tick_player.js';
import { EmbeddedComputeGateway } from './embedded_compute_gateway.js';
import { LocalStorageGateway } from './local_storage_gateway.js';
import { IndicatorCatalogClient } from './catalog_client.js';
import { IndicatorController } from './indicator_controller.js';
import { TradeMarkersRenderer } from './trade_markers_renderer.js';
import { MarketProfileClient, MP_TO_LATEST } from './market_profile_client.js';
import { MarketProfileFormingClient } from './market_profile_forming_client.js';
import { DwellAccumulator } from '../../domain/market_profile_dwell_accumulator.js';
import { MarketProfileHistogramPrimitive } from './market_profile_primitive.js';
import { ProfileSink, TfPeriodSink } from './mp_primitive_roles.js';
import { mpSupportsTf, mpTfPeriodSrc } from '../../domain/mp_source_capability.js';
import { TfPeriodProfileClient } from './tf_period_profile_client.js';
import { TfPeriodJitterBuffer } from './tf_period_jitter_buffer.js';
import { TfPeriodProfileActor } from './tf_period_profile_actor.js';
import { TF_BAR_SEC } from '../../domain/tf_meta.js';
import { TfPeriodTooltip, formatPeriodLabel } from './tf_period_tooltip.js';
import { MarketProfileActor } from './market_profile_actor.js';
import { TickvolBandsActor } from './tickvol_bands_actor.js';
import { TickvolBandsController } from './tickvol_bands_controller.js';
import { ChartInteractionController } from './chart_interaction_controller.js';
import { createChartWithMainSeries, makeUpdatePaneHeight } from './chart_bootstrap.js';
import { ScrollToLatestButton } from './scroll_to_latest_button.js';
import { TimeframeMenu, timeframeLabels } from './timeframe_menu.js';
// チャートテンプレート（基本設計_チャートテンプレート v0.1.1 §7.1）: gateway・menu・dialogs・協働子を
//   ここで生成・注入する。統合 UI の唯一の bootstrap 経路（E-9）であるため、この配線でライブ・
//   リプレイ両モードに同時成立する。
import { LocalStorageTemplateGateway } from './local_storage_template_gateway.js';
import { ChartTemplateMenu } from './chart_template_menu.js';
import { ChartTemplateDialogs } from './chart_template_dialogs.js';
import { ChartTemplateController } from './chart_template_controller.js';
// GrowthCoordinator は共有 market_profile モジュール（usecase/growth_coordinator.js）へ移設済み。
//   present は adapter/front/mp_live_mode_coordinator.js（symlink）経由で import（byte 不変 retarget）。
import { GrowthCoordinator } from './mp_live_mode_coordinator.js';

// 既定時間足（1 分足原子からの初期表示足）と直近表示本数（§配信設計: リサンプル＋直近 N 本）。
//   1 分足原子の全期間（数百万点）を直接配信しないため、/candles・/compute を直近 N 本へ制限する。
export const DEFAULT_TIMEFRAME = '1D';
export const RECENT_BARS = 1500;

// protocol → モード判定。http/https は served（B方式）、それ以外（file: 等）は A方式。
export function modeForProtocol(protocol) {
  return protocol === 'http:' || protocol === 'https:' ? 'b' : 'a';
}

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

// GET /forming_bar?datasetRef=&timeframe= で選択 tf の「現在期間の形成中バー」を取得する（B方式）。
//   応答 {ok, bar:{time,open,high,low,close,volume}|null}。対象外/ティック無し/失敗時は null。
async function fetchFormingBar(fetchImpl, datasetRef, timeframe) {
  if (typeof fetchImpl !== 'function') {
    return null;
  }
  try {
    let url = `/forming_bar?datasetRef=${encodeURIComponent(datasetRef)}`;
    if (timeframe) {
      url += `&timeframe=${encodeURIComponent(timeframe)}`;
    }
    const resp = await fetchImpl(url);
    if (!resp.ok) {
      return null;
    }
    const payload = await resp.json();
    return payload && payload.ok ? payload.bar : null;
  } catch {
    return null;
  }
}

// GET /live_ticks?since= で増分 tick を取得する（B方式・ISSUE-049）。応答
//   {ok, ticks:[[ms,mid],...], serverNowMs}。LiveTickPlayer が clockOffset 維持と再生に使う。
//   失敗時は null（player は次 poll で回復・巻き戻さない）。
async function fetchLiveTicks(fetchImpl, since = 0) {
  if (typeof fetchImpl !== 'function') {
    return null;
  }
  try {
    const resp = await fetchImpl(`/live_ticks?since=${encodeURIComponent(since)}`);
    if (!resp.ok) {
      return null;
    }
    const payload = await resp.json();
    return payload && payload.ok ? payload : null;
  } catch {
    return null;
  }
}

// グローバル LightweightCharts（bundled JS が window へ公開）を引数で受け取り、
// チャート + ローソク系列を生成して ChartRenderer に渡す。
// served（http://）時は ComputeHttpClient + /candles、file:// 時は EmbeddedComputeGateway + SAMPLE_DATA。
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
  // 形成中バー（最新足の足内更新）のポーリング間隔。インジ再計算とは分離（高頻度・価格のみ）。
  formingIntervalMs = 5000,
  // [統合レイヤ・オプション注入] 未指定（スタンドアロン live）は現状のコードパスを 1 バイトも
  //   変えない（IndicatorController・リプレイ層なし）。統合レイヤ（unified_ui）だけが
  //   `{ ReplayIndicatorController, setupReplay }` を注入し、controller を派生クラスで生成して
  //   （untilTime=undefined＝live byte 等価）リプレイ層を配線する。live root はリプレイのコードを
  //   import しない（注入のみ）＝スタンドアロン live で 404／結合を生まない。
  replay = undefined,
} = {}) {
  const mode = modeForProtocol(protocol);

  // チャート生成（組み立て点）。生成オプション・メイン系列は共有ヘルパ chart_bootstrap（ISSUE-123・
  //   present/replay 単一ソース）に集約。系列追加系 API は以後 ChartRenderer に隠蔽。
  const { chart, mainSeries } = createChartWithMainSeries({ lwc, container });

  // ポート実装の組み立て（モード別）。
  //   B方式: ComputeHttpClient（fetch /compute）— params 実反映。candles は /candles から取得し、
  //          SAMPLE_DATA（635KB）は読み込まない。
  //   A方式: SAMPLE_DATA を動的 import し、初期ローソク描画 + EmbeddedComputeGateway（params 未反映）。
  let compute;
  let initialCandles = null; // A方式の初期ローソク（renderer 生成後に setCandles で描画する）。
  if (mode === 'b') {
    compute = new ComputeHttpClient({ fetch });
  } else {
    const { SAMPLE_DATA } = await import('../../../data/sample_data.js');
    initialCandles = SAMPLE_DATA.candles;
    compute = new EmbeddedComputeGateway(SAMPLE_DATA);
  }

  // クロスヘア価格読み取り欄（左上固定オーバーレイ）のビュー。ChartRenderer の onCrosshairReadout
  //   に (dto) => view.render(dto) を注入する（#legend の指標管理行とは別物として分離・相乗りしない）。
  //   doc 不在（SSR/テスト）でも render は防御的に no-op（要素不在で安全）。
  const readoutView = new CrosshairReadoutView({ document: doc, elementId: 'crosshair-readout' });

  // ChartRenderer は upstream API の唯一の隔離点。v5 シリーズ定義（LineSeries/HistogramSeries）と
  // createTextWatermark を lwc 名前空間ごと渡す（系列追加系 API 名の参照を本所外へ漏らさない）。
  const renderer = new ChartRenderer({
    chart, mainSeries, lwc, onCrosshairReadout: (dto) => readoutView.render(dto),
  });

  // 価格軸ホイールズームの座標→価格変換に使う pane 高（container 高 - timeScale 高）を供給する。
  //   coordinateToPrice(paneHeight) で価格レンジ下端を読むために必要。container/timeScale 非対応
  //   （SSR/テスト）では設定できないため no-op（handlePriceWheel は pane 高未供給時に安全に false）。
  //   リサイズで container 高が変わるため、autoSize 変化に追随できるよう wheel 発火時にも再計算する。
  //   実体は共有ヘルパ chart_bootstrap.makeUpdatePaneHeight（ISSUE-123 単一ソース）。
  const updatePaneHeight = makeUpdatePaneHeight({ container, chart, renderer });
  updatePaneHeight();

  // A方式の初期ローソクは renderer.setCandles で描画する（直接 mainSeries.setData ではなく
  //   経由させることで読み取り欄の最新足の単一源 _lastBar が立ち、hover 解除でも OHLC が出る）。
  if (initialCandles) {
    renderer.setCandles(initialCandles);
  }
  const persistence = new LocalStorageGateway(storage);
  // テンプレート永続化（§4.2 の 3 キー）。既存 LocalStorageGateway は無改変（ISP）。接頭辞は
  //   注入された storage（統合 UI は scopedStorage）が付けるため gateway は自前で付けない。
  const templateStore = new LocalStorageTemplateGateway(storage);
  const catalog = new IndicatorCatalogClient();
  // param 既定値の単一情報源（back catalog_schema）を GET /catalog で解決する（ISSUE-092 ③）。
  //   B方式のみ取得し、失敗時は静的既定（catalog.js リテラル）へフォールバック（オフライン耐性・UI 不変）。
  //   A方式(file:)はサーバ無しのためスキップ（静的既定）。overlay は controller 生成前に完了させ、
  //   以後のインスタンス生成が単一情報源の既定値を用いるようにする。load は例外を投げない（内部で吸収）。
  if (mode === 'b') {
    await catalog.load(fetch);
  }

  // 時間足切替で candles を再取得するためのローダ（B方式のみ）。A方式（SAMPLE_DATA・再集計不可）は null。
  //   controller.setTimeframe が (datasetRef, timeframe) で呼び、直近 recentBars 本へ制限して取得する。
  const loadCandles = (mode === 'b')
    ? (ref, tf) => fetchCandles(fetch, ref, tf, recentBars)
    : null;

  // Market Profile（独立アクター・candle 版 MVP）の組み立て。取得（client）と描画（primitive）を
  //   注入し、トグル ON まで /market_profile を fetch しない・mainSeries へ attach しない（非破壊）。
  //   getContext は取得時点の現在チャート状態（datasetRef / 選択時間足 / 直近本数）を遅延読み取りする。
  //   ここで controller より先に生成し、(1) controller へ注入（インジケーターメニュー導線）と
  //   (2) 戻り値（既存トグル導線）の両方へ同一アクターを渡す＝二重導線で同一状態を共有する。
  //   getContext は controller._timeframe を遅延参照する（呼び出しは setEnabled/refresh 時＝
  //   controller 代入後）。
  let controller;
  // ISSUE-082: リプレイモードは present から撤去（リプレイバーの構築・配線なし）。actor の
  //   リプレイ機構は replay_ui（別アプリ）専用の共有資産として温存されている。
  // MP primitive を変数化し、時間足毎profile列（tf-period）actor と共有する（同一 primitive の
  //   setTfPeriods で描画＝mainSeries へ二重 attach しない）。
  const mpPrimitive = new MarketProfileHistogramPrimitive();
  // ISSUE-099 🟡-5: 単一 primitive を 2 ロールへ分離注入（god interface 解消）。attach 点は 1 つのまま
  //   （両 sink が同一 primitive を包む）。MarketProfileActor には ProfileSink、tf-period 系には TfPeriodSink。
  const mpProfileSink = new ProfileSink(mpPrimitive);
  const mpTfPeriodSink = new TfPeriodSink(mpPrimitive);
  // src の tf-period 対応判定は記述子（supportedTfs）が単一情報源（ISSUE-097 🟡-8・旧 ZP_TF_ALLOWED）。
  //   委譲述語（sessionsDrawnByTfPeriod）と tf-period 有効化（tfpShouldOn）の **両方**がこの同一条件を
  //   使う＝どちらか片方だけだと「MP はタイルを委譲したのに tf-period は無効」で誰も日別を描かない空白が
  //   生じる（実UI検証で検出）。marketProfile は直後に代入（closure 遅延評価で吸収）。
  const mpSrc = () => (marketProfile && typeof marketProfile.srcParam === 'function'
    ? marketProfile.srcParam() : null);
  const zpTfOk = () => mpSupportsTf(mpSrc(), controller._timeframe);
  // ISSUE-066: MP パラメータ変更（gear の src/mode 等）を tf-period 列アクターへ即時伝播するフック。
  //   tf-period 配線（mode==='b'）で実体を代入する。未配線（A方式・非served）は no-op（byte 不変）。
  // tf-period 列を描ける tf（ISSUE-086: 全時間足統一）。player tf（1m..1D）＋バケット tf（1W/1M）。
  //   isPlayerTimeframe は LiveTickPlayer（tick 再生）の対応判定で別物＝ここでは列描画の判定に使わない。
  const isTfPeriodTimeframe = (tf) => isPlayerTimeframe(tf) || tf === '1W' || tf === '1M';
  let refreshTfPeriodNow = () => {};
  let liveGrowTfPeriod = () => {};
  // [統合レイヤ・MP 単一化] 未注入（standalone live）は base MarketProfileActor（無改変）。統合レイヤ注入時のみ
  //   ReplayMarketProfileActor（3状態 to: LATEST=ライブ byte 等価／int=リプレイ pull-at-T／null=restore）で
  //   生成する。opts は同一（base 継承＝tfPeriod hooks/formingClient/makeAccumulator をそのまま受ける）。
  const MpActorCtor = (replay && replay.ReplayMarketProfileActor) || MarketProfileActor;
  const marketProfile = new MpActorCtor({
    client: new MarketProfileClient({ fetch }),
    primitive: mpProfileSink,
    mainSeries,
    // ISSUE-066: setParams 完了時に tf-period 列を即時再取得（sessions×ライブで src 変更が可視レンジ
    //   変化を待たず反映される）。tf-period 非配線時は no-op。
    onParamsChanged: () => refreshTfPeriodNow(),
    // ISSUE-083: 日別×tf-period 描画×growing（FOLLOW）の live tick で当日チャンクを再取得し当日列を
    //   育てる（zp/dwell 共通）。tf-period 非配線（A方式）時は no-op。
    onSessionsLiveGrow: () => liveGrowTfPeriod(),
    // tick 逐次成長（ticklive）: forming 取得 client と DwellAccumulator factory を注入する。
    //   未注入なら onLiveTick は refresh へ byte-identical 委譲（後方互換）。注入で ticklive が有効化される。
    formingClient: new MarketProfileFormingClient({ fetch }),
    makeAccumulator: () => new DwellAccumulator(),
    // 日別プロファイルを tf-period 列（tfPeriodActor）が描くモードか（served かつ対応 tf）。true のとき
    //   MarketProfileActor は日別タイルを描かず candle 透明化も tf-period へ委ねる（初回の「日別(candle)→
    //   (tf-period)」ちらつき防止・ISSUE-055）。controller は呼び出し時に確定済み（後方参照）。
    sessionsDrawnByTfPeriod: () => mode === 'b' && isTfPeriodTimeframe(controller._timeframe)
      && zpTfOk(),
    // 増分2: スナップショットのローソクトリム源（renderer.setCandleTrim）。lwc 直叩きは renderer に隔離。
    renderer,
    getCandles: () => renderer.getCandles(),
    getContext: () => {
      const ctx = { datasetRef, timeframe: controller._timeframe, limit: recentBars };
      // Model A Phase3（sessions 因果成長・機構A）: growing×sessions のときだけ因果カーソル
      //   to=cursor(=最新観測足 time) を送出する。refresh(to, sessions=1) で backend が当日タイルを
      //   [session_start, to) に限定集計し（過去日は静的）、reveal/ライブ前進で当日タイルが育つ。
      //   未来リーク禁止（to<=cursor＝最新足以下・当日完成を先出ししない）。他モード/静止（ANALYSIS）は
      //   to を載せない＝present#2 byte 不変。cursor 源=最新ローソク time（renderer.getCandles 末尾）。
      //   mpLiveModeCoordinator/marketProfile は呼び出し時（setEnabled/refresh 時）に確定済み（後方参照）。
      if (mpLiveModeCoordinator && mpLiveModeCoordinator.isGrowing()
          && marketProfile && typeof marketProfile.isSessions === 'function'
          && marketProfile.isSessions()) {
        const cs = renderer.getCandles();
        const last = Array.isArray(cs) && cs.length ? cs[cs.length - 1] : null;
        if (last && last.time != null) {
          ctx.to = last.time;
        }
      }
      // [統合レイヤ・MP 単一化] sessions 因果カーソルが to を立てなかった経路（normal 等）で、統合レイヤ注入時のみ
      //   mode 連動 to を載せる。ライブ＝MP_TO_LATEST（client が clock 省略へ翻訳＝base byte 等価・実証済み）、
      //   リプレイ＝controller._untilTime（int T＝pull-at-T）。standalone（未注入）は ctx.to 未設定のまま＝不変。
      if (ctx.to == null && replay && typeof replay.isLiveMode === 'function') {
        ctx.to = replay.isLiveMode() ? MP_TO_LATEST : controller._untilTime;
      }
      return ctx;
    },
  });

  // チャート操作（縦価格パン・wheel 価格ズーム・dblclick reset）の配線は
  //   ChartInteractionController（adapter/front）へ分離した（ISSUE-040a）。Composition Root は
  //   new して install するだけに縮小する（配線専用）。振る舞い本体・座標計算・イベント登録順・分岐は
  //   同コントローラに byte 不変で移設済み（挙動不変）。getController は controller を遅延参照する
  //   （controller はこの直後に代入されるため、install 時点の未確定を () => controller で吸収する＝
  //   旧実装の外側 let クロージャと同一挙動）。updatePaneHeight は初期供給（上記）と同一関数を注入する。
  new ChartInteractionController({
    container,
    renderer,
    getController: () => controller,
    updatePaneHeight,
  }).install();

  // ISSUE-116: 「最新のバーまでスクロール」ボタン（» ・TradingView 相当）。過去へ遡った状態で
  //   チャート右下ホットゾーンへホバーしたときのみ表示し、クリックで最新足へ復帰する。
  //   DOM 不在（SSR/テスト）は install 内の防御で no-op。
  new ScrollToLatestButton({ container, renderer, document: doc }).install();

  // ISSUE-117: 時間足ドロップダウンの開閉制御（選択・active 同期は bind() の data-timeframe 配線）。
  new TimeframeMenu({ document: doc }).install();

  // チャートテンプレートのメニュー・ダイアログ（§6.1・§6.2）。項目 DOM は共有 JS が生成し、
  //   index.html には空マウント（#tpl-menu）のみを置く。メニューは協働子を import せず
  //   コールバック注入で結ぶ（DIP）。協働子は controller 生成後に代入されるため遅延参照する。
  let chartTemplates = null;
  const chartTemplateDialogs = new ChartTemplateDialogs({ document: doc });
  const chartTemplateMenu = new ChartTemplateMenu({
    document: doc,
    // U6: 開くたびに最新のビューモデルで再描画する（restore() との順序依存を作らない）。
    provide: () => (chartTemplates ? chartTemplates.viewModel() : {}),
    onSelect: (templateId) => (chartTemplates ? chartTemplates.applyTemplate(templateId) : undefined),
    onSave: () => (chartTemplates ? chartTemplates.openSaveDialog() : undefined),
    onBind: (templateId) => (chartTemplates ? chartTemplates.bindCurrentTimeframe(templateId) : undefined),
    onManage: () => (chartTemplates ? chartTemplates.openManageDialog() : undefined),
  });
  chartTemplateMenu.install();

  // ライブ連動（present 固有・B方式のみ）: チャートのライブトグル状態（FOLLOW/ANALYSIS）を MP の成長状態へ
  //   連動させる共有協調役（Model A 直交化）。表示モードは gear 選択を維持し、FOLLOW→growing=true（足内成長）／
  //   ANALYSIS→growing=false（static）。defaultMode は catalog の MP mode 既定（catalog_entry の 'mode' 既定
  //   ＝'normal'）。reapply は controller の MP 再適用（mode 維持＋growing トグル）へ遅延束縛する（controller は
  //   直後に代入されるため () => controller で吸収）。A方式（file://・mode!=='b'）は null＝連動を配線しない
  //   （resolver 未注入で MP 挙動 byte 不変）。
  const mpLiveModeCoordinator = (mode === 'b')
    ? new GrowthCoordinator({
        defaultMode: 'normal',
        reapply: () => (controller ? controller.reapplyMarketProfileMode() : undefined),
      })
    : null;

  // 未注入（スタンドアロン）は IndicatorController（既存経路・runtime byte 不変）。統合レイヤ注入時のみ
  //   ReplayIndicatorController（untilTime=undefined で live 等価）で生成する。opts は同一（下記 verbatim）。
  const IndicatorControllerCtor = (replay && replay.ReplayIndicatorController) || IndicatorController;
  controller = new IndicatorControllerCtor({
    catalog, compute, persistence, renderer, document: doc, mode, datasetRef,
    timeframe, recentBars, loadCandles, marketProfile,
    // 連動配線時のみ resolver を注入（未注入＝MP へ渡す mode をそのまま＝byte 不変）。
    //   mode 解決役: 選択表示モードを返す（'ticklive' 置換なし）。growth 解決役: FOLLOW/ANALYSIS→growing 信号。
    mpModeResolver: mpLiveModeCoordinator ? (m) => mpLiveModeCoordinator.resolve(m) : null,
    mpGrowthResolver: mpLiveModeCoordinator ? () => mpLiveModeCoordinator.isGrowing() : null,
  });

  // テンプレート協働子（§7.1）。有効時間足集合は composition root から注入する（U1・present は
  //   既定 9 足＝時間足メニューと同一集合。単一情報源は domain/tf_meta.js の TF_BAR_SEC＝
  //   LAYERING_CONVENTIONS「UI の時間足ボタン集合もこの集合から乖離させない」）。
  chartTemplates = new ChartTemplateController(controller, {
    gateway: templateStore,
    menu: chartTemplateMenu,
    dialogs: chartTemplateDialogs,
    validTimeframes: Object.keys(TF_BAR_SEC),
    // 保存ダイアログの文言「この時間足（日）に紐付ける」用のラベル写像（§6.2）。
    //   単一情報源は timeframe_menu.js の groups（キーとラベルを二重定義しない）。
    timeframeLabels: timeframeLabels(),
  });
  // 時間足切替への介入（§7.2）: 購読スロット（setTimeframeObserver）は単数かつ売買マーカーで
  //   使用済み（E-7）のため使わず、own property での差し替え 1 行で行う。順序（除去 → 切替 →
  //   適用）と再入防止は協働子が所有する（root はここに手続きを持たない）。
  const proceedSetTimeframe = controller.setTimeframe.bind(controller);
  const proceedTemplateTimeframe = (tf) => chartTemplates.onTimeframeChange(tf, proceedSetTimeframe);

  // 取引密度帯（時刻帯の背景色・1 時間足以下）。アクター駆動型のためレジストリへ登録する
  //   （台帳 actor_driven_ids.js の 1 行追記と本登録で完結＝IndicatorController は不変）。
  //   統合 UI では live root だけが実行されるため、この配線でライブ・リプレイ双方に効く。
  //   getUntil: リプレイは単一時計 to（controller._untilTime）＝当日を集計に含めない因果窓の基準。
  //   ライブは _untilTime 非在席（undefined）＝null を返す＝サーバの現在時刻。
  const tickvolBands = new TickvolBandsActor({
    fetch, datasetRef, renderer,
    getTimeframe: () => controller._timeframe,
    getUntil: () => (controller._untilTime != null ? controller._untilTime : null),
  });
  controller.registerActorController('tickvol_bands', new TickvolBandsController(controller, tickvolBands));
  // 時間足切替: 帯は時間足に依存しない（サーバは常に 1 分足原子で集計）ので再取得せず、塗る足だけ引き直す。
  //   購読スロット（setTimeframeObserver）は売買マーカーが占有済みのため、テンプレート協働子と同じ
  //   own property 差し替えを**その内側へ**チェーンする（既存の介入順序を壊さない）。
  controller.setTimeframe = (tf) => {
    const done = proceedTemplateTimeframe(tf);
    tickvolBands.onTimeframeChange();
    return done;
  };
  // リプレイ時計の前進: セッション日が変わったときだけ再取得する（日内は応答不変＝当日非参照）。
  if (typeof controller.setUntilTime === 'function') {
    const proceedUntil = controller.setUntilTime.bind(controller);
    controller.setUntilTime = (t) => {
      const done = proceedUntil(t);
      tickvolBands.onClock();
      return done;
    };
  }

  // 時間足毎profile列（tf-period・最小価格単位・ローリング窓＋ジッターバッファ）の配線（served のみ）。
  //   sessions モード（marketProfile.isSessions()）かつ対応 tf（1m..1D）のとき、可視レンジぶんの列を
  //   jitter buffer 経由で取得し MP と共有の primitive へ setTfPeriods で描く（sessions の tf 一般化。
  //   primitive._draw は tf-period を優先＝旧 per-day sessions を上書き）。可視レンジ変化（スクロール/ズーム／
  //   sessions 有効化時の focusTimeRange）で refresh＝ローリング。先読み完了（onReady）で再描画＝ジッターバッファ。
  let tfPeriodActor = null;
  if (mode === 'b') {
    const getVisibleRange = () => {
      const ts = typeof chart.timeScale === 'function' ? chart.timeScale() : null;
      const r = ts && typeof ts.getVisibleRange === 'function' ? ts.getVisibleRange() : null;
      return (r && r.from != null && r.to != null) ? { from: Number(r.from), to: Number(r.to) } : null;
    };
    // ISSUE-055（windowSec tf 連動）: 取得窓を tf のバー秒に比例させ、1 画面を少数チャンクで満たす。
    //   6h 固定だと 1D は可視数十日を 6h 刻み＝274本(81%空)へ肥大する。規則 clamp(barSec×K, 6h, 45d)。
    //   1m は 6h 据置（barSec×K<6h）、1D は 45d 上限で数本に収束。cacheMax は可視 chunk 数を上回る値
    //   （32）にして、ローリング中に可視列が LRU 破棄される＝フラッシュを防ぐ。
    const TFP_BAR_SEC = TF_BAR_SEC; // 単一情報源（domain/tf_meta.js・ISSUE-087 🔴-2）。
    const TFP_WINDOW_MIN = 6 * 3600;      // 下限 6h（intraday 据置）。
    const TFP_WINDOW_MAX = 45 * 86400;    // 上限 45 日（1D の 1 チャンク応答肥大を抑える）。
    const TFP_PERIODS_PER_CHUNK = 96;     // 1 チャンク≒96 周期ぶん（1 画面を数チャンクに収める）。
    const windowSecForTf = (tf) => {
      const bar = TFP_BAR_SEC[tf] || 86400;
      // ISSUE-086: バケット tf（1W/1M）は 1 周期=1 列で応答が軽く、45 日上限のままだと全期間表示で
      //   チャンクが百超に分裂する（実測 192 リクエスト・LRU 上限 32 で破棄再取得のスラッシング）。
      //   上限クランプを外し 96 周期/チャンク（1W≈1.8年・1M≈8年）で数チャンクに収める。
      if (tf === '1W' || tf === '1M') {
        return bar * TFP_PERIODS_PER_CHUNK;
      }
      return Math.max(TFP_WINDOW_MIN, Math.min(TFP_WINDOW_MAX, bar * TFP_PERIODS_PER_CHUNK));
    };
    const tfBuf = new TfPeriodJitterBuffer({
      client: new TfPeriodProfileClient({ fetch }),
      datasetRef,
      windowSecForTf,
      cacheMax: 32,
      onReady: () => { if (tfPeriodActor) tfPeriodActor.onChunkReady(); },
    });
    // src=zp（超過占有 z(p)）の tf-period 透過: MP の src 選択が zp のとき列も zp で取得する。
    //   backend の対応 tf 判定（zpTfOk）は上段＝委譲述語と同一定義（単一情報源）。
    tfPeriodActor = new TfPeriodProfileActor({
      jitterBuffer: tfBuf,
      primitive: mpTfPeriodSink,
      getTimeframe: () => controller._timeframe,
      getVisibleRange,
      renderer, // ISSUE-055: 列が描けた時点で candle 透明化（MarketProfileActor から委譲）。
      getSrc: () => mpTfPeriodSrc(mpSrc()),
      // 方向背景（依頼者指示 2026-07-13）: 列 time と同一周期グリッドの candle から陽/陰を注釈する。
      getCandles: () => renderer.getCandles(),
    });
    // tf-period ホバー読取ツールチップ（依頼者指示 2026-07-13・a案）: クロスヘア座標 DTO（renderer）
    //   → 該当列レベル探索（primitive.tfPeriodLevelAt）→ カーソル追従表示（TfPeriodTooltip）。
    //   列非表示（日別モード外・非対応 tf）・レベル不在（空行）・チャート外は hide。
    const tfpTooltip = new TfPeriodTooltip({ document: doc, container });
    renderer.setTfPeriodHoverHandler((pos) => {
      if (!pos || !tfPeriodActor || !tfPeriodActor.isEnabled()) {
        tfpTooltip.hide();
        return;
      }
      const hit = mpTfPeriodSink.tfPeriodLevelAt(pos.time, pos.price);
      if (!hit) {
        tfpTooltip.hide();
        return;
      }
      tfpTooltip.show(pos.x, pos.y, { ...hit, timeLabel: formatPeriodLabel(hit.time) });
    });
    const tfpShouldOn = () => !!(marketProfile && typeof marketProfile.isSessions === 'function'
      && marketProfile.isSessions()) && isTfPeriodTimeframe(controller._timeframe)
      // src=zp は backend 対応 tf（15m..1D）のみ列を出せる（1m/5m は 400 → 列を出さない。
      //   このとき委譲述語も false になり MP actor が日別タイルを自前描画＝フォールバック）。
      && zpTfOk();
    // ISSUE-066: MP パラメータ変更時の tf-period 即時再適用。tfpShouldOn なら setEnabled(true)＝
    //   refresh→ensure で jitter buffer の src 差分キャッシュ破棄→新 src 再fetch→再描画。不成立
    //   （sessions 解除/非対応 tf）は列を消す。可視レンジ変化のデバウンスと違い**即時**（src 切替の反映）。
    // ISSUE-054: 「レンジ」(barw) を tf-period 列にも効かせる。日別プロファイルの描画を tf-period が
    //   担う経路では、レンジを変えても列は最小価格単位のままで、`/market_profile` 由来の POC/VA だけが
    //   変わる＝**パラメータが部分的にしか効かない**状態だった。列は取得・キャッシュを変えずに
    //   **描画時に barw 幅へ束ねる**（測定は最小単位のまま保つ＝粗ビンのアーティファクトを持ち込まない）。
    const syncTfBinWidth = () => {
      if (typeof mpTfPeriodSink.setTfBinWidth === 'function'
          && typeof marketProfile.barwParam === 'function') {
        mpTfPeriodSink.setTfBinWidth(marketProfile.barwParam());
      }
    };
    refreshTfPeriodNow = () => {
      if (!tfPeriodActor) { return; }
      syncTfBinWidth();                     // レンジ変更を列へ即時反映（取得の要否と独立）。
      if (!tfpShouldOn()) {
        if (tfPeriodActor.isEnabled()) { tfPeriodActor.setEnabled(false); }
        return;
      }
      tfPeriodActor.setEnabled(true);
    };
    syncTfBinWidth();                       // 初期値（保存済みインスタンスのレンジ）を反映する。
    // ISSUE-083: MP の live tick（sessions×tfDraws×growing）→ 当日チャンクの stale-while-revalidate
    //   再取得（tfPeriodActor.onLiveTick→jitterBuffer.refreshAt）。throttle は actor 側（既定 5s）。
    //   列非描画中（isEnabled=false＝日別解除・非対応 tf）は発火しない。
    liveGrowTfPeriod = () => {
      if (tfPeriodActor && tfPeriodActor.isEnabled()) {
        tfPeriodActor.onLiveTick();
      }
    };
    const tsSub = typeof chart.timeScale === 'function' ? chart.timeScale() : null;
    if (tsSub && typeof tsSub.subscribeVisibleTimeRangeChange === 'function') {
      // ISSUE-055（A案: ローリング中は再取得/再描画しない）: 可視レンジ変化のたびに setEnabled(true)→refresh
      //   （fetch fan-out＋全再描画）を呼ぶと、ドラッグ中に storm 化して重く・列が出入りして**フラッシュ**する。
      //   そこで **ON 時のローリング取得は末尾デバウンス**（停止後 1 回だけ ensure+描画）にする。ドラッグ中は
      //   既取得列が primitive の毎フレーム再描画で時間軸に固定されて滑らかにパン追従する（fetch 0・不変）。
      //   OFF（sessions 解除/非対応 tf）は列を残さないため即時反映する。tf 非依存＝全時間足に等しく効く。
      const TFP_ROLL_DEBOUNCE_MS = 150; // 「スクロール停止」判定の末尾待ち（体感即応と storm 抑制の均衡）。
      let tfpRollTimer = null;
      tsSub.subscribeVisibleTimeRangeChange(() => {
        if (!tfpShouldOn()) {
          if (tfpRollTimer != null) { clearTimeout(tfpRollTimer); tfpRollTimer = null; }
          if (tfPeriodActor.isEnabled()) {
            tfPeriodActor.setEnabled(false); // sessions OFF / 非対応 tf → 列を消す（即時）。
          }
          return;
        }
        // ON: ローリング停止後に 1 回だけ確保＋描画（ドラッグ中は既取得列がパン追従＝再取得しない）。
        if (tfpRollTimer != null) { clearTimeout(tfpRollTimer); }
        tfpRollTimer = setTimeout(() => {
          tfpRollTimer = null;
          if (tfpShouldOn()) {
            tfPeriodActor.setEnabled(true); // enable＋ensure（可視窓の確保）＋描画を 1 回。
          }
        }, TFP_ROLL_DEBOUNCE_MS);
      });
    }
  }

  // B方式は /candles から実 OHLCV を取得し、メイン系列を差し替える（/compute と時間軸を揃える）。
  //   初期は既定時間足・直近 recentBars 本。取得失敗時は SAMPLE_DATA のまま（フォールバック）。
  // 初回表示の最大遡り期間（直近1年）。ISSUE-055: 1D は数年ぶんの足がロードされ、setCandles の
  //   自動フィットで全期間が可視になると 日別 tf-period が可視域ぶん一括取得され応答肥大（実測87MB）で
  //   初回表示が重い。初回可視範囲を直近1年へ寄せ、古い範囲はスクロールで（A案デバウンス＋per-day
  //   キャッシュで滑らか）。データが1年未満（intraday 等）のときは全期間で不変。
  const INITIAL_VIEW_SPAN_SEC = 365 * 86400;
  const ready = (mode === 'b')
    ? fetchCandles(fetch, datasetRef, timeframe, recentBars).then((candles) => {
        if (candles && candles.length > 0) {
          // renderer.setCandles 経由で _lastBar も立てる（読み取り欄の hover 解除フォールバック）。
          renderer.setCandles(candles);
          // 初回可視範囲を直近1年へ限定する（全期間フィットを上書き＝tf-period 初回一括取得を抑制）。
          const lastT = candles[candles.length - 1].time;
          const firstT = candles[0].time;
          if (typeof renderer.focusTimeRange === 'function'
              && Number.isFinite(lastT) && Number.isFinite(firstT)
              && lastT - firstT > INITIAL_VIEW_SPAN_SEC) {
            renderer.focusTimeRange(lastT - INITIAL_VIEW_SPAN_SEC, lastT);
          }
        }
      })
    : Promise.resolve();

  // ライブ更新（1 分間隔）の組み立て。served（B方式）のみ。tick は controller 経由の再計算
  //   ＋ /candles 再取得 → 最新足を renderer.updateLastCandle で差分反映する。start は入口
  //   （index.html）が served 時のみ呼ぶ。A方式（file://）は null（更新を配線しない）。
  // LiveTickPlayer（12 秒固定遅延の tick 再生）を配線するのは served（B方式）のみ＝価格の唯一の
  //   書き手。このとき旧 2 系統（LiveUpdater / FormingBarUpdater）の価格上書きは 12 秒より古い
  //   データで巻き戻すため suppressPriceUpdate=true で止める（指標再計算は従来どおり）。
  //   file://（A方式）は player 不在＝false で既存挙動 byte 不変。
  const playerActive = (mode === 'b');

  const liveUpdater = (mode === 'b')
    ? new LiveUpdater({
        controller,
        renderer,
        loadCandles: (ref, tf) => fetchCandles(fetch, ref, tf, recentBars),
        datasetRef,
        getTimeframe: () => controller._timeframe,
        setInterval: setIntervalImpl,
        clearInterval: clearIntervalImpl,
        intervalMs: liveIntervalMs,
        suppressPriceUpdate: playerActive,
      })
    : null;

  // 形成中バー（最新足の足内更新）の組み立て。served（B方式）のみ。/forming_bar から選択 tf の
  //   形成中バーを取得し、(1) renderer.updateLastCandle で価格の最新足を反映、(2) 指標も
  //   recomputeAllApplied({mode:'latest'}) で最新点をティック由来に再計算する（backend が
  //   mode=latest 時に形成中バーを最新足として計算へ織り込む）。LiveUpdater(60s) との分離の実体は
  //   「/candles 全件再取得(Live) vs /forming_bar(Forming)」であり、指標再計算はどちらも latest。
  //   start は入口（index.html）が served 時のみ呼ぶ。
  const formingBarUpdater = (mode === 'b')
    ? new FormingBarUpdater({
        controller,
        renderer,
        loadFormingBar: (ref, tf) => fetchFormingBar(fetch, ref, tf),
        datasetRef,
        getTimeframe: () => controller._timeframe,
        setInterval: setIntervalImpl,
        clearInterval: clearIntervalImpl,
        intervalMs: formingIntervalMs,
        // 価格抑止は tf 依存: player が扱う固定周期（1m..1D）は player が唯一の書き手＝抑止する。
        //   player 非対応の 1W/1M（カレンダー周期）は player が no-op のため、FormingBarUpdater が
        //   /forming_bar（backend のロールアップ方式で 1W/1M も供給）を描く価格の書き手になる＝抑止しない。
        suppressPriceUpdate: playerActive ? () => isPlayerTimeframe(controller._timeframe) : false,
      })
    : null;

  // LiveTickPlayer（12 秒固定遅延のなめらか tick 再生・ISSUE-049）の組み立て。served（B方式）のみ。
  //   /live_ticks から increment 取得しキュー → 100ms 粒度で serverNow-12000 以前の tick を現在 tf の
  //   形成中バーへ累積 → renderer.updateLastCandle。tf 切替・起動時は /forming_bar でシード。start は
  //   入口（index.html）が served 時のみ呼ぶ。A方式（file://）は null（既存挙動 byte 不変）。
  const liveTickPlayer = playerActive
    ? new LiveTickPlayer({
        renderer,
        fetchLiveTicks: (since) => fetchLiveTicks(fetch, since),
        loadFormingBar: (ref, tf) => fetchFormingBar(fetch, ref, tf),
        datasetRef,
        getTimeframe: () => controller._timeframe,
        setInterval: setIntervalImpl,
        clearInterval: clearIntervalImpl,
        // tick 粒度の指標末尾追従（統一設計 2026-07-22）: tick 適用のたびに登録オシレーターの
        //   末尾差分再計算を要求する（coalesce は controller 側＝過負荷にならない）。
        onFormingUpdate: () => controller.requestFormingRecompute(),
        // バー確定駆動の full 再計算（ISSUE-151）: 期間ロールオーバー＝直前バー確定で全指標を
        //   再計算する（リプレイの毎バーその場計算と同一意味論。coalesce/pending は controller 側）。
        onBarClose: () => controller.requestFullRecompute(),
      })
    : null;

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
  // 現在値の大型表示（#current-price・サイズは CSS 規定）。candle 変更 observer は単一スロットのため
  //   tradeMarkers への通知と同一コールバック内で現在値ビューも更新する（ユーザー指示 2026-07-23）。
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

  // 時間足変更を売買マーカーへ通知し、該当時間足（建玉の時間足）以外は非表示にする。
  //   初期時間足を反映し、以降は controller の時間足購読で連動する。
  tradeMarkers.setCurrentTimeframe(timeframe);
  controller.setTimeframeObserver((tf) => {
    tradeMarkers.setCurrentTimeframe(tf);
    // ISSUE-090: tf 切替で tf-period 列を即時再適用する。従来は可視レンジ変化イベント頼みで、
    //   レンジが変わらない切替（週→日→週 等）では旧 tf の列が残留し「週間隔÷7 の細い列」に
    //   見える実機バグ（依頼者報告）が起きた。refreshTfPeriodNow は非対応状態なら列を消す。
    refreshTfPeriodNow();
  });

  // ライブ追従トグル（present 固有）。B方式（mode==='b'）のみ配線する。install() でボタン click＋
  //   可視範囲購読を配線し、初期 FOLLOW を適用（LiveUpdater 起動所有権を controller へ・start は冪等）。
  //   A方式（file://）は null（ボタンは index.html 側で disabled のまま非活性）。
  const liveFollowController = (mode === 'b')
    ? new LiveFollowController({
        liveUpdater,
        // ライブ価格の書き手も FOLLOW/ANALYSIS で start/stop（ANALYSIS で価格を凍結＝トグルを効かせる）。
        liveTickPlayer,
        formingBarUpdater,
        renderer,
        document: doc,
        buttonId: 'live-follow-toggle',
        mode,
        // ライブ連動: FOLLOW/ANALYSIS 遷移を協調役へ通知（MP を growing↔static で連動・表示モードは維持）。
        //   協調役不在（A方式）は未注入＝既存ライブトグル挙動 byte 不変。
        onLiveStateChange: mpLiveModeCoordinator
          ? (isFollow) => mpLiveModeCoordinator.onLiveStateChange(isFollow)
          : undefined,
      })
    : null;
  if (liveFollowController) {
    liveFollowController.install();
  }

  // [統合レイヤ・オプション注入] 全 live 配線の後にリプレイ層を配線する（注入時のみ）。setupReplay は
  //   chart/mainSeries/controller/renderer を受けて再生ドライバの外殻ハンドル { enable, disable, destroy }
  //   を返す。統合レイヤは初期 live で即 disable()（untilTime=undefined＝live 等価）し、トグルで
  //   enable/disable する。chart は本 bootstrap の 1 回生成のみ（リプレイ層は同一 chart を共有＝再構築なし）。
  //   MP 単一化: setupReplay へ**単一 MP アクター**（上で ReplayMarketProfileActor を注入生成）を渡す。
  //   ライブ（to=LATEST）は再生ドライバ非駆動（disable で playing=false＝render/animate 停止）＝mpDriver 不発火で
  //   base 経路のみ。リプレイ（to=int T）は再生中に mpDriver が push 駆動（standalone replay と同一・onLiveTick の
  //   int 経路は isGrowingPush で no-op＝二重駆動なし）。未注入（standalone live）は呼ばない。
  let replayHandle = null;
  if (replay && typeof replay.setupReplay === 'function') {
    replayHandle = await replay.setupReplay({
      chart, mainSeries, controller, renderer,
      datasetRef, recentBars, document: doc, fetchImpl: fetch,
      marketProfile,
    });
  }

  // marketProfile は controller 生成前に組み立て済み（controller へ注入＋既存トグル用に戻り値へ）。
  //   トグル配線は入口（index.html）が marketProfile.setEnabled(on) を呼ぶ（bootstrap に副作用を足さない）。
  return { chart, mainSeries, renderer, controller, mode, ready, tickvolBands, liveUpdater, formingBarUpdater, liveTickPlayer, tradeMarkers, marketProfile, liveFollowController, mpLiveModeCoordinator, tfPeriodActor, replayHandle, chartTemplates, chartTemplateMenu, chartTemplateDialogs };
}
