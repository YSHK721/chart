// 手入力欄の表示をモデル値へ同期する（ISSUE-368 工程 3・D-3）のテスト。
//
// 設計入力（唯一の仕様源）: .doc/POSITION_SIZING_CHART_INTEGRATION_DESIGN.md
//   「追補: 工程 2」丸めの適用点 経路 7（手入力欄）、
//   同「決定: 案 E」原因 β（丸めの適用点が散ると**表示と値が食い違う**）、
//   同「フェイルセーフ」（**手入力は従来どおり可**＝仕様未解決でも落とさない）、
//   依頼者裁定 2026-08-20（D-3・UI 変更として承認済み）。
//
// 除去する原因（実測）: `step='1'` の銘柄で `58700.4` と打つと、水準（モデル）は 58700 になるが
//   欄の表示は `58700.4` のまま残る。ISSUE-368 が除去している「表示と値の乖離」と同型で、
//   利用者は自分が打った 58700.4 で計算されていると誤解する。
//
// 対象外（意図的に**しない**こと）:
//   - 入力途中（`input` イベント）での書き戻し。打っている最中に値が飛ぶとカーソルを奪う。
//   - 空欄の 0 埋め。既定水準が `null` である前提（`_emitLevels` の `num()`）を壊さない。
//
// 構造: Arrange-Act-Assert（AAA）。実物の共有配線で組む（補助は tests/support）。

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { boot, priceInput, dialogRoot, flatten } from './support/position_sizing_boot.js';

const JP225_REF = 'jp225_tick';        // 台帳: tick=1.0（刻み 1）
const UNKNOWN_REF = 'unknown_dataset_ref';   // 銘柄仕様が解決できない ref

// 実 DOM の入力手順: 1 文字ずつ 'input' が出て、確定（blur / Enter）で 'change' が出る。
function type(el, text) {
  el.value = text;
  el.fire('input');
}

const commit = (el) => el.fire('change');

// ---------------------------------------------------------------------------
// 入力確定で表示をモデルへ合わせる
// ---------------------------------------------------------------------------

test('TC-PI01 刻みの外の値を打って確定すると、欄の表示がモデル値になる（表示と値を食い違わせない）', () => {
  // Arrange: JP225（刻み 1）。
  const ctx = boot(JP225_REF);
  const stop = priceInput(ctx, 'stop');
  // Act
  type(stop, '58700.4');
  commit(stop);
  // Assert: モデルは既に 58700（domain の関門）。表示もそれに一致する。
  assert.equal(ctx.positionSizing.levels().stopPrice, 58700, '水準が刻み上にない（前提の崩れ）');
  assert.equal(stop.value, '58700', `欄の表示がモデル値に合っていない: ${stop.value}`);
});

test('TC-PI02 建値欄でも同じ（stop だけを直して取り残さない）', () => {
  // Arrange
  const ctx = boot(JP225_REF);
  const entry = priceInput(ctx, 'entry:0');
  // Act
  type(entry, '58700.4');
  commit(entry);
  // Assert
  assert.equal(entry.value, '58700');
});

test('TC-PI03 利確欄でも同じ（3 種の入力先すべてが同じ規則で動く）', () => {
  // Arrange
  const ctx = boot(JP225_REF);
  const take = priceInput(ctx, 'take');
  // Act
  type(take, '59000.7');
  commit(take);
  // Assert
  assert.equal(take.value, '59001');
});

// ---------------------------------------------------------------------------
// 打っている最中は奪わない
// ---------------------------------------------------------------------------

test('TC-PI04 入力途中（input）では書き戻さない（打っている最中に値が飛ばない）', () => {
  // Arrange
  const ctx = boot(JP225_REF);
  const stop = priceInput(ctx, 'stop');
  // Act: 'input' だけ（まだ確定していない）。
  type(stop, '58700.4');
  // Assert: 表示は打ったまま。モデルだけが刻みへ丸まっている。
  assert.equal(stop.value, '58700.4', '入力途中で書き戻してカーソルを奪っている');
  assert.equal(ctx.positionSizing.levels().stopPrice, 58700);
});

test('TC-PI05 打ち進める途中の各文字で書き戻さない（1 文字ごとに値が飛ばない）', () => {
  // Arrange
  const ctx = boot(JP225_REF);
  const stop = priceInput(ctx, 'stop');
  // Act: '5' → '58' → '587' … と打つ。
  for (const text of ['5', '58', '587', '5870', '58700', '58700.', '58700.4']) {
    type(stop, text);
    // Assert（各段）: 打った文字がそのまま残る。
    assert.equal(stop.value, text, `入力途中 '${text}' で書き戻している`);
  }
});

