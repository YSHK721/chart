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
function renderInto(response, { periodAnnotator = null } = {}) {
  const doc = fakeDoc();
  const host = fakeEl('div');
  const view = createReachSheetView({ doc, periodAnnotator });
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
  ladderRow({ price: 66099.7, timeframe: '1D', label: 'MA ema5 hlc3', distance: 343.7, gap_to_previous: null, horizon_marks: ['long'], horizon_p: { short: 0.93, medium: 0.71, long: 0.88 }, naming: { name: 'MA', level: '', period: 5, source: 'hlc3', extra: '' } }),
  ladderRow({ price: 65770.7, timeframe: '5m', label: 'cvfe 内側上 1σ', distance: 14.7, gap_to_previous: 7.6, horizon_marks: ['short'], horizon_p: { short: 0.052, medium: 0.067, long: 0.126 }, naming: { name: 'cvfe', level: 'u1', period: 1329, source: null, extra: 'sigma_outer=3.0' } }),
  ladderRow({ price: 65754.5, timeframe: '1m', label: 'MA ema60 high', distance: -1.5, gap_to_previous: 31.6, horizon_marks: ['short'], horizon_p: { short: 0.052, medium: 0.067, long: 0.126 }, naming: { name: 'MA', level: '', period: 60, source: 'high', extra: '' } }),
];

