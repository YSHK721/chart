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

import { colorForP, alphaForP, stripGradient, tailUnscaledColor, NO_LEVEL_COLOR } from '../js/adapter/front/heat_scale.js';
// 密度の写像は名前空間で受ける。存在しない名前を named import すると ESM の**連結**が
//   落ちて本ファイルの検定が丸ごと走らなくなり、「どの表明が何を捕まえたか」が読めない。
import * as heatScale from '../js/adapter/front/heat_scale.js';

/** `rgba(r, g, b, a)` から a を取り出す（色文字列の形も同時に固定する）。 */
function alphaOf(css) {
  const m = /^rgba\(\s*\d+\s*,\s*\d+\s*,\s*\d+\s*,\s*([0-9.]+)\s*\)$/.exec(css);
  assert.ok(m, `rgba(...) 形ではありません: ${css}`);
  return Number(m[1]);
}

/**
 * 密度の目盛りの下限が、その上に伸びる幅（上限 − 下限）に対して占めるべき最小の比。
 *
 * 「下限が在る」だけでは足りない（在っても効かない値へ弱まりうる）ので、効いていることを
 * 比で表明する。1/4 は、下限を目盛り全体の中で**目に付く一段**として要求する水準であり、
 * 下限の絶対値（現在 0.12）にも上限の絶対値（現在 0.40）にも依存しない。
 */
