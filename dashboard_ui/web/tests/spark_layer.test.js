// spark_layer — 第 2 表セル背景の下層＝指標ミニ描画（依頼者指示 2026-09-04・同日明確化）。
//
// 設計入力: 背景は 2 層（上層＝ヒートストリップ・下層＝本モジュールの SVG 画像レイヤー）。
//   形は指標ペインと同じ読み（積み上がる量＝棒・それ以外＝ライン）。値はサーバ応答
//   そのもの＝フロントは数値を再計算しない（ここで行うのは表示スケーリングだけ）。
//
// 構造は AAA。テスト名は「対象_条件_期待結果」。

import { test, describe } from 'node:test';
import assert from 'node:assert/strict';

import { sparkLayer } from '../js/adapter/front/spark_layer.js';
import { CHART_COLORS } from '../js/adapter/front/heat_scale.js';

/** data URI から SVG 本文を取り出す（レイヤーの形も同時に固定する）。 */
function svgOf(layer) {
  const m = /^url\("data:image\/svg\+xml;utf8,(.+)"\)$/.exec(layer);
  assert.ok(m, `url("data:image/svg+xml;utf8,...") 形ではありません: ${layer}`);
  return decodeURIComponent(m[1]);
}

describe('spark_layer — 指標ミニ描画（背景の下層）', () => {
  test('a_line_spark_draws_one_polyline_point_per_interval_centre', () => {
    // Arrange: 4 区間（古い順）。Act
    const svg = svgOf(sparkLayer([10, 20, 30, 20], { bars: false }));
    // Assert: 区間の中心 x = 5, 15, 25, 35（ヒートの縞と同じ等分割＝縞と区間が揃う）。
    const points = /points="([^"]+)"/.exec(svg)[1].split(' ');
    assert.deepEqual(points.map((pt) => pt.split(',')[0]), ['5.00', '15.00', '25.00', '35.00']);
    // 最大値（30）が最上・最小値（10）が最下（min–max を版面高さへ写す）。
    const ys = points.map((pt) => Number(pt.split(',')[1]));
    assert.equal(Math.min(...ys), ys[2]);
    assert.equal(Math.max(...ys), ys[0]);
  });

  test('a_bar_spark_grows_from_the_zero_baseline_like_a_histogram', () => {
    // 指標ペインの tickvol と同じ読み: 基線は 0。値 2 倍 → 高さも 2 倍。
    const svg = svgOf(sparkLayer([50, 100], { bars: true }));
    const heights = [...svg.matchAll(/<rect [^>]*height="([0-9.]+)"/g)].map((m) => Number(m[1]));
    assert.equal(heights.length, 2);
    assert.ok(Math.abs(heights[1] - 2 * heights[0]) < 0.05, `高さ比が 2 倍でない: ${heights}`);
  });

  test('an_unreadable_interval_is_skipped_not_invented', () => {
    // null（値なし）の区間は点を発明しない（線はその点を飛ばす）。
    const svg = svgOf(sparkLayer([10, null, 30], { bars: false }));
    const points = /points="([^"]+)"/.exec(svg)[1].split(' ');
    assert.equal(points.length, 2);
  });

  test('fewer_than_two_readable_values_make_no_layer', () => {
    // 1 点では動きが描けない（レイヤーを発明しない）。
    assert.equal(sparkLayer([10], { bars: false }), '');
    assert.equal(sparkLayer([null, null, 10], { bars: true }), '');
    assert.equal(sparkLayer([], {}), '');
    assert.equal(sparkLayer(undefined, {}), '');
  });

  test('a_flat_series_is_drawn_at_mid_height_not_dropped', () => {
    // 全区間同値（span=0）でも「動きが無い」ことを描く（無言で消さない）。
    const svg = svgOf(sparkLayer([7, 7, 7], { bars: false }));
    const ys = /points="([^"]+)"/.exec(svg)[1].split(' ').map((pt) => Number(pt.split(',')[1]));
    assert.ok(ys.every((y) => y === 15), `中央高さでない: ${ys}`);
  });

  test('the_spark_uses_only_the_single_source_chart_colour', () => {
    // front の色定義は 1 冊に 1 つ（heat_scale.test.js の第 2 定義検査と対）。
    //   ヒートは色相＝量を担うので、下層は中間トーン 1 色（CHART_COLORS.text）。
    for (const bars of [false, true]) {
      assert.ok(svgOf(sparkLayer([1, 2, 3], { bars })).includes(CHART_COLORS.text));
    }
  });
});
