// 計算量テスト（絶対命令 2026-08-28）— チャート一覧に**作ってから捨てる**描画が無いことを固定する。
//
// なぜ状態検証と別に要るか: /reach_sheet の応答は 1 秒周期で来る。毎描画で全価格線を
//   作り直す実装でも版面は正しいままなので、DOM やタイトルを見る検定は緑のまま浪費を保護する
//   （ISSUE-450 / ISSUE-257 と同型）。したがって**発行の回数**を数える。時間は測らない。
//
// 最小形: _fake_lwc.js の stats（createPriceLine / removePriceLine / applyOptions / setData への
//   呼び出し = 発行）を Test Spy にして、
//     発行した線 − 出力に残った線 = 0（余計に作って捨てた線が 1 本も無い）
//   を assert する。**回数そのものは期待値に焼き込まない**（焼き込むと浪費が仕様へ昇格する）。
//   期待値はすべて入力データから導く。
//
// 構造は AAA。テスト名は「対象_条件_期待結果」。

import { test, describe } from 'node:test';
import assert from 'node:assert/strict';

import { fakeDoc, fakeEl, sheetResponse, ladderRow } from './_fake_dom.js';
import { fakeLwc } from './_fake_lwc.js';
import { createTimeframeChartsView } from '../js/adapter/front/timeframe_charts_view.js';
import { DASHBOARD_TIMEFRAMES } from '../js/adapter/front/timeframes.js';

function harness() {
  const spy = fakeLwc();
  const view = createTimeframeChartsView({ doc: fakeDoc(), lwc: spy.lwc });
  view.mount(fakeEl('div'));
  return { spy, view };
}

/** 全時間足に n 本ずつ水準を持つ応答（入力から期待値を導くための素材）。 */
function responseWithLevels(perTimeframe, { pricesShiftedBy = 0 } = {}) {
  const rows = [];
  for (const timeframe of DASHBOARD_TIMEFRAMES) {
    for (let i = 0; i < perTimeframe; i += 1) {
      rows.push(ladderRow({
        timeframe,
        label: `水準 ${i}`,
        price: 65000 + i * 10 + pricesShiftedBy,
        distance: i * 10 - 20,
      }));
    }
  }
  return sheetResponse({ rows });
}

describe('charts_paint_complexity — 発行した線 − 出力に残った線 = 0', () => {
  test('issued_price_lines_minus_attached_price_lines_is_zero_after_render', () => {
    // Arrange
    const h = harness();
    // Act
    h.view.render(responseWithLevels(3));
    // Assert: 作った線（現在値含む）は 1 本残らず出力に居る＝作って捨てた線が無い。
    assert.equal(
      h.spy.stats.createPriceLine - h.spy.stats.removePriceLine,
      h.spy.attachedPriceLines(),
    );
    assert.equal(h.spy.stats.removePriceLine, 0);
  });

  test('rendering_the_same_response_again_issues_nothing', () => {
    // 応答は 1 秒周期で来る。内容が同じなら描画の発行は 0 でなければならない。
    const h = harness();
    const response = responseWithLevels(3);
    h.view.render(response);
    const before = h.spy.snapshot();
    // Act
    h.view.render(response);
    // Assert
    assert.deepEqual(h.spy.snapshot(), before);
  });

  test('issue_count_is_driven_by_content_change_not_by_render_count', () => {
    // オーダーの表明（2 点固定）: 同じ内容なら描画回数を増やしても発行は増えない。
    const run = (renders) => {
      const h = harness();
      const response = responseWithLevels(2);
      for (let i = 0; i < renders; i += 1) {
        h.view.render(response);
      }
      return h.spy.snapshot().createPriceLine;
    };
    // Act / Assert
    assert.equal(run(2), run(10));
  });

  test('a_moved_price_updates_the_line_in_place_instead_of_recreating_it', () => {
    // 水準はティックで動く。動くたびに作り直すと毎秒（水準数）本の線が捨てられる。
    const h = harness();
    h.view.render(responseWithLevels(3));
    const before = h.spy.snapshot();
    // Act: 全水準の価格だけが動く（ラベル＝同一の線）。
    h.view.render(responseWithLevels(3, { pricesShiftedBy: 1.5 }));
    // Assert: 作り直しは 0。動かした線の数は入力から導く（8 足 × 3 本・焼き込みではない）。
    const after = h.spy.snapshot();
    assert.equal(after.createPriceLine, before.createPriceLine);
    assert.equal(after.removePriceLine, before.removePriceLine);
    assert.equal(
      after.applyOptions - before.applyOptions,
      DASHBOARD_TIMEFRAMES.length * 3,
    );
  });

  test('a_removed_level_removes_only_that_line', () => {
    // 指名が変わる（§4.5）と水準は入れ替わる。消えた線**だけ**を消す。
    const h = harness();
    h.view.render(responseWithLevels(3));
    const before = h.spy.snapshot();
    // Act: 各時間足 3 本 → 2 本（末尾の 1 本が消える）。
    h.view.render(responseWithLevels(2));
    // Assert: 期待値は入力の差（8 足 × 1 本）から導く。
    const after = h.spy.snapshot();
    assert.equal(after.removePriceLine - before.removePriceLine, DASHBOARD_TIMEFRAMES.length);
    assert.equal(after.createPriceLine, before.createPriceLine);
    assert.equal(
      after.createPriceLine - after.removePriceLine,
      h.spy.attachedPriceLines(),
    );
  });

  test('render_never_touches_candle_data', () => {
    // ローソクの供給は candle_poller / setCandles の経路だけ。render が setData を呼ぶ実装は
    //   「毎秒ローソクを流し込み直す」浪費であり、見た目は正しいまま残る。
    const h = harness();
    h.view.setCandles('1m', [{ time: 60, open: 1, high: 2, low: 0.5, close: 1.5 }]);
    const before = h.spy.snapshot().setData;
    // Act
    for (let i = 0; i < 5; i += 1) {
      h.view.render(responseWithLevels(2));
    }
    // Assert
    assert.equal(h.spy.snapshot().setData, before);
  });

  test('set_candles_issues_exactly_one_set_data_per_delivery', () => {
    // 供給 1 回 = 流し込み 1 回（発行 − 使用 = 0 のローソク版）。
    const h = harness();
    const before = h.spy.snapshot().setData;
    // Act
    h.view.setCandles('1m', [{ time: 60, open: 1, high: 2, low: 0.5, close: 1.5 }]);
    h.view.setCandles('5m', [{ time: 300, open: 1, high: 2, low: 0.5, close: 1.5 }]);
    // Assert: 期待値は供給の回数（2 回）から導く。
    assert.equal(h.spy.snapshot().setData - before, 2);
  });
});
