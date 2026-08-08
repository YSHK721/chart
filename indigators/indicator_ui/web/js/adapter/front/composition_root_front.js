// composition_root_front.js（フロント側 Composition Root）。
//
// 設計入力: 内部設計書 §2.1（framework/front/composition_root_front.js）、§3.3.5（ComputeHttpClient）、
//   §6.3（/candles）、内部設計_パラメータ設定ダイアログ §9（B方式 params 実反映）。
// 依存配線: catalog / compute / persistence / renderer を IndicatorController に注入する唯一の点。
//   - upstream JS（LightweightCharts）は ChartRenderer の生成にのみ使い、ここでは
//     chart / mainSeries を作って渡す（系列追加系 API 名はここで参照しない）。

// ライブ／リプレイ両 root で同一の配線は chart_app_wiring.js が単一ソースとして所有する
//   （ISSUE-278 #4: リプレイ側は本ファイルの全文フォークだった＝ライブの修正が届かなかった）。
//   本 root はライブ固有の差（ライブ 3 ポーラ・ライブ追従・tf-period 列・成長協調役・
//   リプレイ層のオプション注入）だけを書く。
import {
  composeChartShell,
  installSharedUi,
  wireControllerCollaborators,
  fetchCandles,
} from './chart_app_wiring.js';
import { LiveUpdater } from './live_updater.js';
import { LiveFollowController } from './live_follow_controller.js';
import { FormingBarUpdater } from './forming_bar_updater.js';
import { LiveTickPlayer } from './live_tick_player.js';
import { IndicatorController } from './indicator_controller.js';
import { MarketProfileClient, MP_TO_LATEST } from './market_profile_client.js';
import { MarketProfileFormingClient } from './market_profile_forming_client.js';
import { DwellAccumulator } from '../../domain/market_profile_dwell_accumulator.js';
import { MarketProfileHistogramPrimitive } from './market_profile_primitive.js';
import { ProfileSink, TfPeriodSink } from './mp_primitive_roles.js';
import { mpSupportsTf, mpTfPeriodSrc } from '../../domain/mp_source_capability.js';
import { TfPeriodProfileClient } from './tf_period_profile_client.js';
import { TfPeriodJitterBuffer } from './tf_period_jitter_buffer.js';
import { TfPeriodProfileActor } from './tf_period_profile_actor.js';
import { TF_BAR_SEC, isKnownTimeframe } from '../../domain/tf_meta.js';
import { TfPeriodTooltip, formatPeriodLabel } from './tf_period_tooltip.js';
import { MarketProfileActor } from './market_profile_actor.js';
// GrowthCoordinator は共有 market_profile モジュール（usecase/growth_coordinator.js）へ移設済み。
//   present は adapter/front/mp_live_mode_coordinator.js（symlink）経由で import（byte 不変 retarget）。
import { GrowthCoordinator } from './mp_live_mode_coordinator.js';

