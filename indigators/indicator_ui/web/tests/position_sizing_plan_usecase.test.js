// usecase/position_sizing_plan.js（Step 2＋3 の合成・ViewModel 生成）の検証（ISSUE-368 スライス 5）。
//
// 設計入力: 設計書 §3 UC-01/UC-03 ／ §5 Input Boundary（setLevels / setParams / runMonteCarlo）。
// 役割分担:
//   - 計算そのものは domain（edge_ruin_core / split_entry_plan / price_levels）が持つ。
//     本 usecase は「Step 1 の結果から採用 f を選び（Step 2）、水準とパラメータを Step 3 へ渡す」
//     合成だけを行う。式は 1 つも持たない。
//   - MC は MonteCarloPort 越し（Worker か fake かを知らない）。
// 参照実装との対応:
//   :580 chosenF()   採用 f の選択（full は max(f*,0)・half・safe）
//   :643 S.fFull/fHalf/fSafe は **runMC が入るまで 0**＝計算前は f=0（:1092「採用 f = 0%」）
//   :585-592 派生カード（q / EV / f* / ハーフ）は MC 非依存で即時表示される
// 構造: Arrange-Act-Assert。MonteCarloPort は同期 fake（Worker 非依存）。

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { PositionSizingPlanUseCase } from '../js/usecase/position_sizing_plan.js';
import { McUnavailableError } from '../js/usecase/mc_port.js';
import { createPriceLevels } from '../js/domain/price_levels.js';
import { solveEdgeRuin } from '../js/domain/edge_ruin_core.js';

const LEVELS = createPriceLevels({
  direction: 'long',
  entryPrices: [58700, 59700, 60700],
  stopPrice: 58340,
  takePrice: 61500,
});

// 参照実装 :278-289 / :355-362 の既定値。
const PARAMS = Object.freeze({
  winRate: 0.38,
  payoffRatio: 2.74,
  ruinLevel: 0.5,
  alpha: 0.01,
  horizon: 50,
  splitCount: 20,
  seed: 1,
  sims: 50,
  fractionChoice: 'safe',
  balance: 172000,
  pointValue: 1,
  marginRate: 0.1,
  lotMode: 'int',
  capBasis: 'lc',
  weightPattern: 'linear',
});

// 同期 fake の MonteCarloPort（domain の権威をそのまま使う＝値の正しさは golden 検定が担保）。
function fakePort({ progress = [], fail = null } = {}) {
  return {
    calls: [],
    async solve(spec, onProgress) {
      this.calls.push(spec);
      if (fail) { throw fail; }
      for (const r of progress) { if (onProgress) onProgress(r); }
      return solveEdgeRuin(spec);
    },
  };
}

function build(overrides = {}) {
  const port = overrides.port || fakePort();
  const uc = new PositionSizingPlanUseCase({
    mcPort: port,
    levels: overrides.levels || LEVELS,
    params: { ...PARAMS, ...(overrides.params || {}) },
  });
  return { uc, port };
}

test('契約違反の port は生成時に投げる（配線ミスを本番まで運ばない）', () => {
  // Arrange / Act / Assert
  assert.throws(() => new PositionSizingPlanUseCase({ mcPort: {}, levels: LEVELS, params: PARAMS }),
    /solve/);
});

test('MC 実行前は採用 f = 0 で、ロットも 0（参照実装 :1092 と同条件）', () => {
  // Arrange / Act
  const { uc } = build();
  const vm = uc.viewModel();
  // Assert
  assert.equal(vm.fraction, 0);
  assert.equal(vm.edge, null, 'MC 未実行は edge 無し');
  assert.equal(vm.plan.total_lot, 0);
});

test('派生カード（q / EV / f* / ハーフ）は MC 非依存で即時に出る（:585-592）', () => {
  // Arrange / Act
  const { uc } = build();
  const d = uc.viewModel().derived;
  // Assert — 値そのものは domain の権威に委ねるので、ここでは関係だけを固定する
  assert.equal(d.lossRate, 1 - PARAMS.winRate);
  assert.equal(d.expectedValue, PARAMS.payoffRatio * PARAMS.winRate - (1 - PARAMS.winRate));
  assert.ok(d.kellyFraction > 0);
  assert.equal(d.halfKellyFraction, Math.max(d.kellyFraction, 0) / 2);
});

