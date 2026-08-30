// timeframe_charts_view — 各時間足チャート一覧の版面（ISSUE-452 内容 2）。
//
// 固定する契約:
//   - 8 時間足のタイルを DASHBOARD_TIMEFRAMES の順で出す（1 時間足 = 1 枚）
//   - 水準線は /reach_sheet 応答の rows から**その時間足のぶんだけ**引く（再計算しない）
//   - 現在値の線は全タイル共通の 1 点（§4.1）
//   - ローソクは time 厳密増加へ整えてから流し込む（ISSUE-167）
//   - 失敗（lwc 不在・ローソク取得失敗・応答異常）は**文字で掲示**する（無言縮退の禁止）
//   - unmount は共有の器へ 1 要素も残さず、チャート実体も破棄する
//
// 構造は AAA。テスト名は「対象_条件_期待結果」。

import { test, describe } from 'node:test';
import assert from 'node:assert/strict';

import { fakeDoc, fakeEl, flatten, textOf, sheetResponse, ladderRow } from './_fake_dom.js';
import { fakeLwc } from './_fake_lwc.js';
import { createTimeframeChartsView, chartsLibUsable } from '../js/adapter/front/timeframe_charts_view.js';
import { DASHBOARD_TIMEFRAMES } from '../js/adapter/front/timeframes.js';

function harness() {
  const doc = fakeDoc();
  const host = fakeEl('div');
  const spy = fakeLwc();
  const view = createTimeframeChartsView({ doc, lwc: spy.lwc });
  view.mount(host);
  return { doc, host, spy, view };
}

/** タイルの一覧（DOM 上の並び順）。 */
function tilesOf(host) {
  return flatten(host).filter((el) => el.classList.contains('dash-chart-tile'));
}

