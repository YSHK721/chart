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
import { MarketProfileClient } from './market_profile_client.js';
import { MarketProfileFormingClient } from './market_profile_forming_client.js';
import { DwellAccumulator } from '../../domain/market_profile_dwell_accumulator.js';
import { MarketProfileHistogramPrimitive } from './market_profile_primitive.js';
import { TfPeriodProfileClient } from './tf_period_profile_client.js';
import { TfPeriodJitterBuffer } from './tf_period_jitter_buffer.js';
import { TfPeriodProfileActor } from './tf_period_profile_actor.js';
import { TfPeriodTooltip, formatPeriodLabel } from './tf_period_tooltip.js';
import { MarketProfileActor } from './market_profile_actor.js';
import { MarketProfileReplayBar } from './market_profile_replay_bar.js';
import { ChartInteractionController } from './chart_interaction_controller.js';
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
} = {}) {
  const mode = modeForProtocol(protocol);

  // チャート生成（組み立て点。系列追加系 API は ChartRenderer に隠蔽）。
  // v5: background は { type: ColorType.Solid, color }、panes のリサイズ separator は既定 ON。
  const chart = lwc.createChart(container, {
    layout: {
      background: { type: lwc.ColorType.Solid, color: '#131722' },
      textColor: '#d1d4dc',
      // ペイン境界のドラッグ・リサイズ（separator）を有効化（高さ調整・機能④）。
      panes: { enableResize: true, separatorColor: '#2a2e39', separatorHoverColor: 'rgba(178,181,189,0.2)' },
    },
    grid: { vertLines: { color: '#1f2530' }, horzLines: { color: '#1f2530' } },
    // クロスヘアを Normal（自由追従）に。既定 Magnet(1) は水平線を最寄り足の価格へスナップさせるため、
    //   カーソル位置どおりに動かしたいという要望で Normal(0) に変更（enum 無い環境向けに 0 フォールバック）。
    crosshair: { mode: (lwc.CrosshairMode && lwc.CrosshairMode.Normal) || 0 },
    rightPriceScale: { borderColor: '#2a2e39' },
    // 日中足（1m/1h 等）でも時刻が読めるよう timeVisible を有効化（秒は非表示）。
    timeScale: { borderColor: '#2a2e39', timeVisible: true, secondsVisible: false },
    autoSize: true,
  });
  // v5: addCandlestickSeries は廃止。addSeries(CandlestickSeries, ...) でメイン pane(0) に追加。
  const mainSeries = chart.addSeries(lwc.CandlestickSeries, {
    upColor: '#26a69a', downColor: '#ef5350',
    borderUpColor: '#26a69a', borderDownColor: '#ef5350',
    wickUpColor: '#26a69a', wickDownColor: '#ef5350',
  });

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
  const updatePaneHeight = () => {
    if (typeof renderer.setPaneHeight !== 'function') {
      return;
    }
    const ch = container && typeof container.clientHeight === 'number' ? container.clientHeight : 0;
    const ts = typeof chart.timeScale === 'function' ? chart.timeScale() : null;
    const th = ts && typeof ts.height === 'function' ? ts.height() : 0;
    const paneHeight = ch - th;
    if (paneHeight > 0) {
      renderer.setPaneHeight(paneHeight);
    }
  };
  updatePaneHeight();

  // A方式の初期ローソクは renderer.setCandles で描画する（直接 mainSeries.setData ではなく
  //   経由させることで読み取り欄の最新足の単一源 _lastBar が立ち、hover 解除でも OHLC が出る）。
  if (initialCandles) {
    renderer.setCandles(initialCandles);
  }
  const persistence = new LocalStorageGateway(storage);
  const catalog = new IndicatorCatalogClient();

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
  // リプレイスライダバー（増分1）。replay=ON で下部に表示。input で対応足 time を actor.setReplayCursor へ。
  //   candles は renderer.getCandles()（取得済み・読取のみ）を再利用し、min/max・index→time を賄う。
  //   doc/container 不在（SSR/テスト）でも内部で no-op（防御）。
  // バーのホストは index.html の #mp-replay-bar-host（チャート下部・sibling）を優先し、
  //   不在時は container（後方互換・テスト）へフォールバックする。#chart 内へ差し込まない
  //   （lightweight-charts が #chart を専有するため、canvas と重ならない sibling へ置く）。
  const replayHost = (doc && typeof doc.getElementById === 'function'
    ? doc.getElementById('mp-replay-bar-host') : null) || container;
  const replayBar = new MarketProfileReplayBar({
    document: doc,
    container: replayHost,
    onScrub: (time) => { if (controller && controller._marketProfile) { controller._marketProfile.setReplayCursor(time); } },
    // 増分2: モード（アンカー/ローリング）・スナップショット変更 → actor が現在 T で再取得する。
    onChange: () => { if (controller && controller._marketProfile) { controller._marketProfile.onReplayControlsChange(); } },
  });
  // MP primitive を変数化し、時間足毎profile列（tf-period）actor と共有する（同一 primitive の
  //   setTfPeriods で描画＝mainSeries へ二重 attach しない）。
  const mpPrimitive = new MarketProfileHistogramPrimitive();
  // src=zp（超過占有 z(p)）の tf-period 対応判定。backend の zp 列は tf 15m..1D のみ（1m/5m は
  //   周期内分数が退化し 400）。委譲述語（sessionsDrawnByTfPeriod）と tf-period 有効化（tfpShouldOn）の
  //   **両方**がこの同一条件を使う＝どちらか片方だけだと「MP はタイルを委譲したのに tf-period は無効」で
  //   誰も日別を描かない空白が生じる（実UI検証で検出）。marketProfile は直後に代入（closure 遅延評価で吸収）。
  const ZP_TF_ALLOWED = new Set(['15m', '30m', '1h', '4h', '1D']);
  const mpSrc = () => (marketProfile && typeof marketProfile.srcParam === 'function'
    ? marketProfile.srcParam() : null);
  const zpTfOk = () => (mpSrc() !== 'zp' || ZP_TF_ALLOWED.has(controller._timeframe));
  // ISSUE-066: MP パラメータ変更（gear の src/mode 等）を tf-period 列アクターへ即時伝播するフック。
  //   tf-period 配線（mode==='b'）で実体を代入する。未配線（A方式・非served）は no-op（byte 不変）。
  let refreshTfPeriodNow = () => {};
  const marketProfile = new MarketProfileActor({
    client: new MarketProfileClient({ fetch }),
    primitive: mpPrimitive,
    mainSeries,
    replayBar,
    // ISSUE-066: setParams 完了時に tf-period 列を即時再取得（sessions×ライブで src 変更が可視レンジ
    //   変化を待たず反映される）。tf-period 非配線時は no-op。
    onParamsChanged: () => refreshTfPeriodNow(),
    // tick 逐次成長（ticklive）: forming 取得 client と DwellAccumulator factory を注入する。
    //   未注入なら onLiveTick は refresh へ byte-identical 委譲（後方互換）。注入で ticklive が有効化される。
    formingClient: new MarketProfileFormingClient({ fetch }),
    makeAccumulator: () => new DwellAccumulator(),
    // 日別プロファイルを tf-period 列（tfPeriodActor）が描くモードか（served かつ対応 tf）。true のとき
    //   MarketProfileActor は日別タイルを描かず candle 透明化も tf-period へ委ねる（初回の「日別(candle)→
    //   (tf-period)」ちらつき防止・ISSUE-055）。controller は呼び出し時に確定済み（後方参照）。
    sessionsDrawnByTfPeriod: () => mode === 'b' && isPlayerTimeframe(controller._timeframe)
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
      return ctx;
    },
  });

  // チャート操作（スワイプスクラブ・縦価格パン・wheel 価格ズーム・dblclick reset）の配線は
  //   ChartInteractionController（adapter/front）へ分離した（ISSUE-040a）。Composition Root は
  //   new して install するだけに縮小する（配線専用）。振る舞い本体・座標計算・イベント登録順・分岐は
  //   同コントローラに byte 不変で移設済み（挙動不変）。getController は controller を遅延参照する
  //   （controller はこの直後に代入されるため、install 時点の未確定を () => controller で吸収する＝
  //   旧実装の外側 let クロージャと同一挙動）。updatePaneHeight は初期供給（上記）と同一関数を注入する。
  new ChartInteractionController({
    container,
    renderer,
    replayBar,
    getController: () => controller,
    updatePaneHeight,
  }).install();

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

  controller = new IndicatorController({
    catalog, compute, persistence, renderer, document: doc, mode, datasetRef,
    timeframe, recentBars, loadCandles, marketProfile,
    // 連動配線時のみ resolver を注入（未注入＝MP へ渡す mode をそのまま＝byte 不変）。
    //   mode 解決役: 選択表示モードを返す（'ticklive' 置換なし）。growth 解決役: FOLLOW/ANALYSIS→growing 信号。
    mpModeResolver: mpLiveModeCoordinator ? (m) => mpLiveModeCoordinator.resolve(m) : null,
    mpGrowthResolver: mpLiveModeCoordinator ? () => mpLiveModeCoordinator.isGrowing() : null,
  });

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
    const TFP_BAR_SEC = {
      '1m': 60, '5m': 300, '15m': 900, '30m': 1800, '1h': 3600, '4h': 14400, '1D': 86400,
    };
    const TFP_WINDOW_MIN = 6 * 3600;      // 下限 6h（intraday 据置）。
    const TFP_WINDOW_MAX = 45 * 86400;    // 上限 45 日（1D の 1 チャンク応答肥大を抑える）。
    const TFP_PERIODS_PER_CHUNK = 96;     // 1 チャンク≒96 周期ぶん（1 画面を数チャンクに収める）。
    const windowSecForTf = (tf) => {
      const bar = TFP_BAR_SEC[tf] || 86400;
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
      primitive: mpPrimitive,
      getTimeframe: () => controller._timeframe,
      getVisibleRange,
      renderer, // ISSUE-055: 列が描けた時点で candle 透明化（MarketProfileActor から委譲）。
      getSrc: () => (mpSrc() === 'zp' ? 'zp' : null),
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
      const hit = mpPrimitive.tfPeriodLevelAt(pos.time, pos.price);
      if (!hit) {
        tfpTooltip.hide();
        return;
      }
      tfpTooltip.show(pos.x, pos.y, { ...hit, timeLabel: formatPeriodLabel(hit.time) });
    });
    const tfpShouldOn = () => !!(marketProfile && typeof marketProfile.isSessions === 'function'
      && marketProfile.isSessions()) && isPlayerTimeframe(controller._timeframe)
      // src=zp は backend 対応 tf（15m..1D）のみ列を出せる（1m/5m は 400 → 列を出さない。
      //   このとき委譲述語も false になり MP actor が日別タイルを自前描画＝フォールバック）。
      && zpTfOk();
    // ISSUE-066: MP パラメータ変更時の tf-period 即時再適用。tfpShouldOn なら setEnabled(true)＝
    //   refresh→ensure で jitter buffer の src 差分キャッシュ破棄→新 src 再fetch→再描画。不成立
    //   （sessions 解除/非対応 tf）は列を消す。可視レンジ変化のデバウンスと違い**即時**（src 切替の反映）。
    refreshTfPeriodNow = () => {
      if (!tfPeriodActor) { return; }
      if (!tfpShouldOn()) {
        if (tfPeriodActor.isEnabled()) { tfPeriodActor.setEnabled(false); }
        return;
      }
      tfPeriodActor.setEnabled(true);
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
  renderer.setCandleObserver(() => tradeMarkers.onCandlesChanged());

  // 時間足変更を売買マーカーへ通知し、該当時間足（建玉の時間足）以外は非表示にする。
  //   初期時間足を反映し、以降は controller の時間足購読で連動する。
  tradeMarkers.setCurrentTimeframe(timeframe);
  controller.setTimeframeObserver((tf) => tradeMarkers.setCurrentTimeframe(tf));

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

  // marketProfile は controller 生成前に組み立て済み（controller へ注入＋既存トグル用に戻り値へ）。
  //   トグル配線は入口（index.html）が marketProfile.setEnabled(on) を呼ぶ（bootstrap に副作用を足さない）。
  return { chart, mainSeries, renderer, controller, mode, ready, liveUpdater, formingBarUpdater, liveTickPlayer, tradeMarkers, marketProfile, replayBar, liveFollowController, mpLiveModeCoordinator, tfPeriodActor };
}
