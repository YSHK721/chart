// indicator_controller.js — 追い越された（stale な）compute 応答の破棄検証（ISSUE-194）。
//
// 背景（実測で再現した欠陥）:
//   facade.recompute の generation は「呼び出し時スナップショットの instance.generation + 1」で
//   決まる。よって同一 instance への再計算が**並行**すると両者が同じ generation を発番し、
//   どちらも accepts() を通る。ライブ更新設計（update_scheduler.js 冒頭・chart_renderer.js
//   updateSeriesTail のコメント）は「遅れて届いた古い応答は per-instance generation の
//   latest-wins が破棄する」ことを前提にしているが、この同値発番のため破棄が働かなかった。
//   実害: 足内 latest 応答（旧時間足）が setTimeframe 後の full 描画を上書きし、末尾 1 点だけが
//   旧時間足の値になる。時間足間の値差が大きい長期 EMA ほど急落として見える。
//
// 構造: Arrange-Act-Assert（AAA）。DOM/ネット非依存（全注入・recording fake）。

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { IndicatorController } from '../js/adapter/front/indicator_controller.js';
import { get } from '../js/usecase/catalog.js';

function recordingRenderer() {
  const calls = { renderLine: [], updateSeriesTail: [], removes: [] };
  return {
    calls,
    renderLine: (id, payloads) => calls.renderLine.push({ id, payloads }),
    renderHistogram: () => {},
    renderHorizontal: () => {},
    updateSeriesTail: (key, points) => calls.updateSeriesTail.push({ key, points }),
    setData: () => {}, setVisible: () => {}, remove: (id) => calls.removes.push(id),
    setCandles: () => {}, resyncMissedCandles: () => false, updateLastCandle: () => {},
    getSeriesStyles: () => [], applySeriesStyle: () => {},
  };
}

// 旧時間足 5m の latest 応答を 1 度だけ保留する controller を組む。
//   release() で保留を解放する（setTimeframe の full 応答より後に届かせるため）。
function controllerWithHeldLatest(renderer) {
  const noop = () => {};
  const computeCalls = [];
  let release = null;
  const ctrl = new IndicatorController({
    catalog: { listIndicators: () => [], get },
    compute: {
      compute: async (req) => {
        computeCalls.push({ mode: req.mode, timeframe: req.timeframe, generation: req.generation });
        if (req.mode === 'latest' && release === null) {
          await new Promise((res) => { release = res; });
          // 旧時間足 5m で計算された末尾点（長期 EMA は時間足間で大きく異なる）。
          return { ok: true, generation: req.generation,
            series: [{ name: 'MA', kind: 'line', data: [{ time: 9999, value: 62720 }] }] };
        }
        // 新時間足 1h の full 応答。
        return { ok: true, generation: req.generation,
          series: [{ name: 'MA', kind: 'line', data: [{ time: 9000, value: 65308 }] }] };
      },
    },
    persistence: {
      loadApplied: () => [], saveApplied: noop, loadFavorites: () => [], saveFavorites: noop,
      loadUiState: () => ({}), saveUiState: noop, nextSeq: () => 1,
    },
    renderer, document: null, mode: 'b', datasetRef: 'jp225_tick',
    timeframe: '5m', recentBars: 1500,
    loadCandles: async () => [{ time: 1, open: 1, high: 1, low: 1, close: 1 }],
  });
  return { ctrl, computeCalls, releaseLatest: () => release() };
}

test('setTimeframe に追い越された latest 応答は末尾へ反映されない（ISSUE-194）', async () => {
  // Arrange: 5m で指標を 1 件適用する。
  const renderer = recordingRenderer();
  const { ctrl, computeCalls, releaseLatest } = controllerWithHeldLatest(renderer);
  await ctrl.applyIndicator('moving_averages', 'default');

  // Act 1: 足内 latest 再計算を 5m で発行する（応答は保留させる）。
  const forming = ctrl.recomputeFormingTails();
  await new Promise((r) => setTimeout(r, 0));

  // Act 2: その最中に時間足を 1h へ切替える（full 再計算＝正しい値で再描画される）。
  await ctrl.setTimeframe('1h');
  assert.equal(renderer.calls.renderLine.at(-1).payloads[0].data[0].value, 65308,
    '切替後は 1h の値で描画されていること');

  // Act 3: 保留していた旧 5m の latest 応答を解放する。
  releaseLatest();
  await forming;

  // Assert: 同一 generation で発番されていた（＝generation では検出できない）ことを固定する。
  const latestReq = computeCalls.find((c) => c.mode === 'latest');
  const fullAfter = computeCalls.filter((c) => c.timeframe === '1h').at(-1);
  assert.equal(latestReq.timeframe, '5m');
  assert.equal(latestReq.generation, fullAfter.generation);

  // Assert: 追い越された stale 応答は末尾へ適用されない。
  assert.deepEqual(renderer.calls.updateSeriesTail, [],
    '旧時間足で計算された stale な末尾点が適用されてはならない');
});

test('追い越されていない latest 応答は従来どおり末尾へ反映される（非退行）', async () => {
  // Arrange
  const renderer = recordingRenderer();
  const noop = () => {};
  const ctrl = new IndicatorController({
    catalog: { listIndicators: () => [], get },
    compute: {
      compute: async (req) => ({ ok: true, generation: req.generation,
        series: [{ name: 'MA', kind: 'line', data: [{ time: 100, value: 42 }] }] }),
    },
    persistence: {
      loadApplied: () => [], saveApplied: noop, loadFavorites: () => [], saveFavorites: noop,
      loadUiState: () => ({}), saveUiState: noop, nextSeq: () => 1,
    },
    renderer, document: null, mode: 'b', datasetRef: 'jp225_tick',
    timeframe: '5m', recentBars: 1500,
    loadCandles: async () => [{ time: 1, open: 1, high: 1, low: 1, close: 1 }],
  });
  await ctrl.applyIndicator('moving_averages', 'default');

  // Act
  await ctrl.recomputeFormingTails();

  // Assert
  assert.equal(renderer.calls.updateSeriesTail.length, 1);
  assert.deepEqual(renderer.calls.updateSeriesTail[0].points, [{ time: 100, value: 42 }]);
});