describe('reach_sheet_view — 第 1 表（価格ラダー）', () => {
  test('the_view_builds_its_own_dom_so_the_page_declares_no_table', () => {
    // Arrange / Act
    const { host } = renderInto(sheetResponse({ rows: THREE_ROWS, current_index: 2 }));
    // Assert: 器の中身は View が作る（index.html は空のまま）。
    assert.ok(flatten(host).some((el) => el.tagName === 'TABLE'));
  });

  test('every_ladder_row_shows_distance_price_gap_timeframe_and_naming', () => {
    // 水準列は指標名 / 期間 / ソースの 3 列（依頼者指示 2026-08-30）。中身はサーバの
    //   `naming`（構造化）から読む。label は識別子であり版面には出さない。
    const { host } = renderInto(sheetResponse({ rows: THREE_ROWS, current_index: 2 }));
    const first = rowsOf(host).find((r) => !r.classList.contains('dash-ladder-current'));
    assert.equal(cellText(first, 'distance'), '+343.7');
    assert.equal(cellText(first, 'price'), '66,099.7');
    assert.equal(cellText(first, 'timeframe'), '1D');
    assert.equal(cellText(first, 'name'), 'MA');
    assert.equal(cellText(first, 'period'), '5');
    assert.equal(cellText(first, 'source'), 'hlc3');
  });

  test('the_level_cell_is_painted_by_its_defining_quantile_and_stays_blank_otherwise', () => {
    // 依頼者裁定 2026-08-30: 水準セルの背景は定義分位 p（heat_scale が唯一源）。
    //   p を持たない水準（σ 帯・mean）は色を置かない。
    const rows = [
      ladderRow({ price: 66298.9, timeframe: '15m', label: 'a', distance: 542.9, gap_to_previous: null, horizon_marks: [], horizon_p: {}, naming: { name: 'btlm_trail', level: 'q95', level_p: 0.95, period: 252, source: 'hlc3', extra: '' } }),
      ladderRow({ price: 66071.5, timeframe: '5m', label: 'b', distance: 315.5, gap_to_previous: null, horizon_marks: [], horizon_p: {}, naming: { name: 'btlm_trail', level: 'mean', level_p: null, period: 266, source: 'hlc3', extra: '' } }),
    ];
    const { host } = renderInto(sheetResponse({ rows, current_index: 2 }));
    const [q95Row, meanRow] = rowsOf(host).filter((r) => !r.classList.contains('dash-ladder-current'));
    const cellOf = (tr) => flatten(tr).find((el2) => el2.dataset && el2.dataset.cell === 'level');
    assert.match(cellOf(q95Row).style.backgroundColor, /^rgba\(/);
    assert.equal(cellOf(meanRow).style.backgroundColor, '');
  });

  test('the_level_part_of_the_series_shows_in_its_own_column', () => {
    // q95 等の水準も列へ分割（依頼者指示 2026-08-30）。
    const { host } = renderInto(sheetResponse({ rows: THREE_ROWS, current_index: 2 }));
    const second = rowsOf(host).filter((r) => !r.classList.contains('dash-ladder-current'))[1];
    assert.equal(cellText(second, 'name'), 'cvfe+1');   // 名前セル＝名前と +N 印のみ
    assert.equal(cellText(second, 'level'), 'u1');
  });

  test('a_row_without_structured_naming_still_shows_its_label', () => {
    // 旧応答（naming なし）でも情報を落とさない（指標名セルへ label をそのまま出す）。
    const rows = [ladderRow({ price: 66099.7, timeframe: '1D', label: 'MA ema5 hlc3', distance: 343.7, gap_to_previous: null, horizon_marks: [], horizon_p: {} })];
    const { host } = renderInto(sheetResponse({ rows, current_index: 1 }));
    const first = rowsOf(host).find((r) => !r.classList.contains('dash-ladder-current'));
    assert.equal(cellText(first, 'name'), 'MA ema5 hlc3');
  });

  test('extra_settings_collapse_into_a_count_mark_with_a_tooltip', () => {
    // k=v の羅列は伝わらない（依頼者指摘 2026-08-30）。版面は指標名＋「+N」だけにし、
    //   中身はツールチップ（title）が持つ。
    const rows = [ladderRow({ price: 66099.7, timeframe: '1h', label: 'x', distance: 10, gap_to_previous: null, horizon_marks: [], horizon_p: {}, naming: { name: 'btlm_trail_q95', period: 115, source: 'hlc3', extra: 'band_method=empirical q_out=0.999' } })];
    const { host } = renderInto(sheetResponse({ rows, current_index: 1 }));
    const first = rowsOf(host).find((r) => !r.classList.contains('dash-ladder-current'));
    const mark = first.querySelector('.dash-ladder-extra-mark');
    assert.equal(textOf(mark), '+2');
    assert.match(mark.title, /band_method=empirical q_out=0.999/);
    assert.doesNotMatch(cellText(first, 'name'), /band_method/);
  });

  test('the_period_cell_carries_the_preset_annotation_when_the_table_knows_the_bars', () => {
    // 期間の暦期間注記は注入された換算（唯一源 = period_presets.js）から。無注入なら本数のみ。
    const annotator = (timeframe, bars) => (timeframe === '5m' && bars === 1329 ? '1週' : null);
    const { host } = renderInto(
      sheetResponse({ rows: THREE_ROWS, current_index: 2 }), { periodAnnotator: annotator },
    );
    const second = rowsOf(host).filter((r) => !r.classList.contains('dash-ladder-current'))[1];
    assert.match(cellText(second, 'period'), /1329/);
    assert.match(textOf(second.querySelector('.dash-ladder-period-preset')), /1週/);
  });

  test('the_gap_column_is_empty_on_the_top_row_because_there_is_no_row_above_it', () => {
    // gap_to_previous は「直前行との価格差」。先頭には直前行が無い（null）。
    //   語（「差」）は列見出しの補足が担うので、欄には数値だけを置く（モックの i.gap）。
    const { host } = renderInto(sheetResponse({ rows: THREE_ROWS, current_index: 2 }));
    const [first, second] = rowsOf(host).filter((r) => !r.classList.contains('dash-ladder-current'));
    assert.equal(cellText(first, 'gap'), '');
    assert.equal(cellText(second, 'gap'), '7.6');
  });

  test('the_next_target_marks_have_their_own_column_apart_from_the_distance', () => {
    // 距離と次のターゲットも別列（依頼者指示 2026-08-30「距離 · 次のターゲットも各列に分離しろ」）。
    const { host } = renderInto(sheetResponse({ rows: THREE_ROWS, current_index: 2 }));
    const head = flatten(host).find((el) => el.tagName === 'TH' && el.dataset.cell === 'next');
    assert.ok(head, '次のターゲットの列見出しがありません');
    assert.match(textOf(head), /次のターゲット/);
    // 並びは 次のターゲット → 距離（依頼者指示「順番を逆に」）。
    const headRow = flatten(host).find((el) => el.tagName === 'TR');
    assert.deepEqual(
      [...headRow.children].slice(0, 2).map((th) => th.dataset.cell),
      ['next', 'distance'],
    );
    // 印は next 列のセルに居て、距離セルには同居しない。
    const first = rowsOf(host).find((r) => !r.classList.contains('dash-ladder-current'));
    const distanceCell = flatten(first).find((el) => el.classList.contains('dash-ladder-distance-cell'));
    assert.equal(flatten(distanceCell).some((el) => el.classList.contains('dash-ladder-marks')), false);
    const nextCell = flatten(first).find((el) => el.classList.contains('dash-ladder-next-cell'));
    assert.ok(flatten(nextCell).some((el) => el.classList.contains('dash-ladder-marks')));
    assert.match(textOf(nextCell), /長期 · 上/);   // THREE_ROWS[0] は long の印を持つ。
  });

  test('the_gap_has_its_own_column_whose_head_says_what_it_is', () => {
    // 差は独立列（依頼者指示 2026-08-30「価格と直前行の差を分離して各列に」）。
    //   数値だけの欄なので、意味（直前行との差）は列見出しが持つ。
    const { host } = renderInto(sheetResponse({ rows: THREE_ROWS, current_index: 2 }));
    const head = flatten(host).find((el) => el.tagName === 'TH' && el.dataset.cell === 'gap');
    assert.ok(head, '差の列見出しがありません');
    assert.match(textOf(head), /差/);
    assert.match(textOf(head), /直前行/);
    // 価格セルの中には差が同居しない（分離の表明）。
    const first = rowsOf(host).find((r) => !r.classList.contains('dash-ladder-current'));
    const priceCell = flatten(first).find((el) => el.classList.contains('dash-ladder-price'));
    assert.equal(flatten(priceCell).some((el) => el.dataset.cell === 'gap'), false);
  });

  test('a_level_below_the_current_price_is_marked_as_reached_and_one_above_is_not', () => {
    // モックの凡例:「現在値より下（到達済み＝支持側）／現在値より上（未到達＝抵抗側）」。
    //   判定はサーバが与えた距離の符号だけで行う（版面の意味を 2 つにしない）。
    const { host } = renderInto(sheetResponse({ rows: THREE_ROWS, current_index: 2 }));
    const levelRows = rowsOf(host).filter((r) => !r.classList.contains('dash-ladder-current'));
    assert.equal(levelRows[0].classList.contains('dash-ladder-pending'), true);
    assert.equal(levelRows[2].classList.contains('dash-ladder-hit'), true);
  });

  test('a_reached_row_shows_its_reach_time_between_gap_and_timeframe', () => {
    // 依頼者指示 2026-08-30:「差」と「時間足」の間に「到達時間」。表記 YYYY/MM/DD HH:MM:SS（UTC）。
    //   1767229323 = 2026-01-01 01:02:03 UTC（時刻系は marketdata と同じ UTC・§6.2 定義 C）。
    const rows = [ladderRow({ price: 65754.5, timeframe: '1m', label: 'a', distance: -1.5, gap_to_previous: null, horizon_marks: [], horizon_p: {}, reach: { reached: true, since_time: 1767229323, truncated: false } })];
    const { host } = renderInto(sheetResponse({ rows, current_index: 0 }));
    const row = rowsOf(host).find((r) => !r.classList.contains('dash-ladder-current'));
    assert.equal(cellText(row, 'reach_time'), '2026/01/01 01:02:03');
    // 列の位置＝差と時間足の間（見出し行の並びで表明する）。
    const heads = flatten(host)
      .filter((el) => el.tagName === 'TH' && el.dataset && el.dataset.cell)
      .map((el) => el.dataset.cell);
    const at = (cell) => heads.indexOf(cell);
    assert.ok(at('gap') >= 0 && at('gap') < at('reach_time') && at('reach_time') < at('timeframe'));
  });

  test('an_unreached_row_leaves_the_reach_time_blank_instead_of_inventing_one', () => {
    // 未到達（reached=false・since_time=null）は空欄。0 や現在時刻を埋めない（発明しない）。
    const { host } = renderInto(sheetResponse({ rows: THREE_ROWS, current_index: 2 }));
    const pending = rowsOf(host).filter((r) => !r.classList.contains('dash-ladder-current'))[0];
    assert.equal(cellText(pending, 'reach_time'), '');
  });

  test('a_truncated_reach_time_carries_a_qualifier_tooltip', () => {
    // 履歴の先頭で切れているときは「これ以前から到達している可能性」を title で限定する
    //   （§9-5: 縮退・不確かさを無言にしない）。
    const rows = [ladderRow({ price: 65754.5, timeframe: '1m', label: 'a', distance: -1.5, gap_to_previous: null, horizon_marks: [], horizon_p: {}, reach: { reached: true, since_time: 1767229323, truncated: true } })];
    const { host } = renderInto(sheetResponse({ rows, current_index: 0 }));
    const row = rowsOf(host).find((r) => !r.classList.contains('dash-ladder-current'));
    const cell = flatten(row).find((el) => el.dataset.cell === 'reach_time');
    assert.match(cell.title, /これ以前から到達/);
  });

  test('the_timeframe_of_a_row_carries_the_tone_of_its_place_in_the_display_order', () => {
    // モックの r0〜r7。並びの唯一源は timeframes.js（第 2 表の列と同じ）。
    const { host } = renderInto(sheetResponse({ rows: THREE_ROWS, current_index: 2 }));
    const levelRows = rowsOf(host).filter((r) => !r.classList.contains('dash-ladder-current'));
    const pillOf = (row) => flatten(row).find((el) => el.classList.contains('dash-tf-pill'));
    assert.equal(pillOf(levelRows[0]).classList.contains('dash-tf-r5'), true);   // 1D
    assert.equal(pillOf(levelRows[2]).classList.contains('dash-tf-r0'), true);   // 1m
  });

  test('an_unknown_timeframe_gets_no_tone_instead_of_being_snapped_to_a_nearby_one', () => {
    // 知らない足に近い番号を与えると、版面上は正しい足に見えたまま取り違える。
    const rows = [ladderRow({ timeframe: '7m' })];
    const { host } = renderInto(sheetResponse({ rows, current_index: 0 }));
    // 行のセル内のピルを見る（時間足の選択バーにも同クラスのピルが居るため、行に限定する）。
    const row = rowsOf(host).find((r) => !r.classList.contains('dash-ladder-current'));
    const pill = flatten(row).find((el) => el.classList.contains('dash-tf-pill'));
    assert.equal(textOf(pill), '7m');
    assert.equal(/dash-tf-r\d/.test(pill.className), false);
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
    // §4.7 の「次のターゲット」。向きは距離の符号で決まる。
    //   表記は旧版面の「← 長期・上」からモックの地平バッジ「長期 · 上」へ改めた
    //   （ISSUE-463・版面同期）。名指す内容（地平と向き）は変えていない。
    const { host } = renderInto(sheetResponse({ rows: THREE_ROWS, current_index: 2 }));
    const levelRows = rowsOf(host).filter((r) => !r.classList.contains('dash-ladder-current'));
    assert.match(cellText(levelRows[0], 'marks'), /長期 · 上/);
    assert.match(cellText(levelRows[2], 'marks'), /短期 · 下/);
  });

  test('a_row_marked_for_two_horizons_shows_both', () => {
    // §4.7 の実測行（中期・下 と 長期・下 が同じ行に付く）。
    const rows = [ladderRow({ distance: -209.1, horizon_marks: ['medium', 'long'] })];
    const { host } = renderInto(sheetResponse({ rows, current_index: 0 }));
    const row = rowsOf(host).find((r) => !r.classList.contains('dash-ladder-current'));
    assert.match(cellText(row, 'marks'), /中期 · 下/);
    assert.match(cellText(row, 'marks'), /長期 · 下/);
    // 地平ごとに別の印にする（モックの b.next.h1 / h2 / h3）。同じ見た目だと段が読めない。
    const badges = flatten(row).filter((el) => el.classList.contains('dash-ladder-next'));
    assert.deepEqual(
      badges.map((b) => (b.classList.contains('dash-ladder-next-h2') ? 'h2' : 'h3')),
      ['h2', 'h3'],
    );
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

  // §7 更新粒度・縮退の掲示（dash-granularity-notice）は依頼者指示 2026-08-30 で廃止。

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

  // ---- 現在値中心の窓（依頼者指示 2026-08-30: 本数を絞る・縦スクロール不要） ----
  test('a_long_ladder_is_windowed_around_the_current_row', () => {
    // Arrange: 100 本・現在値は 50 本目の位置。
    const many = Array.from({ length: 100 }, (_unused, i) =>
      ladderRow({ price: 70000 - i * 10, label: `L${i}`, distance: 50 - i }));
    // Act
    const { host } = renderInto(sheetResponse({ rows: many, current_index: 50 }));
    // Assert: 建つのは前後 15 本＋現在値行だけ。中心に現在値行が居る。
    const rows = rowsOf(host);
    assert.equal(rows.length, 31);
    assert.equal(rows[15].classList.contains('dash-ladder-current'), true);
    // 窓の直外の行は無く、窓の内の端は在る（切り出し位置の表明）。
    assert.equal(/L34\b/.test(textOf(host)), false);
    assert.equal(/L35\b/.test(textOf(host)), true);
    assert.equal(/L64\b/.test(textOf(host)), true);
    assert.equal(/L65\b/.test(textOf(host)), false);
  });

  test('the_window_note_posts_how_many_rows_continue_outside', () => {
    // 無言の縮退禁止: 窓の外の水準が「存在しない」と読める版面にしない。
    const many = Array.from({ length: 100 }, (_unused, i) =>
      ladderRow({ price: 70000 - i * 10, label: `L${i}`, distance: 50 - i }));
    const { host } = renderInto(sheetResponse({ rows: many, current_index: 50 }));
    // Assert
    assert.match(textOf(host), /全 100 本中/);
    assert.match(textOf(host), /上に 35 本・下に 35 本/);
  });

  test('a_short_ladder_shows_everything_and_posts_no_window_note', () => {
    const { host } = renderInto(sheetResponse({ rows: THREE_ROWS, current_index: 2 }));
    assert.equal(rowsOf(host).length, 4);
    assert.equal(/全 3 本中/.test(textOf(host)), false);
  });

  test('a_window_at_the_top_edge_keeps_the_current_row_and_posts_only_the_lower_rest', () => {
    // 境界値: 現在値が先頭側（上に隠す行が無い）。
    const many = Array.from({ length: 40 }, (_unused, i) =>
      ladderRow({ price: 70000 - i * 10, label: `L${i}`, distance: 2 - i }));
    const { host } = renderInto(sheetResponse({ rows: many, current_index: 2 }));
    const rows = rowsOf(host);
    assert.equal(rows[2].classList.contains('dash-ladder-current'), true);
    assert.match(textOf(host), /上に 0 本・下に 23 本/);
  });

  test('window_build_cost_does_not_grow_with_the_ladder_length', () => {
    // 計算量（絶対命令 §4.1・2 点固定）: 窓の外は**建てない**。入力を倍にしても
    //   建つ行数は変わらない（建ててから隠すと捨てる色計算が毎描画発生する）。
    const build = (n) => {
      const many = Array.from({ length: n }, (_unused, i) =>
        ladderRow({ price: 70000 - i * 10, label: `L${i}`, distance: 50 - i }));
      const { host } = renderInto(sheetResponse({ rows: many, current_index: Math.floor(n / 2) }));
      return rowsOf(host).length;
    };
    assert.equal(build(100), build(200));
  });

  // ---- 現在値行のティック方向（依頼者指示 2026-08-30: ラベル削除・色で上下） ----
  const currentRowOf = (host) => rowsOf(host).find((r) => r.classList.contains('dash-ladder-current'));

  test('the_current_row_carries_no_explanatory_label_any_more', () => {
    // 「全時間足で同一の 1 点」は出さない（依頼者指示: ラベル削除）。
    const { host } = renderInto(sheetResponse({ rows: THREE_ROWS, current_index: 2 }));
    assert.equal(/全時間足で同一/.test(textOf(host)), false);
    // 行の構造（現在値・価格）は保つ。
    assert.match(textOf(currentRowOf(host)), /現在値/);
  });

  test('a_price_update_flashes_the_direction_colour_and_clears_after_the_css_fade', () => {
    // 依頼者指示 2026-08-31: 更新頻度を方向色の濃度で表現し、2 秒でフェードアウト。
    //   1 秒の視覚フェードは CSS（dash-tick-fade）が担い、クラスの後始末は animationend。
    const doc = fakeDoc();
    const host = fakeEl('div');
    const view = createReachSheetView({ doc, now: () => 1000 });
    view.mount(host);
    view.render(sheetResponse({ rows: THREE_ROWS, current_index: 2, current_price: 65756.0 }));
    let row = currentRowOf(host);
    assert.equal(row.classList.contains('dash-ladder-current-up'), false);
    // Act: 上へ動く → 濃度付きで点く。
    view.render(sheetResponse({ rows: THREE_ROWS, current_index: 2, current_price: 65758.5 }));
    row = currentRowOf(host);
    assert.equal(row.classList.contains('dash-ladder-current-up'), true);
    assert.equal(Number(row.style['--tick-strength']) >= 25, true);
    // Act: 下へ動くと色相が切り替わる。
    view.render(sheetResponse({ rows: THREE_ROWS, current_index: 2, current_price: 65750.0 }));
    row = currentRowOf(host);
    assert.equal(row.classList.contains('dash-ladder-current-down'), true);
    assert.equal(row.classList.contains('dash-ladder-current-up'), false);
    // Act: フェード完了（animationend）でクラスが外れる（タイマーも再描画も要らない）。
    (row._listeners.animationend || []).forEach((fn) => fn({}));
    assert.equal(row.classList.contains('dash-ladder-current-down'), false);
  });

  test('the_flash_density_grows_with_the_update_frequency', () => {
    // 濃度＝更新頻度（依頼者指示 2026-08-31。動きの大きさではない）。同じ観測窓に更新が
    //   多いほど濃い。上限 100・下限 25（動いたことが見える最小濃度）。
    const strengthAfterUpdates = (count) => {
      const doc = fakeDoc();
      const host = fakeEl('div');
      const view = createReachSheetView({ doc, now: () => 1000 });
      view.mount(host);
      view.render(sheetResponse({ rows: THREE_ROWS, current_index: 2, current_price: 65756.0 }));
      view.updateCurrentPrice(65756.0);   // シード。
      for (let i = 1; i <= count; i += 1) {
        view.updateCurrentPrice(65756.0 + i);
      }
      return Number(currentRowOf(host).style['--tick-strength']);
    };
    const sparse = strengthAfterUpdates(1);
    const busy = strengthAfterUpdates(6);
    const saturated = strengthAfterUpdates(40);
    assert.equal(sparse >= 25, true, '疎な更新が無色です（動いたことが見えない）');
    assert.equal(busy > sparse, true, `濃度が頻度に追随していません: ${sparse} → ${busy}`);
    assert.equal(saturated, 100);   // 上限で飽和。
  });

  test('the_tick_fade_lives_in_css_as_a_two_second_animation_with_density_mixing', async () => {
    // 時間の唯一源は CSS（2s・依頼者指示 2026-08-31 で 1 秒 → 2 秒）。濃度は --tick-strength の
    //   混色（頻度を色の濃さとして読む）。
    const { readFileSync } = await import('node:fs');
    const { fileURLToPath } = await import('node:url');
    const css = readFileSync(fileURLToPath(new URL('../css/dashboard.css', import.meta.url)), 'utf8');
    assert.match(css, /animation:\s*dash-tick-fade\s+2s/);
    assert.match(css, /--tick-strength/);
  });

  test('the_current_row_stamps_the_last_update_time_from_the_injected_clock', () => {
    // 依頼者指示 2026-08-31: 現在値行へ UPDATE:yyyy/mm/dd hh:mm:ss。時計は注入
    //   （View は時計を持たない規約のまま）。表記は到達時間と同じ唯一源＝UTC。
    //   1767229323 = 2026-01-01 01:02:03 UTC。
    const doc = fakeDoc();
    const host = fakeEl('div');
    let clockAt = 1767229323;
    const view = createReachSheetView({ doc, now: () => clockAt });
    view.mount(host);
    // 初回の観測も「更新」として記録する（開いた直後から空欄にしない）。
    view.render(sheetResponse({ rows: THREE_ROWS, current_index: 2, current_price: 65756.0 }));
    assert.match(textOf(currentRowOf(host)), /UPDATE:2026\/01\/01 01:02:03/);
    // 価格が変わらない描画では時刻を進めない（最終「更新」の時刻であって描画の時刻ではない）。
    clockAt = 1767229324;
    view.render(sheetResponse({ rows: THREE_ROWS, current_index: 2, current_price: 65756.0 }));
    assert.match(textOf(currentRowOf(host)), /UPDATE:2026\/01\/01 01:02:03/);
    // 価格が変わった描画で時刻が進む。
    clockAt = 1767229325;
    view.render(sheetResponse({ rows: THREE_ROWS, current_index: 2, current_price: 65757.0 }));
    assert.match(textOf(currentRowOf(host)), /UPDATE:2026\/01\/01 01:02:05/);
  });

  test('a_view_without_a_clock_shows_no_update_stamp_instead_of_a_wrong_one', () => {
    // 時計が注入されない環境では欄を出さない（発明しない）。
    const { host } = renderInto(sheetResponse({ rows: THREE_ROWS, current_index: 2, current_price: 65756.0 }));
    assert.doesNotMatch(textOf(currentRowOf(host)), /UPDATE:/);
  });

  // ---- 次のターゲット印の移動先の残光（依頼者承認 2026-08-31） ----

  /** 印が行 A（先頭）に付いた応答と、行 B（2 行目）へ移った応答（構造は同一）。 */
  const markedRows = (ownerLabel) => [
    ladderRow({ price: 66000, timeframe: '1h', label: 'A', distance: 244,
      horizon_marks: ownerLabel === 'A' ? ['medium'] : [], horizon_p: {} }),
    ladderRow({ price: 65900, timeframe: '1h', label: 'B', distance: 144,
      horizon_marks: ownerLabel === 'B' ? ['medium'] : [], horizon_p: {} }),
  ];
  const nextCellOf = (host, label) => {
    const row = rowsOf(host).find((r) => !r.classList.contains('dash-ladder-current')
      && (flatten(r).find((el) => el.dataset.cell === 'name') || {}).textContent === label);
    return flatten(row).find((el) => el.classList.contains('dash-ladder-next-cell'));
  };

  test('a_mark_move_glows_the_whole_destination_row_after_the_playback_delay', () => {
    // 依頼者指示 2026-08-31:「移動した価格帯の行全体に色を乗せてフェードアウト」。
    //   表示は 12 秒遅延の再生系列なので、発光も検出＋12 秒で点く（同期しないと画面上の
    //   跨ぎより 12 秒早く点いて、跨ぐ頃には消えている——実測で体感できなかった原因）。
    let clockAt = 1000;
    const doc = fakeDoc();
    const host = fakeEl('div');
    const view = createReachSheetView({ doc, now: () => clockAt });
    view.mount(host);
    view.render(sheetResponse({ rows: markedRows('A'), current_index: 2 }));
    // Act: 印が B へ移る（検出時刻 1003 → 表示時刻 1015）。
    clockAt = 1003;
    view.render(sheetResponse({ rows: markedRows('B'), current_index: 2 }));
    const rowOfB = () => rowsOf(host).find((r) => !r.classList.contains('dash-ladder-current')
      && (flatten(r).find((el) => el.dataset.cell === 'name') || {}).textContent === 'B');
    assert.equal(rowOfB().classList.contains('dash-ladder-row-moved'), false);   // まだ点かない。
    // Act: 表示時刻に達した tick 適用の契機で行全体が点く（render を待たない）。
    clockAt = 1016;
    view.updateCurrentPrice(65756.0);   // 再生開始（externalPrice のシード）＝tick 契機。
    const glowing = rowOfB();
    assert.equal(glowing.classList.contains('dash-ladder-row-moved'), true);
    assert.equal(glowing.style['--row-glow-delay'], '-1s');   // 表示時刻から 1 秒経過を引き継ぐ。
    const rowOfA = rowsOf(host).find((r) => !r.classList.contains('dash-ladder-current')
      && (flatten(r).find((el) => el.dataset.cell === 'name') || {}).textContent === 'A');
    assert.equal(rowOfA.classList.contains('dash-ladder-row-moved'), false);
  });

  test('a_rebuild_during_the_glow_resumes_the_fade_instead_of_restarting_it', () => {
    let clockAt = 1000;
    const doc = fakeDoc();
    const host = fakeEl('div');
    const view = createReachSheetView({ doc, now: () => clockAt });
    view.mount(host);
    view.render(sheetResponse({ rows: markedRows('A'), current_index: 2 }));
    clockAt = 1003;
    view.render(sheetResponse({ rows: markedRows('B'), current_index: 2 }));   // 表示時刻 1015。
    // Act: 表示中（1018）に別の内容変化で再構築 → 経過 3 秒を負の delay で引き継ぐ。
    clockAt = 1018;
    view.render(sheetResponse({ rows: markedRows('B'), current_index: 2, current_price: 65801.0 }));
    const rowOfB = () => rowsOf(host).find((r) => !r.classList.contains('dash-ladder-current')
      && (flatten(r).find((el) => el.dataset.cell === 'name') || {}).textContent === 'B');
    assert.equal(rowOfB().classList.contains('dash-ladder-row-moved'), true);
    assert.equal(rowOfB().style['--row-glow-delay'], '-3s');
    // Act: 表示時刻＋8 秒（1023）以降は乗せ直さない（終わった効果の再適用＝再点滅を作らない）。
    clockAt = 1024;
    view.render(sheetResponse({ rows: markedRows('B'), current_index: 2 }));
    assert.equal(rowOfB().classList.contains('dash-ladder-row-moved'), false);
  });

  test('the_first_response_and_a_stationary_mark_do_not_glow', () => {
    // 初回応答は基準（移動ではない）。動かない印も光らせない。時計の無い環境も光らせない。
    let clockAt = 1000;
    const doc = fakeDoc();
    const host = fakeEl('div');
    const view = createReachSheetView({ doc, now: () => clockAt });
    view.mount(host);
    view.render(sheetResponse({ rows: markedRows('A'), current_index: 2 }));
    clockAt = 1020;
    view.render(sheetResponse({ rows: markedRows('A'), current_index: 2, current_price: 65801.0 }));
    const rowOfA = rowsOf(host).find((r) => !r.classList.contains('dash-ladder-current')
      && (flatten(r).find((el) => el.dataset.cell === 'name') || {}).textContent === 'A');
    assert.equal(rowOfA.classList.contains('dash-ladder-row-moved'), false);
    const { host: noClock } = renderInto(sheetResponse({ rows: markedRows('A'), current_index: 2 }));
    const bare = rowsOf(noClock).find((r) => !r.classList.contains('dash-ladder-current'));
    assert.equal(bare.classList.contains('dash-ladder-row-moved'), false);
  });

  test('the_row_glow_blinks_three_times_over_eight_seconds_as_an_overlay', async () => {
    // 時間の唯一源は CSS（8s）。
    const { readFileSync } = await import('node:fs');
    const { fileURLToPath } = await import('node:url');
    const css = readFileSync(fileURLToPath(new URL('../css/dashboard.css', import.meta.url)), 'utf8');
    // 点滅フェードアウト（依頼者指示 2026-08-31: 点灯→フェードを 3 回）。1 周期 2.667s × 3
    //   ＝ 全体 8 秒（View の賞味期限と一致）。
    assert.match(css, /animation:\s*dash-row-glow\s+2\.667s\s+ease-out\s+3/);
    // オーバーレイ（::after）方式であること（子要素の背景に隠されない・実測 2026-08-31）。
    assert.match(css, /dash-ladder-row-moved t[hd]::after/);
  });

  // ---- なめらか tick 再生（依頼者指示 2026-08-31: ライブチャート仕様に合わせる） ----

  test('a_smooth_tick_rewrites_the_current_row_in_place_without_rebuilding_the_table', () => {
    // 100ms 粒度の tick 適用で表全体を作り直すと 1 秒あたり最大 10 回の全再構築になる。
    //   触ってよいのは現在値行の文字と効果クラスだけ（作ってから捨てる仕事を生まない）。
    const doc = fakeDoc();
    const host = fakeEl('div');
    const view = createReachSheetView({ doc, now: () => 1767229323 });
    view.mount(host);
    view.render(sheetResponse({ rows: THREE_ROWS, current_index: 2, current_price: 65756.0 }));
    const rowBefore = currentRowOf(host);
    const levelRowsBefore = rowsOf(host).filter((r) => r !== rowBefore);
    // Act: シード（初回・変化なし扱い）→ 変化。
    view.updateCurrentPrice(65756.0);
    view.updateCurrentPrice(65761.0);
    // Assert: 現在値行は**同じ要素のまま**中身が変わる（行の置換も表の再構築もしない）。
    const rowAfter = currentRowOf(host);
    assert.equal(rowAfter, rowBefore);
    assert.match(textOf(rowAfter), /65,761\.0/);
    assert.equal(rowAfter.classList.contains('dash-ladder-current-up'), true);
    assert.match(textOf(rowAfter), /UPDATE:2026\/01\/01 01:02:03/);
    rowsOf(host).filter((r) => r !== rowAfter).forEach((row, i) => {
      assert.equal(row, levelRowsBefore[i]);   // 水準行はノードごと不変。
    });
  });

  test('a_tick_with_the_same_price_touches_no_dom_at_all', () => {
    // 同値 tick は何も作らない（発行した仕事 − 使った仕事 = 0・絶対命令 §4.1）。
    const doc = fakeDoc();
    const host = fakeEl('div');
    const view = createReachSheetView({ doc, now: () => 1767229323 });
    view.mount(host);
    view.render(sheetResponse({ rows: THREE_ROWS, current_index: 2, current_price: 65756.0 }));
    view.updateCurrentPrice(65761.0);
    const sizeBefore = flatten(host).length;
    const classBefore = currentRowOf(host).className;
    view.updateCurrentPrice(65761.0);
    view.updateCurrentPrice(65761.0);
    assert.equal(flatten(host).length, sizeBefore);
    assert.equal(currentRowOf(host).className, classBefore);
  });

  test('the_first_smooth_price_stamps_the_time_but_does_not_flash', () => {
    // 初回の外部価格はシード（12 秒遅延の系列の起点）。応答の価格と比べて偽の発光を作らない。
    const doc = fakeDoc();
    const host = fakeEl('div');
    const view = createReachSheetView({ doc, now: () => 1767229323 });
    view.mount(host);
    view.render(sheetResponse({ rows: THREE_ROWS, current_index: 2, current_price: 65756.0 }));
    view.updateCurrentPrice(65740.0);   // 応答の価格より 16 点下（遅延系列の初回）。
    const row = currentRowOf(host);
    assert.equal(row.classList.contains('dash-ladder-current-down'), false);
    assert.equal(row.classList.contains('dash-ladder-current-up'), false);
    assert.match(textOf(row), /65,740\.0/);
    assert.match(textOf(row), /UPDATE:/);
  });

  test('once_smooth_playback_starts_the_response_price_no_longer_writes_the_display', () => {
    // 外部価格が唯一の書き手（参照実装 LiveTickPlayer の suppressPriceUpdate と同じ規約）。
    //   応答（遅延なし）と再生（12 秒遅延）を比較すると毎描画が偽の更新になるため、
    //   応答描画は行の構成だけを更新し、価格の表示と効果には触らない。
    const doc = fakeDoc();
    const host = fakeEl('div');
    const view = createReachSheetView({ doc, now: () => 1767229323 });
    view.mount(host);
    view.render(sheetResponse({ rows: THREE_ROWS, current_index: 2, current_price: 65756.0 }));
    view.updateCurrentPrice(65740.0);
    view.updateCurrentPrice(65742.0);   // 上へ動いた → 緑。
    // Act: 応答の価格が動いても表示は外部価格のまま・向きも外部系列のまま。
    view.render(sheetResponse({ rows: THREE_ROWS, current_index: 2, current_price: 65800.0 }));
    let row = currentRowOf(host);
    assert.match(textOf(row), /65,742\.0/);
    assert.doesNotMatch(textOf(row), /65,800\.0/);
    assert.equal(row.classList.contains('dash-ladder-current-up'), true);
    // Act: 動きの無い次の描画周期でも地色は保たれる（状態・中間色なし）。
    view.render(sheetResponse({ rows: THREE_ROWS, current_index: 2, current_price: 65800.0 }));
    row = currentRowOf(host);
    assert.equal(row.classList.contains('dash-ladder-current-up'), true);
  });

  test('smooth_level_values_rewrite_price_distance_and_gap_in_place', () => {
    // 依頼者指示 2026-08-31: 距離・価格・差もライブチャートと同じ更新粒度。
    //   式はサーバの参照定義（domain/price_ladder.py）: 距離 = 水準 − 現在値 /
    //   差 = 直前行の水準 − 自行の水準。材料（水準＝tails・現在値＝再生価格）はサーバ計算。
    const rows = [
      ladderRow({ price: 66000.0, timeframe: '1h', label: 'A', distance: 244.0, gap_to_previous: null, horizon_marks: [], horizon_p: {}, instance_key: ['ma', 'default', '{}', '1h'], series: 'MA' }),
      ladderRow({ price: 65900.0, timeframe: '1h', label: 'B', distance: 144.0, gap_to_previous: 100.0, horizon_marks: [], horizon_p: {}, instance_key: ['ma9', 'default', '{}', '1h'], series: 'MA' }),
    ];
    const doc = fakeDoc();
    const host = fakeEl('div');
    const view = createReachSheetView({ doc, now: () => 1767229323 });
    view.mount(host);
    view.render(sheetResponse({ rows, current_index: 2, current_price: 65756.0 }));
    const rowNodes = rowsOf(host).filter((r) => !r.classList.contains('dash-ladder-current'));
    // Act 1: 現在値だけ動く → 全行の距離が追随（行の作り直しなし）。
    view.updateCurrentPrice(65756.0);
    view.updateCurrentPrice(65746.0);
    assert.equal(cellText(rowNodes[0], 'distance'), '+254.0');
    assert.equal(cellText(rowNodes[1], 'distance'), '+154.0');
    assert.deepEqual(rowsOf(host).filter((r) => !r.classList.contains('dash-ladder-current')), rowNodes);
    // Act 2: 水準（tails）が動く → 価格・距離・差が追随。
    view.updateLevelValues((instanceKey, series) => (
      instanceKey[0] === 'ma' && series === 'MA' ? 66010.0 : undefined
    ));
    assert.equal(cellText(rowNodes[0], 'price'), '66,010.0');
    assert.equal(cellText(rowNodes[0], 'distance'), '+264.0');
    assert.equal(cellText(rowNodes[1], 'gap'), '110.0');   // 直前行の水準が動いた＝差も動く。
    // 動かなかった行の水準はそのまま。
    assert.equal(cellText(rowNodes[1], 'price'), '65,900.0');
  });

  test('smooth_level_values_do_nothing_before_playback_starts', () => {
    // 外部価格（再生系列）が無い間は流さない（応答の 1s 表示のまま・値の系列を混ぜない）。
    const { host, view } = renderInto(sheetResponse({ rows: THREE_ROWS, current_index: 2, current_price: 65756.0 }));
    const first = rowsOf(host).find((r) => !r.classList.contains('dash-ladder-current'));
    view.updateLevelValues(() => 99999.0);
    assert.equal(cellText(first, 'price'), '66,099.7');
    assert.equal(cellText(first, 'distance'), '+343.7');
  });

  // ---- 表示範囲の切替（依頼者指示 2026-08-30: 短期・中期・長期・全期間） ----
  const scopeButton = (host, key) => flatten(host)
    .find((node) => node.dataset && node.dataset.scope === key);
  const press = (button) => { (button._listeners.click || []).forEach((fn) => fn({})); };
  const MIXED = [
    ladderRow({ price: 68000, timeframe: '1W', label: 'W-up', distance: 2244 }),
    ladderRow({ price: 67000, timeframe: '1D', label: 'D-up', distance: 1244 }),
    ladderRow({ price: 66500, timeframe: '4h', label: 'H4-up', distance: 744 }),
    ladderRow({ price: 66000, timeframe: '1h', label: 'H1-up', distance: 244 }),
    ladderRow({ price: 65900, timeframe: '15m', label: 'M15-up', distance: 144 }),
    ladderRow({ price: 65800, timeframe: '1m', label: 'M1-up', distance: 44 }),
    ladderRow({ price: 65700, timeframe: '1h', label: 'H1-dn', distance: -56 }),
    ladderRow({ price: 65600, timeframe: '1D', label: 'D-dn', distance: -156 }),
  ];

  test('the_period_bar_offers_the_three_groups_and_zenkikan_all_groups_on_by_default', () => {
    // 期間は複数選択（依頼者指示 2026-08-30）。既定は全選択＝3 グループとも点灯・全期間は消灯。
    const { host } = renderInto(sheetResponse({ rows: MIXED, current_index: 6 }));
    for (const key of ['short', 'medium', 'long', 'all']) {
      assert.ok(scopeButton(host, key), `期間 ${key} のボタンがありません`);
    }
    assert.ok(scopeButton(host, 'short').classList.contains('is-active'));
    assert.ok(scopeButton(host, 'medium').classList.contains('is-active'));
    assert.ok(scopeButton(host, 'long').classList.contains('is-active'));
    assert.equal(scopeButton(host, 'all').classList.contains('is-active'), false);
    assert.match(textOf(host), /全期間/);
  });

  test('period_groups_toggle_their_disjoint_timeframe_bands_and_combine_freely', () => {
    // 期間グループは §4.3 の閾値（1h・1D）で区切った互いに素な帯をまとめてトグルする。
    const { host } = renderInto(sheetResponse({ rows: MIXED, current_index: 6 }));
    // Act: 短期の帯（1m/5m/15m）を外す。
    press(scopeButton(host, 'short'));
    // Assert
    assert.equal(rowsOf(host).length, 7);   // 中期＋長期 6 本 ＋ 現在値行。
    assert.equal(/M1-up|M15-up/.test(textOf(host)), false);
    // Act: 長期の帯（1D/1W/1M）も外す → 中期の帯だけが残る（複数選択の組合せ）。
    press(scopeButton(host, 'long'));
    // Assert
    assert.equal(rowsOf(host).length, 4);   // H4-up / H1-up ＋ 現在値行 ＋ H1-dn。
    assert.equal(/W-up|D-up|D-dn/.test(textOf(host)), false);
    const rows = rowsOf(host);
    assert.equal(rows[2].classList.contains('dash-ladder-current'), true);
  });

  test('a_period_group_lights_only_when_all_its_timeframes_are_selected', () => {
    // 期間ボタンと時間足ピルは**同一の選択集合**を操作する（軸を 2 本にしない）。
    const { host } = renderInto(sheetResponse({ rows: MIXED, current_index: 6 }));
    // Act: 中期の帯の 1 本（1h）だけをピルで外す。
    press(tfButton(host, '1h'));
    // Assert: 中期は消灯（帯が欠けた）・短期と長期は点灯のまま。
    assert.equal(scopeButton(host, 'medium').classList.contains('is-active'), false);
    assert.ok(scopeButton(host, 'short').classList.contains('is-active'));
    assert.ok(scopeButton(host, 'long').classList.contains('is-active'));
    // Act: 1h を戻すと中期も点灯へ戻る。
    press(tfButton(host, '1h'));
    assert.ok(scopeButton(host, 'medium').classList.contains('is-active'));
  });

  test('the_all_scope_drops_the_window_and_shows_every_row', () => {
    // 全期間＝窓なし全表示（従来の全量へ戻す選択肢）。
    const many = Array.from({ length: 100 }, (_unused, i) =>
      ladderRow({ price: 70000 - i * 10, label: `L${i}`, distance: 50 - i }));
    const { host } = renderInto(sheetResponse({ rows: many, current_index: 50 }));
    // Act
    press(scopeButton(host, 'all'));
    // Assert: 全 100 本＋現在値行・窓の掲示なし。
    assert.equal(rowsOf(host).length, 101);
    assert.equal(/全 100 本中/.test(textOf(host)), false);
  });

  test('pressing_zenkikan_again_returns_to_the_windowed_view_without_a_new_response', () => {
    // 切替は直近の応答の描き直しだけ（発行を生まない——発行判定は sheet_poller の唯一責務）。
    const many = Array.from({ length: 100 }, (_unused, i) =>
      ladderRow({ price: 70000 - i * 10, label: `L${i}`, distance: 50 - i }));
    const { host } = renderInto(sheetResponse({ rows: many, current_index: 50 }));
    press(scopeButton(host, 'all'));
    // Act
    press(scopeButton(host, 'all'));
    // Assert
    assert.equal(rowsOf(host).length, 31);
    assert.match(textOf(host), /全 100 本中/);
  });

  test('toggling_a_period_group_twice_restores_the_original_rows', () => {
    // 往復の同値性（トグルの表明）。
    const { host } = renderInto(sheetResponse({ rows: MIXED, current_index: 6 }));
    const before = rowsOf(host).length;
    press(scopeButton(host, 'medium'));
    press(scopeButton(host, 'medium'));
    assert.equal(rowsOf(host).length, before);
  });

  // ---- 時間足の選択（依頼者指示 2026-08-30: 時間足も選択できるように） ----
  const tfButton = (host, timeframe) => flatten(host)
    .filter((node) => node.classList.contains('dash-ladder-tf-btn'))
    .find((node) => node.dataset.timeframe === timeframe);

  test('the_tf_bar_offers_every_timeframe_all_selected_by_default', () => {
    const { host } = renderInto(sheetResponse({ rows: MIXED, current_index: 6 }));
    for (const timeframe of ['1m', '5m', '15m', '1h', '4h', '1D', '1W', '1M']) {
      assert.ok(tfButton(host, timeframe), `時間足 ${timeframe} の選択が無い`);
    }
    // 既定は全選択＝全行が出る（MIXED 8 本＋現在値行）。
    assert.equal(rowsOf(host).length, 9);
  });

  test('deselecting_a_timeframe_removes_only_its_rows', () => {
    const { host } = renderInto(sheetResponse({ rows: MIXED, current_index: 6 }));
    // Act: 1h を外す。
    press(tfButton(host, '1h'));
    // Assert: 1h の 2 本だけ消える（他は残る）。
    assert.equal(rowsOf(host).length, 7);
    assert.equal(/H1-up|H1-dn/.test(textOf(host)), false);
    assert.match(textOf(host), /H4-up/);
    // 現在値行は距離の符号の変わり目に入り直す（1h を除く正距離 5 本の直後）。
    const rows = rowsOf(host);
    const currentAt = rows.findIndex((r) => r.classList.contains('dash-ladder-current'));
    assert.equal(currentAt, 5);
  });

  test('timeframe_pills_and_period_groups_edit_the_same_selection', () => {
    const { host } = renderInto(sheetResponse({ rows: MIXED, current_index: 6 }));
    // Act: 短期の帯をまとめて外し、さらに 1h をピルで外す → 4h / 1D / 1W だけ。
    press(scopeButton(host, 'short'));
    press(tfButton(host, '1h'));
    // Assert
    assert.equal(rowsOf(host).length, 5);   // W-up / D-up / H4-up / D-dn ＋ 現在値行。
    assert.equal(/H1-up|H1-dn|M15-up|M1-up/.test(textOf(host)), false);
  });

  test('reselecting_the_timeframe_restores_its_rows', () => {
    const { host } = renderInto(sheetResponse({ rows: MIXED, current_index: 6 }));
    press(tfButton(host, '1h'));
    // Act
    press(tfButton(host, '1h'));
    // Assert: 全選択へ戻る＝全行復活。
    assert.equal(rowsOf(host).length, 9);
  });

  test('an_empty_selection_is_posted_instead_of_a_silent_blank_table', () => {
    const rows = [ladderRow({ timeframe: '1m', price: 65800, label: 'only', distance: 44 })];
    const { host } = renderInto(sheetResponse({ rows, current_index: 1 }));
    // Act: 唯一の該当足を外す。
    press(tfButton(host, '1m'));
    // Assert: 水準行は 0 だが、理由を掲示する（無言の空にしない）。
    assert.equal(rowsOf(host).filter((r) => !r.classList.contains('dash-ladder-current')).length, 0);
    assert.match(textOf(host), /表示できる水準がありません/);
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
