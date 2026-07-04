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
import { EmbeddedComputeGateway } from './embedded_compute_gateway.js';
import { LocalStorageGateway } from './local_storage_gateway.js';
import { IndicatorCatalogClient } from './catalog_client.js';
import { ReplayIndicatorController } from './replay_indicator_controller.js';
import { TradeMarkersRenderer } from './trade_markers_renderer.js';
import { MarketProfileFormingClient } from './market_profile_forming_client.js';
import { MarketProfileClient } from './market_profile_client.js';
import { MarketProfileHistogramPrimitive } from './market_profile_primitive.js';
import { MarketProfileReplayBar } from './market_profile_replay_bar.js';
import { ReplayMarketProfileActor } from './replay_market_profile_actor.js';
import { DwellAccumulator } from '../../domain/market_profile_dwell_accumulator.js';

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

  const controller = new ReplayIndicatorController({
    catalog, compute, persistence, renderer, document: doc, mode, datasetRef,
    timeframe, recentBars, loadCandles,
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

  return { chart, mainSeries, renderer, controller, mode, ready, liveUpdater, tradeMarkers, marketProfile, replayBar };
}
