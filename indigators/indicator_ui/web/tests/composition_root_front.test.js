// composition_root_front.js（フロント側 Composition Root）の配線切替検証。
//
// 設計入力: 内部設計書 §2.1 / §3.3.5（ComputeHttpClient）/ §6.3（/candles）、
//   パラメータ設定ダイアログ §9（B方式 params 実反映）。
// 観点:
//   - modeForProtocol: http/https → 'b'、file:/その他 → 'a'。
//   - bootstrap: served（http）時は ComputeHttpClient（/compute）を注入し /candles を取得して
//     メイン系列を差し替える。file:// 時は EmbeddedComputeGateway + SAMPLE_DATA。
// 構造: Arrange-Act-Assert。upstream JS（lwc）と fetch は Fake を注入（DOM/実ネットワーク非依存）。

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { bootstrap, modeForProtocol } from '../js/adapter/front/composition_root_front.js';
import { ComputeHttpClient } from '../js/adapter/front/compute_http_client.js';
import { EmbeddedComputeGateway } from '../js/adapter/front/embedded_compute_gateway.js';
import { LiveUpdater } from '../js/adapter/front/live_updater.js';
import { LiveTickPlayer } from '../js/adapter/front/live_tick_player.js';

// Fake lwc（v5）: createChart → chart（addSeries/panes/addPane/timeScale/subscribeCrosshairMove）。
//   ColorType / CandlestickSeries / createTextWatermark も公開（composition・ChartRenderer が参照）。
function fakeLwc() {
  const setDataCalls = [];
  const createChartOpts = [];
  const addSeriesOpts = [];
  const mainSeries = { setData: (d) => setDataCalls.push(d) };
  const chart = {
    addSeries: (_type, opts) => { addSeriesOpts.push(opts); return mainSeries; },
    timeScale: () => ({ fitContent: () => {} }),
    panes: () => [{ setStretchFactor: () => {}, paneIndex: () => 0 }],
    addPane: () => ({ addSeries: () => ({ setData: () => {} }), setStretchFactor: () => {}, paneIndex: () => 1 }),
    removePane: () => {},
    removeSeries: () => {},
    subscribeCrosshairMove: () => {},
  };
  return {
    lwc: {
      createChart: (_c, opts) => { createChartOpts.push(opts); return chart; },
      ColorType: { Solid: 'solid' },
      CrosshairMode: { Normal: 0, Magnet: 1 },
      CandlestickSeries: {}, LineSeries: {}, HistogramSeries: {},
      createTextWatermark: () => ({ applyOptions: () => {}, detach: () => {} }),
    },
    setDataCalls,
    createChartOpts,
    addSeriesOpts,
  };
}

const noStorage = { getItem: () => null, setItem: () => {}, removeItem: () => {} };

test('modeForProtocol maps http/https to b and others to a', () => {
  assert.equal(modeForProtocol('http:'), 'b');
  assert.equal(modeForProtocol('https:'), 'b');
  assert.equal(modeForProtocol('file:'), 'a');
  assert.equal(modeForProtocol('about:'), 'a');
});

test('bootstrap injects ComputeHttpClient and mode=b when served over http', async () => {
  // Arrange
  const { lwc } = fakeLwc();
  const fakeFetch = async () => ({ ok: true, async json() { return { ok: true, candles: [] }; } });
  // Act
  const { controller, mode, ready } = await bootstrap({
    lwc, container: {}, doc: null, storage: noStorage, protocol: 'http:', fetch: fakeFetch,
  });
  await ready;
  // Assert
  assert.equal(mode, 'b');
  assert.ok(controller._compute instanceof ComputeHttpClient);
});