describe('timeframe_charts_view — 版面と水準線', () => {
  test('mount_builds_one_tile_per_timeframe_in_display_order', () => {
    // Arrange / Act
    const h = harness();
    // Assert: 1 時間足 = 1 枚・並びは表示の唯一源（timeframes.js）と同一。
    const tiles = tilesOf(h.host);
    assert.deepEqual(tiles.map((tile) => tile.dataset.timeframe), [...DASHBOARD_TIMEFRAMES]);
    assert.equal(h.spy.charts.length, DASHBOARD_TIMEFRAMES.length);
    // 各チャートにローソク系列が 1 本ずつ。
    for (const chart of h.spy.charts) {
      assert.equal(chart.series.length, 1);
      assert.equal(chart.series[0].kind, h.spy.lwc.CandlestickSeries);
    }
  });

  test('mount_without_a_host_fails_closed', () => {
    const view = createTimeframeChartsView({ doc: fakeDoc(), lwc: fakeLwc().lwc });
    assert.throws(() => view.mount(null), /ホスト/);
  });

  test('render_draws_each_rows_level_only_on_its_own_timeframe_chart', () => {
    // Arrange
    const h = harness();
    const response = sheetResponse({
      rows: [
        ladderRow({ timeframe: '5m', price: 65803.4, label: 'cvfe 外側上 2σ', distance: 47.4 }),
        ladderRow({ timeframe: '1D', price: 66099.7, label: 'MA ema5 hlc3', distance: 343.7 }),
      ],
    });
    // Act
    h.view.render(response);
    // Assert: 5m のタイルには 5m の水準（＋現在値）だけ、1m のタイルには現在値だけ。
    const seriesOf = (tf) => h.spy.charts[DASHBOARD_TIMEFRAMES.indexOf(tf)].series[0];
    const titles = (tf) => seriesOf(tf).priceLines.map((line) => line.options.title).sort();
    assert.deepEqual(titles('5m'), ['cvfe 外側上 2σ', '現在値']);
    assert.deepEqual(titles('1D'), ['MA ema5 hlc3', '現在値']);
    assert.deepEqual(titles('1m'), ['現在値']);
    const level = seriesOf('5m').priceLines.find((line) => line.options.title !== '現在値');
    assert.equal(level.options.price, 65803.4);
  });

  test('the_current_price_line_is_the_same_single_point_on_every_tile', () => {
    // §4.1: 現在値は全時間足で同一の 1 点。
    const h = harness();
    // Act
    h.view.render(sheetResponse({ current_price: 65756.0 }));
    // Assert
    for (const chart of h.spy.charts) {
      const current = chart.series[0].priceLines.filter((line) => line.options.title === '現在値');
      assert.equal(current.length, 1);
      assert.equal(current[0].options.price, 65756.0);
    }
  });

  test('reached_and_pending_levels_take_the_support_and_resistance_colors', () => {
    // 意味はラダーの凡例と同じ（下＝到達済み＝支持側）。具体 hex は焼き込まず、
    //   ローソクの上昇色／下降色（同じ意味の唯一源）との一致で表明する。
    const h = harness();
    h.view.render(sheetResponse({
      rows: [
        ladderRow({ timeframe: '1m', price: 65700, label: '下の水準', distance: -56.0 }),
        ladderRow({ timeframe: '1m', price: 65800, label: '上の水準', distance: 44.0 }),
      ],
    }));
    const series = h.spy.charts[0].series[0];
    const lineBy = (title) => series.priceLines.find((line) => line.options.title === title);
    // Assert
    assert.equal(lineBy('下の水準').options.color, series.options.upColor);
    assert.equal(lineBy('上の水準').options.color, series.options.downColor);
  });

  test('render_with_a_failed_response_posts_the_reason_and_keeps_existing_lines', () => {
    const h = harness();
    h.view.render(sheetResponse({ rows: [ladderRow({ timeframe: '1m', label: 'L', price: 65700 })] }));
    const before = h.spy.charts[0].series[0].priceLines.length;
    // Act
    h.view.render({ ok: false, error: { type: 'TransportError', message: '接続できません' } });
    // Assert: 理由を掲示し、線は前回の正常応答のまま（失敗のたびの明滅を作らない）。
    assert.match(textOf(h.host), /接続できません/);
    assert.equal(h.spy.charts[0].series[0].priceLines.length, before);
  });

  test('set_candles_enforces_strictly_increasing_time_before_drawing', () => {
    // ISSUE-167: 重複 time が 1 本でも混じると lightweight-charts は毎フレーム throw する。
    const h = harness();
    // Act: 順不同＋重複 time（後着優先）。
    h.view.setCandles('5m', [
      { time: 120, open: 2, high: 3, low: 1, close: 2 },
      { time: 60, open: 1, high: 2, low: 1, close: 1 },
      { time: 120, open: 5, high: 6, low: 4, close: 5 },
    ]);
    // Assert
    const drawn = h.spy.charts[DASHBOARD_TIMEFRAMES.indexOf('5m')].series[0].data;
    assert.deepEqual(drawn.map((candle) => candle.time), [60, 120]);
    assert.equal(drawn[1].open, 5);
    // 本数はタイルの掲示にも出る（読込中の掲示を置き換える）。
    const tile = tilesOf(h.host)[DASHBOARD_TIMEFRAMES.indexOf('5m')];
    assert.match(textOf(tile), /2 本/);
  });

  test('set_candles_for_an_unknown_timeframe_fails_closed', () => {
    // 結線の取り違え（poller と表示の時間足集合のズレ）を無言で握り潰さない。
    const h = harness();
    assert.throws(() => h.view.setCandles('9x', []), /9x/);
  });

  test('a_candle_fetch_failure_is_posted_on_the_tile', () => {
    const h = harness();
    // Act
    h.view.setCandleError('1h', 'ローソクの供給元が応答しません（HTTP 404）');
    // Assert: 空チャートと区別できる文字で掲示する。
    const tile = tilesOf(h.host)[DASHBOARD_TIMEFRAMES.indexOf('1h')];
    assert.match(textOf(tile), /HTTP 404/);
    assert.ok(flatten(tile).some((el) => el.classList.contains('dash-chart-status-error')));
  });

  test('a_missing_chart_library_is_posted_instead_of_a_silent_blank', () => {
    // 単体ページ（live vendor 不在）で黙って空になると、失敗と区別が付かない。
    const doc = fakeDoc();
    const host = fakeEl('div');
    const view = createTimeframeChartsView({ doc, lwc: null });
    // Act
    view.mount(host);
    // Assert: 掲示があり、タイルは無く、render / setCandles は落ちない。
    assert.match(textOf(host), /lightweight-charts/);
    assert.equal(tilesOf(host).length, 0);
    assert.doesNotThrow(() => view.render(sheetResponse()));
    assert.doesNotThrow(() => view.setCandles('1m', []));
    assert.equal(chartsLibUsable(null), false);
  });

  test('unmount_removes_everything_and_disposes_every_chart', () => {
    const h = harness();
    h.view.render(sheetResponse({ rows: [ladderRow({ timeframe: '1m' })] }));
    // Act
    h.view.unmount();
    // Assert: 器へ 1 要素も残さない（sim と共有・ISSUE-460 の器規約）。チャート実体も破棄。
    assert.equal(h.host.children.length, 0);
    assert.equal(h.spy.stats.chartRemove, h.spy.charts.length);
    // unmount 後の遅延着弾は捨てる（unhandled rejection を作らない）。
    assert.doesNotThrow(() => h.view.setCandles('1m', []));
  });
});
