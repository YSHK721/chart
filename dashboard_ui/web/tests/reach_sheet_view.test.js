// reach_sheet_view — 第 1 表（価格ラダー）の版面。
//
// 設計入力:
//   §4.7 版面（距離 / 価格 / 差 / 時間足 / 水準ラベル・地平 3 段の直上直下印・現在値の独立行）
//   §5.5.5 / §5.5.6（価格セルの背景を地平 3 段で 3 分割し、`p` を heat_scale で塗る。
//                    **数値は表示せず色だけ**。候補が無い地平は空にして色を置かない）
//   §7（`cvfe` は増分器が無く段 1 でしか更新されない。**隠さず**更新粒度を表示する）
//   arch-spec §9（応答のフィールド名をそのまま使う。フロントは数値を再計算しない）
//
// 版面の DOM は View が生成し所有する（index.html へ表を直書きしない・overlay_host.js 規約）。
//
// 構造は AAA。テスト名は「対象_条件_期待結果」。

import { test, describe } from 'node:test';
import assert from 'node:assert/strict';

import { fakeDoc, fakeEl, flatten, textOf, sheetResponse, ladderRow } from './_fake_dom.js';
import { createReachSheetView } from '../js/adapter/front/reach_sheet_view.js';
import { colorForP } from '../js/adapter/front/heat_scale.js';

/** 版面を組んで応答を 1 回描く（AAA の Arrange をまとめる）。 */
function renderInto(response) {
  const doc = fakeDoc();
  const host = fakeEl('div');
  const view = createReachSheetView({ doc });
  view.mount(host);
  view.render(response);
  return { doc, host, view };
}

/** 行要素（現在値行を含む・上から順）。 */
function rowsOf(host) {
  return flatten(host).filter((el) => el.classList.contains('dash-ladder-row'));
}

/** その行の 1 セルのテキスト。 */
function cellText(row, name) {
  const cell = flatten(row).find((el) => el.dataset.cell === name);
  return cell ? cell.textContent : null;
}

const THREE_ROWS = [
  ladderRow({ price: 66099.7, timeframe: '1D', label: 'MA ema5 hlc3', distance: 343.7, gap_to_previous: null, horizon_marks: ['long'], horizon_p: { short: 0.93, medium: 0.71, long: 0.88 } }),
  ladderRow({ price: 65770.7, timeframe: '5m', label: 'cvfe 内側上 1σ', distance: 14.7, gap_to_previous: 7.6, horizon_marks: ['short'], horizon_p: { short: 0.052, medium: 0.067, long: 0.126 } }),
  ladderRow({ price: 65754.5, timeframe: '1m', label: 'MA ema60 high', distance: -1.5, gap_to_previous: 31.6, horizon_marks: ['short'], horizon_p: { short: 0.052, medium: 0.067, long: 0.126 } }),
];

