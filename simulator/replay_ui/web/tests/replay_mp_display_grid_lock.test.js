// 過去不変（バー境界跨ぎで確定済み価格帯の bin が動かない）: リプレイ×dwell 再生で backend base=1 forming の
//   表示レンジ priceMin/priceMax は成長窓 [from,to] の実データ min(low)/max(high) 由来のため、to が伸びると
//   レンジ拡大→binw=(priceMax-priceMin)/nBins 変化→不変 fine 格子（k=floor(mid/GRID_W)）を動くグリッドで
//   再ビニング→過去ビンが再分配され動く（既存バグ・pull-at-T 無関係）。
// 参照実装 prototype_260630-01/mp_core.py: fine=絶対格子（floor(price/GRID_W=10)・不変・:126）、表示 bin は
//   price_min/price_max/n_bins から静的導出（:227,254）。成長中に再導出しないのが正解（classic MP 固定
//   ティックサイズ格子）。
// 本テスト: replay subclass の表示グリッド固定 lockedDisplayGrid が「binw 固定＋原点を絶対格子アンカー＋
//   端に bin 追加（既存境界不動）」を満たすことを検証する（過去不変の機構的担保）。
// 構造: Arrange-Act-Assert。純関数として検証（DOM/fetch 非依存）。

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { lockedDisplayGrid } from '../js/adapter/front/replay_market_profile_actor.js';

// lockedDisplayGrid が参照する actor 内部（_params.bins / _getContext().timeframe / _displayGridLock）の最小 fake。
function fakeActor(bins) {
  return { _params: { bins }, _getContext: () => ({ timeframe: '1h' }), _displayGridLock: null };
}

test('lockedDisplayGrid: 上方レンジ拡大でも binw を固定し原点を不動に保つ（確定バンドの bin 境界が跨ぎで不変）', () => {
  // Arrange: セッション開始（binw=10）。
  const a = fakeActor(10);
  const g1 = lockedDisplayGrid(a, { priceMin: 1000, priceMax: 1100, nBins: 10 }, 0);
  const binw1 = (g1.priceMax - g1.priceMin) / g1.nBins;
  // Act: バー境界跨ぎで上端が 1155 まで伸びる（素なら binw=15.5 へ rescale されるはず）。
  const g2 = lockedDisplayGrid(a, { priceMin: 1000, priceMax: 1155, nBins: 10 }, 0);
  const binw2 = (g2.priceMax - g2.priceMin) / g2.nBins;
  // Assert
  assert.equal(binw1, 10, '初回 binw=(1100-1000)/10=10');
  assert.equal(binw2, 10, 'binw は固定（成長で rescale しない）');
  assert.equal(g2.priceMin, 1000, '原点は不動（絶対格子）');
  assert.ok(g2.nBins >= g1.nBins, 'レンジ拡大は端に bin を追加（既存境界を動かさない）');
  assert.equal(g1.priceMin + 5 * binw1, g2.priceMin + 5 * binw2, '確定バンド [1000,1100] の bin 境界が跨ぎで不変');
});

test('lockedDisplayGrid: 下方レンジ拡大は原点を binw 倍数（絶対格子）へアンカーし既存境界を動かさない', () => {
  // Arrange: binw=10 でロック。
  const a = fakeActor(10);
  lockedDisplayGrid(a, { priceMin: 1000, priceMax: 1100, nBins: 10 }, 0);
  // Act: 下端が 953 まで伸びる。
  const g = lockedDisplayGrid(a, { priceMin: 953, priceMax: 1100, nBins: 10 }, 0);
  // Assert: 原点は floor(953/10)*10=950（絶対格子・binw 倍数）、binw 固定、絶対格子点 1050 は境界のまま。
  assert.equal((g.priceMax - g.priceMin) / g.nBins, 10, 'binw 固定');
  assert.equal(g.priceMin, 950, '原点=floor(953/10)*10=950（絶対格子）');
  assert.equal((1050 - g.priceMin) % 10, 0, '確定バンド境界は絶対格子上で不変');
});

test('lockedDisplayGrid: セッション鍵（from|tf|bins）変更で再ロック（新レンジ基準の binw を採る）', () => {
  const a = fakeActor(10);
  lockedDisplayGrid(a, { priceMin: 1000, priceMax: 1100, nBins: 10 }, 0); // binw=10
  const g = lockedDisplayGrid(a, { priceMin: 1000, priceMax: 1200, nBins: 10 }, 999); // from 変更→再ロック
  assert.equal((g.priceMax - g.priceMin) / g.nBins, 20, 'from 変更で再ロック（新 binw=(1200-1000)/10=20）');
});

test('lockedDisplayGrid: レンジ不成立（dataMax<=dataMin）は backend 値そのまま（非破壊・縮退検知温存）', () => {
  const a = fakeActor(10);
  const g = lockedDisplayGrid(a, { priceMin: 1000, priceMax: 1000, nBins: 10 }, 0);
  assert.equal(g.priceMin, 1000);
  assert.equal(g.priceMax, 1000);
  assert.equal(g.nBins, 10);
});
