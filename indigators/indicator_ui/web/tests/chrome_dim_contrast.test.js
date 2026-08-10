// chrome_dim_contrast.test.js — 減光・tint を「対地コントラスト比の目標」で持つ（段階 5-D 追補 2）。
//
// 病因（実測）: チャート本体の 3 派生 slot は加法 delta で持っていたため、地を変えると効果が消えた。
//     analysisTint      … 地 #ffffff で CR 1.0000（＝地と同一。tint が「何も起きない」）
//     dimCandle         … 地 #ffffff で CR 1.0018（減光が視認不能）
//     replayBoundaryDim … 地 #000000 で CR 1.0000（境界減光が消滅）
//   パネルが地に溶けたのと同じ欠陥で、機能が黙って死ぬ。
//
// 是正: これらの slot の意味は「地からわずかに離れた色」であって特定の色相シフトではない。手で
//   選ばれた現行値に紛れ込んだ微妙な色味には意味が無いため、**対地コントラスト比の目標**で持つ
//   （ISSUE-346 が level / muted に適用したのと同じ規律）。CR は地に対する相対量なので、地が
//   変わっても「地からこれだけ離れている」が保たれる。方向は contrastAnchor 側（潰れない唯一の向き）。
//
// 目標値は現行の暗い地での実測 CR:
//     dimCandle 1.017 ／ analysisTint 1.040 ／ replayBoundaryDim 1.084
//
// 縮退規則: 到達不能な目標は mixAtContrast が argmin |CR − target| として**最良の到達点**へ倒す
//   （color_value.js に既述の単一の式。専用の分岐を持たない）。本件の目標は最大でも 1.084 で、
//   anchor 側の上限 CR は全域で 4.5 以上あるため、実測上どの地でも縮退は起きない（下の検定が実証）。

import test from 'node:test';
import assert from 'node:assert/strict';

import { resolveAllChrome, resolveChromeSlotColor } from '../js/usecase/color_resolver.js';
import { CHROME_SLOTS, CHROME_CURRENT } from '../js/usecase/chrome_tokens.js';
import { expandRoleColors } from '../js/usecase/color_derivation.js';
import { contrastAnchor, contrastRatio } from '../js/domain/color_value.js';

const GROUNDS = ['#131722', '#0d1b3e', '#ffffff', '#f5f5f5', '#808080', '#000000'];

// 減光の強さの順（弱 → 強）。順序が崩れると「弱い減光のほうが濃い」という破綻になる。
const DIM_ORDER = ['dimCandle', 'analysisTint', 'replayBoundaryDim'];

// 地と区別できるとみなす下限。1.0 ちょうどは「地と同一＝機能が消えた」ことを意味する。
const MIN_SEPARATION_CR = 1.01;

const themeFor = (surface) => ({ roleColors: expandRoleColors({ surface, primary: '#42a5f5' }) });

// --- 通過条件 1: 恒等 ------------------------------------------------------

test('TC-DC01 恒等: テーマ未設定なら 3 slot とも現行リテラルと厳密一致する', () => {
  // Arrange / Act
  const { slots } = resolveAllChrome(null);
  // Assert
  assert.equal(slots.dimCandle, '#16191f');
  assert.equal(slots.analysisTint, '#1b1a24');
  assert.equal(slots.replayBoundaryDim, '#090d18');
  for (const id of DIM_ORDER) {
    assert.equal(slots[id], CHROME_CURRENT[id], id);
  }
});

// --- 通過条件 2: 地と区別できる --------------------------------------------

test('TC-DC02 6 地すべてで 3 slot の対地コントラスト比が 1.01 以上（機能が消えない）', () => {
  // Arrange
  const failures = [];
  // Act
  for (const ground of GROUNDS) {
    const { slots } = resolveAllChrome(themeFor(ground));
    for (const id of DIM_ORDER) {
      const cr = contrastRatio(slots[id], ground);
      if (!(cr >= MIN_SEPARATION_CR)) failures.push(`${ground} → ${id}=${slots[id]} CR=${cr.toFixed(4)}`);
    }
  }
  // Assert
  assert.deepEqual(failures, [], `地と区別できない:\n${failures.join('\n')}`);
});

// --- 通過条件 3: 飽和しない ------------------------------------------------

