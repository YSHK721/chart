// indicator_controller.js（F3 系列名照合の実行主体・§3.3.6）の純ロジック検証。
//
// 設計入力: 内部設計書 §3.3.6（_validateSeriesNames: 応答 series[].name を
//   SeriesDef.series_name（dynamic は series_name_pattern 展開）集合と突合し、
//   不一致系列はスキップ＋console.warn。正常系 pOL 99% を誤検出しない）。
// 照合基準は domain SeriesDef（catalog 由来）。DOM 非依存の純ロジックのみ検証。
// 構造: Arrange-Act-Assert（AAA）。

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { IndicatorController } from '../js/adapter/front/indicator_controller.js';
import { ReplayIndicatorController } from '../js/adapter/front/replay_indicator_controller.js';
import { get } from '../js/usecase/catalog.js';

// DOM/port を使わない純ロジック検証のため、ports は最小スタブで生成。
function controller() {
  const noop = () => {};
  return new IndicatorController({
    catalog: { listIndicators: () => [], get },
    compute: { compute: async () => ({ ok: true, generation: 0, series: [] }) },
    persistence: { loadApplied: () => [], saveApplied: noop, loadFavorites: () => [], saveFavorites: noop, loadUiState: () => ({}), saveUiState: noop, nextSeq: () => 1 },
    renderer: { renderLine: noop, renderHorizontal: noop, setData: noop, setVisible: noop, remove: noop },
    facade: {},
    document: null,
  });
}

test('_expectedSeriesNames expands static series_name set (tgp_btlm)', () => {
  const ctrl = controller();
  const def = get('tgp_btlm');
  const expected = ctrl._expectedSeriesNames(def);
  assert.ok(expected.has('btlm_mean'));
  assert.ok(expected.has('btlm_q5'));
  assert.ok(expected.has('btlm_q95'));
});

test('_expectedSeriesNames expands dynamic series_name_pattern set (profit_band 28 names)', () => {
  const ctrl = controller();
  const def = get('profit_band');
  const expected = ctrl._expectedSeriesNames(def);
  assert.equal(expected.size, 28);
  assert.ok(expected.has('pOL 99%'));
  assert.ok(expected.has('nOH 51%'));
});

// 汎用の params 対応 F3 展開（*FromParam）の検証。合成 def を用いる（moving_averages は
//   現在 4 固定系列のため dynamic pattern を持たないが、_expandPattern の汎用機能は維持する）。
const SYNTH_DYNAMIC_DEF = {
  id: 'synthetic',
  series: [{
    dynamic: true,
    seriesNamePattern: {
      template: '{bucket} {pct}',
      bucketsFromParam: 'types', bucketsUpper: true,
      pctsFromParam: 'periods', pctsInt: true,
      buckets: ['X'], pcts: ['1'],
    },
  }],
};

test('_expectedSeriesNames derives names from params, allowing arbitrary periods (252)', () => {
  const ctrl = controller();
  const params = { types: ['sma', 'ema'], periods: [252] };
  const expected = ctrl._expectedSeriesNames(SYNTH_DYNAMIC_DEF, params);
  assert.ok(expected.has('SMA 252'));
  assert.ok(expected.has('EMA 252'));
  const kept = ctrl._validateSeriesNames(
    [{ name: 'SMA 252', kind: 'line', data: [] }, { name: 'EMA 252', kind: 'line', data: [] }],
    SYNTH_DYNAMIC_DEF, params,
  );
  assert.deepEqual(kept.map((p) => p.name), ['SMA 252', 'EMA 252']);
});

test('_expectedSeriesNames falls back to static buckets/pcts when params omitted', () => {
  const ctrl = controller();
  const fb = ctrl._expectedSeriesNames(SYNTH_DYNAMIC_DEF);
  assert.ok(fb.has('X 1'));
  assert.ok(!fb.has('SMA 252'));
});

