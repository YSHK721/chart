// composition_root_front.js（フロント側 Composition Root）の配線切替検証。
//
// 設計入力: 内部設計書 §2.1 / §3.3.5（ComputeHttpClient）/ §6.3（/candles）、
//   パラメータ設定ダイアログ §9（B方式 params 実反映）。
// 観点:
//   - bootstrap: served（http）時は ComputeHttpClient（/compute）を注入し /candles を取得して
//     メイン系列を差し替える。
// 構造: Arrange-Act-Assert。upstream JS（lwc）と fetch は Fake を注入（DOM/実ネットワーク非依存）。

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { bootstrap } from '../js/adapter/front/composition_root_front.js';
import { ComputeHttpClient } from '../js/adapter/front/compute_http_client.js';
import { LiveUpdater } from '../js/adapter/front/live_updater.js';
import { ReplayMarketProfileActor } from '../js/adapter/front/replay_market_profile_actor.js';
import { MarketProfileActor } from '../js/adapter/front/market_profile_actor.js';
import { ReplayIndicatorController } from '../js/adapter/front/replay_indicator_controller.js';
import { IndicatorController } from '../js/adapter/front/indicator_controller.js';

// Fake lwc（v5）: createChart → chart（addSeries/panes/addPane/timeScale/subscribeCrosshairMove）。
//   ColorType / CandlestickSeries / createTextWatermark も公開（composition・ChartRenderer が参照）。
function fakeLwc() {
  const setDataCalls = [];
  const mainSeries = { setData: (d) => setDataCalls.push(d) };
  const chart = {
    addSeries: () => mainSeries,
    timeScale: () => ({ fitContent: () => {} }),
    panes: () => [{ setStretchFactor: () => {}, paneIndex: () => 0 }],
    addPane: () => ({ addSeries: () => ({ setData: () => {} }), setStretchFactor: () => {}, paneIndex: () => 1 }),
    removePane: () => {},
    removeSeries: () => {},
    subscribeCrosshairMove: () => {},
  };
  return {
    lwc: {
      createChart: () => chart,
      ColorType: { Solid: 'solid' },
      CandlestickSeries: {}, LineSeries: {}, HistogramSeries: {},
      createTextWatermark: () => ({ applyOptions: () => {}, detach: () => {} }),
    },
    setDataCalls,
  };
}

const noStorage = { getItem: () => null, setItem: () => {}, removeItem: () => {} };


test('bootstrap injects ComputeHttpClient when served over http', async () => {
  // Arrange
  const { lwc } = fakeLwc();
  const fakeFetch = async () => ({ ok: true, async json() { return { ok: true, candles: [] }; } });
  // Act
  const { controller, ready } = await bootstrap({
    lwc, container: {}, doc: null, storage: noStorage, protocol: 'http:', fetch: fakeFetch,
  });
  await ready;
  // Assert
  assert.ok(controller._compute instanceof ComputeHttpClient);
  // reveal 対応の subclass を生成する（共有 present base を extends＝単一ソース＋reveal 拡張）。
  assert.ok(controller instanceof ReplayIndicatorController, 'controller は ReplayIndicatorController');
  assert.ok(controller instanceof IndicatorController, 'subclass は共有 IndicatorController を extends する（LSP）');
});

test('bootstrap (served) fetches /candles and replaces main series data', async () => {
  // Arrange
  const { lwc, setDataCalls } = fakeLwc();
  const candles = [{ time: 1277769600, open: 1.2667, high: 1.6667, low: 1.1693, close: 1.5927 }];
  let candlesUrl = null;
  const fakeFetch = async (url) => {
    candlesUrl = url;
    return { ok: true, async json() { return { ok: true, candles }; } };
  };
  // Act
  const { ready } = await bootstrap({
    lwc, container: {}, doc: null, storage: noStorage, protocol: 'https:', fetch: fakeFetch,
  });
  await ready;
  // Assert: 既定時間足（1D）・直近 RECENT_BARS（1500）本を /candles へ伝搬する（§配信設計）。
  assert.match(candlesUrl, /^\/candles\?datasetRef=sample&timeframe=1D&limit=1500$/);
  // B方式は SAMPLE_DATA を読み込まず、/candles 取得後に setData する（唯一の setData が取得 candles）。
  assert.deepEqual(setDataCalls.at(-1), candles);
});

test('bootstrap (served) draws nothing when /candles fetch fails (no SAMPLE_DATA in B mode)', async () => {
  // Arrange
  const { lwc, setDataCalls } = fakeLwc();
  const fakeFetch = async () => { throw new TypeError('Failed to fetch'); };
  const before = setDataCalls.length;
  // Act
  const { ready } = await bootstrap({
    lwc, container: {}, doc: null, storage: noStorage, protocol: 'http:', fetch: fakeFetch,
  });
  await ready;
  // Assert: B方式は SAMPLE_DATA を読み込まないため、/candles 失敗時は setData 0 回（空チャート）。
  assert.equal(setDataCalls.length, before + 0);
});