describe('reach_sheet_view — 第 1 表（価格ラダー）', () => {
  test('the_view_builds_its_own_dom_so_the_page_declares_no_table', () => {
    // Arrange / Act
    const { host } = renderInto(sheetResponse({ rows: THREE_ROWS, current_index: 2 }));
    // Assert: 器の中身は View が作る（index.html は空のまま）。
    assert.ok(flatten(host).some((el) => el.tagName === 'TABLE'));
  });

  test('every_ladder_row_shows_distance_price_gap_timeframe_and_label', () => {
    // §4.7 の版面の 5 要素。
    const { host } = renderInto(sheetResponse({ rows: THREE_ROWS, current_index: 2 }));
    const first = rowsOf(host).find((r) => !r.classList.contains('dash-ladder-current'));
    assert.equal(cellText(first, 'distance'), '+343.7');
    assert.equal(cellText(first, 'price'), '66,099.7');
    assert.equal(cellText(first, 'timeframe'), '1D');
    assert.equal(cellText(first, 'label'), 'MA ema5 hlc3');
  });

  test('the_gap_column_is_empty_on_the_top_row_because_there_is_no_row_above_it', () => {
    // gap_to_previous は「直前行との価格差」。先頭には直前行が無い（null）。
    const { host } = renderInto(sheetResponse({ rows: THREE_ROWS, current_index: 2 }));
    const [first, second] = rowsOf(host).filter((r) => !r.classList.contains('dash-ladder-current'));
    assert.equal(cellText(first, 'gap'), '');
    assert.equal(cellText(second, 'gap'), '差 7.6');
  });

  test('a_negative_distance_keeps_its_sign_so_below_levels_read_as_below', () => {
    const { host } = renderInto(sheetResponse({ rows: THREE_ROWS, current_index: 2 }));
    const below = rowsOf(host).filter((r) => !r.classList.contains('dash-ladder-current'))[2];
    assert.equal(cellText(below, 'distance'), '-1.5');
  });

  test('the_current_price_row_sits_at_the_index_the_server_reported', () => {
    // §4.1: 現在値は独立行として価格順の位置に入る。並びはサーバ側が単一ソース。
    const { host } = renderInto(sheetResponse({ rows: THREE_ROWS, current_index: 2, current_price: 65756.0 }));
    const rows = rowsOf(host);
    assert.equal(rows[2].classList.contains('dash-ladder-current'), true);
    assert.match(textOf(rows[2]), /現在値/);
    assert.match(textOf(rows[2]), /65,756\.0/);
  });

  test('the_current_price_row_is_the_only_one_of_its_kind', () => {
    const { host } = renderInto(sheetResponse({ rows: THREE_ROWS, current_index: 2 }));
    assert.equal(rowsOf(host).filter((r) => r.classList.contains('dash-ladder-current')).length, 1);
  });

  test('the_current_price_row_goes_last_when_every_level_is_above', () => {
    // 境界値: current_index = 行数（全水準が現在値より上）。
    const { host } = renderInto(sheetResponse({ rows: THREE_ROWS, current_index: 3 }));
    const rows = rowsOf(host);
    assert.equal(rows.length, 4);
    assert.equal(rows[3].classList.contains('dash-ladder-current'), true);
  });

  test('the_current_price_row_goes_first_when_every_level_is_below', () => {
    // 境界値: current_index = 0。
    const { host } = renderInto(sheetResponse({ rows: THREE_ROWS, current_index: 0 }));
    assert.equal(rowsOf(host)[0].classList.contains('dash-ladder-current'), true);
  });

  test('an_empty_ladder_still_shows_the_current_price_row', () => {
    // 境界値: 水準 0 本。空表にせず現在値だけは出す（無言の空白を作らない）。
    const { host } = renderInto(sheetResponse({ rows: [], current_index: 0 }));
    assert.equal(rowsOf(host).length, 1);
    assert.match(textOf(host), /現在値/);
  });

  test('a_horizon_mark_names_its_horizon_and_the_direction_from_the_current_price', () => {
    // §4.7: 「← 長期・上」「← 短期・下」。向きは距離の符号で決まる。
    const { host } = renderInto(sheetResponse({ rows: THREE_ROWS, current_index: 2 }));
    const levelRows = rowsOf(host).filter((r) => !r.classList.contains('dash-ladder-current'));
    assert.match(cellText(levelRows[0], 'marks'), /長期・上/);
    assert.match(cellText(levelRows[2], 'marks'), /短期・下/);
  });

  test('a_row_marked_for_two_horizons_shows_both', () => {
    // §4.7 の実測行「← 中期・下／長期・下」。
    const rows = [ladderRow({ distance: -209.1, horizon_marks: ['medium', 'long'] })];
    const { host } = renderInto(sheetResponse({ rows, current_index: 0 }));
    const marks = cellText(rowsOf(host).find((r) => !r.classList.contains('dash-ladder-current')), 'marks');
    assert.match(marks, /中期・下/);
    assert.match(marks, /長期・下/);
  });

  test('an_unmarked_row_shows_no_mark_at_all', () => {
    const rows = [ladderRow({ horizon_marks: [] })];
    const { host } = renderInto(sheetResponse({ rows, current_index: 0 }));
    assert.equal(cellText(rowsOf(host).find((r) => !r.classList.contains('dash-ladder-current')), 'marks'), '');
  });

  // ---- §5.5.5 / §5.5.6 背景 3 分割 --------------------------------------
  test('the_price_cell_carries_exactly_three_horizon_bands', () => {
    const { host } = renderInto(sheetResponse({ rows: THREE_ROWS, current_index: 2 }));
    const first = rowsOf(host).find((r) => !r.classList.contains('dash-ladder-current'));
    const bands = flatten(first).filter((el) => el.classList.contains('dash-ladder-band'));
    assert.deepEqual(bands.map((b) => b.dataset.horizon), ['short', 'medium', 'long']);
  });

  test('each_band_is_painted_by_the_single_heat_scale_from_the_p_the_server_sent', () => {
    // §5.5.5: 配色の基準は §5.3 の連続量 `p` をそのまま使う（1 冊に 1 つ）。
    const { host } = renderInto(sheetResponse({ rows: THREE_ROWS, current_index: 2 }));
    const first = rowsOf(host).find((r) => !r.classList.contains('dash-ladder-current'));
    const bands = flatten(first).filter((el) => el.classList.contains('dash-ladder-band'));
    assert.equal(bands[0].style.backgroundColor, colorForP(0.93));
    assert.equal(bands[1].style.backgroundColor, colorForP(0.71));
    assert.equal(bands[2].style.backgroundColor, colorForP(0.88));
  });

  test('the_bands_never_print_the_p_value_as_text', () => {
    // §5.5.6:「数値は表示せず**色だけ**にする」。
    const { host } = renderInto(sheetResponse({ rows: THREE_ROWS, current_index: 2 }));
    const first = rowsOf(host).find((r) => !r.classList.contains('dash-ladder-current'));
    for (const band of flatten(first).filter((el) => el.classList.contains('dash-ladder-band'))) {
      assert.equal(band.textContent, '');
    }
  });

  test('a_horizon_without_any_candidate_is_left_empty_instead_of_being_filled_with_neutral', () => {
    // §5.5.5:「候補が 1 つも残らないときは**空**にし、色を置かない（無言で 0.5 を埋めない）」。
    const rows = [ladderRow({ horizon_p: { short: 0.2, medium: null, long: null } })];
    const { host } = renderInto(sheetResponse({ rows, current_index: 0 }));
    const bands = flatten(rowsOf(host)[1]).filter((el) => el.classList.contains('dash-ladder-band'));
    assert.equal(bands[0].style.backgroundColor, colorForP(0.2));
    assert.equal(bands[1].style.backgroundColor, '');
    assert.equal(bands[2].style.backgroundColor, '');
  });

  test('a_missing_horizon_key_is_treated_as_no_candidate_not_as_an_error', () => {
    // 境界値: 応答にその地平のキー自体が無い（候補ゼロと同義）。
    const rows = [ladderRow({ horizon_p: { short: 0.2 } })];
    const { host } = renderInto(sheetResponse({ rows, current_index: 0 }));
    const bands = flatten(rowsOf(host)[1]).filter((el) => el.classList.contains('dash-ladder-band'));
    assert.equal(bands[1].style.backgroundColor, '');
  });

  test('an_unknown_horizon_key_is_surfaced_instead_of_silently_painting_nothing', () => {
    // 地平の名前の唯一源は dashboard_ui/domain/horizon.py の Horizon（short / medium / long）。
    //   arch-spec §9 の例示は `mid` と書いているが、同 §9 は「実際の enum 値名は
    //   horizon.py を読んで確定せよ」と定めており、enum が正である。
    //
    // なぜ検出するか: サーバ側が `mid` を出すと、中期の帯は**ただ色が付かないだけ**になる。
    //   背景は色しか出さない（§5.5.6「数値は表示せず色だけ」）ので、食い違いが版面から
    //   区別できない——「候補が無い地平」と同じ見た目になる。無言で通すと契約のズレが
    //   永久に見つからない。
    const rows = [ladderRow({ horizon_p: { short: 0.2, mid: 0.5, long: 0.9 } })];
    const { host } = renderInto(sheetResponse({ rows, current_index: 0 }));
    assert.match(textOf(host), /mid/);
  });

  test('the_known_horizon_keys_alone_raise_no_complaint', () => {
    // 恒真にしない: 正しいキーだけのときは何も言わない。
    const { host } = renderInto(sheetResponse({ rows: THREE_ROWS, current_index: 2 }));
    assert.equal(/未知の地平/.test(textOf(host)), false);
  });

  // ---- §7 更新粒度 ------------------------------------------------------
  test('a_bar_close_degradation_is_shown_instead_of_being_hidden', () => {
    // §7: `cvfe` は増分器が無く段 1 でしか更新されない。差を隠して「リアルタイム」と称さない。
    const response = sheetResponse({
      rows: THREE_ROWS, current_index: 2,
      degradations: [{ instance_key: ['cvfe', 'default', '{}', '5m'], granularity: 'bar_close', reason: '増分器が無い' }],
    });
    const { host } = renderInto(response);
    const notice = flatten(host).find((el) => el.classList.contains('dash-granularity-notice'));
    assert.ok(notice, '更新粒度の掲示がありません');
    assert.match(textOf(notice), /バー確定/);
    assert.match(textOf(notice), /cvfe/);
    assert.match(textOf(notice), /5m/);
  });

  test('no_degradation_means_no_notice_is_shown', () => {
    const { host } = renderInto(sheetResponse({ rows: THREE_ROWS, current_index: 2, degradations: [] }));
    assert.equal(flatten(host).some((el) => el.classList.contains('dash-granularity-notice')), false);
  });

  // ---- 失敗・再描画 -----------------------------------------------------
  test('a_failed_response_shows_its_reason_instead_of_an_empty_table', () => {
    // 無言縮退の禁止: 空表と「取れなかった」を区別できるようにする。
    const { host } = renderInto({ ok: false, error: { type: 'BindingMissing', message: '紐付けがありません' } });
    assert.match(textOf(host), /紐付けがありません/);
  });

  test('rendering_twice_does_not_accumulate_rows', () => {
    // 段 2 は毎ティック描き直す。積み上がると行が二重になる。
    const doc = fakeDoc();
    const host = fakeEl('div');
    const view = createReachSheetView({ doc });
    view.mount(host);
    // Act
    view.render(sheetResponse({ rows: THREE_ROWS, current_index: 2 }));
    const afterFirst = rowsOf(host).length;
    view.render(sheetResponse({ rows: THREE_ROWS, current_index: 2 }));
    // Assert
    assert.equal(rowsOf(host).length, afterFirst);
  });

  test('a_recovered_response_clears_the_previous_error_message', () => {
    const doc = fakeDoc();
    const host = fakeEl('div');
    const view = createReachSheetView({ doc });
    view.mount(host);
    view.render({ ok: false, error: { type: 'X', message: '一時的な失敗' } });
    // Act
    view.render(sheetResponse({ rows: THREE_ROWS, current_index: 2 }));
    // Assert
    assert.equal(/一時的な失敗/.test(textOf(host)), false);
  });

  test('unmount_removes_everything_the_view_built', () => {
    const doc = fakeDoc();
    const host = fakeEl('div');
    const view = createReachSheetView({ doc });
    view.mount(host);
    view.render(sheetResponse({ rows: THREE_ROWS, current_index: 2 }));
    // Act
    view.unmount();
    // Assert
    assert.equal(host.children.length, 0);
  });

  test('rendering_before_mount_fails_closed_rather_than_silently_dropping_the_sheet', () => {
    const view = createReachSheetView({ doc: fakeDoc() });
    assert.throws(() => view.render(sheetResponse()), /mount/);
  });

  test('the_view_never_talks_to_the_network_itself', async () => {
    // 表示層は描くだけ（発行は sheet_poller / reach_sheet_client の責務）。混ざると
    //   「描くたびに発行する」欠陥が入り込み、出力は正しいまま無駄が増える。
    const { readFileSync } = await import('node:fs');
    const { fileURLToPath } = await import('node:url');
    const src = readFileSync(fileURLToPath(new URL('../js/adapter/front/reach_sheet_view.js', import.meta.url)), 'utf8')
      .replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '');
    assert.equal(/\bfetch\b|XMLHttpRequest|setInterval|setTimeout/.test(src), false);
  });
});