test('_validateSeriesNames keeps matching series and drops mismatches (F3 §3.3.6)', () => {
  const ctrl = controller();
  const def = get('tgp_btlm');
  const payloads = [
    { name: 'btlm_mean', kind: 'line', data: [] },
    { name: 'btlm_GARBAGE', kind: 'line', data: [] }, // 契約違反 → スキップ対象
    { name: 'btlm_q95', kind: 'line', data: [] },
  ];
  const kept = ctrl._validateSeriesNames(payloads, def);
  assert.deepEqual(kept.map((p) => p.name), ['btlm_mean', 'btlm_q95']);
});

test('_validateSeriesNames does NOT false-positive on the valid pOL 99% (D-2)', () => {
  const ctrl = controller();
  const def = get('profit_band');
  const payloads = [{ name: 'pOL 99%', kind: 'line', data: [] }, { name: 'pOL_99', kind: 'line', data: [] }];
  const kept = ctrl._validateSeriesNames(payloads, def);
  // series_name 'pOL 99%' は通過、source_column 風の 'pOL_99' はスキップ（照合は series_name 基準）
  assert.deepEqual(kept.map((p) => p.name), ['pOL 99%']);
});

test('_validateSeriesNames keeps horizontal_line whose name matches series_name (price_range_power)', () => {
  const ctrl = controller();
  const def = get('price_range_power');
  const payloads = [{ name: 'price_range_power', kind: 'horizontal_line', lines: [] }];
  const kept = ctrl._validateSeriesNames(payloads, def);
  assert.deepEqual(kept.map((p) => p.name), ['price_range_power']);
});

// ===========================================================================
// 既定 variant 解決（profit_band の既定は是正版 robust）
// ===========================================================================

// _defaultVariant は def.compute.variants[0] を既定として返す実解決関数。
// profit_band の既定が欠陥版 global ではなく是正版 robust（因果窓＋比率/ATR
// 正規化）であることを、catalog の配列順ではなく実解決関数経由で固定する。
test('_defaultVariant resolves profit_band default to robust (not the flawed global)', () => {
  // Arrange: 実 catalog の profit_band 定義を取得
  const ctrl = controller();
  const def = get('profit_band');
  // Act: 既定 variant 解決の実経路（_defaultVariant）を通す
  const resolved = ctrl._defaultVariant(def);
  // Assert: 既定は是正版 robust
  assert.equal(resolved, 'robust');
  // 非破壊確認: global は選択肢として温存される
  assert.ok(def.compute.variants.includes('global'));
  assert.ok(def.compute.variants.includes('robust'));
});

// ===========================================================================
// setTimeframe（§チャート表示時間選択・1 分足原子から resample）
// ===========================================================================

// 計算呼び出しを記録し generation をエコーする compute（recompute 採用条件 accepts を満たす）。
function recordingController() {
  const noop = () => {};
  const computeCalls = [];
  const setCandlesCalls = [];
  const loadCandlesCalls = [];
  const ctrl = new IndicatorController({
    catalog: { listIndicators: () => [], get },
    compute: { compute: async (req) => { computeCalls.push(req); return { ok: true, generation: req.generation ?? 0, series: [] }; } },
    persistence: { loadApplied: () => [], saveApplied: noop, loadFavorites: () => [], saveFavorites: noop, loadUiState: () => ({}), saveUiState: noop, nextSeq: () => 1 },
    renderer: { renderLine: noop, renderHorizontal: noop, setData: noop, setVisible: noop, remove: noop, setCandles: (c) => setCandlesCalls.push(c) },
    document: null,
    mode: 'b',
    datasetRef: 'jp225_m1',
    timeframe: '1D',
    recentBars: 1500,
    loadCandles: async (ref, tf) => { loadCandlesCalls.push([ref, tf]); return [{ time: 1, open: 1, high: 1, low: 1, close: 1 }]; },
  });
  return { ctrl, computeCalls, setCandlesCalls, loadCandlesCalls };
}

