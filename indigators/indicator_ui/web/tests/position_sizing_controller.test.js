// position_sizing_controller.js（計算機の協働子・ISSUE-368 スライス 7）のテスト。
//
// 設計入力（唯一の仕様源）: .doc/POSITION_SIZING_CHART_INTEGRATION_DESIGN.md
//   §5 Input Boundary（usecase は setLevels / setParams / runMonteCarlo）、
//   §5「PlanPresenterPort / LevelViewPort は class ではなく**コールバック注入**」、
//   §6（モーダル＝Presenter・primitive＝水準線の表示先）、
//   スライス 7（協働子は共有配線が生成し、root は識別子の受け渡しのみ）、
//   「R-P1/R-P3」（ピッカーのアーム要求と右クリック項目の受け口）。
//
// 責務（本 class）: dialog / picker / primitive と usecase を**繋ぐ**だけ。式も判定も持たない
//   （ColorThemeController と同じ位置づけ）。
// 構造: Arrange-Act-Assert。usecase は実物、dialog/picker/primitive は fake。

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { PositionSizingController } from '../js/adapter/front/position_sizing_controller.js';
import { PositionSizingPlanUseCase } from '../js/usecase/position_sizing_plan.js';
import { createPriceLevels } from '../js/domain/price_levels.js';
import { solveEdgeRuin } from '../js/domain/edge_ruin_core.js';
import { McUnavailableError } from '../js/usecase/mc_port.js';

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

const LEVELS = {
  direction: 'long', entryPrices: [58700, 58600, 58500], stopPrice: 58340, takePrice: 59200,
};

function build({ mcPort = { solve: async (spec) => solveEdgeRuin(spec) } } = {}) {
  const calls = {
    rendered: [], opened: 0, armed: [], prices: [], added: [], levels: [], notified: [], synced: [],
  };
  const dialog = {
    open: () => { calls.opened += 1; },
    render: (vm) => calls.rendered.push(vm),
    setPrice: (t, p) => calls.prices.push([t, p]),
    addEntryPrice: (p) => calls.added.push(p),
    syncPrices: (l) => calls.synced.push(l),
  };
  const picker = { arm: (t) => calls.armed.push(t), disarm: () => calls.armed.push(null) };
  const primitive = { setLevels: (l) => calls.levels.push(l) };
  // 本番の合成根と同じく、PriceLevels は 1 個だけ作って usecase と協働子の両方へ渡す
  //   （2 個作ると「usecase が持つ水準」と「drag が掴む水準」が割れる）。
  const levels = createPriceLevels(LEVELS);
  const usecase = new PositionSizingPlanUseCase({ mcPort, levels, params: PARAMS });
  const controller = new PositionSizingController({
    usecase, dialog, picker, primitive, levels, toast: { show: (m) => calls.notified.push(m) },
  });
  return {
    controller, calls, usecase,
  };
}

test('TC-PC01 open() でモーダルを開き、現在の計画を描く（開いた瞬間に空欄にしない）', () => {
  // Arrange
  const { controller, calls } = build();
  // Act
  controller.open();
  // Assert
  assert.equal(calls.opened, 1);
  assert.equal(calls.rendered.length, 1);
  // MC 前は参照実装 :643（S.f*=0）と同じく採用 f=0＝ロットも 0。実物の usecase を通した証拠として
  //   派生カード（MC 非依存）が入っていることを見る。
  assert.equal(calls.rendered[0].plan.total_lot, 0);
  assert.ok(calls.rendered[0].derived.kellyFraction > 0, '実物の usecase の ViewModel を渡す');
});

test('TC-PC02 描画のたびに水準線（primitive）へも同じ ViewModel の水準を配る（表示先は 2 つ）', () => {
  // Arrange
  const { controller, calls } = build();
  // Act
  controller.open();
  // Assert
  assert.deepEqual(calls.levels[0].entryPrices, LEVELS.entryPrices);
  assert.equal(calls.levels[0].stopPrice, LEVELS.stopPrice);
  assert.deepEqual(
    calls.levels[0],
    calls.rendered[0].levelLines,
    '水準線へ渡すのはモーダルへ渡したのと同じ ViewModel の levelLines（表示が割れない）',
  );
});

test('TC-PC03 setParams はそのまま usecase へ渡り、結果が両方の表示先へ届く', async () => {
  // Arrange: 採用 f が確定した後（MC 実行後）に残高を 2 倍にする。
  const { controller, calls } = build();
  controller.open();
  await controller.runMonteCarlo();
  const before = calls.rendered[calls.rendered.length - 1].plan.total_lot;
  // Act
  controller.setParams({ balance: 344000 });
  // Assert
  const last = calls.rendered[calls.rendered.length - 1];
  assert.ok(before > 0, 'MC 後はロットが立つ');
  assert.ok(last.plan.total_lot > before, '残高 2 倍でロットが増える');
});

test('TC-PC04 setLevels は水準を作り直して再計算する（価格の単一ソースは水準側）', () => {
  // Arrange
  const { controller, calls } = build();
  controller.open();
  // Act
  controller.setLevels({ ...LEVELS, stopPrice: 58200 });
  // Assert
  const last = calls.rendered[calls.rendered.length - 1];
  assert.equal(last.levelLines.stopPrice, 58200);
  assert.equal(calls.levels[calls.levels.length - 1].stopPrice, 58200);
});

