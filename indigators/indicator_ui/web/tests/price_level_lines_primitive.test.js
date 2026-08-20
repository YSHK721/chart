// price_level_lines_primitive.js（水準線プリミティブ）の仕様検証（ISSUE-368 スライス 4）。
//
// 設計入力: 設計書 §6「Adapter: PriceLevelLinesPrimitive」／出力 3 スライス 4。
//   雛形は pair_lines_primitive.js（attached/detached/paneViews/_draw・useBitmapCoordinateSpace・
//   priceToCoordinate の範囲外 null はスキップ・setChromeColors 対応）。
// 観点:
//   - 建値 K 本・損切り・利確・ロスカットを水平線として描く
//   - 範囲外（priceToCoordinate が null）の線はスキップし例外を投げない
//   - **最新 y 座標表**を保持し、px 許容つきの掴み判定を提供する
//     （掴み判定を drag 側に持たせると「描いた位置」と「掴める位置」が二重定義になる）
//   - ロスカットは読み取り専用＝掴めない（設計 §4-E「読み取り専用のロスカット 1 本」）
//   - setChromeColors は全域的（null・非オブジェクト・部分指定でも例外を投げず現行値を保つ）
// 構造: Arrange-Act-Assert。fake target/scale/series で座標・色を観測（canvas 実描画は実 UI 検証へ）。

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { PriceLevelLinesPrimitive } from '../js/adapter/front/price_level_lines_primitive.js';

// 価格 → y 座標は「58700 を y=100 とし 10pt 下がるごとに +1px」の一次写像で与える。
//   可視範囲は y ∈ [-400, 400]（= 価格 54700〜62700）。範囲外は null（実 lwc の
//   priceToCoordinate と同じ契約）。負の y も返す：lwc はペイン上端より上でも
//   可視範囲内なら座標を返し、真に範囲外のときだけ null を返すため。
const Y_OF = (price) => {
  const y = 100 + (58700 - price) / 10;
  return y >= -400 && y <= 400 ? y : null;
};

function fakeTarget() {
  const ops = [];
  const ctx = {
    save() { ops.push(['save']); },
    restore() { ops.push(['restore']); },
    beginPath() { ops.push(['beginPath']); },
    moveTo(x, y) { ops.push(['moveTo', x, y]); },
    lineTo(x, y) { ops.push(['lineTo', x, y]); },
    stroke() { ops.push(['stroke']); },
    setLineDash(d) { ops.push(['setLineDash', d.join(',')]); },
    set strokeStyle(v) { ops.push(['strokeStyle', v]); },
    get strokeStyle() { return null; },
    set lineWidth(v) { ops.push(['lineWidth', v]); },
    get lineWidth() { return null; },
  };
  return {
    ops,
    useBitmapCoordinateSpace(fn) {
      fn({ context: ctx, bitmapSize: { width: 800, height: 400 }, horizontalPixelRatio: 1, verticalPixelRatio: 1 });
    },
  };
}

function attach(primitive, { toY = Y_OF } = {}) {
  let updates = 0;
  primitive.attached({
    chart: { timeScale: () => ({ width: () => 800 }) },
    series: { priceToCoordinate: toY },
    requestUpdate: () => { updates += 1; },
  });
  return { updates: () => updates };
}

const LEVELS = Object.freeze({
  direction: 'long',
  entryPrices: [58700, 59700],
  stopPrice: 58340,
  takePrice: 61500,
  losscutPrice: 57000,
});

// 描画された水平線の y をすべて拾う（moveTo の y ＝ その線の位置）。
function drawnYs(primitive) {
  const target = fakeTarget();
  primitive.draw(target);
  return target.ops.filter((o) => o[0] === 'moveTo').map((o) => o[2]);
}

test('attach 前の描画は何もしない（座標源が無い＝防御）', () => {
  // Arrange
  const p = new PriceLevelLinesPrimitive();
  p.setLevels(LEVELS);
  // Act
  const target = fakeTarget();
  p.draw(target);
  // Assert
  assert.deepEqual(target.ops, []);
});

test('建値 K 本・損切り・利確・ロスカットを水平線として描く', () => {
  // Arrange
  const p = new PriceLevelLinesPrimitive();
  attach(p);
  // Act
  p.setLevels(LEVELS);
  // Assert
  assert.deepEqual(drawnYs(p).sort((a, b) => a - b),
    [LEVELS.takePrice, LEVELS.entryPrices[1], LEVELS.entryPrices[0], LEVELS.stopPrice, LEVELS.losscutPrice]
      .map(Y_OF).sort((a, b) => a - b));
});

test('範囲外（priceToCoordinate が null）の線はスキップし例外を投げない', () => {
  // Arrange — 利確だけ可視範囲外へ出す
  const p = new PriceLevelLinesPrimitive();
  attach(p);
  p.setLevels({ ...LEVELS, takePrice: 99999 });
  // Act
  const ys = drawnYs(p);
  // Assert
  assert.equal(ys.length, 4, '範囲外 1 本ぶんだけ減る');
  assert.ok(!ys.includes(Y_OF(99999)));
});

