// oscillator_sheet_view — 第 2 表（オシレータ水準到達表）の版面。
//
// 設計入力:
//   §5.1 行 = 指標インスタンス / 列 = 表示時間足 8 列（MTF 共通列は無い）
//   §5.2 セル = 配色（連続量 `p`・**段の名前は表示しない**）＋現在値の数字（必ず併記）＋到達時刻。
//        **水準が存在しないセルは隠さない**。空欄にせず「水準なし」と明示する（無言の縮退を作らない）。
//   §5.3 / §5.4 濃さ = `p`（0 = 沈静 / 0.5 = 中立 / 1 = 過熱）。段の名前は出さない。
//   §5.3.2 GPD が当てはまらないセルは帯外を**単一色**にして目盛りが無いことを示す。
//   §9-5 / arch-spec T-11 到達時刻の表示粒度: **当日は時刻・過去日は相対表記**。
//   arch-spec §9 応答のフィールド名をそのまま読む（`value` / `p` / `tail_unscaled` /
//        `reach` / `unavailable_reason`）。フロントは数値を再計算しない。
//
// 構造は AAA。テスト名は「対象_条件_期待結果」。

import { test, describe } from 'node:test';
import assert from 'node:assert/strict';

import { fakeDoc, fakeEl, flatten, textOf, sheetResponse, oscCell } from './_fake_dom.js';
import { createOscillatorSheetView } from '../js/adapter/front/oscillator_sheet_view.js';
import { colorForP, tailUnscaledColor } from '../js/adapter/front/heat_scale.js';

/** 2026-08-29 12:00:00 UTC を「今」として固定する（実時計に依存させない）。 */
const NOW_UNIX = Date.UTC(2026, 7, 29, 12, 0, 0) / 1000;

function renderInto(response, { now = NOW_UNIX } = {}) {
  const doc = fakeDoc();
  const host = fakeEl('div');
  const view = createOscillatorSheetView({ doc, now: () => now });
  view.mount(host);
  view.render(response);
  return { doc, host, view };
}

/** 指標行 × 時間足で 1 セルを引く。 */
function cellAt(host, indicatorId, timeframe) {
  return flatten(host).find(
    (el) => el.classList.contains('dash-osc-cell')
      && el.dataset.indicator === indicatorId
      && el.dataset.timeframe === timeframe,
  );
}

const CELLS = [
  oscCell({ indicator_id: 'ma_marod', timeframe: '1m', value: 0.8, p: 0.31 }),
  oscCell({ indicator_id: 'ma_marod', timeframe: '1D', value: 4.2, p: 0.94, reach: { reached: true, since_time: NOW_UNIX - 3 * 86400, truncated: false } }),
  oscCell({ indicator_id: 'tickvol', timeframe: '1M', value: null, p: null, tail_unscaled: false, unavailable_reason: '観測 30 件未満' }),
  oscCell({ indicator_id: 'tickvol', timeframe: '1W', value: 812, p: 0.77, tail_unscaled: true }),
];

