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

// ISSUE-105 🟡-2 回帰: フェーズ1（直列計算の await）中に凡例 close で当該インスタンスが
//   state から除去された場合、accepted 済み job をフェーズ2 で描画すると renderer に
//   系列/ペインが再生成され、凡例行の無い「ゾンビペイン」が残留してライブ更新を受け続ける。
//   ガード（描画直前の state 在席確認）により、除去済みインスタンスは _renderInstance を
//   通さず renderer.remove のみ行う。
test('recomputeAllApplied skips drawing an instance removed during the compute await (zombie-pane guard)', async () => {
  const noop = () => {};
  const removeCalls = [];
  let releaseCompute;
  const computeGate = new Promise((r) => { releaseCompute = r; });
  let gateArmed = false; // apply の compute は素通し、recompute の compute だけゲートする。
  const ctrl = new IndicatorController({
    catalog: { listIndicators: () => [], get },
    compute: {
      compute: async (req) => {
        if (gateArmed) { await computeGate; }
        return { ok: true, generation: req.generation ?? 0, series: [] };
      },
    },
    persistence: { loadApplied: () => [], saveApplied: noop, loadFavorites: () => [], saveFavorites: noop, loadUiState: () => ({}), saveUiState: noop, nextSeq: () => 1 },
    renderer: { renderLine: noop, renderHorizontal: noop, renderHistogram: noop, setData: noop, setVisible: noop, remove: (id) => removeCalls.push(id) },
    document: null,
  });
  // Arrange: 指標を 1 つ適用（apply の compute はゲート前＝即時解決）。
  await ctrl.applyIndicator('tgp_btlm', 'default');
  const instId = ctrl._state.applied[0].instanceId;
  // フェーズ2 の描画呼び出しを観測する spy。
  const rendered = [];
  const origRender = ctrl._renderInstance.bind(ctrl);
  ctrl._renderInstance = (job) => { rendered.push(job.instanceId); return origRender(job); };

  // Act: recompute を開始（フェーズ1 の compute await で停止）→ その最中に close で除去 → 解放。
  gateArmed = true;
  const recomputePromise = ctrl.recomputeAllApplied();
  await Promise.resolve(); // フェーズ1 の compute await へ制御を渡す。
  ctrl.removeInstance(instId); // 凡例 close 相当（state から除去）。
  releaseCompute();
  await recomputePromise;

  // Assert: 除去済みインスタンスはフェーズ2 で描画されない（ゾンビペイン再生成なし）。
  assert.ok(!rendered.includes(instId), '除去済みインスタンスは _renderInstance を通さない');
  // 保険で renderer.remove が呼ばれている（removeInstance の 1 回＋ガードの 1 回）。
  assert.ok(removeCalls.includes(instId), 'ガードが renderer.remove を確実に呼ぶ');
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