// ===========================================================================
// LiveUpdater 配線（served のみ・1 分間隔ライブ更新）
//   合成根は LiveUpdater を組み立てて bootstrap 戻り値に加える（setInterval は合成根に置かない）。
//   index.html が served 時のみ liveUpdater.start() を呼ぶ（file:// はスキップ）。
// ===========================================================================

test('bootstrap (served) builds a LiveUpdater and exposes it on the return value', async () => {
  // Arrange
  const { lwc } = fakeLwc();
  const fakeFetch = async () => ({ ok: true, async json() { return { ok: true, candles: [] }; } });
  // Act
  const { liveUpdater, ready } = await bootstrap({
    lwc, container: {}, doc: null, storage: noStorage, protocol: 'http:', fetch: fakeFetch,
  });
  await ready;
  // Assert: served は LiveUpdater を組み立てて戻り値に載せる（start は index.html 側）。
  assert.ok(liveUpdater instanceof LiveUpdater);
});


// ===========================================================================
// Market Profile 全モード配線（MP DI は composition root に集約）
//   共有 present MarketProfileActor を extends した ReplayMarketProfileActor を組み立て、戻り値に
//   marketProfile として公開する（replay.js/setupReplay 内で new しない＝DI 集約）。
//   client（/market_profile）/formingClient（/market_profile_forming）/renderer/replayBar/getContext(to) を注入。
// ===========================================================================

test('bootstrap builds a ReplayMarketProfileActor (subclass of shared MarketProfileActor) as marketProfile (initially disabled)', async () => {
  // Arrange
  const { lwc } = fakeLwc();
  const fakeFetch = async () => ({ ok: true, async json() { return { ok: true, candles: [] }; } });
  // Act
  const { marketProfile, ready } = await bootstrap({
    lwc, container: {}, doc: null, storage: noStorage, protocol: 'http:', fetch: fakeFetch,
  });
  await ready;
  // Assert: subclass が組み立てられ（共有 MarketProfileActor を extends＝fork ではない）、既定は無効
  //   （トグル OFF＝既存 replay へ非干渉）。
  assert.ok(marketProfile instanceof ReplayMarketProfileActor);
  assert.ok(marketProfile instanceof MarketProfileActor, '共有 MarketProfileActor を継承する（単一ソース）');
  assert.equal(marketProfile.isEnabled(), false);
});

test('bootstrap injects the same marketProfile instance into the controller (menu enablement path)', async () => {
  // 設計入力: 是正 step3「composition_root_front が同一 actor を controller と setupReplay 両方へ注入」。
  //   これにより メニュー（controller.applyIndicator('market_profile')）が同一 actor を有効化し、
  //   render/animateForming の駆動フック（setupReplay 側）が isEnabled()=true を観測して育てる。
  const { lwc } = fakeLwc();
  const fakeFetch = async () => ({ ok: true, async json() { return { ok: true, candles: [] }; } });
  const { controller, marketProfile, ready } = await bootstrap({
    lwc, container: {}, doc: null, storage: noStorage, protocol: 'http:', fetch: fakeFetch,
  });
  await ready;
  assert.strictEqual(controller._marketProfile, marketProfile,
    'controller と setupReplay は同一 marketProfile 実体を共有する');
});

test('bootstrap marketProfile enterBar posts base=1/now to /market_profile_forming when enabled', async () => {
  // Arrange: forming エンドポイントの URL を捕捉する fake fetch。
  const { lwc } = fakeLwc();
  const urls = [];
  const fakeFetch = async (url) => {
    urls.push(url);
    if (String(url).startsWith('/market_profile_forming')) {
      return { ok: true, async json() {
        return { ok: true, formingStart: 1000, ticks: [], baseFine: [0, 0, 0], baseKmin: 100,
          activeTable: [[1]], priceMin: 1000, priceMax: 1100, nBins: 3, gridW: 10, now: 1000 };
      } };
    }
    return { ok: true, async json() { return { ok: true, candles: [] }; } };
  };
  // Act
  const { marketProfile, ready } = await bootstrap({
    lwc, container: {}, doc: null, storage: noStorage, protocol: 'http:', fetch: fakeFetch,
    datasetRef: 'jp225_tick',
  });
  await ready;
  await marketProfile.setEnabled(true);
  // 全モード機能化: enterBar は ticklive モードでのみ push 駆動する（self-guard）。ticklive を選択してから駆動。
  marketProfile.setParams({ mode: 'ticklive' });
  await marketProfile.enterBar(1000);
  // Assert: forming 取得が composition の fetch 経由で飛ぶ（base=1・now=T・datasetRef 連動）。
  //   src=dwell は backend controller が強制するため URL には出ない（buildFormingUrl は src を送らない）。
  const formingUrl = urls.find((u) => String(u).startsWith('/market_profile_forming'));
  assert.ok(formingUrl, '/market_profile_forming へ取得が飛ぶ');
  assert.match(formingUrl, /datasetRef=jp225_tick/);
  assert.match(formingUrl, /base=1/);
  assert.match(formingUrl, /now=1000/);
});