// 既定時間足（1 分足原子からの初期表示足）と直近表示本数（§配信設計: リサンプル＋直近 N 本）。
//   1 分足原子の全期間（数百万点）を直接配信しないため、/candles・/compute を直近 N 本へ制限する。
export const DEFAULT_TIMEFRAME = '1D';
export const RECENT_BARS = 1500;

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
//
//   req（{specs, datasetRef, timeframe, limit}）を添えると、サーバは
//     - barTimes / nowBarTime: 各 tick の所属バー time（timeframe があれば常に・全時間足で唯一源）
//     - tails: 各ティック時点の指標末尾値（specs 申告時のみ）
//   を同梱する。limit は /compute と同一規約（表示範囲＝窓長）で、これを付けないとサーバ 1 ステップの
//   費用が全件に比例する。req なし＝従来クエリ＝従来応答（byte 不変）。
export async function fetchLiveTicks(fetchImpl, since = 0, req = null) {
  if (typeof fetchImpl !== 'function') {
    return null;
  }
  // 応答が返らない接続を打ち切る（ISSUE-263）。LiveTickPlayer は未完了の poll がある間は次を
  //   出さない（同時要求数 1・ISSUE-257）。その状態でこの fetch が永久に返らないと **poll が
  //   恒久停止**する（ガード導入前は要求が重なることで結果的に流れ続けていた＝退行）。
  //   中断は「失敗」として扱われ、player は次の poll で回復する（カーソルは巻き戻さない）。
  const timeoutMs = (req && Number.isFinite(req.timeoutMs)) ? req.timeoutMs : null;
  const controller = (timeoutMs !== null && typeof AbortController === 'function')
    ? new AbortController() : null;
  const timer = controller
    ? setTimeout(() => controller.abort(), timeoutMs) : null;
  try {
    let url = `/live_ticks?since=${encodeURIComponent(since)}`;
    if (req) {
      // timeframe は常に付ける（各 tick の所属バー time＝barTimes の解決に必要。指標を 1 つも
      //   適用していなくても要る）。specs があるときだけ指標末尾値（tails）も同梱される。
      if (req.timeframe) {
        url += `&timeframe=${encodeURIComponent(req.timeframe)}`;
      }
      if (req.datasetRef) {
        url += `&datasetRef=${encodeURIComponent(req.datasetRef)}`;
      }
      if (req.specs && req.specs.length) {
        url += `&specs=${encodeURIComponent(JSON.stringify(req.specs))}`;
        if (req.limit !== undefined && req.limit !== null) {
          url += `&limit=${encodeURIComponent(req.limit)}`;
        }
        // 末尾値を計算する区間（ISSUE-257）。未指定＝サーバは全 tick で計算（旧挙動）。
        if (req.tailsWithinMs !== undefined && req.tailsWithinMs !== null) {
          url += `&tailsWithinMs=${encodeURIComponent(req.tailsWithinMs)}`;
        }
      }
    }
    const resp = controller ? await fetchImpl(url, { signal: controller.signal })
      : await fetchImpl(url);
    if (!resp.ok) {
      return null;
    }
    const payload = await resp.json();
    return payload && payload.ok ? payload : null;
  } catch {
    return null;   // 中断・ネットワーク失敗とも null（player は次 poll で回復する）
  } finally {
    if (timer !== null) {
      clearTimeout(timer);   // 成功・失敗・中断のいずれでもタイマーを残さない
    }
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
  // controller 以前の組み立て（チャート・描画・永続化・catalog）は両 root 共有の単一ソースへ委譲する。
  //   ISSUE-278 #4: ここを各 root が手書きしていたため、ライブの修正がリプレイへ届かなかった。
  const {
    chart, mainSeries, compute, paneLegendView, currentPriceView, renderer,
    updatePaneHeight, persistence, templateStore, catalog, loadCandles,
  } = await composeChartShell({ lwc, container, doc, storage, fetch, datasetRef, recentBars });

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
  // ISSUE-260: バリューエリア比率（MP の va パラメータ）。tf-period 列にも同じ比率を効かせる
  //   （未設定は null＝サーバ既定へ委ねる＝従来 URL byte 不変）。
  const mpVa = () => (marketProfile && typeof marketProfile.vaParam === 'function'
    ? marketProfile.vaParam() : null);
  const zpTfOk = () => mpSupportsTf(mpSrc(), controller._timeframe);
  // ISSUE-066: MP パラメータ変更（gear の src/mode 等）を tf-period 列アクターへ即時伝播するフック。
  //   tf-period 配線（mode==='b'）で実体を代入する。未配線（A方式・非served）は no-op（byte 不変）。
  // tf-period 列を描ける tf（ISSUE-086: 全時間足統一）＝既知 tf のすべて（台帳の 1 判定）。
  const isTfPeriodTimeframe = (tf) => isKnownTimeframe(tf);
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
    sessionsDrawnByTfPeriod: () => isTfPeriodTimeframe(controller._timeframe) && zpTfOk(),
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

  // controller に依存しない UI 部品（操作・スクロール・時間足メニュー・テンプレートメニュー）は
  //   共有配線が install する。controller / テンプレート協働子は遅延参照で渡す（生成はこの後）。
  let chartTemplates = null;
  const { chartTemplateMenu, chartTemplateDialogs } = installSharedUi({
    container,
    renderer,
    doc,
    getController: () => controller,
    updatePaneHeight,
    getTemplates: () => chartTemplates,
    // ツールバーの構成（ISSUE-278 #16）: ライブ追従トグルは本 root（ライブ）が常に持つ。
    //   リプレイのオン・オフトグルは**リプレイ層が注入されたページ**＝統合 UI のときだけ置く
    //   （standalone live には切替先が無い）。差はフラグ 1 つで表し markup は複製しない。
    toolbar: { liveFollow: true, enterReplay: !!replay },
  });
  // リプレイ操作バーの DOM もリプレイ層が所有する（live root はリプレイのコードを import しない＝
  //   注入のみ。未注入の standalone live では生成されない＝従来どおりバーは存在しない）。
  if (replay && typeof replay.installReplayBar === 'function') {
    replay.installReplayBar(doc, { withClose: true });
  }

  // ライブ連動（present 固有・B方式のみ）: チャートのライブトグル状態（FOLLOW/ANALYSIS）を MP の成長状態へ
  //   連動させる共有協調役（Model A 直交化）。表示モードは gear 選択を維持し、FOLLOW→growing=true（足内成長）／
  //   ANALYSIS→growing=false（static）。defaultMode は catalog の MP mode 既定（catalog_entry の 'mode' 既定
  //   ＝'normal'）。reapply は controller の MP 再適用（mode 維持＋growing トグル）へ遅延束縛する（controller は
  //   直後に代入されるため () => controller で吸収）。
  const mpLiveModeCoordinator = new GrowthCoordinator({
    defaultMode: 'normal',
    reapply: () => (controller ? controller.reapplyMarketProfileMode() : undefined),
  });

  // 未注入（スタンドアロン）は IndicatorController（既存経路・runtime byte 不変）。統合レイヤ注入時のみ
  //   ReplayIndicatorController（untilTime=undefined で live 等価）で生成する。opts は同一（下記 verbatim）。
  const IndicatorControllerCtor = (replay && replay.ReplayIndicatorController) || IndicatorController;
  controller = new IndicatorControllerCtor({
    catalog, compute, persistence, renderer, document: doc, datasetRef,
    timeframe, recentBars, loadCandles, marketProfile,
    // 連動配線時のみ resolver を注入（未注入＝MP へ渡す mode をそのまま＝byte 不変）。
    //   mode 解決役: 選択表示モードを返す（'ticklive' 置換なし）。growth 解決役: FOLLOW/ANALYSIS→growing 信号。
    mpModeResolver: mpLiveModeCoordinator ? (m) => mpLiveModeCoordinator.resolve(m) : null,
    mpGrowthResolver: mpLiveModeCoordinator ? () => mpLiveModeCoordinator.isGrowing() : null,
  });
  // ISSUE-276: ペイン別凡例へ行（ラベル・可視・操作）を供給する。生成直後に結線するため、
  //   restore()/bind() の初回描画から新しい凡例に載る。
  controller.setPaneLegendView(paneLegendView);

  // controller 生成後の協働子（テンプレート・取引密度帯・売買マーカー・現在値）は共有配線が結ぶ。
  //   ライブ固有の追加は onTimeframeChanged（tf 切替時の tf-period 列の即時再適用）だけ。
  //   ISSUE-090: 従来は可視レンジ変化イベント頼みで、レンジが変わらない切替（週→日→週 等）では
  //   旧 tf の列が残留し「週間隔÷7 の細い列」に見える実機バグが起きた。
  const { chartTemplates: templates, tickvolBands, tradeMarkers } = wireControllerCollaborators({
    controller, renderer, doc, fetch, datasetRef, timeframe, recentBars,
    templateStore, chartTemplateMenu, chartTemplateDialogs,
    lwc, mainSeries, chart, container, currentPriceView,
    onTimeframeChanged: () => refreshTfPeriodNow(),
  });
  chartTemplates = templates;

  // 時間足毎profile列（tf-period・最小価格単位・ローリング窓＋ジッターバッファ）の配線（served のみ）。
  //   sessions モード（marketProfile.isSessions()）かつ対応 tf（1m..1D）のとき、可視レンジぶんの列を
  //   jitter buffer 経由で取得し MP と共有の primitive へ setTfPeriods で描く（sessions の tf 一般化。
  //   primitive._draw は tf-period を優先＝旧 per-day sessions を上書き）。可視レンジ変化（スクロール/ズーム／
  //   sessions 有効化時の focusTimeRange）で refresh＝ローリング。先読み完了（onReady）で再描画＝ジッターバッファ。
  let tfPeriodActor = null;
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
    // 取得パラメータ（ISSUE-260）: src（zp 透過）と va（バリューエリア比率）を 1 つの組で渡す。
    //   va は MP のパラメータ（catalog 既定＝Python 唯一源の生成物）をそのまま透過し、backend が
    //   同一規則で解決する＝`/market_profile` の POC/VA と列の VA が同じ比率で決まる。
    getQuery: () => ({ src: mpTfPeriodSrc(mpSrc()), va: mpVa() }),
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

  // B方式は /candles から実 OHLCV を取得し、メイン系列を差し替える（/compute と時間軸を揃える）。
  //   初期は既定時間足・直近 recentBars 本。取得失敗時は SAMPLE_DATA のまま（フォールバック）。
  // 初回表示の最大遡り期間（直近1年）。ISSUE-055: 1D は数年ぶんの足がロードされ、setCandles の
  //   自動フィットで全期間が可視になると 日別 tf-period が可視域ぶん一括取得され応答肥大（実測87MB）で
  //   初回表示が重い。初回可視範囲を直近1年へ寄せ、古い範囲はスクロールで（A案デバウンス＋per-day
  //   キャッシュで滑らか）。データが1年未満（intraday 等）のときは全期間で不変。
  const INITIAL_VIEW_SPAN_SEC = 365 * 86400;
  const ready = fetchCandles(fetch, datasetRef, timeframe, recentBars).then((candles) => {
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
      });

  // ライブ更新（1 分間隔）の組み立て。served（B方式）のみ。tick は controller 経由の再計算
  //   ＋ /candles 再取得 → 最新足を renderer.updateLastCandle で差分反映する。start は入口
  //   （index.html）が served 時のみ呼ぶ。A方式（file://）は null（更新を配線しない）。
  // LiveTickPlayer（12 秒固定遅延の tick 再生）を配線するのは served（B方式）のみ＝価格の唯一の
  //   書き手。このとき旧 2 系統（LiveUpdater / FormingBarUpdater）の価格上書きは 12 秒より古い
  //   データで巻き戻すため suppressPriceUpdate=true で止める。
  //   ISSUE-253: player は**全時間足**を同一経路で扱う（バー帰属はサーバ供給）。かつては
  //   floor で周期を作っていたため 1W/1M だけ player 非対応で、価格の書き手が tf によって
  //   入れ替わっていた（更新粒度が時間足で変わる原因）。その tf 依存の配線を廃止する。
  //   file://（A方式）は player 不在＝false で既存挙動 byte 不変。
  const playerActive = true;

  const liveUpdater = new LiveUpdater({
        controller,
        renderer,
        loadCandles: (ref, tf) => fetchCandles(fetch, ref, tf, recentBars),
        datasetRef,
        getTimeframe: () => controller._timeframe,
        setInterval: setIntervalImpl,
        clearInterval: clearIntervalImpl,
        intervalMs: liveIntervalMs,
        suppressPriceUpdate: playerActive,
      });

  // 形成中バー（最新足の足内更新）の組み立て。served（B方式）のみ。/forming_bar から選択 tf の
  //   形成中バーを取得し、(1) renderer.updateLastCandle で価格の最新足を反映、(2) 指標も
  //   recomputeAllApplied({mode:'latest'}) で最新点をティック由来に再計算する（backend が
  //   mode=latest 時に形成中バーを最新足として計算へ織り込む）。LiveUpdater(60s) との分離の実体は
  //   「/candles 全件再取得(Live) vs /forming_bar(Forming)」であり、指標再計算はどちらも latest。
  //   start は入口（index.html）が served 時のみ呼ぶ。
  const formingBarUpdater = new FormingBarUpdater({
        controller,
        renderer,
        loadFormingBar: (ref, tf) => fetchFormingBar(fetch, ref, tf),
        datasetRef,
        getTimeframe: () => controller._timeframe,
        setInterval: setIntervalImpl,
        clearInterval: clearIntervalImpl,
        intervalMs: formingIntervalMs,
        // 価格の書き手は player ただ 1 つ（全時間足で同一）。tf による切り替えは持たない。
        suppressPriceUpdate: playerActive,
      });

  // LiveTickPlayer（12 秒固定遅延のなめらか tick 再生・ISSUE-049）の組み立て。served（B方式）のみ。
  //   /live_ticks から increment 取得しキュー → 100ms 粒度で serverNow-12000 以前の tick を現在 tf の
  //   形成中バーへ累積 → renderer.updateLastCandle。tf 切替・起動時は /forming_bar でシード。start は
  //   入口（index.html）が served 時のみ呼ぶ。A方式（file://）は null（既存挙動 byte 不変）。
  const liveTickPlayer = playerActive
    ? new LiveTickPlayer({
        renderer,
        fetchLiveTicks: (since, req) => fetchLiveTicks(fetch, since, req),
        loadFormingBar: (ref, tf) => fetchFormingBar(fetch, ref, tf),
        datasetRef,
        getTimeframe: () => controller._timeframe,
        setInterval: setIntervalImpl,
        clearInterval: clearIntervalImpl,
        // tick 粒度の指標末尾追従（ISSUE-250 Phase 1）: poll で適用中インスタンスを申告し、
        //   応答に同梱された各ティック時点の末尾値を tick 適用と同一同期ブロックで描く。
        //   HTTP 往復が tick 路から消えるため「指標更新回数 == ローソク更新回数」が構成上成立する。
        getComputeSpecs: () => controller.appliedComputeSpecs(),
        getLimit: () => controller.computeLimit(),
        applyFormingTails: (tails, barTime) => controller.applyFormingTails(tails, barTime),
        // バー確定駆動の full 再計算（ISSUE-151）: 期間ロールオーバー＝直前バー確定で全指標を
        //   再計算する（リプレイの毎バーその場計算と同一意味論。coalesce/pending は controller 側）。
        onBarClose: () => controller.requestFullRecompute(),
      })
    : null;

  // ライブ追従トグル（present 固有）。install() でボタン click を配線し、初期 FOLLOW を適用する
  //   （LiveUpdater 起動所有権を controller へ・start は冪等）。index.html は初期 disabled で置き、
  //   install() が活性化する＝配線されたときだけ押せる（ISSUE-275: 配信方式による分岐は持たない）。
  const liveFollowController = new LiveFollowController({
        liveUpdater,
        // ライブ価格の書き手も FOLLOW/ANALYSIS で start/stop（ANALYSIS で価格を凍結＝トグルを効かせる）。
        liveTickPlayer,
        formingBarUpdater,
        renderer,
        document: doc,
        buttonId: 'live-follow-toggle',
        // ライブ連動: FOLLOW/ANALYSIS 遷移を協調役へ通知（MP を growing↔static で連動・表示モードは維持）。
        //   協調役不在（A方式）は未注入＝既存ライブトグル挙動 byte 不変。
        onLiveStateChange: mpLiveModeCoordinator
          ? (isFollow) => mpLiveModeCoordinator.onLiveStateChange(isFollow)
          : undefined,
      });
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
  return { chart, mainSeries, renderer, controller, ready, tickvolBands, liveUpdater, formingBarUpdater, liveTickPlayer, tradeMarkers, marketProfile, liveFollowController, mpLiveModeCoordinator, tfPeriodActor, replayHandle, chartTemplates, chartTemplateMenu, chartTemplateDialogs };
}