describe('oscillator_sheet_view — 第 2 表（オシレータ水準到達表）', () => {
  test('the_view_builds_its_own_dom_so_the_page_declares_no_table', () => {
    const { host } = renderInto(sheetResponse({ cells: CELLS }));
    assert.ok(flatten(host).some((el) => el.tagName === 'TABLE'));
  });

  test('rows_are_indicators_and_columns_are_the_eight_timeframes', () => {
    // §5.1: 行 = 指標インスタンス / 列 = 表示時間足 8 列。
    const { host } = renderInto(sheetResponse({ cells: CELLS }));
    const headers = flatten(host).filter((el) => el.tagName === 'TH' && el.dataset.timeframe);
    assert.deepEqual(headers.map((h) => h.dataset.timeframe), ['1m', '5m', '15m', '1h', '4h', '1D', '1W', '1M']);
    const rows = flatten(host).filter((el) => el.classList.contains('dash-osc-row'));
    assert.deepEqual(rows.map((r) => r.dataset.indicator), ['ma_marod', 'tickvol']);
  });

  test('a_cell_is_painted_by_the_same_single_heat_scale_as_the_first_table', () => {
    // §5.5.7: 配色の基準は両表とも §5.3 の `p` で、1 冊に 1 つ。
    const { host } = renderInto(sheetResponse({ cells: CELLS }));
    assert.equal(cellAt(host, 'ma_marod', '1m').style.backgroundColor, colorForP(0.31));
  });

  test('a_cell_always_prints_its_current_value_because_colour_cannot_be_read_as_a_quantity', () => {
    // §5.2:「色から絶対量は読めないため、**現在値の数字は必ず併記する**」。
    const { host } = renderInto(sheetResponse({ cells: CELLS }));
    assert.match(textOf(cellAt(host, 'ma_marod', '1m')), /0\.8/);
  });

  test('a_cell_shows_the_price_reaching_its_quantile_band_when_the_server_projects_one', () => {
    // 依頼者指示 2026-08-30: 分位水準に達したときの価格を表示（サーバの閉形式逆写像・
    //   フロントは数値を再計算しない）。表記は第 1 表と同じ唯一源（format.js）。
    const cells = [oscCell({ indicator_id: 'ma_marod', timeframe: '1m', value: 0.8, p: 0.31, level_price: 65930.55 })];
    const { host } = renderInto(sheetResponse({ cells }));
    const shown = flatten(cellAt(host, 'ma_marod', '1m'))
      .find((el) => el.classList.contains('dash-osc-level-price'));
    assert.ok(shown, '分位水準到達価格が表示されていません');
    assert.equal(textOf(shown), '65,930.6');
  });

  test('a_cell_without_a_projection_shows_no_level_price', () => {
    // 逆算できない instance（tickvol 等）は null＝出さない（値を発明しない）。
    const { host } = renderInto(sheetResponse({ cells: CELLS }));
    assert.equal(
      flatten(cellAt(host, 'tickvol', '1W')).some((el) => el.classList.contains('dash-osc-level-price')),
      false,
    );
  });

  test('a_cell_never_prints_the_name_of_a_band', () => {
    // §5.3 / §5.4:「**段の名前は出さない**」（v0.6.0 の依頼者裁定で廃止された語彙）。
    const { host } = renderInto(sheetResponse({ cells: CELLS }));
    const shown = textOf(host);
    for (const banned of ['帯内', '上帯超', '下帯超', 'ext 超', 'ext超']) {
      assert.equal(shown.includes(banned), false, `段の名前が出ています: ${banned}`);
    }
  });

  test('a_cell_without_a_level_says_so_instead_of_being_left_blank', () => {
    // §5.2:「**水準が存在しないセルは隠さない**。空欄にせず水準なしと明示する」。
    const { host } = renderInto(sheetResponse({ cells: CELLS }));
    const cell = cellAt(host, 'tickvol', '1M');
    assert.match(textOf(cell), /水準なし/);
    assert.equal(cell.style.backgroundColor, '');
  });

  test('a_cell_without_a_level_still_shows_the_reason_it_has_none', () => {
    // 無言の縮退を作らない（理由が読めれば設定の誤りと本数不足を区別できる）。
    const { host } = renderInto(sheetResponse({ cells: CELLS }));
    assert.match(cellAt(host, 'tickvol', '1M').title, /30 件未満/);
  });

  test('a_tail_unscaled_cell_uses_the_out_of_band_single_colour', () => {
    // §5.3.2: GPD が当てはまらない 7 セルは帯外を単一色にして「目盛りが無い」ことを示す。
    const { host } = renderInto(sheetResponse({ cells: CELLS }));
    const cell = cellAt(host, 'tickvol', '1W');
    assert.equal(cell.style.backgroundColor, tailUnscaledColor());
    assert.equal(cell.classList.contains('dash-osc-tail-unscaled'), true);
  });

  test('a_tail_unscaled_cell_still_prints_its_current_value', () => {
    const { host } = renderInto(sheetResponse({ cells: CELLS }));
    assert.match(textOf(cellAt(host, 'tickvol', '1W')), /812/);
  });

  test('a_missing_cell_for_a_timeframe_is_rendered_as_no_level_not_as_a_gap', () => {
    // 境界値: 応答にそのセルが無い（その足に instance が無い）。列は欠けさせない。
    const { host } = renderInto(sheetResponse({ cells: CELLS }));
    assert.match(textOf(cellAt(host, 'ma_marod', '4h')), /水準なし/);
  });

  // ---- §9-5 / T-11 到達時刻の表示粒度 ------------------------------------
  test('a_reach_time_from_today_is_shown_as_a_clock_time', () => {
    // 当日は時刻（§9-5 の括弧書き・arch-spec T-11）。
    const cells = [oscCell({ indicator_id: 'ma_marod', timeframe: '15m', p: 0.9, reach: { reached: true, since_time: Date.UTC(2026, 7, 29, 9, 12, 0) / 1000, truncated: false } })];
    const { host } = renderInto(sheetResponse({ cells }));
    assert.match(textOf(cellAt(host, 'ma_marod', '15m')), /09:12/);
  });

  test('a_reach_time_from_yesterday_is_shown_as_a_relative_phrase', () => {
    const cells = [oscCell({ indicator_id: 'ma_marod', timeframe: '4h', p: 0.9, reach: { reached: true, since_time: Date.UTC(2026, 7, 28, 22, 0, 0) / 1000, truncated: false } })];
    const { host } = renderInto(sheetResponse({ cells }));
    assert.match(textOf(cellAt(host, 'ma_marod', '4h')), /昨日/);
  });

  test('a_reach_time_from_several_days_ago_counts_the_days', () => {
    const { host } = renderInto(sheetResponse({ cells: CELLS }));
    assert.match(textOf(cellAt(host, 'ma_marod', '1D')), /3日前/);
  });

  test('a_cell_that_has_not_reached_its_level_shows_no_time', () => {
    // §5.2:「到達時刻は**帯を出ているときのみ**」。§5.4 の版面では「―」。
    const cells = [oscCell({ indicator_id: 'ma_marod', timeframe: '1m', p: 0.3, reach: { reached: false, since_time: 12345, truncated: false } })];
    const { host } = renderInto(sheetResponse({ cells }));
    const shown = textOf(cellAt(host, 'ma_marod', '1m'));
    assert.equal(/\d{2}:\d{2}/.test(shown), false);
    assert.match(shown, /―/);
  });

  test('a_reach_time_truncated_by_the_history_says_it_cannot_be_traced_further', () => {
    // ReachState.truncated: since_time は「これ以上遡れない」ことしか意味しない。
    //   断定して表示すると、履歴の外の到達を当日の到達と誤読させる。
    const cells = [oscCell({ indicator_id: 'ma_marod', timeframe: '1W', p: 0.95, reach: { reached: true, since_time: NOW_UNIX - 40 * 86400, truncated: true } })];
    const { host } = renderInto(sheetResponse({ cells }));
    assert.match(textOf(cellAt(host, 'ma_marod', '1W')), /以前/);
  });

  // ---- 失敗・再描画 -----------------------------------------------------
  test('a_failed_response_shows_its_reason_instead_of_an_empty_table', () => {
    const { host } = renderInto({ ok: false, error: { type: 'X', message: '取得できません' } });
    assert.match(textOf(host), /取得できません/);
  });

  test('rendering_twice_does_not_accumulate_rows', () => {
    const doc = fakeDoc();
    const host = fakeEl('div');
    const view = createOscillatorSheetView({ doc, now: () => NOW_UNIX });
    view.mount(host);
    view.render(sheetResponse({ cells: CELLS }));
    const afterFirst = flatten(host).filter((el) => el.classList.contains('dash-osc-row')).length;
    view.render(sheetResponse({ cells: CELLS }));
    assert.equal(flatten(host).filter((el) => el.classList.contains('dash-osc-row')).length, afterFirst);
  });

  test('unmount_removes_everything_the_view_built', () => {
    const doc = fakeDoc();
    const host = fakeEl('div');
    const view = createOscillatorSheetView({ doc, now: () => NOW_UNIX });
    view.mount(host);
    view.render(sheetResponse({ cells: CELLS }));
    view.unmount();
    assert.equal(host.children.length, 0);
  });

  test('rendering_before_mount_fails_closed', () => {
    const view = createOscillatorSheetView({ doc: fakeDoc(), now: () => NOW_UNIX });
    assert.throws(() => view.render(sheetResponse()), /mount/);
  });

  test('the_view_never_talks_to_the_network_itself', async () => {
    const { readFileSync } = await import('node:fs');
    const { fileURLToPath } = await import('node:url');
    const src = readFileSync(fileURLToPath(new URL('../js/adapter/front/oscillator_sheet_view.js', import.meta.url)), 'utf8')
      .replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '');
    assert.equal(/\bfetch\b|XMLHttpRequest|setInterval|setTimeout/.test(src), false);
  });
});