test('TC-DC03 6 地すべてで 3 slot が #ffffff / #000000 へ飽和しない', () => {
  // Arrange
  const failures = [];
  // Act
  for (const ground of GROUNDS) {
    const { slots } = resolveAllChrome(themeFor(ground));
    for (const id of DIM_ORDER) {
      if (slots[id] === '#ffffff' || slots[id] === '#000000') failures.push(`${ground} → ${id}=${slots[id]}`);
    }
  }
  // Assert
  assert.deepEqual(failures, [], `飽和した:\n${failures.join('\n')}`);
});

// --- 通過条件 4: 強さの順序が保たれる --------------------------------------

test('TC-DC04 6 地すべてで減光の強さが dimCandle < analysisTint < replayBoundaryDim を保つ', () => {
  // Arrange
  const failures = [];
  // Act
  for (const ground of GROUNDS) {
    const { slots } = resolveAllChrome(themeFor(ground));
    const crs = DIM_ORDER.map((id) => ({ id, cr: contrastRatio(slots[id], ground) }));
    for (let i = 1; i < crs.length; i += 1) {
      if (!(crs[i].cr > crs[i - 1].cr)) {
        failures.push(`${ground}: ${crs[i - 1].id}(${crs[i - 1].cr.toFixed(4)}) >= ${crs[i].id}(${crs[i].cr.toFixed(4)})`);
      }
    }
  }
  // Assert
  assert.deepEqual(failures, [], `減光の強弱が逆転した:\n${failures.join('\n')}`);
});

// --- 目標が実際に達成されているか（縮退が起きていないことの実証）-----------

test('TC-DC05 6 地すべてで達成 CR は目標の ±0.01 以内（縮退が起きていない）', () => {
  // Arrange: 目標は台帳が単一情報源。ここで数値を書き写さない。
  const targets = Object.fromEntries(
    CHROME_SLOTS.filter((s) => s.crTarget != null).map((s) => [s.id, s.crTarget]),
  );
  assert.deepEqual(Object.keys(targets).sort(), [...DIM_ORDER].sort(), '台帳の crTarget が 3 点');
  const failures = [];
  // Act
  for (const ground of GROUNDS) {
    const { slots } = resolveAllChrome(themeFor(ground));
    for (const id of DIM_ORDER) {
      const got = contrastRatio(slots[id], ground);
      if (Math.abs(got - targets[id]) > 0.01) {
        failures.push(`${ground} → ${id}: 目標 ${targets[id]} / 実測 ${got.toFixed(4)}`);
      }
    }
  }
  // Assert
  assert.deepEqual(failures, [], `目標に届かなかった（縮退）:\n${failures.join('\n')}`);
});

test('TC-DC06 目標値は現行の暗い地での実測 CR と一致する（設計値ではなく逆算）', () => {
  // Arrange / Act / Assert
  const base = CHROME_CURRENT.layoutBackground; // #131722
  for (const id of DIM_ORDER) {
    const slot = CHROME_SLOTS.find((s) => s.id === id);
    const measured = contrastRatio(CHROME_CURRENT[id], base);
    assert.ok(Math.abs(measured - slot.crTarget) < 0.001,
      `${id}: 現行実測 ${measured.toFixed(4)} / 台帳 ${slot.crTarget}`);
  }
});

// --- 方向 ------------------------------------------------------------------

test('TC-DC07 3 slot は contrastAnchor 側へ寄る（潰れない唯一の向き）', () => {
  // Arrange / Act / Assert
  for (const ground of ['#131722', '#ffffff']) {
    const anchorIsWhite = contrastAnchor(ground) === '#ffffff';
    const { slots } = resolveAllChrome(themeFor(ground));
    for (const id of DIM_ORDER) {
      const ch = (h) => [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16));
      const sum = (h) => ch(h).reduce((a, b) => a + b, 0);
      assert.equal(sum(slots[id]) > sum(ground), anchorIsWhite,
        `${ground}/${id}: anchor 側へ寄っていない（${slots[id]}）`);
    }
  }
});

test('TC-DC08 台帳: crTarget と delta / ramp は排他（派生規則を 2 つ持たない）', () => {
  for (const s of CHROME_SLOTS.filter((x) => x.crTarget != null)) {
    assert.equal(s.delta, undefined, `${s.id}: delta を併せ持つ`);
    assert.equal(s.ramp, undefined, `${s.id}: ramp を併せ持つ`);
    assert.equal(s.derivedFrom, 'surface', `${s.id}: 地からの派生である`);
  }
});
