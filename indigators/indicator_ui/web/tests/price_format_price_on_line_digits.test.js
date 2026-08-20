// price_format.js の `priceOnLine(value, digits)`（ISSUE-368 工程 3・D-2）のテスト。
//
// 設計入力（唯一の仕様源）:
//   - 参照実装 integrated_position_sizing_calculator.html `:777`
//     （数直線マーカー＝線に添える価格は `Math.round(val).toLocaleString()`）。
//   - .doc/POSITION_SIZING_CHART_INTEGRATION_DESIGN.md「追補: 工程 2」E-2
//     （表示桁 `digits` の**定義は Python 台帳ただ 1 つ**。JS は生成物を読むだけ）。
//   - 依頼者裁定 2026-08-20（D-2）: 表示桁の権威を台帳 1 つにする。
//     ただし `digits` 未指定のときは従来と**完全同一**であること（既存の面を動かさない）。
//
// 除去する原因: 表示桁の権威が「参照実装の整数固定」と「台帳の digits」の 2 つに割れると、
//   刻みが 0.1 の銘柄で「入る値は 8568.9・線の表示は 8,569」という乖離が生まれる
//   （ISSUE-368 が除去している「表示と値の乖離」と同型）。
//
// 構造: Arrange-Act-Assert（AAA）。純関数のみ・DOM/lwc 非依存。

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readdirSync, readFileSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';

import { priceOnLine } from '../js/adapter/front/price_format.js';

// 従来出力の**独立な再計算**（参照実装 :777 の式そのもの）。実装から import しない
//   ＝実装を書き換えたら食い違いが出る（実装と期待値が同じ関数を指すと何も検定できない）。
const legacy = (v) => Math.round(v).toLocaleString();

// 境界を含む代表値。`.5` の丸め方向（Math.round は正負とも +∞ 側）と桁区切りの有無、
//   実 UI 実測の生値（ISSUE-368）、負値（ロスカット価格が 0 未満になりうる面）を含める。
const VALUES = Object.freeze([
  0, 1, 999, 1000, 58998.75, 58998.5, 58999.5, 58998.4999999,
  62707.710070965324, 1234567.4, 8568.85, -58998.5, -58998.75, -0.4,
]);

// ---------------------------------------------------------------------------
// 🔴 Red 駆動（新しい振る舞い）: digits を受け取り、その桁で表示する
// ---------------------------------------------------------------------------

test('TC-PF01 digits=2 は小数 2 桁で表示する（台帳の digits に従う）', () => {
  // Arrange / Act / Assert
  assert.equal(priceOnLine(58998.75, 2), '58,998.75');
  assert.equal(priceOnLine(58999, 2), '58,999.00', '桁は常に埋める（欄ごとに幅が揺れない）');
});

test('TC-PF02 digits=1 は小数 1 桁で表示する（刻み 0.1 の銘柄）', () => {
  // Arrange / Act / Assert
  assert.equal(priceOnLine(8568.85, 1), '8,568.9');
  assert.equal(priceOnLine(8568.9, 1), '8,568.9');
});

test('TC-PF03 digits は表示桁の丸めにも効く（桁を落とすときに切り捨てない）', () => {
  // Arrange: 境界値は**二進で正確に表せる値**を使う。`1.005` の実体は 1.0049999999999998934
  //   （node 実測）で 1.005 ではないため、期待値 '1.01' は実装ではなく期待値の側が誤りになる。
  //   `1.125`（= 9/8）は正確な値で、×100 が厳密に 112.5 になる真の `.5` 境界である。
  // Act / Assert: 最近傍へ丸める（切り捨てなら '1.12' になる）。
  assert.equal(priceOnLine(1.125, 2), '1.13', '`.5` 境界を上へ丸めていない');
  assert.equal(priceOnLine(1.129, 2), '1.13', '桁を切り捨てている');
  assert.equal(priceOnLine(1.124, 2), '1.12');
});

// ---------------------------------------------------------------------------
// 仕様固定（既存の面を 1 バイトも動かさない）— Red 駆動ではなく「変えていない」ことの実測
// ---------------------------------------------------------------------------

test('TC-PF04 digits 未指定は従来（参照実装 :777）と完全同一', () => {
  // Arrange / Act / Assert
  for (const v of VALUES) {
    assert.equal(priceOnLine(v), legacy(v), `digits 未指定で従来と食い違う: ${v}`);
  }
});

test('TC-PF05 digits=0 は従来出力と厳密一致（JP225 は digits=0 ＝見た目の変化 0）', () => {
  // Arrange / Act / Assert: 負値の `.5`（Math.round は +∞ 側へ丸める）まで含めて一致すること。
  for (const v of VALUES) {
    assert.equal(priceOnLine(v, 0), legacy(v), `digits=0 で従来と食い違う: ${v}`);
  }
});

test('TC-PF06 非有限は従来どおりそのまま文字列化する（無音で 0 へ倒さない）', () => {
  // Arrange / Act / Assert
  assert.equal(priceOnLine(NaN), legacy(NaN));
  assert.equal(priceOnLine(NaN, 0), legacy(NaN));
  assert.equal(priceOnLine(NaN, 2), 'NaN');
});

// ---------------------------------------------------------------------------
// 構造（第 2 実装の禁止）
// ---------------------------------------------------------------------------

const FRONT_DIR = fileURLToPath(new URL('../js/adapter/front/', import.meta.url));
const frontFiles = () => readdirSync(FRONT_DIR).filter((n) => n.endsWith('.js'));

test('TC-PF07 線に添える価格の書式は front 配下 1 ファイルだけが定義する', () => {
  // Arrange / Act
  const definers = frontFiles()
    .filter((n) => /export function priceOnLine/.test(readFileSync(join(FRONT_DIR, n), 'utf8')));
  // Assert: 定義が増えると「ゴーストは整数・モーダルは 2 桁」の取り残しが再発する。
  assert.deepEqual(definers, ['price_format.js'], `書式の定義が複数ある: ${definers.join(', ')}`);
});

test('TC-PF08 ゴースト（消費者）は書式を自前で組まない（import で取る）', () => {
  // Arrange
  const src = readFileSync(join(FRONT_DIR, 'price_pick_controller.js'), 'utf8');
  // Act / Assert
  assert.equal(
    /toLocaleString|toFixed/.test(src), false,
    'price_pick_controller.js が数値書式を自前で持っている（第 2 実装）',
  );
  assert.equal(/from '\.\/price_format\.js'/.test(src), true, '書式を単一ソースから取っていない');
});