test('runMonteCarlo で edge が入り、採用 f がロットへ効く', async () => {
  // Arrange
  const { uc, port } = build();
  assert.equal(uc.viewModel().plan.total_lot, 0);
  // Act
  const vm = await uc.runMonteCarlo();
  // Assert
  assert.equal(port.calls.length, 1);
  assert.deepEqual(port.calls[0], {
    win_rate: 0.38, payoff_ratio: 2.74, ruin_level: 0.5, alpha: 0.01,
    horizon: 50, split_count: 20, seed: 1, sims: 50,
  }, 'spec は golden fixture と同じ snake_case で渡す');
  assert.ok(vm.edge, 'edge が入る');
  assert.equal(vm.fraction, vm.edge.constrainedFraction, "既定 'safe' は破産確率制約 f");
});

test('採用 f の 3 択が参照実装 :580 と同一（full は max(f*,0)）', async () => {
  // Arrange
  const { uc } = build();
  await uc.runMonteCarlo();
  const edge = uc.viewModel().edge;
  // Act / Assert
  assert.equal(uc.setParams({ fractionChoice: 'half' }).fraction, edge.halfKellyFraction);
  assert.equal(uc.setParams({ fractionChoice: 'full' }).fraction, Math.max(edge.kellyFraction, 0));
  assert.equal(uc.setParams({ fractionChoice: 'safe' }).fraction, edge.constrainedFraction);
});

test('EV≤0（f* が負）でも full は 0 へ丸める（賭けない・:580 の max）', async () => {
  // Arrange — p=0.30, R=1.2 は EV<0
  const { uc } = build({ params: { winRate: 0.3, payoffRatio: 1.2, fractionChoice: 'full' } });
  // Act
  const vm = await uc.runMonteCarlo();
  // Assert
  assert.ok(vm.edge.kellyFraction < 0);
  assert.equal(vm.fraction, 0);
  assert.equal(vm.plan.total_lot, 0);
});

test('setLevels は水準を差し替えて計画を作り直す（価格が単一ソース）', async () => {
  // Arrange
  const { uc } = build();
  await uc.runMonteCarlo();
  const before = uc.viewModel().plan.distances[0];
  // Act
  const vm = uc.setLevels(LEVELS.withStop(58000));
  // Assert
  assert.equal(vm.plan.stop_price, 58000);
  assert.ok(vm.plan.distances[0] > before, '距離は保持せず毎回派生する');
});

test('配置の不変条件違反は ViewModel に出す（例外にしない＝掴んだまま操作を続けられる）', () => {
  // Arrange
  const { uc } = build();
  // Act — ロングで損切りを建値より上へ
  const vm = uc.setLevels(LEVELS.withStop(59000));
  // Assert
  assert.deepEqual(vm.violations, ['stop_invalid']);
  assert.equal(vm.plan.stop_invalid, true, '計画側の分岐も同時に立つ');
});

test('進捗はそのまま呼び出し側へ中継する（usecase が握り潰さない）', async () => {
  // Arrange
  const seen = [];
  const { uc } = build({ port: fakePort({ progress: [0.25, 0.5, 1] }) });
  // Act
  await uc.runMonteCarlo((r) => seen.push(r));
  // Assert
  assert.deepEqual(seen, [0.25, 0.5, 1]);
});

test('MC 失敗は McUnavailableError として伝わり、直前の計画は壊れない', async () => {
  // Arrange
  const boom = new McUnavailableError('Worker 起動失敗');
  const { uc } = build({ port: fakePort({ fail: boom }) });
  const before = uc.viewModel().plan.total_lot;
  // Act / Assert
  await assert.rejects(() => uc.runMonteCarlo(), McUnavailableError);
  assert.equal(uc.viewModel().plan.total_lot, before, '失敗しても表示中の計画を壊さない');
  assert.equal(uc.viewModel().edge, null);
});

test('ロスカット価格は計画から出す（水準線が読み取り専用で描ける）', async () => {
  // Arrange
  const { uc } = build({ params: { fractionChoice: 'full' } });
  // Act
  const vm = await uc.runMonteCarlo();
  // Assert
  assert.equal(vm.levelLines.losscutPrice, vm.plan.losscut_price);
  assert.deepEqual([...vm.levelLines.entryPrices], [...LEVELS.entryPrices]);
  assert.equal(vm.levelLines.stopPrice, LEVELS.stopPrice);
  assert.equal(vm.levelLines.takePrice, LEVELS.takePrice);
});

test('合計ロット 0 のときロスカット線は出さない（建玉が無い＝到達価格が無い）', () => {
  // Arrange / Act — MC 前は f=0
  const { uc } = build();
  // Assert
  assert.equal(uc.viewModel().levelLines.losscutPrice, null);
});