test('setTimeframe is a no-op when the timeframe is unchanged', async () => {
  const { ctrl, loadCandlesCalls } = recordingController();
  await ctrl.setTimeframe('1D'); // 既定と同一
  assert.equal(ctrl._timeframe, '1D');
  assert.equal(loadCandlesCalls.length, 0);
});

test('setTimeframe re-fetches candles and replaces the main series via renderer.setCandles', async () => {
  const { ctrl, loadCandlesCalls, setCandlesCalls } = recordingController();
  await ctrl.setTimeframe('1W');
  assert.equal(ctrl._timeframe, '1W');
  // datasetRef と新時間足で candles を再取得し、メイン系列へ反映する。
  assert.deepEqual(loadCandlesCalls.at(-1), ['jp225_m1', '1W']);
  assert.equal(setCandlesCalls.length, 1);
});

test('setTimeframe recomputes applied indicators carrying the new timeframe and limit', async () => {
  const { ctrl, computeCalls } = recordingController();
  // 指標を 1 つ適用（apply 時は timeframe='1D'）。
  await ctrl.applyIndicator('tgp_btlm', 'default');
  const beforeCount = computeCalls.length;
  // 時間足切替 → 適用済み指標が新時間足で再計算される。
  await ctrl.setTimeframe('1W');
  const after = computeCalls.slice(beforeCount);
  assert.ok(after.length >= 1, '再計算の compute が発火する');
  // 再計算 compute は新 timeframe と直近 N 本（limit）を伴う（gateway 注入）。
  const last = after.at(-1);
  assert.equal(last.timeframe, '1W');
  assert.equal(last.limit, 1500);
});

test('setTimeframe keeps isRecomputing() true during the candles fetch await (live tick is skipped) — 🟡-2 regression', async () => {
  const noop = () => {};
  let releaseCandles;
  const candlesGate = new Promise((r) => { releaseCandles = r; });
  const ctrl = new IndicatorController({
    catalog: { listIndicators: () => [], get },
    compute: { compute: async (req) => ({ ok: true, generation: req.generation ?? 0, series: [] }) },
    persistence: { loadApplied: () => [], saveApplied: noop, loadFavorites: () => [], saveFavorites: noop, loadUiState: () => ({}), saveUiState: noop, nextSeq: () => 1 },
    renderer: { renderLine: noop, renderHorizontal: noop, setData: noop, setVisible: noop, remove: noop, setCandles: noop },
    document: null,
    mode: 'b',
    datasetRef: 'jp225_m1',
    timeframe: '1D',
    recentBars: 1500,
    // candles 取得を deferred でブロックし、取得 await 中の状態を観測可能にする。
    loadCandles: async () => { await candlesGate; return [{ time: 1, open: 1, high: 1, low: 1, close: 1 }]; },
  });
  // setTimeframe を開始（candles 取得 await で停止する）。
  const p = ctrl.setTimeframe('1W');
  await Promise.resolve();
  // 🟡-2: 取得 await 中も競合ガードが立っていること＝この隙にライブ tick が割り込めない。
  //   （バッチ全体を包む前は false になり、二重 compute の窓が開いていた。）
  assert.equal(ctrl.isRecomputing(), true);
  releaseCandles();
  await p;
  // バッチ完了後は解除される。
  assert.equal(ctrl.isRecomputing(), false);
});

