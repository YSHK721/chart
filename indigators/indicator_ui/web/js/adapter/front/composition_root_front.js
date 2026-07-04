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
import { FormingBarUpdater } from './forming_bar_updater.js';
import { EmbeddedComputeGateway } from './embedded_compute_gateway.js';
import { LocalStorageGateway } from './local_storage_gateway.js';
import { IndicatorCatalogClient } from './catalog_client.js';
import { IndicatorController } from './indicator_controller.js';
import { TradeMarkersRenderer } from './trade_markers_renderer.js';
import { MarketProfileClient } from './market_profile_client.js';
import { MarketProfileHistogramPrimitive } from './market_profile_primitive.js';
import { MarketProfileActor } from './market_profile_actor.js';
import { MarketProfileReplayBar } from './market_profile_replay_bar.js';

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
  const marketProfile = new MarketProfileActor({
    client: new MarketProfileClient({ fetch }),
    primitive: new MarketProfileHistogramPrimitive(),
    mainSeries,
    replayBar,
    // 増分2: スナップショットのローソクトリム源（renderer.setCandleTrim）。lwc 直叩きは renderer に隔離。
    renderer,
    getCandles: () => renderer.getCandles(),
    getContext: () => ({ datasetRef, timeframe: controller._timeframe, limit: recentBars }),
  });

  // 増分2 スワイプ: チャートコンテナの横ドラッグで T をスクラブする（replay ON 中のみ）。
  //   ★プロト準拠の**相対デルタ方式**（prototype_260630-01/js/app.js L442-457）:
  //     pointerdown で開始 x（startX）と開始 index（startIdx）を記録するだけ（＝クリック位置へ飛ばさない）。
  //     pointermove で dIdx=round((x - startX)/pixelsPerBar) を startIdx に足して T を更新する。
  //     pixelsPerBar は renderer 側で barSpacing を返しつつ極小時は 8px を下限にする（ズームアウト時に
  //     わずかなマウス移動でスライダが暴走する不具合の修正）。旧実装は coordinateToLogical の絶対
  //     マッピング（下限なし）で過敏だった。lwc 座標 API は renderer に隔離済み。
  //   container/doc 不在（SSR/テスト）や pointer 非対応は no-op（防御）。
  //   ★リプレイ中も**縦成分は価格パン**する（要望「拡大しても上下も移動」）。横=スクラブ・縦=価格パンの
  //     2D 操作にする。純横（dy=0）は価格を触らず T 追従を維持、純縦は index 不変でスクラブせず価格のみ動く。
  if (container && typeof container.addEventListener === 'function') {
    let swiping = false;
    let swipeStartX = 0;
    let swipeStartIdx = 0;
    let lastScrubIdx = 0; // 冗長スクラブ回避（縦のみドラッグで同 index を再取得しない）。
    let lastSwipeY = 0;   // 縦パンの前フレーム y（価格パンの dy 算出）。
    const isReplayOn = () => !!(controller && controller._marketProfile
      && typeof controller._marketProfile.isReplay === 'function' && controller._marketProfile.isReplay());
    const rectLeft = () => (typeof container.getBoundingClientRect === 'function'
      ? container.getBoundingClientRect().left : 0);
    container.addEventListener('pointerdown', (e) => {
      if (!isReplayOn() || e.button !== 0) {
        return; // 左ボタンのみ（右クリック等でスワイプ開始しない・2Dパン側と整合）。
      }
      swiping = true;
      renderer.setUserInteraction(false); // 通常スクロール/ズームを停止（スワイプ捕捉）。
      // 開始点のみ記録（クリック位置へは飛ばさない＝プロト mousedown 相当）。
      swipeStartX = e.clientX - rectLeft();
      swipeStartIdx = typeof replayBar.currentIndex === 'function' ? replayBar.currentIndex() : 0;
      lastScrubIdx = swipeStartIdx;
      lastSwipeY = e.clientY;
    });
    container.addEventListener('pointermove', (e) => {
      if (!swiping || !isReplayOn()) {
        return;
      }
      // 横成分: T スクラブ（相対デルタ・index 変化時のみ再取得＝縦のみドラッグで無駄打ちしない）。
      const x = e.clientX - rectLeft();
      const px = renderer.pixelsPerBar(); // barSpacing（極小時は 8px 下限＝プロト準拠）。
      const idx = swipeStartIdx + Math.round((x - swipeStartX) / px); // 左ドラッグ=過去へ。
      if (idx !== lastScrubIdx) {
        lastScrubIdx = idx;
        replayBar.scrubToLogical(idx); // clamp は scrubToLogical 内で実施。
      }
      // 縦成分: 価格パン。**価格ズーム中（isPriceZoomed）のみ**（非リプレイ側と統一）。全体表示で
      //   縦パンすると override が張られ空白露出＝撤去した不具合をリプレイ中に再現するため、ゲートする。
      const dy = e.clientY - lastSwipeY;
      lastSwipeY = e.clientY;
      if (typeof renderer.isPriceZoomed === 'function' && renderer.isPriceZoomed()) {
        updatePaneHeight(); // autoSize 追随。
        renderer.panPriceByPixels(dy);
      }
    });
    const endSwipe = () => {
      if (!swiping) {
        return;
      }
      swiping = false;
      renderer.setUserInteraction(true); // 通常操作を復元。
    };

    container.addEventListener('pointerup', endSwipe);
    container.addEventListener('pointerleave', endSwipe);
  }

  // 価格軸ホイールズームの配線（wheel / dblclick）。既存操作（本体ホイール=時間軸ズーム・軸ドラッグ）は不変。
  //   wheel: 価格軸領域上（renderer が x>=timeScale().width() で判定）のときだけ価格ズームし preventDefault。
  //     handlePriceWheel が false（本体領域・データ無し）なら preventDefault せず時間軸ズームへ委ねる。
  //     passive:false で登録（preventDefault を有効化）。リプレイの pointer swipe とは別イベントで非干渉。
  //   dblclick: 価格軸領域なら自動スケールへ復帰（resetPriceZoom）。
  //   座標は **clientX/Y - コンテナ矩形**（コンテナ左上基準）で計算する。offsetX はイベント target
  //   （lwc の内部 canvas＝価格軸 canvas 等）基準になり、軸上では小さい値→本体領域と誤判定して
  //   価格ズームが発火しない（実機で確認したバグの修正）。lwc 座標/priceScale API は renderer に隔離済み。
  if (container && typeof container.addEventListener === 'function') {
    const containerXY = (e) => {
      const r = typeof container.getBoundingClientRect === 'function'
        ? container.getBoundingClientRect() : { left: 0, top: 0 };
      return { x: e.clientX - r.left, y: e.clientY - r.top };
    };
    container.addEventListener('wheel', (e) => {
      // リサイズ追随: 価格変換前に pane 高を再計算する（autoSize で container 高が変わるため）。
      updatePaneHeight();
      const { x, y } = containerXY(e);
      const handled = renderer.handlePriceWheel(x, y, e.deltaY);
      if (handled) {
        // lwc は自前の wheel リスナーで defaultPrevented を尊重せず時間軸ズームも実行してしまう。
        // capture 段階（本リスナー）で stopPropagation し、lwc へ届く前に止める（実機で確認したバグの修正）。
        if (typeof e.preventDefault === 'function') {
          e.preventDefault();
        }
        if (typeof e.stopPropagation === 'function') {
          e.stopPropagation();
        }
      }
    }, { passive: false, capture: true });
    container.addEventListener('dblclick', (e) => {
      if (renderer.isOverPriceAxis(containerXY(e).x)) {
        renderer.resetPriceZoom();
      }
    });

    // 本体ドラッグの縦成分で価格パン（上下移動）を **価格ズーム中（override 有効時）に限り** 行う。
    //   ・全体表示（自動スケール）では縦パンしない＝空白が出て拡大縮小に見える不具合を出さない（撤去理由）。
    //   ・価格軸ホイールズーム後（renderer.isPriceZoomed()）は縦パンを許可＝拡大した価格帯の外も辿れる
    //     （ユーザFB「その価格帯以外確認できないのは問題」への対応）。横は lwc の時間パンに委ねる。
    //   価格軸上・リプレイ中は対象外（リプレイは横スワイプが占有・軸は lwc ネイティブ）。
    let vpanActive = false;
    let lastVpanY = 0;
    const isReplayOn2 = () => !!(controller && controller._marketProfile
      && typeof controller._marketProfile.isReplay === 'function' && controller._marketProfile.isReplay());
    container.addEventListener('pointerdown', (e) => {
      if (isReplayOn2() || e.button !== 0) {
        return;
      }
      if (renderer.isOverPriceAxis(containerXY(e).x)) {
        return; // 価格軸上は lwc ネイティブのスケールに委ねる。
      }
      vpanActive = true;
      lastVpanY = e.clientY;
    });
    container.addEventListener('pointermove', (e) => {
      if (!vpanActive) {
        return;
      }
      if ((e.buttons & 1) === 0) {
        vpanActive = false;
        return;
      }
      const dy = e.clientY - lastVpanY;
      lastVpanY = e.clientY;
      // ★価格ズーム中のみ縦パン（全体表示では価格を触らず自動スケール維持）。
      if (typeof renderer.isPriceZoomed === 'function' && renderer.isPriceZoomed()) {
        updatePaneHeight();
        renderer.panPriceByPixels(dy);
      }
    });
    const endVpan = () => { vpanActive = false; };
    container.addEventListener('pointerup', endVpan);
    container.addEventListener('pointerleave', endVpan);
  }

  controller = new IndicatorController({
    catalog, compute, persistence, renderer, document: doc, mode, datasetRef,
    timeframe, recentBars, loadCandles, marketProfile,
  });

  // B方式は /candles から実 OHLCV を取得し、メイン系列を差し替える（/compute と時間軸を揃える）。
  //   初期は既定時間足・直近 recentBars 本。取得失敗時は SAMPLE_DATA のまま（フォールバック）。
  const ready = (mode === 'b')
    ? fetchCandles(fetch, datasetRef, timeframe, recentBars).then((candles) => {
        if (candles && candles.length > 0) {
          // renderer.setCandles 経由で _lastBar も立てる（読み取り欄の hover 解除フォールバック）。
          renderer.setCandles(candles);
        }
      })
    : Promise.resolve();

  // ライブ更新（1 分間隔）の組み立て。served（B方式）のみ。tick は controller 経由の再計算
  //   ＋ /candles 再取得 → 最新足を renderer.updateLastCandle で差分反映する。start は入口
  //   （index.html）が served 時のみ呼ぶ。A方式（file://）は null（更新を配線しない）。
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

  // marketProfile は controller 生成前に組み立て済み（controller へ注入＋既存トグル用に戻り値へ）。
  //   トグル配線は入口（index.html）が marketProfile.setEnabled(on) を呼ぶ（bootstrap に副作用を足さない）。
  return { chart, mainSeries, renderer, controller, mode, ready, liveUpdater, formingBarUpdater, tradeMarkers, marketProfile, replayBar };
}