// ===========================================================================
// クロスヘア価格読み取り欄（左上オーバーレイ）の配線
//   composition root が CrosshairReadoutView を生成し、ChartRenderer の onCrosshairReadout に
//   (dto) => view.render(dto) を注入する。crosshair 発火で読み取り要素へ描画される。
// ===========================================================================

// crosshair handler を捕捉できる fake lwc（fireCrosshair で発火可能）。
function fakeLwcFireable() {
  const created = [];
  const mainSeries = { setData: () => {}, update: () => {} };
  const CandlestickSeries = {};
  const handlers = [];
  const chart = {
    // 最初の addSeries（CandlestickSeries）は main 系列を返す。以降（overlay）は別系列。
    addSeries: (def, opts) => {
      if (def === CandlestickSeries) { return mainSeries; }
      const s = { _opts: opts, setData: () => {}, applyOptions: () => {} }; created.push(s); return s;
    },
    timeScale: () => ({ fitContent: () => {} }),
    panes: () => [{ setStretchFactor: () => {}, paneIndex: () => 0 }],
    addPane: () => ({ addSeries: () => ({ setData: () => {} }), setStretchFactor: () => {}, setPreserveEmptyPane: () => {}, paneIndex: () => 1 }),
    removePane: () => {}, removeSeries: () => {},
    // 実 lwc の subscribeCrosshairMove はマルチキャスト（複数購読が共存）。ChartRenderer と
    //   TradeMarkersRenderer（v4・C1）が共に購読するため、単一スロットではなく全ハンドラを保持する。
    subscribeCrosshairMove: (h) => { handlers.push(h); },
    fireCrosshair: (param) => { handlers.forEach((h) => h(param)); },
  };
  return {
    lwc: {
      createChart: () => chart,
      ColorType: { Solid: 'solid' },
      CandlestickSeries, LineSeries: {}, HistogramSeries: {},
      createTextWatermark: () => ({ applyOptions: () => {}, detach: () => {} }),
    },
    chart, mainSeries,
  };
}

// crosshair-readout 要素を持つ fake document（CrosshairReadoutView の描画先）。
function fakeReadoutDoc() {
  const mk = () => ({ className: '', textContent: '', style: {}, children: [],
    set innerHTML(v) { if (v === '') this.children = []; }, get innerHTML() { return ''; },
    append(...n) { this.children.push(...n); } });
  const readout = mk();
  return {
    _readout: readout,
    getElementById: (id) => (id === 'crosshair-readout' ? readout : null),
    createElement: () => mk(),
  };
}

test('bootstrap wires onCrosshairReadout so crosshair moves render into #crosshair-readout', async () => {
  // Arrange
  const { lwc, chart, mainSeries } = fakeLwcFireable();
  const doc = fakeReadoutDoc();
  // Act
  const { ready } = await bootstrap({
    lwc, container: {}, doc, storage: noStorage, protocol: 'file:',
  });
  await ready;
  // crosshair 移動を発火（main OHLC を seriesData に載せる）。
  chart.fireCrosshair({ time: 1277769600, seriesData: new Map([[mainSeries, { open: 1.2, high: 1.6, low: 1.1, close: 1.5 }]]) });
  // Assert: 読み取り要素へ何か描画される（OHLC 行）。
  assert.ok(doc._readout.children.length > 0, 'crosshair readout element should be populated');
});


// ===========================================================================
// ChartInteractionController 配線（価格軸 wheel ズーム・dblclick・本体縦パン）
//   composition root は new して install() するだけに縮小する（配線専用）。
//   参照実装 indigators/indicator_ui/.../composition_root_front.js L262-277 と同型の配線。
// ===========================================================================

test('bootstrap wires ChartInteractionController: registers wheel/dblclick/pointer listeners on container', async () => {
  // Arrange: addEventListener を記録する container Fake（他配線に非干渉な補助 API も併設）。
  const { lwc } = fakeLwc();
  const registered = [];
  const container = {
    addEventListener(type, fn, opts) { registered.push({ type, opts }); },
    getBoundingClientRect() { return { left: 0, top: 0 }; },
    clientHeight: 400,
  };
  // Act（A方式・file://＝fetch 不要で同期配線を確認）。
  const { ready } = await bootstrap({
    lwc, container, doc: null, storage: noStorage, protocol: 'file:',
  });
  await ready;
  // Assert: wheel は passive:false + capture:true で配線され、dblclick / pointerdown も配線される。
  const wheel = registered.find((r) => r.type === 'wheel');
  assert.ok(wheel, 'wheel リスナーが配線される');
  assert.equal(wheel.opts?.passive, false);
  assert.equal(wheel.opts?.capture, true);
  assert.ok(registered.some((r) => r.type === 'dblclick'), 'dblclick リスナーが配線される');
  assert.ok(registered.some((r) => r.type === 'pointerdown'), 'pointerdown（本体縦パン）が配線される');
});