// 時間足切替の画面更新は「全計算 → 同期一括描画」で行い、メインチャートと各指標を同時に更新する。
//   旧実装は (1) 先に setCandles でメインのみ即描画 → (2) 指標を直列ループで「compute→即描画」し、
//   各 await でブラウザが中間状態を1指標ずつ描画＝バラバラ更新になっていた（ISSUE-023）。
test('setTimeframe batches all renders after every compute resolves (ISSUE-023 regression)', async () => {
  const noop = () => {};
  const events = [];
  let releaseCompute;
  const computeGate = new Promise((r) => { releaseCompute = r; });
  let gateCompute = false; // apply 時は即時解決し、時間足切替の compute のみ gate で止める。
  const ctrl = new IndicatorController({
    catalog: { listIndicators: () => [], get },
    compute: {
      compute: async (req) => {
        if (gateCompute) {
          events.push('compute');
          await computeGate;
        }
        return { ok: true, generation: req.generation ?? 0, series: [] };
      },
    },
    persistence: { loadApplied: () => [], saveApplied: noop, loadFavorites: () => [], saveFavorites: noop, loadUiState: () => ({}), saveUiState: noop, nextSeq: () => 1 },
    renderer: {
      renderLine: () => events.push('renderLine'),
      renderHorizontal: noop,
      renderHistogram: () => events.push('renderHistogram'),
      setData: noop,
      setVisible: noop,
      remove: () => events.push('remove'),
      setCandles: () => events.push('setCandles'),
    },
    document: null,
    mode: 'b',
    datasetRef: 'jp225_m1',
    timeframe: '1D',
    recentBars: 1500,
    loadCandles: async () => [{ time: 1, open: 1, high: 1, low: 1, close: 1 }],
  });
  // 指標を1つ適用（apply の compute は gate しない＝即時）。
  await ctrl.applyIndicator('tgp_btlm', 'default');
  gateCompute = true;
  events.length = 0; // apply 由来のイベントを除外。

  // Act: 時間足切替を開始（指標 compute が gate でブロックされる）。
  const p = ctrl.setTimeframe('1W');
  await new Promise((r) => setTimeout(r, 0)); // candles 取得 await＋compute 到達まで進める。

  // Assert(1): compute は開始しているが、描画は一切起きていない＝メインも指標も先行描画しない。
  assert.ok(events.includes('compute'), '指標の計算は開始している');
  assert.ok(!events.includes('setCandles'), '計算完了前にメイン系列を描画しない');
  assert.ok(!events.includes('remove') && !events.includes('renderLine'), '計算完了前に指標を描画しない');

  // 計算を解放 → 同期一括描画フェーズへ。
  releaseCompute();
  await p;

  // Assert(2): すべての compute が、いかなる描画よりも前に並ぶ（compute-all → render-all）。
  const isRender = (e) => e === 'setCandles' || e === 'remove' || e === 'renderLine' || e === 'renderHistogram';
  const firstRender = events.findIndex(isRender);
  const lastCompute = events.lastIndexOf('compute');
  assert.ok(firstRender > -1 && lastCompute > -1, '計算と描画の双方が記録される');
  assert.ok(firstRender > lastCompute, 'すべての計算が描画より前に実行される（一括描画）');
  // メイン系列も同じバッチで描画される（メインのみ先行描画しない）。
  assert.ok(events.includes('setCandles'), 'メイン系列が描画される');
});

// ===========================================================================
// isRecomputing（競合ガード・ライブ更新の tick スキップ判定の単一権威）
//   LiveUpdater は独自フラグを持たず controller.isRecomputing() を参照する。
// ===========================================================================

// compute を外部 deferred で制御し、再計算の「実行中」を観測できる controller。
function deferredController() {
  const noop = () => {};
  let release;
  const gate = new Promise((resolve) => { release = resolve; });
  const ctrl = new IndicatorController({
    catalog: { listIndicators: () => [], get },
    compute: { compute: async (req) => { await gate; return { ok: true, generation: req.generation ?? 0, series: [] }; } },
    persistence: { loadApplied: () => [], saveApplied: noop, loadFavorites: () => [], saveFavorites: noop, loadUiState: () => ({}), saveUiState: noop, nextSeq: () => 1 },
    renderer: { renderLine: noop, renderHorizontal: noop, renderHistogram: noop, setData: noop, setVisible: noop, remove: noop },
    document: null,
  });
  return { ctrl, release };
}

