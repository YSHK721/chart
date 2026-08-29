// heat_scale — 連続量 `p ∈ [0,1]` から色への写像が**1 冊に 1 つ**であることの固定。
//
// 設計書 §5.3（配色の基準 = 因果ローリング分位 p・段の名前は表示しない）／§5.5.5（第 1 表の
//   背景も同じ p の目盛りを使う＝読み方を 2 通りにしない）／§5.3.2（GPD が当てはまらない
//   セルは帯外を単一色にして「目盛りが無い」ことを示す）。
//
// なぜ色を「不透明度」で作るのか: 統合ページの下部ペインへ直接挿すため、背景色は宿主の
//   テーマ（明/暗）の上に載る。地の色を塗ると片方のテーマで文字が読めなくなる。中立
//   （p = 0.5）を**完全透明**にして地をそのまま見せ、0.5 から離れるほど濃くする形にすれば、
//   どちらのテーマでも「濃さ ＝ 0.5 からの隔たり」という読み方が壊れない。
//
// 構造は AAA。テスト名は「対象_条件_期待結果」。

import { test, describe } from 'node:test';
import assert from 'node:assert/strict';

import { colorForP, alphaForP, tailUnscaledColor, NO_LEVEL_COLOR } from '../js/adapter/front/heat_scale.js';

/** `rgba(r, g, b, a)` から a を取り出す（色文字列の形も同時に固定する）。 */
function alphaOf(css) {
  const m = /^rgba\(\s*\d+\s*,\s*\d+\s*,\s*\d+\s*,\s*([0-9.]+)\s*\)$/.exec(css);
  assert.ok(m, `rgba(...) 形ではありません: ${css}`);
  return Number(m[1]);
}

describe('heat_scale — p から色への唯一の写像', () => {
  test('color_for_p_at_neutral_is_fully_transparent', () => {
    // Arrange: 中立 = 0.5（§5.3 の目盛りの中央）。
    // Act
    const css = colorForP(0.5);
    // Assert: 地（宿主テーマ）をそのまま見せる＝不透明度 0。
    assert.equal(alphaOf(css), 0);
  });

  test('color_for_p_at_both_ends_reaches_the_same_maximum_opacity', () => {
    // Act
    const calm = colorForP(0);
    const hot = colorForP(1);
    // Assert: 端の濃さは左右で等しい（片側だけ目立つと「0.5 からの隔たり」が読めない）。
    assert.equal(alphaOf(calm), alphaOf(hot));
    assert.ok(alphaOf(hot) > 0, '端が透明では色が意味を持たない');
  });

  test('color_for_p_below_and_above_neutral_use_different_hues', () => {
    // Assert: 沈静側と過熱側は色相で区別する（濃さだけだと向きが読めない）。
    const calmRgb = colorForP(0.1).replace(/,\s*[0-9.]+\s*\)$/, ')');
    const hotRgb = colorForP(0.9).replace(/,\s*[0-9.]+\s*\)$/, ')');
    assert.notEqual(calmRgb, hotRgb);
  });

  test('color_for_p_gets_denser_as_p_moves_away_from_neutral', () => {
    // Assert: 単調性を 2 点以上で固定する（両側とも）。
    assert.ok(alphaForP(0.9) > alphaForP(0.7));
    assert.ok(alphaForP(0.7) > alphaForP(0.6));
    assert.ok(alphaForP(0.1) > alphaForP(0.3));
    assert.ok(alphaForP(0.3) > alphaForP(0.4));
  });

  test('color_for_p_of_null_is_no_color_so_the_cell_stays_empty', () => {
    // §5.5.5: その地平の候補が 1 つも残らないときは**空**にし、色を置かない
    //   （無言で 0.5 を埋めない）。
    assert.equal(colorForP(null), NO_LEVEL_COLOR);
    assert.equal(colorForP(undefined), NO_LEVEL_COLOR);
    assert.equal(NO_LEVEL_COLOR, '');
  });

  test('color_for_p_out_of_the_unit_range_is_refused', () => {
    // フェイルクローズ: 範囲外は握り潰さない（p の定義が壊れた合図なので黙って丸めない）。
    assert.throws(() => colorForP(1.2), /p/);
    assert.throws(() => colorForP(-0.1), /p/);
    assert.throws(() => colorForP(Number.NaN), /p/);
    assert.throws(() => colorForP('0.5'), /p/);
  });

  test('tail_unscaled_color_is_outside_the_p_scale', () => {
    // §5.3.2: GPD が当てはまらないセルの帯外は**単一色**。目盛りの上の色と衝突させない
    //   （衝突すると「濃さ」を読んだ利用者が在りもしない p を読むことになる）。
    const single = tailUnscaledColor();
    const onScale = [0, 0.1, 0.25, 0.5, 0.75, 0.9, 1].map((p) => colorForP(p));
    assert.ok(!onScale.includes(single), `単一色が目盛りの色と衝突しています: ${single}`);
  });

  test('the_scale_has_no_second_definition_in_the_front_tree', async () => {
    // 「配色の基準は 1 冊に 1 つ」（§5.5.7）を機械的に固定する。他の front モジュールが
    //   自前の rgba/hsl を書き始めたら、この検定が落ちる。
    const { readdirSync, readFileSync, statSync } = await import('node:fs');
    const { fileURLToPath } = await import('node:url');
    const jsRoot = fileURLToPath(new URL('../js', import.meta.url));
    const files = [];
    const walk = (dir) => {
      for (const name of readdirSync(dir)) {
        const full = `${dir}/${name}`;
        if (statSync(full).isDirectory()) walk(full);
        else if (name.endsWith('.js')) files.push(full);
      }
    };
    walk(jsRoot);
    const offenders = files
      .filter((f) => !f.endsWith('/heat_scale.js'))
      .filter((f) => /rgba?\(|hsla?\(|#[0-9a-fA-F]{3,8}\b/.test(
        readFileSync(f, 'utf8').replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, ''),
      ));
    assert.deepEqual(offenders, [], '色の第 2 定義があります（heat_scale.js が唯一源）');
  });
});
