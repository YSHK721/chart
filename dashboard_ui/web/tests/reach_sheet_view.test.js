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
  ladderRow({ price: 66099.7, timeframe: '1D', label: 'MA ema5 hlc3', distance: 343.7, gap_to_previous: null, horizon_marks: ['long'], horizon_p: { short: 0.93, medium: 0.71, long: 0.88 }, naming: { name: 'MA', period: 5, source: 'hlc3', extra: '' } }),
  ladderRow({ price: 65770.7, timeframe: '5m', label: 'cvfe 内側上 1σ', distance: 14.7, gap_to_previous: 7.6, horizon_marks: ['short'], horizon_p: { short: 0.052, medium: 0.067, long: 0.126 }, naming: { name: 'cvfe_u1', period: 1329, source: null, extra: 'sigma_outer=3.0' } }),
  ladderRow({ price: 65754.5, timeframe: '1m', label: 'MA ema60 high', distance: -1.5, gap_to_previous: 31.6, horizon_marks: ['short'], horizon_p: { short: 0.052, medium: 0.067, long: 0.126 }, naming: { name: 'MA', period: 60, source: 'high', extra: '' } }),
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

  test('the_price_column_head_says_where_the_smaller_number_under_it_comes_from', () => {
    // 「差」の語を欄から外した以上、その意味は表頭が持たねばならない（意味の消失を防ぐ）。
    const { host } = renderInto(sheetResponse({ rows: THREE_ROWS, current_index: 2 }));
    const head = flatten(host).find((el) => el.tagName === 'TH' && el.dataset.cell === 'price');
    assert.match(textOf(head), /直前行との差/);
  });

  test('a_level_below_the_current_price_is_marked_as_reached_and_one_above_is_not', () => {
    // モックの凡例:「現在値より下（到達済み＝支持側）／現在値より上（未到達＝抵抗側）」。
    //   判定はサーバが与えた距離の符号だけで行う（版面の意味を 2 つにしない）。
    const { host } = renderInto(sheetResponse({ rows: THREE_ROWS, current_index: 2 }));
    const levelRows = rowsOf(host).filter((r) => !r.classList.contains('dash-ladder-current'));
    assert.equal(levelRows[0].classList.contains('dash-ladder-pending'), true);
    assert.equal(levelRows[2].classList.contains('dash-ladder-hit'), true);
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
    const pill = flatten(host).find((el) => el.classList.contains('dash-tf-pill'));
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

  test('an_instance_that_cannot_be_projected_is_shown_with_its_reason', () => {
    // §7 / レビュー 🟡-2: `granularity: "none"` は「バー確定でも回復しない」＝背景が塗られない。
    //   掲示を bar_close だけに絞ると、この欠落は画面上で無言になる（サーバが理由を送っても
    //   誰も読まない＝結線が端まで通っていない状態）。
    const response = sheetResponse({
      rows: THREE_ROWS, current_index: 2,
      degradations: [{
        instance_key: ['ma_marod', 'default', '{}', '1h'], granularity: 'none',
        reason: '増分器が宣言されていないため前進評価できません',
      }],
    });
    const { host } = renderInto(response);
    const notice = flatten(host).find((el) => el.classList.contains('dash-granularity-notice'));
    assert.ok(notice, '掲示がありません');
    assert.match(textOf(notice), /ma_marod/);
    assert.match(textOf(notice), /1h/);
    assert.match(textOf(notice), /前進評価/);
  });

  test('a_bar_close_notice_is_not_replaced_by_the_unprojectable_one', () => {
    // 2 種類が同時に出ても、どちらも消えない（片方だけ残す実装への退行を防ぐ）。
    const response = sheetResponse({
      rows: THREE_ROWS, current_index: 2,
      degradations: [
        { instance_key: ['cvfe', 'default', '{}', '5m'], granularity: 'bar_close', reason: '増分器が無い' },
        { instance_key: ['ma_marod', 'default', '{}', '1h'], granularity: 'none', reason: '前進評価できません' },
      ],
    });
    const { host } = renderInto(response);
    const notice = flatten(host).find((el) => el.classList.contains('dash-granularity-notice'));
    assert.match(textOf(notice), /cvfe/);
    assert.match(textOf(notice), /ma_marod/);
  });

  // ---- ISSUE-466 掲示の集約と人間向け文言 ---------------------------------
  //
  // 実テンプレートでは同じ理由の縮退が 24 件（8 足 × 3 本）まとめて出る。1 件 1 文で
  // 並べると内部エラーの原文が 24 回繰り返され、読む側は「何が起きたか」を取り出せない
  // （認知負荷の最小化・厳命 2026-07-30）。**隠さない**性質は保ったまま、同一の
  // (指標, 理由種別) を 1 行へ畳み、件数を出し、原文は title へ退避する。
  const MA_KEYS = ['1m', '5m', '15m', '1h', '4h', '1D', '1W', '1M'].flatMap((tf) => (
    [24, 50, 200].map((length) => ['moving_averages', 'default', `{"length": ${length}}`, tf])
  ));
  const CORE_REASON = "系列を供給できないため除外した: 計算できません: ('moving_averages', '1h')"
    + ' — validation: add_moving_averages が受理しない param が渡されました:'
    + " ['wait_for_close']。variant ごとの受理引数は GET /catalog の paramScopes を参照してください。";
  const MA_DEGRADATIONS = MA_KEYS.map((instance_key) => ({
    instance_key, granularity: 'none', reason: CORE_REASON,
  }));

  /** 掲示の要素（無ければ undefined）。 */
  function noticeOf(host) {
    return flatten(host).find((el) => el.classList.contains('dash-granularity-notice'));
  }

  test('the_same_indicator_and_reason_are_aggregated_into_one_line', () => {
    const { host } = renderInto(sheetResponse({
      rows: THREE_ROWS, current_index: 2, degradations: MA_DEGRADATIONS,
    }));

    const text = textOf(noticeOf(host));
    // 指標名は 1 回だけ（24 回並べない）。
    assert.equal(text.split('moving_averages').length - 1, 1);
    // 件数は出す（何本が落ちたかを読める）。
    assert.match(text, /8 足/);
    assert.match(text, /24 本/);
  });

  test('the_internal_error_text_is_moved_to_the_title_and_summarised_in_the_body', () => {
    const { host } = renderInto(sheetResponse({
      rows: THREE_ROWS, current_index: 2, degradations: MA_DEGRADATIONS,
    }));

    const notice = noticeOf(host);
    // 本文は人間向け 1 行（内部エラーの原文を本文に出さない）。
    assert.equal(/paramScopes を参照してください/.test(textOf(notice)), false);
    assert.match(textOf(notice), /受理されないパラメータ wait_for_close/);
    // 原文は捨てない（title へ退避する＝隠さない）。
    const carrier = flatten(notice).find((el) => String(el.title).includes('paramScopes'));
    assert.ok(carrier, '内部エラーの原文が title に残っていません');
  });

  test('two_different_reasons_of_one_indicator_stay_separate', () => {
    // 恒真にしない: 集約は (指標, 理由種別) 単位であり、指標だけで畳まない。
    const { host } = renderInto(sheetResponse({
      rows: THREE_ROWS, current_index: 2,
      degradations: [
        { instance_key: ['profit_rsi', 'default', '{}', '1W'], granularity: 'none', reason: CORE_REASON },
        {
          instance_key: ['profit_rsi', 'default', '{}', '1M'], granularity: 'none',
          reason: "系列を供給できないため除外した: 計算できません: ('profit_rsi', '1M')"
            + ' — validation: E01_INSUFFICIENT_BARS: バー数 171 では σ̂ を 1 本も出力できない（523 本以上が必要）',
        },
      ],
    }));

    const text = textOf(noticeOf(host));
    assert.match(text, /受理されないパラメータ/);
    assert.match(text, /本数/);
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