test('isRecomputing returns false before any recompute', () => {
  const { ctrl } = deferredController();
  assert.equal(ctrl.isRecomputing(), false);
});

test('isRecomputing is true while a recompute is in flight and false after it settles', async () => {
  // Arrange: 指標を 1 つ適用（apply の compute は即時解決させるため gate 前に release）。
  const { ctrl, release } = deferredController();
  // apply はゲート前に解決させたいので、まず apply を走らせ release で通す。
  const applyPromise = ctrl.applyIndicator('tgp_btlm', 'default');
  release();
  await applyPromise;
  const instId = ctrl._state.applied[0].instanceId;

  // 次の recompute 用に新しいゲートを張る（deferredController は 1 つの gate のため再構成）。
  let release2;
  const gate2 = new Promise((r) => { release2 = r; });
  ctrl._compute.compute = async (req) => { await gate2; return { ok: true, generation: req.generation ?? 0, series: [] }; };

  // Act: recompute を開始（await しない＝実行中）。
  const recomputePromise = ctrl.recomputeInstance(instId, null, {});
  // Assert: 実行中は true。
  assert.equal(ctrl.isRecomputing(), true);
  // 解放後は false に戻る。
  release2();
  await recomputePromise;
  assert.equal(ctrl.isRecomputing(), false);
});

// ===========================================================================
// recomputeAllApplied（ライブ更新の再計算入口・適用済み全指標を現 params/timeframe で再計算）
// ===========================================================================

test('recomputeAllApplied recomputes every applied instance with current params', async () => {
  const { ctrl, computeCalls } = recordingController();
  await ctrl.applyIndicator('tgp_btlm', 'default');
  const before = computeCalls.length;
  // Act
  await ctrl.recomputeAllApplied();
  const after = computeCalls.slice(before);
  // Assert: 適用済み 1 指標の再計算 compute が発火し、現時間足（1D）を伴う。
  assert.ok(after.length >= 1, '適用済み指標の再計算 compute が発火する');
  assert.equal(after.at(-1).timeframe, '1D');
});

test('recomputeAllApplied is a no-op when nothing is applied', async () => {
  const { ctrl, computeCalls } = recordingController();
  const before = computeCalls.length;
  await ctrl.recomputeAllApplied();
  assert.equal(computeCalls.length, before);
});

// restore で永続化時間足を復元した際、時間足購読者へ通知する。
//   回帰: code-review 🔴。通知欠落だと売買マーカーの該当時間足フィルタが
//   restore 後の現在時間足を旧値のまま誤判定し、該当時間足なのに非表示になる（逆動作）。
test('restore notifies the timeframe observer with the restored timeframe (trade-markers gate regression)', async () => {
  const noop = () => {};
  const ctrl = new IndicatorController({
    catalog: { listIndicators: () => [], get },
    compute: { compute: async () => ({ ok: true, generation: 0, series: [] }) },
    persistence: { loadApplied: () => [], saveApplied: noop, loadFavorites: () => [], saveFavorites: noop, loadUiState: () => ({ timeframe: '1m' }), saveUiState: noop, nextSeq: () => 1 },
    renderer: { renderLine: noop, renderHorizontal: noop, setData: noop, setVisible: noop, remove: noop, setCandles: noop },
    facade: {},
    document: null,
    timeframe: '1D',
  });
  const seen = [];
  ctrl.setTimeframeObserver((tf) => seen.push(tf));
  await ctrl.restore();
  assert.ok(seen.includes('1m'), `restore は復元時間足 1m を購読者へ通知する（実際: ${JSON.stringify(seen)}）`);
});