test('未指定（null）の利確・ロスカットは線を作らない', () => {
  // Arrange
  const p = new PriceLevelLinesPrimitive();
  attach(p);
  // Act
  p.setLevels({ ...LEVELS, takePrice: null, losscutPrice: null });
  // Assert
  assert.equal(drawnYs(p).length, 3, '建値 2 本＋損切り 1 本');
});

test('最新 y 座標表から px 許容つきで掴み対象を引ける（描いた位置と掴める位置が同一ソース）', () => {
  // Arrange
  const p = new PriceLevelLinesPrimitive();
  attach(p);
  p.setLevels(LEVELS);
  drawnYs(p);                       // 直近の描画で y 表が確定する
  const stopY = Y_OF(LEVELS.stopPrice);

  // Act / Assert
  assert.deepEqual(p.handleAt(stopY, 4), { kind: 'stop', index: null });
  assert.deepEqual(p.handleAt(stopY + 3, 4), { kind: 'stop', index: null }, '許容内は掴める');
  assert.equal(p.handleAt(stopY + 9, 4), null, '許容外は掴めない');
  assert.deepEqual(p.handleAt(Y_OF(LEVELS.entryPrices[1]), 4), { kind: 'entry', index: 1 },
    '建値は番号まで返す（どの建玉かが一意に決まる）');
});

test('掴み判定は最も近い線を選ぶ（重なりでどちらが掴めるか一意に決まる）', () => {
  // Arrange — 建値#1 と 損切り を 4px 差で近接させる
  const p = new PriceLevelLinesPrimitive();
  attach(p);
  p.setLevels({ ...LEVELS, entryPrices: [58700], stopPrice: 58660 });
  drawnYs(p);
  const entryY = Y_OF(58700);
  const stopY = Y_OF(58660);
  assert.equal(stopY - entryY, 4);

  // Act / Assert
  assert.deepEqual(p.handleAt(entryY + 1, 10), { kind: 'entry', index: 0 });
  assert.deepEqual(p.handleAt(stopY - 1, 10), { kind: 'stop', index: null });
});

test('ロスカットは読み取り専用＝掴めない（描かれるが handle にならない）', () => {
  // Arrange
  const p = new PriceLevelLinesPrimitive();
  attach(p);
  p.setLevels(LEVELS);
  const ys = drawnYs(p);
  const lcY = Y_OF(LEVELS.losscutPrice);
  // Assert
  assert.ok(ys.includes(lcY), 'ロスカット線は描かれる');
  assert.equal(p.handleAt(lcY, 4), null, 'が、掴めない');
});

test('範囲外になった線は掴み対象からも外れる（描いていない線を掴ませない）', () => {
  // Arrange
  const p = new PriceLevelLinesPrimitive();
  attach(p);
  p.setLevels({ ...LEVELS, takePrice: 99999 });
  drawnYs(p);
  // Act / Assert
  assert.equal(p.handleAt(-40, 8), null);
});

test('setLevels は再描画を要求する（attach 前は no-op）', () => {
  // Arrange
  const before = new PriceLevelLinesPrimitive();
  before.setLevels(LEVELS);          // attach 前＝例外を投げない
  const p = new PriceLevelLinesPrimitive();
  const h = attach(p);
  // Act
  p.setLevels(LEVELS);
  // Assert
  assert.equal(h.updates(), 1);
});

test('setChromeColors は全域的（null・非オブジェクト・部分指定で例外を投げない）', () => {
  // Arrange
  const p = new PriceLevelLinesPrimitive();
  attach(p);
  p.setLevels(LEVELS);
  // Act / Assert
  assert.doesNotThrow(() => p.setChromeColors(null));
  assert.doesNotThrow(() => p.setChromeColors('nope'));
  assert.doesNotThrow(() => p.setChromeColors({ pairLineWin: 123 }));
  assert.equal(drawnYs(p).length, 5, '解釈できない指定でも描画は続く');
});

test('setChromeColors で配られた色が実際に描画へ反映される', () => {
  // Arrange
  const p = new PriceLevelLinesPrimitive();
  attach(p);
  p.setLevels(LEVELS);
  // Act
  p.setChromeColors({ pairLineWin: '#00ff00', pairLineLoss: '#ff0000', priceLine: '#0000ff' });
  const target = fakeTarget();
  p.draw(target);
  const colors = target.ops.filter((o) => o[0] === 'strokeStyle').map((o) => o[1]);
  // Assert
  assert.ok(colors.includes('#00ff00'), '利確に profit 色');
  assert.ok(colors.includes('#ff0000'), '損切りに loss 色');
  assert.ok(colors.includes('#0000ff'), '建値に価格線色');
});

test('paneViews / detached は lwc プリミティブ契約を満たす', () => {
  // Arrange
  const p = new PriceLevelLinesPrimitive();
  attach(p);
  p.setLevels(LEVELS);
  // Act / Assert
  const views = p.paneViews();
  assert.equal(views.length, 1);
  assert.equal(typeof views[0].renderer().draw, 'function');
  p.detached();
  const target = fakeTarget();
  views[0].renderer().draw(target);
  assert.deepEqual(target.ops, [], 'detach 後は描かない');
});
