// モーダル（position_sizing_dialog.js）と usecase（position_sizing_plan.js）の **ViewModel 契約**の検定。
//
// なぜ別立てか（Pre-mortem・ISSUE-368 スライス 6）: モーダル側の単体検定は手書きの VM リテラルを
//   render へ渡すため、**キー名が実物とずれても緑のまま**通る。ずれた場合の症状は「モーダルが
//   ずっと『—』を出す」＝例外も赤も出ない静かな故障で、実 UI を開くまで気づけない
//   （ISSUE-291「受け口だけでなく端から端まで結線を固定」と同型）。
//   そこで**実物の usecase が作った ViewModel**をそのまま流し、表示が埋まることを固定する。
//
// 設計入力: 設計書 §3 UC-03（Output Model の項目）・§5 Input Boundary・
//   スライス 6「表示値は usecase の ViewModel を表示（第 2 実装を作らない）」。
// 構造: Arrange-Act-Assert（AAA）。DOM は最小スタブ、MC は同期 fake（MonteCarloPort 契約）。

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { PositionSizingDialog } from '../js/adapter/front/position_sizing_dialog.js';
import { PositionSizingPlanUseCase } from '../js/usecase/position_sizing_plan.js';
import { createPriceLevels } from '../js/domain/price_levels.js';
import { solveEdgeRuin } from '../js/domain/edge_ruin_core.js';

class El {
  constructor(tag = 'div') {
    this.tag = tag;
    this.children = [];
    this.dataset = {};
    this.textContent = '';
    this.value = '';
    this.type = '';
    this.step = '';
    this.min = '';
    this.parentNode = null;
    this._cls = new Set();
    this._handlers = {};
  }

  get className() { return [...this._cls].join(' '); }

  set className(v) { this._cls = new Set(String(v).split(/\s+/).filter(Boolean)); }

  get classList() {
    const s = this._cls;
    return {
      add: (c) => s.add(c),
      remove: (c) => s.delete(c),
      contains: (c) => s.has(c),
      toggle: (c, on) => {
        const next = on === undefined ? !s.has(c) : on;
        if (next) { s.add(c); } else { s.delete(c); }
      },
    };
  }

  get innerHTML() { return ''; }

  set innerHTML(v) {
    if (v === '') {
      for (const k of this.children) { k.parentNode = null; }
      this.children = [];
    }
  }

  append(...kids) {
    for (const k of kids) {
      if (k && typeof k === 'object') { k.parentNode = this; this.children.push(k); }
    }
  }

  appendChild(k) { this.append(k); return k; }

  removeChild(k) {
    this.children = this.children.filter((c) => c !== k);
    if (k) { k.parentNode = null; }
    return k;
  }

  setAttribute() {}

  addEventListener(ev, fn) { (this._handlers[ev] ??= []).push(fn); }

  fire(ev, arg = {}) { for (const fn of this._handlers[ev] ?? []) { fn(arg); } }
}

function flatten(el, out = []) {
  for (const kid of el.children ?? []) { out.push(kid); flatten(kid, out); }
  return out;
}

const byOut = (root, key) => flatten(root).find((e) => e.dataset && e.dataset.psOut === key) ?? null;

// MonteCarloPort の同期 fake（Worker を使わずに domain の権威をそのまま回す）。
const SYNC_MC_PORT = { solve: async (spec) => solveEdgeRuin(spec) };

// 参照実装の既定値（設計書スライス 0 通過条件 1 と同じ材料）。sims は検定用に小さくする。
const PARAMS = {
  winRate: 0.38,
  payoffRatio: 2.74,
  ruinLevel: 0.5,
  alpha: 0.01,
  horizon: 250,
  splitCount: 20,
  seed: 12345,
  sims: 200,
  fractionChoice: 'safe',
  balance: 172000,
  pointValue: 1,
  marginRate: 0.1,
  weightPattern: 'linear',
  lotMode: 'int',
  capBasis: 'margin',
};

function newUseCase() {
  return new PositionSizingPlanUseCase({
    mcPort: SYNC_MC_PORT,
    levels: createPriceLevels({
      direction: 'long',
      entryPrices: [58700, 58600, 58500],
      stopPrice: 58340,
      takePrice: 59200,
    }),
    params: PARAMS,
  });
}

function openDialog() {
  const doc = { body: new El('body'), createElement: (t) => new El(t) };
  const dialog = new PositionSizingDialog({ document: doc });
  dialog.open();
  return { dialog, root: doc.body.children[0] };
}

test('TC-VC01 実物の ViewModel（MC 前）で Step 3 の表示欄が埋まる（キー名の食い違いを検出する）', () => {
  // Arrange
  const uc = newUseCase();
  const { dialog, root } = openDialog();
  // Act
  dialog.render(uc.viewModel());
  // Assert: MC 前でも Step 3（ロット変換）は動く＝「—」のままにならない。
  for (const key of ['totalLot', 'avgPrice', 'requiredMargin', 'marginUse', 'losscutPrice', 'buildableLot']) {
    assert.notEqual(byOut(root, key).textContent, '—', `${key} が ViewModel から引けていない（キー名の食い違い）`);
  }
  // 派生カード（MC 非依存）も同様。
  for (const key of ['lossRate', 'expectedValue', 'kellyFraction', 'halfKellyFraction']) {
    assert.notEqual(byOut(root, key).textContent, '—', `${key} が ViewModel から引けていない`);
  }
});

test('TC-VC02 MC 実行後は制約 f と採用 f が埋まる（Step 1/2 の表示も実物の VM で成立する）', async () => {
  // Arrange
  const uc = newUseCase();
  const { dialog, root } = openDialog();
  // Act
  const vm = await uc.runMonteCarlo();
  dialog.render(vm);
  // Assert
  // 表示は参照実装の書式（f 系は % ・RoR は小数 1 桁）。本検定の目的は「VM の値が表示まで
  //   届いているか（キー名の食い違いが無いか）」であり、書式そのものの権威は TC-PD38 が持つ。
  assert.equal(
    byOut(root, 'constrainedFraction').textContent,
    `${(vm.edge.constrainedFraction * 100).toFixed(2)}%`,
  );
  assert.equal(
    byOut(root, 'rorAtConstrained').textContent,
    `${(vm.edge.rorAtConstrained * 100).toFixed(1)}%`,
  );
  assert.equal(byOut(root, 'fraction').textContent, `${(vm.fraction * 100).toFixed(2)}%`);
  assert.notEqual(byOut(root, 'fraction').textContent, '—');
});

test('TC-VC03 水準の違反（ロングで損切りが建値より上）は VM の判定がそのまま警告に出る', () => {
  // Arrange: stop を建値より上に置く＝参照実装 :971 stopInvalid と同条件。
  const uc = newUseCase();
  uc.setLevels(createPriceLevels({
    direction: 'long', entryPrices: [58700], stopPrice: 58900, takePrice: null,
  }));
  const { dialog, root } = openDialog();
  // Act
  dialog.render(uc.viewModel());
  // Assert
  assert.match(byOut(root, 'warnings').textContent, /stop_invalid/);
});