// =========================================================================
// Market Profile メニュー一本化（#rp-mp 撤去→プロファイルタブ・controller 委譲）
//   設計入力: 是正 step2「controller に MP 分岐追加（present 参照移植・slim actor へ委譲）」。
//   apply→setEnabled(true)+enterBar（即 base）/ remove→setEnabled(false)+detach（あれば）/
//   toggleVisible→setEnabled(visible) / 設定変更→setParams / 再計算(render)経路で /compute へ流さない。
// =========================================================================

// slim actor スパイ（enterBar/feedTick/settleTick/setEnabled/isEnabled/setParams/detach を記録）。
function spyMp() {
  return {
    _en: false, _ticklive: false,
    calls: { setEnabled: [], enter: [], params: [], detach: 0, refresh: 0 },
    isEnabled() { return this._en; },
    // 全モード機能化: mode-aware 駆動の分岐点。setParams({mode:'ticklive'}) で ticklive に入る。
    isTicklive() { return this._en && this._ticklive; },
    setEnabled(v) { this._en = !!v; this.calls.setEnabled.push(!!v); },
    async enterBar(t) { this.calls.enter.push(t); },
    async refresh() { this.calls.refresh += 1; },
    setParams(p) {
      this.calls.params.push(p);
      if (p && p.mode != null) { this._ticklive = (p.mode === 'ticklive'); }
    },
    detach() { this.calls.detach += 1; },
    feedTick() {}, settleTick() {},
  };
}

// MP 委譲用コントローラ。compute はスパイ（MP が /compute へ流れないことを検証）。
//   marketProfile を注入し、untilTime（現在バー T）を任意に設定できる。
function mpController({ untilTime } = {}) {
  const noop = () => {};
  const computeCalls = [];
  const marketProfile = spyMp();
  // reveal（untilTime / MP enterBar 駆動）を検証するため subclass ReplayIndicatorController を用いる。
  const ctrl = new ReplayIndicatorController({
    catalog: { listIndicators: () => [], get },
    compute: { compute: async (req) => { computeCalls.push(req); return { ok: true, generation: 0, series: [] }; } },
    persistence: { loadApplied: () => [], saveApplied: noop, loadFavorites: () => [], saveFavorites: noop, loadUiState: () => ({}), saveUiState: noop, nextSeq: () => 1 },
    renderer: {
      renderLine: noop, renderHorizontal: noop, renderHistogram: noop, setData: noop,
      setVisible: (...a) => { computeCalls.setVisibleCalled = true; ctrl._rendererSetVisible = a; },
      remove: () => { ctrl._rendererRemoveCalled = true; },
    },
    marketProfile,
    document: null,
  });
  if (untilTime != null) ctrl.setUntilTime(untilTime);
  return { ctrl, marketProfile, computeCalls };
}

test('MP apply: setEnabled(true)+setParams and enterBar(current bar T), never touches /compute', async () => {
  // Arrange: 現在バー T=1704074400 をセット。
  const { ctrl, marketProfile, computeCalls } = mpController({ untilTime: 1704074400 });
  // Act
  const inst = await ctrl.applyIndicator('market_profile', 'default');
  // Assert: actor を有効化（setEnabled(true)）＋現在バーで即 enterBar（base 描画）。
  assert.deepEqual(marketProfile.calls.setEnabled, [true]);
  assert.deepEqual(marketProfile.calls.enter, [1704074400], '現在バー T で即 enterBar');
  // setParams で bins/va を渡す（effective 経路）。
  assert.equal(marketProfile.calls.params.length, 1);
  assert.equal(marketProfile.calls.params[0].bins, '60');
  assert.equal(marketProfile.calls.params[0].va, 0.70);
  // /compute へは一切流さない（MP は forming 委譲）。
  const mpCompute = computeCalls.filter((r) => r.indicatorId === 'market_profile');
  assert.equal(mpCompute.length, 0, 'MP は /compute をバイパス');
  // 凡例/永続化のため state に登録される。
  assert.ok(inst && inst.instanceId, 'MP インスタンスが state に登録される');
});