// ---------------------------------------------------------------------------
// 空欄・未入力（既定水準が null である前提を壊さない）
// ---------------------------------------------------------------------------

test('TC-PI06 空欄のまま確定しても null のまま（0 へ倒さない）', () => {
  // Arrange
  const ctx = boot(JP225_REF);
  const take = priceInput(ctx, 'take');
  assert.equal(take.value, '', '前提: 利確の初期値は空欄');
  // Act
  commit(take);
  // Assert
  assert.equal(take.value, '', '空欄が勝手に埋まっている');
  assert.equal(ctx.positionSizing.levels().takePrice, null, '未入力が null でなくなっている');
});

test('TC-PI07 打った値を消して確定しても null のまま（打ち直しを妨げない）', () => {
  // Arrange
  const ctx = boot(JP225_REF);
  const stop = priceInput(ctx, 'stop');
  type(stop, '58700.4');
  commit(stop);
  // Act
  type(stop, '');
  commit(stop);
  // Assert
  assert.equal(stop.value, '');
  assert.equal(ctx.positionSizing.levels().stopPrice, null);
});

// ---------------------------------------------------------------------------
// フェイルセーフ（銘柄仕様が解決できないとき）
// ---------------------------------------------------------------------------

test('TC-PI08 仕様が解決できないときは手入力を丸めない（人が打った値をそのまま使う）', () => {
  // Arrange: 台帳に無い ref＝刻みが不明。設計「フェイルセーフ」で手入力は落とさない。
  const ctx = boot(UNKNOWN_REF);
  const stop = priceInput(ctx, 'stop');
  // Act
  type(stop, '58700.4');
  commit(stop);
  // Assert: 丸めようがないので値も表示も打ったまま（勝手な既定の刻みを当てない）。
  assert.equal(ctx.positionSizing.levels().stopPrice, 58700.4);
  assert.equal(stop.value, '58700.4', '刻みが不明なのに表示を書き換えている');
});

// ---------------------------------------------------------------------------
// 巻き添えの検定（Pre-mortem で挙げた最有力の失敗原因を固定する）
//
//   確定の合図（`onCommitPrices`）は `syncPrices` を呼ぶ。`syncPrices` は当該欄だけでなく
//   **全価格欄・K（splits）・方向（direction）**を書く。したがって「価格を 1 つ確定しただけで
//   他の入力が巻き戻る」が最も起こりやすい壊れ方である。実測で棄却された各点をここで固定する。
// ---------------------------------------------------------------------------

const field = (ctx, key) => flatten(dialogRoot(ctx))
  .find((e) => e.dataset && e.dataset.psField === key);

test('TC-PI09 価格の確定で方向（direction）が巻き戻らない', () => {
  // Arrange: 方向をショートへ変える（方向は水準が持つ＝levelLines.direction）。
  const ctx = boot(JP225_REF);
  const direction = field(ctx, 'direction');
  direction.value = 'short';
  direction.fire('change');
  // Act
  const stop = priceInput(ctx, 'stop');
  type(stop, '58700.4');
  commit(stop);
  // Assert
  assert.equal(direction.value, 'short', '価格の確定で方向が巻き戻っている');
  assert.equal(ctx.positionSizing.levels().direction, 'short');
});

test('TC-PI10 価格の確定で K（分割本数）と建値欄が巻き戻らない', () => {
  // Arrange: K=3 にして 3 本目の建値を打つ。
  const ctx = boot(JP225_REF);
  const splits = field(ctx, 'splits');
  splits.value = '3';
  splits.fire('input');
  type(priceInput(ctx, 'entry:2'), '58800.6');
  // Act: **別の欄**（損切り）を確定する。
  const stop = priceInput(ctx, 'stop');
  type(stop, '58700.4');
  commit(stop);
  // Assert: K も欄も残り、3 本目はモデル値（刻み上）で表示される。
  assert.equal(splits.value, '3', 'K が巻き戻っている');
  assert.equal(priceInput(ctx, 'entry:2')?.value, '58801', '建値欄が消えた／値が失われた');
});

test('TC-PI11 価格の確定でパラメータ欄（勝率など）を巻き添えにしない', () => {
  // Arrange
  const ctx = boot(JP225_REF);
  const winRate = field(ctx, 'winRate');
  winRate.value = '41.5';
  winRate.fire('input');
  // Act
  const stop = priceInput(ctx, 'stop');
  type(stop, '58700.4');
  commit(stop);
  // Assert: 価格でない入力は 1 つも触られない。
  assert.equal(winRate.value, '41.5');
});
