// ISSUE-054: 日別プロファイルの「レンジ」(barw) を tf-period 列にも効かせる。
//
// 検出時の症状（実 UI 実測）: ソース=滞在時間(実ティック)／表示モード=日別プロファイルで
//   レンジを 500 に変えても列の描画が変わらない。切り分け済みの事実は
//     - フロント送信・バックエンド応答はいずれも正常（barw=500 → n_bins=130 等）
//     - 日別領域のピクセル差分が 0.00%（完全一致）
//     - POC/VA の値だけは変わる（69256→69640）
//   ＝ **パラメータが部分的にしか効かない**。
//
// 根因: 対応 tf では日別描画を tf-period 列（最小価格単位）が担い（`_draw` が `_tfPeriods` を
//   優先して早期 return する。ISSUE-055 のちらつき防止設計）、tf-period 側は barw を持たない。
//
// 対応: 取得・キャッシュ・API は変えず、**描画時に barw 幅へ束ねる**。測定は最小価格単位のまま
//   保つ（粗いビンで測ると分布が退行して見えるアーティファクトを持ち込まないため）。

import { test } from 'node:test';
import assert from 'node:assert/strict';

import {
  aggregateLevelsToBins,
  MarketProfileHistogramPrimitive,
} from '../js/adapter/front/market_profile_primitive.js';

// ---------------------------------------------------------------------------
// 純関数
// ---------------------------------------------------------------------------

test('aggregateLevelsToBins: 同一ビンの count を合算しビン中心価格で返す', () => {
  // binWidth=10 → ビン境界は 0 起点の絶対格子（[0,10) [10,20) …）。
  const levels = [[1, 2], [3, 5], [11, 7], [19, 1], [25, 4]];

  const got = aggregateLevelsToBins(levels, 10);

  assert.deepEqual(got, [
    [5, 7],    // [0,10):  2 + 5
    [15, 8],   // [10,20): 7 + 1
    [25, 4],   // [20,30): 4
  ]);
});

test('aggregateLevelsToBins: ビン格子は列に依らない絶対格子（列間で行がずれない）', () => {
  // 同じ価格 103 は、他にどんな価格が同居していてもつねに同じビン [100,110) へ落ちる。
  const colA = aggregateLevelsToBins([[103, 1]], 10);
  const colB = aggregateLevelsToBins([[100, 1], [103, 1], [109, 1]], 10);

  assert.equal(colA[0][0], 105);
  assert.equal(colB[0][0], 105, '列が違っても同じ価格は同じ行に載る');
});

test('aggregateLevelsToBins: 昇順で返す（入力が順不同でも）', () => {
  const got = aggregateLevelsToBins([[95, 1], [5, 1], [45, 1]], 10);
  assert.deepEqual(got.map((x) => x[0]), [5, 45, 95]);
});

test('aggregateLevelsToBins: binWidth 未指定・非正・空入力は素通し（束ねない）', () => {
  const levels = [[1, 2], [3, 5]];
  assert.deepEqual(aggregateLevelsToBins(levels, null), levels);
  assert.deepEqual(aggregateLevelsToBins(levels, 0), levels);
  assert.deepEqual(aggregateLevelsToBins(levels, -5), levels);
  assert.deepEqual(aggregateLevelsToBins([], 10), []);
});

test('aggregateLevelsToBins: 非有限な price/count は落とす（描画を壊さない）', () => {
  const got = aggregateLevelsToBins([[1, 2], [NaN, 9], [3, Infinity], [4, 1]], 10);
  assert.deepEqual(got, [[5, 3]]);
});

// ---------------------------------------------------------------------------
// primitive への適用（描画とホバー読取が同じ行を見る）
// ---------------------------------------------------------------------------

function newPrimitive() {
  const p = new MarketProfileHistogramPrimitive();
  p._visible = true;
  // ホバー読取は px 換算に series を使う。座標源なしなら unit×3 の近似へ縮退する。
  p._series = null;
  return p;
}

const COLUMN = {
  time: 100,
  poc: 103,
  levels: [[101, 1], [102, 2], [103, 9], [104, 2], [111, 3]],
};

test('setTfBinWidth: 未設定なら最小価格単位のまま（従来挙動・後方互換）', () => {
  const p = newPrimitive();
  p.setTfPeriods([COLUMN], 1);

  const hit = p.tfPeriodLevelAt(100, 103);

  assert.equal(hit.price, 103, '元の levels の行を引く');
  assert.equal(hit.value, 9);
  assert.equal(hit.unit, 1, '行幅は最小価格単位');
});

test('setTfBinWidth: 指定するとホバー読取が束ね後の行を返す（描画と同じ行）', () => {
  const p = newPrimitive();
  p.setTfPeriods([COLUMN], 1);

  p.setTfBinWidth(10);

  // binWidth=10 → [100,110) に 1+2+9+2=14、[110,120) に 3。
  const hit = p.tfPeriodLevelAt(100, 103);
  assert.equal(hit.price, 105, 'ビン中心価格');
  assert.equal(hit.value, 14, '同一ビンの count 合計');
  assert.equal(hit.unit, 10, '行幅は束ね幅');
});

test('setTfBinWidth: null で最小価格単位へ復帰する', () => {
  const p = newPrimitive();
  p.setTfPeriods([COLUMN], 1);
  p.setTfBinWidth(10);

  p.setTfBinWidth(null);

  const hit = p.tfPeriodLevelAt(100, 103);
  assert.equal(hit.price, 103);
  assert.equal(hit.value, 9);
});

test('setTfBinWidth: 同値の再設定では再描画要求を出さない（storm 防止）', () => {
  const p = newPrimitive();
  let updates = 0;
  p._update = () => { updates += 1; };

  p.setTfBinWidth(10);
  assert.equal(updates, 1);
  p.setTfBinWidth(10);
  assert.equal(updates, 1, '同値なら _update を呼ばない');
  p.setTfBinWidth(25);
  assert.equal(updates, 2);
});