test('MP apply is single-instance: applying twice does not create a duplicate', async () => {
  const { ctrl, marketProfile } = mpController({ untilTime: 1000 });
  const a = await ctrl.applyIndicator('market_profile', 'default');
  const b = await ctrl.applyIndicator('market_profile', 'default');
  assert.equal(a.instanceId, b.instanceId, '2 回目は既存インスタンスを返す（単一）');
  // 2 回目で二重に enterBar/setEnabled しない（1 回目のみ）。
  assert.deepEqual(marketProfile.calls.setEnabled, [true]);
});

test('MP apply without untilTime skips immediate enterBar (render hook drives it later)', async () => {
  const { ctrl, marketProfile } = mpController(); // untilTime 未設定
  await ctrl.applyIndicator('market_profile', 'default');
  assert.deepEqual(marketProfile.calls.setEnabled, [true]);
  assert.equal(marketProfile.calls.enter.length, 0, 'T 未確定時は即 enterBar しない（render seam が駆動）');
});

test('MP toggleVisible: delegates to setEnabled(visible), not renderer.setVisible', async () => {
  const { ctrl, marketProfile } = mpController({ untilTime: 1000 });
  const inst = await ctrl.applyIndicator('market_profile', 'default');
  marketProfile.calls.setEnabled.length = 0; // apply 分をクリア
  // Act: 表示トグル（true→false）。
  ctrl.toggleVisible(inst.instanceId);
  // Assert: actor.setEnabled(false) へ委譲。renderer.setVisible は MP では呼ばない。
  assert.deepEqual(marketProfile.calls.setEnabled, [false]);
  assert.ok(!ctrl._rendererSetVisible, 'MP は renderer.setVisible を通さない');
});

test('MP removeInstance: setEnabled(false)+detach, not renderer.remove', async () => {
  const { ctrl, marketProfile } = mpController({ untilTime: 1000 });
  const inst = await ctrl.applyIndicator('market_profile', 'default');
  marketProfile.calls.setEnabled.length = 0;
  // Act（MP remove は共有 present の _removeMarketProfile へ委譲＝setEnabled(false) を await 後に detach する
  //   async 経路。setupReplay の removeInstance ラッパは Promise を .then で待つ）。
  await ctrl.removeInstance(inst.instanceId);
  // Assert
  assert.deepEqual(marketProfile.calls.setEnabled, [false]);
  assert.equal(marketProfile.calls.detach, 1, 'detach（あれば）を呼ぶ');
  assert.ok(!ctrl._rendererRemoveCalled, 'MP は renderer.remove を通さない');
  // state から除去される。
  assert.equal(ctrl._state.applied.find((i) => i.instanceId === inst.instanceId), undefined);
});

test('MP settings change (recomputeInstance) — non-ticklive: setParams + as-of refresh, never /compute', async () => {
  const { ctrl, marketProfile, computeCalls } = mpController({ untilTime: 2000 });
  const inst = await ctrl.applyIndicator('market_profile', 'default');
  marketProfile.calls.params.length = 0;
  marketProfile.calls.enter.length = 0;
  marketProfile.calls.refresh = 0;
  const computeBefore = computeCalls.length;
  // Act: gear 設定変更相当（bins を 30 へ・mode 未指定＝normal/非ticklive）。
  await ctrl.recomputeInstance(inst.instanceId, null, { bins: '30', va: 0.8 });
  // Assert: setParams（bins/va）＋ as-of refresh（enterBar ではない）。/compute は増えない。
  assert.equal(marketProfile.calls.params.length, 1);
  assert.equal(marketProfile.calls.params[0].bins, '30');
  assert.equal(marketProfile.calls.refresh, 1, '非ticklive は as-of refresh で再取得（getContext().to=T）');
  assert.deepEqual(marketProfile.calls.enter, [], '非ticklive は enterBar しない');
  const mpCompute = computeCalls.slice(computeBefore).filter((r) => r.indicatorId === 'market_profile');
  assert.equal(mpCompute.length, 0, 'MP 設定変更は /compute へ流さない');
});