test('bootstrap falls back to EmbeddedComputeGateway and mode=a on file://', async () => {
  // Arrange
  const { lwc } = fakeLwc();
  // Act（A方式は SAMPLE_DATA を動的 import するため await）
  const { controller, mode } = await bootstrap({
    lwc, container: {}, doc: null, storage: noStorage, protocol: 'file:',
  });
  // Assert
  assert.equal(mode, 'a');
  assert.ok(controller._compute instanceof EmbeddedComputeGateway);
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

test('bootstrap (file://) exposes liveUpdater=null so no live updates are wired', async () => {
  // Arrange / Act（A方式）。
  const { lwc } = fakeLwc();
  const { liveUpdater } = await bootstrap({
    lwc, container: {}, doc: null, storage: noStorage, protocol: 'file:',
  });
  // Assert: A方式（file://）はライブ更新を配線しない。
  assert.equal(liveUpdater, null);
});

// ===========================================================================
// LiveTickPlayer 配線（served のみ・12 秒固定遅延の tick 再生・ISSUE-049）
//   served では player を組み立て、価格の二重書き排除のため LiveUpdater/FormingBarUpdater へ
//   suppressPriceUpdate=true を渡す。file:// は player=null＝既存挙動 byte 不変。
// ===========================================================================

test('bootstrap (served) builds a LiveTickPlayer and exposes it on the return value', async () => {
  const { lwc } = fakeLwc();
  const fakeFetch = async () => ({ ok: true, async json() { return { ok: true, candles: [] }; } });
  const { liveTickPlayer, ready } = await bootstrap({
    lwc, container: {}, doc: null, storage: noStorage, protocol: 'http:', fetch: fakeFetch,
  });
  await ready;
  assert.ok(liveTickPlayer instanceof LiveTickPlayer);
});

test('bootstrap (file://) exposes liveTickPlayer=null (byte-unchanged A-mode)', async () => {
  const { lwc } = fakeLwc();
  const { liveTickPlayer } = await bootstrap({
    lwc, container: {}, doc: null, storage: noStorage, protocol: 'file:',
  });
  assert.equal(liveTickPlayer, null);
});

test('bootstrap (served) suppresses price on LiveUpdater, and on FormingBarUpdater only for player-driven tf', async () => {
  const { lwc } = fakeLwc();
  const fakeFetch = async () => ({ ok: true, async json() { return { ok: true, candles: [] }; } });
  const { liveUpdater, formingBarUpdater, controller, ready } = await bootstrap({
    lwc, container: {}, doc: null, storage: noStorage, protocol: 'http:', fetch: fakeFetch,
  });
  await ready;
  // LiveUpdater(60s) は served で常に価格抑止（player が唯一の書き手）。
  assert.equal(liveUpdater._suppressPriceUpdate, true);
  // FormingBarUpdater は tf 依存の関数: 固定周期（player 対応）は抑止 true、1W/1M（player 非対応）は false
  //   ＝FormingBarUpdater が /forming_bar を描く価格の書き手になる。
  assert.equal(typeof formingBarUpdater._suppressPriceUpdate, 'function');
  controller._timeframe = '1D';
  assert.equal(formingBarUpdater._suppressPriceUpdate(), true, '1D は player が価格を書く→抑止');
  controller._timeframe = '1W';
  assert.equal(formingBarUpdater._suppressPriceUpdate(), false, '1W は FormingBarUpdater が価格を書く→非抑止');
  controller._timeframe = '1M';
  assert.equal(formingBarUpdater._suppressPriceUpdate(), false, '1M も非抑止');
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

test('bootstrap (file://) establishes _lastBar so hover-off shows OHLC without a prior hover', async () => {
  // 仕様: A方式（file://）でも成立（初期ロードの末尾足が _lastBar に立つ）。bootstrap が
  //   初期 candles を renderer.setCandles 経由で流すことを固定する（直接 mainSeries.setData だと
  //   _lastBar が立たず、hover 解除時に OHLC が空になる回帰を防ぐ）。
  // Arrange
  const { lwc, chart } = fakeLwcFireable();
  const doc = fakeReadoutDoc();
  // Act
  const { ready } = await bootstrap({
    lwc, container: {}, doc, storage: noStorage, protocol: 'file:',
  });
  await ready;
  // hover 解除（seriesData 空・事前 hover なし）。
  chart.fireCrosshair({ time: undefined, seriesData: new Map() });
  // Assert: SAMPLE_DATA 末尾足（close=185）が読み取り欄に描画される（_lastBar フォールバック）。
  const text = JSON.stringify(doc._readout.children);
  assert.match(text, /185/);
});

// ===========================================================================
// 価格軸ホイールズームの配線（wheel / dblclick）
//   composition root がチャートコンテナに wheel（passive:false）と dblclick を配線する。
//   wheel: renderer.handlePriceWheel(x, y, deltaY)（x/y=clientX/Y−コンテナ矩形）が true なら preventDefault。
//   dblclick: 軸領域（renderer.isOverPriceAxis(x)）なら renderer.resetPriceZoom()。
// ===========================================================================

// addEventListener を記録するチャートコンテナ Fake（handler を type で引ける）。
function fakeContainer() {
  const handlers = {};
  return {
    addEventListener(type, fn, opts) { (handlers[type] ||= []).push({ fn, opts }); },
    getBoundingClientRect() { return { left: 0, top: 0 }; },
    fire(type, ev) { (handlers[type] || []).forEach((h) => h.fn(ev)); },
    optsFor(type) { return (handlers[type] || [])[0]?.opts; },
    has(type) { return !!(handlers[type] && handlers[type].length); },
  };
}

test('bootstrap wires a non-passive wheel listener that preventDefaults when handlePriceWheel returns true', async () => {
  // Arrange
  const { lwc } = fakeLwcFireable();
  const container = fakeContainer();
  const { renderer } = await bootstrap({
    lwc, container, doc: null, storage: noStorage, protocol: 'file:',
  });
  // renderer.handlePriceWheel を spy（軸領域内=true 相当）。clientX/Y−rect の座標伝搬も検証。
  const calls = [];
  renderer.handlePriceWheel = (x, y, dy) => { calls.push([x, y, dy]); return true; };
  let prevented = false;
  // Act: 軸領域上でホイール（clientX=610, clientY=180, deltaY=-100・rect フォールバック 0,0）
  container.fire('wheel', { clientX: 610, clientY: 180, deltaY: -100, preventDefault() { prevented = true; } });
  // Assert: passive:false で登録され、handlePriceWheel が呼ばれ、true なので preventDefault
  assert.equal(container.optsFor('wheel')?.passive, false);
  assert.deepEqual(calls.at(-1), [610, 180, -100]);
  assert.equal(prevented, true);
});

test('bootstrap wheel listener does NOT preventDefault when handlePriceWheel returns false (本体ホイールを奪わない)', async () => {
  // Arrange
  const { lwc } = fakeLwcFireable();
  const container = fakeContainer();
  const { renderer } = await bootstrap({
    lwc, container, doc: null, storage: noStorage, protocol: 'file:',
  });
  renderer.handlePriceWheel = () => false; // チャート本体領域＝false
  let prevented = false;
  // Act
  container.fire('wheel', { clientX: 100, clientY: 180, deltaY: -100, preventDefault() { prevented = true; } });
  // Assert: false なら preventDefault しない（時間軸ズームへ委ねる）
  assert.equal(prevented, false);
});

test('bootstrap wires dblclick to resetPriceZoom only when over the price axis', async () => {
  // Arrange
  const { lwc } = fakeLwcFireable();
  const container = fakeContainer();
  const { renderer } = await bootstrap({
    lwc, container, doc: null, storage: noStorage, protocol: 'file:',
  });
  let resetCount = 0;
  renderer.resetPriceZoom = () => { resetCount += 1; };
  renderer.isOverPriceAxis = (x) => x >= 600; // 軸領域=600以上
  // Act: 軸領域内でダブルクリック
  container.fire('dblclick', { clientX: 610, clientY: 100 });
  assert.equal(resetCount, 1, '軸領域のダブルクリックで resetPriceZoom');
  // Act: 軸領域外でダブルクリック → reset されない
  container.fire('dblclick', { clientX: 100, clientY: 100 });
  assert.equal(resetCount, 1, '本体領域のダブルクリックでは reset しない');
});

test('bootstrap: 本体ドラッグの上下パンは全体表示（未ズーム）でも常時有効（ISSUE-108）', async () => {
  // 旧仕様の「価格ズーム中限定」ゲートは旧 override 実装の不具合回避策で、ネイティブ
  //   setVisibleRange 置換により陳腐化したため撤去（ユーザー裁定: 全体表示でも上下移動できる）。
  const { lwc } = fakeLwcFireable();
  const container = fakeContainer();
  const { renderer } = await bootstrap({
    lwc, container, doc: null, storage: noStorage, protocol: 'file:',
  });
  const dys = [];
  renderer.panPriceByPixels = (dy) => { dys.push(dy); return true; };
  renderer.isOverPriceAxis = () => false; // 本体領域。
  // (A) 未ズーム（isPriceZoomed=false）でも縦ドラッグで価格パンする。
  renderer.isPriceZoomed = () => false;
  container.fire('pointerdown', { button: 0, clientX: 100, clientY: 100 });
  container.fire('pointermove', { buttons: 1, clientX: 100, clientY: 140 }); // dy=40
  container.fire('pointerup', {});
  assert.deepEqual(dys, [40], '全体表示（自動スケール）でも縦パンする');
  // (B) ズーム中（isPriceZoomed=true）→ 従来どおり縦成分 dy で価格パン。
  renderer.isPriceZoomed = () => true;
  container.fire('pointerdown', { button: 0, clientX: 100, clientY: 100 });
  container.fire('pointermove', { buttons: 1, clientX: 100, clientY: 130 }); // dy=30
  container.fire('pointermove', { buttons: 1, clientX: 100, clientY: 150 }); // dy=20
  assert.deepEqual(dys, [40, 30, 20], 'ズーム中も縦成分で価格パン');
});

test('bootstrap: リプレイ配線は present に存在しない（ISSUE-082: isReplay=true でもスワイプ捕捉せず通常パンのまま）', async () => {
  // ISSUE-082: リプレイモード（スワイプスクラブ・replayBar）は present から撤去した。
  //   万一 actor が isReplay=true を返しても、swipe 捕捉（setUserInteraction(false)）は発生せず、
  //   本体ドラッグは通常の縦パン（価格ズーム中のみ）として動作する。
  const { lwc } = fakeLwcFireable();
  const container = fakeContainer();
  const { renderer, controller } = await bootstrap({
    lwc, container, doc: null, storage: noStorage, protocol: 'file:',
  });
  controller._marketProfile = { isReplay: () => true }; // 旧リプレイ ON 相当でも無効。
  const interactions = [];
  renderer.setUserInteraction = (on) => { interactions.push(on); };
  const dys = [];
  renderer.panPriceByPixels = (dy) => { dys.push(dy); return true; };
  renderer.isOverPriceAxis = () => false; // 本体領域。
  renderer.isPriceZoomed = () => true;
  container.fire('pointerdown', { button: 0, clientX: 100, clientY: 100 });
  container.fire('pointermove', { buttons: 1, clientX: 140, clientY: 130 }); // dy=30
  container.fire('pointerup', {});
  assert.deepEqual(interactions, [], 'スワイプ捕捉（setUserInteraction）は配線されていない');
  assert.deepEqual(dys, [30], '通常の縦パンとして動作する（リプレイ分岐なし）');
});

test('bootstrap: クロスヘアは Normal(0)＝自由追従で作成する（Magnet スナップを無効化）', async () => {
  // Arrange
  const { lwc, createChartOpts } = fakeLwc();
  // Act
  await bootstrap({ lwc, container: {}, doc: null, storage: noStorage, protocol: 'file:' });
  // Assert: createChart に crosshair.mode = Normal(0) を渡す
  assert.equal(createChartOpts.length, 1);
  assert.equal(createChartOpts[0].crosshair.mode, 0, 'Magnet(1) ではなく Normal(0)');
});

test('bootstrap: sessions×growing の MP ライブ tick が tfPeriodActor.onLiveTick へ配線される（ISSUE-083）', async () => {
  // ISSUE-083（日別プロファイルのライブ育成）: 日別×tf-period 描画×growing（FOLLOW）では、MP の
  //   live tick 経路（onLiveTick→refresh）が composition の配線で tfPeriodActor.onLiveTick を発火し、
  //   当日チャンクの再取得（refreshAt）→当日列の育成につながる。static（growing=false）では発火しない。
  const { lwc } = fakeLwcFireable();
  const fakeFetch = async () => ({ ok: true, async json() { return { ok: true, candles: [] }; } });
  const { marketProfile, tfPeriodActor, ready } = await bootstrap({
    lwc, container: fakeContainer(), doc: null, storage: noStorage, protocol: 'http:', fetch: fakeFetch,
  });
  await ready;
  assert.ok(tfPeriodActor, 'B方式では tfPeriodActor が配線される');
  let calls = 0;
  tfPeriodActor.onLiveTick = () => { calls += 1; };
  marketProfile.setParams({ mode: 'sessions' });
  await marketProfile.setEnabled(true);
  // 列描画中の想定（実UIでは可視レンジ購読/refreshTfPeriodNow が担う）。setParams 時点では MP 未有効＝
  //   isSessions()=false のため配線が setEnabled(false) へ倒す。よって MP 有効化後に列を ON にする。
  tfPeriodActor.setEnabled(true);
  // static（ANALYSIS）: 発火しない。
  await marketProfile.onLiveTick();
  assert.equal(calls, 0, 'static では育成しない');
  // growing（FOLLOW）: live tick → refresh → onSessionsLiveGrow → tfPeriodActor.onLiveTick。
  marketProfile.applyGrowthState({ growing: true });
  await marketProfile.onLiveTick();
  assert.equal(calls, 1, 'growing の live tick で当日列の再取得が発火する');
  // tfPeriodActor 無効（列非描画＝日別解除等）: 配線ゲートで発火しない。
  tfPeriodActor.setEnabled(false);
  await marketProfile.onLiveTick();
  assert.equal(calls, 1, '列非描画中は発火しない');
});

test('bootstrap: メイン系列は現在値ラインを固定色で常時表示する（ISSUE-084: 現在値の視認性）', async () => {
  // 日別プロファイル（ローソク透明化）でも現在値の水準が見えるよう、priceLine を candle 色に
  //   依存しない固定色で明示する（lwc 既定の priceLineColor=''（バー色追従）は透明化で消える）。
  const { lwc, addSeriesOpts } = fakeLwc();
  await bootstrap({ lwc, container: {}, doc: null, storage: noStorage, protocol: 'file:' });
  const main = addSeriesOpts[0];
  assert.ok(main, 'メイン系列の生成オプションが渡る');
  assert.equal(main.priceLineVisible, true, '現在値ラインを表示する');
  assert.equal(typeof main.priceLineColor, 'string', '固定色（バー色追従にしない）');
  assert.ok(main.priceLineColor.length > 0, '固定色が空でない');
  assert.equal(main.lastValueVisible, true, '価格軸の現在値ラベルも表示する');
});

test('bootstrap: 時間足切替で tf-period 列を即時再適用する（旧 tf 列の残留防止・ISSUE-090）', async () => {
  // 実機バグ（依頼者報告）: 週→日→週 のように可視レンジが変わらない tf 切替では
  //   visibleTimeRangeChange が発火せず、旧 tf の列（日次）が週足チャートへ残留して
  //   週間隔÷7 の細い列に見えた。tf 切替オブザーバから refreshTfPeriodNow を明示発火する。
  const { lwc } = fakeLwcFireable();
  const fakeFetch = async () => ({ ok: true, async json() { return { ok: true, candles: [] }; } });
  const { marketProfile, tfPeriodActor, controller, ready } = await bootstrap({
    lwc, container: fakeContainer(), doc: null, storage: noStorage, protocol: 'http:', fetch: fakeFetch,
  });
  await ready;
  marketProfile.setParams({ mode: 'sessions' });
  await marketProfile.setEnabled(true);
  tfPeriodActor.setEnabled(true);
  let refreshes = 0;
  const origSetEnabled = tfPeriodActor.setEnabled.bind(tfPeriodActor);
  tfPeriodActor.setEnabled = (on) => { if (on) refreshes += 1; return origSetEnabled(on); };
  // 時間足切替（observer 経由）→ tf-period が即時 setEnabled(true)（=refresh/ensure）される。
  controller._timeframe = '1W';
  controller._timeframeObserver && controller._timeframeObserver('1W');
  assert.ok(refreshes >= 1, 'tf 切替で tf-period を即時再適用する');
});

// ===========================================================================
// MP 単一化（統合レイヤ配線）: replay 注入時のみ MP アクターを ReplayMarketProfileActor へ差し替え、
//   getContext().to を mode 連動（ライブ=MP_TO_LATEST＝clock省略＝base byte 等価／リプレイ=untilTime）に
//   する。未注入（standalone live）は base のまま＝上の 742 テスト群で byte 不変を担保。
// ===========================================================================

test('bootstrap(replay注入): MP アクターを差し替え、ライブ(isLiveMode=true)は getContext().to=MP_TO_LATEST', async () => {
  const { IndicatorController } = await import('../js/adapter/front/indicator_controller.js');
  const { MP_TO_LATEST } = await import('../js/adapter/front/market_profile_client.js');
  const { lwc } = fakeLwc();
  const fakeFetch = async () => ({ ok: true, async json() { return { ok: true, candles: [] }; } });
  let capturedGetContext = null;
  class StubMpActor {
    constructor(opts) { capturedGetContext = opts.getContext; }
    isSessions() { return false; }
    setEnabled() {} isEnabled() { return false; }
  }
  const { marketProfile, ready } = await bootstrap({
    lwc, container: fakeContainer(), doc: null, storage: noStorage, protocol: 'http:', fetch: fakeFetch,
    replay: {
      // 派生の代わりに base IndicatorController で bootstrap を成立させる（controller._timeframe 等を使用）。
      ReplayIndicatorController: IndicatorController,
      ReplayMarketProfileActor: StubMpActor,
      setupReplay: async () => ({ enable() {}, disable() {}, destroy() {} }),
      isLiveMode: () => true,
    },
  });
  await ready;
  assert.ok(marketProfile instanceof StubMpActor, '注入時は単一 MP アクター（ReplayMarketProfileActor 相当）へ差し替える');
  assert.equal(capturedGetContext().to, MP_TO_LATEST, 'ライブ（isLiveMode=true）は to=MP_TO_LATEST（clock 省略＝base byte 等価）');
});

test('bootstrap(replay注入): リプレイ(isLiveMode=false)は getContext().to=controller._untilTime（int T＝pull-at-T）', async () => {
  const { IndicatorController } = await import('../js/adapter/front/indicator_controller.js');
  const { lwc } = fakeLwc();
  const fakeFetch = async () => ({ ok: true, async json() { return { ok: true, candles: [] }; } });
  let capturedGetContext = null;
  let theController = null;
  // controller._untilTime を後から差せるよう、bootstrap が生成した controller を捕捉する派生を注入する。
  class CapturingController extends IndicatorController {
    constructor(opts) { super(opts); theController = this; }
  }
  class StubMpActor {
    constructor(opts) { capturedGetContext = opts.getContext; }
    isSessions() { return false; }
    setEnabled() {} isEnabled() { return false; }
  }
  const { ready } = await bootstrap({
    lwc, container: fakeContainer(), doc: null, storage: noStorage, protocol: 'http:', fetch: fakeFetch,
    replay: {
      ReplayIndicatorController: CapturingController,
      ReplayMarketProfileActor: StubMpActor,
      setupReplay: async () => ({ enable() {}, disable() {}, destroy() {} }),
      isLiveMode: () => false, // リプレイ
    },
  });
  await ready;
  theController._untilTime = 1704074400; // リプレイの現在時刻 T
  assert.equal(capturedGetContext().to, 1704074400, 'リプレイは to=controller._untilTime（pull-at-T）');
});