const MIN_FLOOR_SHARE = 0.25;

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

  // ---- stripGradient（§5.2 背景ストリップ・依頼者指示 2026-09-04） ------------------
  test('a_strip_paints_each_reading_with_the_same_single_scale', () => {
    // Arrange: 読み 2 区間（古い順）。色は colorForP そのもの＝第 2 定義を作らない。
    const css = stripGradient([
      { p: 0.2, tail_unscaled: false },
      { p: 0.9, tail_unscaled: false },
    ]);
    // Assert: 左＝最古・右＝最新の硬い縞（hard stop）。
    assert.equal(
      css,
      `linear-gradient(to right, ${colorForP(0.2)} 0.0000% 50.0000%, ${colorForP(0.9)} 50.0000% 100.0000%)`,
    );
  });

  test('a_tail_unscaled_reading_in_a_strip_uses_the_out_of_band_single_colour', () => {
    const css = stripGradient([{ p: null, tail_unscaled: true }, { p: 0.5 }]);
    assert.ok(css.includes(tailUnscaledColor()));
  });

  test('an_unreadable_reading_in_a_strip_places_no_colour', () => {
    // 無言で 0.5 を埋めない: p が無い区間は透明（地をそのまま見せる）。
    const css = stripGradient([{ p: null }, { p: 0.75 }]);
    assert.ok(css.startsWith('linear-gradient(to right, rgba(0, 0, 0, 0) '));
  });

  test('an_empty_strip_places_no_colour_at_all', () => {
    assert.equal(stripGradient([]), NO_LEVEL_COLOR);
    assert.equal(stripGradient(undefined), NO_LEVEL_COLOR);
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

// ---------------------------------------------------------------------------
// 密度（量）の写像。`p`（分位）とは**読み方が違う**ので、別の写像として固定する。
//
//   p    は「中立 0.5 からの隔たり」を濃さで表す（双極・向きは色相が担う）。
//   norm は「量そのもの」を濃さで表す（単調・0 が無・1 が最大）。
//
// MP 列（依頼者指示 2026-09-06「色の濃度とグラフで表示しろ」）が求めるのは後者である。
// 前者を量に流用すると濃さが量を表さない: norm ≈ 0.5 が完全透明（＝「密度なし」と
// 見分けが付かない）になり、norm < 0.5 では**低いほど濃い**逆相関の領域ができる。
// 下の表明はこの 2 つの欠陥をそれぞれ性質として捕まえる（同語反復にしない）。
// ---------------------------------------------------------------------------
describe('heat_scale — 密度 norm から色への単調写像', () => {
  /** 代表点（下端・中央・上端を含む昇順の梯子）。 */
  const LADDER = [0, 0.1, 0.25, 0.5, 0.75, 0.9, 1];

  test('color_for_density_at_the_bottom_of_the_scale_still_leaves_visible_ink', () => {
    // Arrange: 量の下端 = 0。ここを完全透明にすると、目盛りの下端側（実測 norm 0.01 で
    //   不透明度 0.004）が地に沈んで**在るはずの量が読めない**（実UI実測 2026-09-06・
    //   依頼者承認）。norm = 0.0（窓内で滞在ゼロの bin）も到達可能な値で、下限により
    //   可視の線になる——版面の区別は「バー無し＝窓外（null）」「バー在り＝窓内」の 2 値
    //   であり、ゼロと極小の見分けより窓外との区別が本質（heat_scale.js の定数の記録参照）。
    // Act
    const alpha = alphaOf(heatScale.colorForDensity(0));

    // Assert: 見えること・下端であること（上限側の値は焼き込まない）。
    assert.ok(alpha > 0, '下端が完全透明では最小の量が読めません');
    assert.ok(alpha < alphaOf(heatScale.colorForDensity(0.1)), '下端が最小量より濃くなっています');
    assert.ok(alpha < alphaForP(0), '下限が濃さの上限に達しています');
  });

  test('color_for_density_keeps_the_floor_a_noticeable_share_of_the_scale_above_it', () => {
    // Arrange / Act: 目盛りの両端。
    const floor = alphaOf(heatScale.colorForDensity(0));
    const ceiling = alphaOf(heatScale.colorForDensity(1));

    // Assert: 下限は、その上に伸びる目盛りの幅に対して**無視できない比**であること。
    //
    // なぜ `floor < ceiling`（＝目盛りが幅を持つ）ではないのか: それは下の全対単調性
    //   `color_for_density_rises_strictly_with_the_density_over_the_whole_scale` が
    //   LADDER の両端（norm 0 と 1）について既に含意しており、新しく捕まえるものが無い。
    //
    // 捕まえたいのは別の壊れ方である: 下限が**在るのに効かない**（例えば 1e-6 まで弱まる）
    //   状態。このとき単調性も `floor < ceiling` も緑のままだが、norm の小さい行は地に沈み、
    //   下限を置いた目的（小さい量も「在る」ことが読める・heat_scale.js:95-103）が消える。
    //   よって下限の**絶対値**ではなく、目盛り幅に対する**比**で固定する。
    //   0.12 という現在値は焼き込まない——CSS 側と同じく、値は実装が唯一源である。
    const span = ceiling - floor;
    assert.ok(floor >= span * MIN_FLOOR_SHARE,
      `下限が目盛り幅に対して無視できる大きさです: 下限 ${floor} < ${span} × ${MIN_FLOOR_SHARE}`);
  });

  test('color_for_density_at_one_reaches_the_maximum_opacity', () => {
    // Arrange: 量の上端 = 1（POC の bin）。
    // Act
    const alpha = alphaOf(heatScale.colorForDensity(1));

    // Assert: 目盛りの端は p の写像の端と同じ上限（濃さの上限を 2 通りにしない）。
    assert.equal(alpha, alphaForP(0));
    assert.ok(alpha > 0);
  });

  test('color_for_density_rises_strictly_with_the_density_over_the_whole_scale', () => {
    // Arrange: 代表 7 点（≥ 3 点）。
    // Act
    const alphas = LADDER.map((norm) => alphaOf(heatScale.colorForDensity(norm)));

    // Assert: n1 < n2 ⟺ alpha(n1) < alpha(n2) を全対で表明する。
    //   「濃いほど量が多い」が目盛りの全域で成り立つこと＝逆相関の領域が無いこと。
    for (let i = 0; i < LADDER.length; i += 1) {
      for (let j = 0; j < LADDER.length; j += 1) {
        assert.equal(
          alphas[i] < alphas[j], LADDER[i] < LADDER[j],
          `濃さが量の順序を保っていません: norm ${LADDER[i]} と ${LADDER[j]}`,
        );
      }
    }
  });

  test('color_for_density_at_mid_scale_stays_visible_so_it_reads_as_a_quantity', () => {
    // Arrange: 双極写像ではここが完全透明になり「密度なし」と区別が付かなくなる。
    // Act
    const alpha = alphaOf(heatScale.colorForDensity(0.5));

    // Assert: 見えること、かつ端との間に順序があること。
    assert.ok(alpha > 0, 'norm = 0.5 が透明では「密度なし」と見分けが付きません');
    assert.ok(alpha < alphaOf(heatScale.colorForDensity(1)));
    assert.ok(alpha > alphaOf(heatScale.colorForDensity(0)));
  });

  test('color_for_density_uses_one_hue_so_only_the_opacity_carries_the_quantity', () => {
    // Arrange / Act: 色相だけを取り出す（不透明度を落とす）。
    const hueOf = (norm) => heatScale.colorForDensity(norm).replace(/,\s*[0-9.]+\s*\)$/, ')');

    // Assert: 量は向きを持たないので、色相は目盛りの全域で 1 つ。
    const hues = new Set(LADDER.map(hueOf));
    assert.equal(hues.size, 1, `密度の色相は 1 つであるべきです: ${[...hues].join(' / ')}`);
  });

  test('color_for_density_of_null_is_no_color_so_the_cell_stays_empty', () => {
    // Arrange: 素材なし・範囲外は「量 0」ではない（0 で埋めると最小密度と読める）。
    // Act / Assert
    assert.equal(heatScale.colorForDensity(null), NO_LEVEL_COLOR);
    assert.equal(heatScale.colorForDensity(undefined), NO_LEVEL_COLOR);
  });

  test('color_for_density_out_of_the_unit_range_is_refused', () => {
    // Arrange / Act / Assert: 定義が壊れた合図なので丸めない（colorForP と同じ流儀）。
    assert.throws(() => heatScale.colorForDensity(1.2), /heat_scale/);
    assert.throws(() => heatScale.colorForDensity(-0.1), /heat_scale/);
    assert.throws(() => heatScale.colorForDensity(Number.NaN), /heat_scale/);
    assert.throws(() => heatScale.colorForDensity('0.5'), /heat_scale/);
  });
});