test('TC-PC05 未入力（null）を含む水準でも例外にせず再計算する（入力途中で落ちない）', () => {
  // Arrange
  const { controller, calls } = build();
  controller.open();
  // Act / Assert
  assert.doesNotThrow(() => controller.setLevels({ ...LEVELS, stopPrice: null }));
  const last = calls.rendered[calls.rendered.length - 1];
  assert.equal(last.plan.total_lot, 0, '計算できない入力ではロット 0（モーダル側は「—」を出す）');
});

test('TC-PC06 requestPick はピッカーをアームする（R-P1 の受け口）', () => {
  // Arrange
  const { controller, calls } = build();
  // Act
  controller.requestPick('stop');
  // Assert
  assert.deepEqual(calls.armed, ['stop']);
});

test('TC-PC07 ピッカーの確定はモーダルへ書き戻す（書き戻し経路は 1 本）', () => {
  // Arrange
  const { controller, calls } = build();
  // Act
  controller.confirmPick('entry:1', 58550);
  // Assert
  assert.deepEqual(calls.prices, [['entry:1', 58550]]);
});

test('TC-PC08 右クリックの損切り／利確はモーダルの当該欄へ、建値は 1 本追加する（R-P3）', () => {
  // Arrange
  const { controller, calls } = build();
  // Act
  controller.setStopPrice(58300);
  controller.setTakePrice(59300);
  controller.addEntryPrice(58450);
  // Assert
  assert.deepEqual(calls.prices, [['stop', 58300], ['take', 59300]]);
  assert.deepEqual(calls.added, [58450]);
});

test('TC-PC09 runMonteCarlo は MC を回して結果を描く（採用 f が確定する）', async () => {
  // Arrange
  const { controller, calls } = build();
  controller.open();
  assert.equal(calls.rendered[0].fraction, 0, 'MC 前は参照実装 :643 と同じく f=0');
  // Act
  await controller.runMonteCarlo();
  // Assert
  const last = calls.rendered[calls.rendered.length - 1];
  assert.ok(last.edge, 'MC 結果が ViewModel に載る');
  assert.ok(last.fraction > 0, '採用 f が確定する');
});

test('TC-PC10 MC の失敗は握り潰さず告知する（押しても何も起きない状態を作らない）', async () => {
  // Arrange
  const { controller, calls } = build({
    mcPort: { solve: async () => { throw new McUnavailableError('Worker 起動失敗'); } },
  });
  controller.open();
  // Act
  await controller.runMonteCarlo();
  // Assert
  assert.equal(calls.notified.length, 1);
  assert.match(calls.notified[0], /計算/, '利用者に分かる文言で知らせる');
});

test('TC-PC11 協働子が未注入でも例外にならない（picker・primitive なしの構成）', () => {
  // Arrange
  const usecase = new PositionSizingPlanUseCase({
    mcPort: { solve: async () => ({}) }, levels: createPriceLevels(LEVELS), params: PARAMS,
  });
  const controller = new PositionSizingController({ usecase, dialog: { open() {}, render() {} } });
  // Act / Assert
  assert.doesNotThrow(() => { controller.open(); controller.requestPick('stop'); });
});

test('TC-PC12 applyLevels(PriceLevels) は drag の更新を取り込み、モーダルへも書き戻す（双方向）', () => {
  // Arrange: drag は PriceLevels の非破壊更新（withStop 等）を渡してくる。
  const { controller, calls } = build();
  controller.open();
  const dragged = createPriceLevels(LEVELS).withStop(58250);
  // Act
  controller.applyLevels(dragged);
  // Assert
  const last = calls.rendered[calls.rendered.length - 1];
  assert.equal(last.levelLines.stopPrice, 58250, '計画が追随する');
  assert.equal(calls.synced.length, 1, 'モーダルの価格欄も追随する（チャート → モーダル）');
  assert.equal(calls.synced[0].stopPrice, 58250);
});

test('TC-PC13 levels() は現在の水準（domain 実体）を返す（drag の掴み対象の入力）', () => {
  // Arrange
  const { controller } = build();
  // Act / Assert
  assert.equal(controller.levels().stopPrice, LEVELS.stopPrice);
  controller.setLevels({ ...LEVELS, stopPrice: 58100 });
  assert.equal(controller.levels().stopPrice, 58100, '更新後は新しい水準を返す（古い実体を掴ませない）');
});

// ---------------------------------------------------------------------------
// 水準の所有者は 1 つ（SOLID リファクタリング 2026-08-20）
//
//   E-02 PriceLevels は「価格水準の単一ソース」である。協働子と usecase が**それぞれ**
//   現在値を保持していると、両者を同時に書く経路（setLevels / applyLevels）を 1 つでも
//   通らない更新が入った瞬間に、「計算に使う水準」と「drag が掴む水準」が割れる。
//   割れても例外は出ず、線だけが古い位置に残る（実 UI を触るまで気づけない）。
//   保持は usecase 1 か所にし、協働子は取り次ぐだけにする。
// ---------------------------------------------------------------------------

test('TC-PC14 水準の保持は usecase 1 か所（協働子は自前の写しを持たない）', () => {
  // Arrange
  const { controller, usecase } = build();
  // Act: usecase 側だけを更新する（協働子の setLevels を通さない経路）。
  usecase.setLevels(createPriceLevels({ ...LEVELS, stopPrice: 57000 }));
  // Assert: 協働子が写しを持っていれば、ここで古い 58340 を返す（＝掴む線と計算がずれる）。
  assert.equal(
    controller.levels().stopPrice,
    57000,
    '協働子が水準の写しを持っている（単一ソースが 2 つに割れる）',
  );
});