test('MP settings change (recomputeInstance) — ticklive: setParams + re-enterBar, never /compute', async () => {
  const { ctrl, marketProfile, computeCalls } = mpController({ untilTime: 2000 });
  const inst = await ctrl.applyIndicator('market_profile', 'default');
  marketProfile.calls.params.length = 0;
  marketProfile.calls.enter.length = 0;
  marketProfile.calls.refresh = 0;
  const computeBefore = computeCalls.length;
  // Act: gear 設定変更で ticklive モードへ（push 系＝base 取り直し）。
  await ctrl.recomputeInstance(inst.instanceId, null, { mode: 'ticklive', bins: '30' });
  // Assert: setParams（mode:ticklive）＋現在バー T で再 enterBar。refresh は呼ばない。/compute は増えない。
  assert.equal(marketProfile.calls.params[0].mode, 'ticklive');
  assert.deepEqual(marketProfile.calls.enter, [2000], 'ticklive は enterBar(T) で base 取り直し');
  assert.equal(marketProfile.calls.refresh, 0, 'ticklive は refresh しない');
  const mpCompute = computeCalls.slice(computeBefore).filter((r) => r.indicatorId === 'market_profile');
  assert.equal(mpCompute.length, 0, 'MP 設定変更は /compute へ流さない');
});

test('MP is skipped in recomputeAllApplied (render hook drives it, not /compute)', async () => {
  const { ctrl, computeCalls } = mpController({ untilTime: 1000 });
  await ctrl.applyIndicator('market_profile', 'default');
  const before = computeCalls.length;
  // Act: ライブ更新/時間足変更の一括再計算。
  await ctrl.recomputeAllApplied();
  // Assert: MP は /compute に載らない（skip）。
  const mpCompute = computeCalls.slice(before).filter((r) => r.indicatorId === 'market_profile');
  assert.equal(mpCompute.length, 0, 'recomputeAllApplied は MP を /compute へ流さない');
});

test('MP restore: re-enables actor from saved params/visibility without touching /compute', async () => {
  // Arrange: 永続化済み MP インスタンス（pairs params・visible=true）を restore する。
  const noop = () => {};
  const computeCalls = [];
  const marketProfile = spyMp();
  const savedInst = {
    instanceId: 'mp-1', indicatorId: 'market_profile', variant: 'default',
    params: [['bins', '30'], ['va', 0.8]], visible: true, generation: 0, seq: 1, createdAt: 0,
  };
  const ctrl = new ReplayIndicatorController({
    catalog: { listIndicators: () => [], get },
    compute: { compute: async (req) => { computeCalls.push(req); return { ok: true, generation: 0, series: [] }; } },
    persistence: {
      loadApplied: () => [savedInst], saveApplied: noop,
      loadFavorites: () => [], saveFavorites: noop, loadUiState: () => ({}), saveUiState: noop, nextSeq: () => 2,
    },
    renderer: { renderLine: noop, renderHorizontal: noop, renderHistogram: noop, setData: noop, setVisible: noop, remove: noop, setCandles: noop },
    marketProfile,
    document: null,
    timeframe: '1D',
  });
  // Act
  await ctrl.restore();
  // Assert: MP は /compute へ流さず、保存 params/可視で actor を復元する。
  const mpCompute = computeCalls.filter((r) => r.indicatorId === 'market_profile');
  assert.equal(mpCompute.length, 0, 'restore は MP を /compute へ流さない');
  assert.deepEqual(marketProfile.calls.setEnabled, [true], '保存可視状態 visible=true で有効化');
  assert.equal(marketProfile.calls.params[0].bins, '30', '保存 bins を actor へ復元');
  assert.equal(marketProfile.calls.params[0].va, 0.8);
});
