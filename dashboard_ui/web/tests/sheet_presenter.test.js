// sheet_presenter — 応答 1 件を受けて「何を描き直すか」を決める判断器（usecase・純ロジック）。
//
// なぜ単体で検定するのか（ISSUE-502 段階 4C・F-3）:
//   分割前この方針は composition_root_front.js（「責務は結線だけ」と自称）の中にあり、
//   「unchanged では表を触らない」「借りたら必ず 1 回描く」「失敗掲示の上へ古い行を戻さない」
//   という**危険な縮退に直結する判断**を、View と fetch を組んだ状態でしか確かめられなかった。
//
// 構造は AAA。テスト名は「対象_条件_期待結果」。

import { test, describe } from 'node:test';
import assert from 'node:assert/strict';

import { createSheetPresenter } from '../js/usecase/sheet_presenter.js';

/** 日付印は固定（日替わりの効果だけを別に見る）。 */
function presenter(dayStamp = () => 0) {
  return createSheetPresenter({ dayStampOf: dayStamp });
}

const OK_A = { ok: true, state: 's1', rows: [{ price: 1 }] };
const OK_B = { ok: true, state: 's2', rows: [{ price: 2 }] };
const FAIL = { ok: false, error: { type: 'X', message: 'だめ' } };

describe('sheet_presenter — 応答の受け取り', () => {
  test('a_full_response_paints_every_panel_and_feeds_the_tail_declaration', () => {
    // Arrange
    const p = presenter();
    // Act
    const plan = p.accept(OK_A);
    // Assert
    assert.equal(plan.full, OK_A);
    assert.equal(plan.hasKeyed, true);
    assert.equal(plan.hasAlways, true);
    assert.equal(p.stateToken(), 's1');
  });

  test('an_identical_response_does_not_rebuild_the_keyed_panels', () => {
    // 省リソース段階 1: 毎秒の全再構築は内容不変時にはまるごと浪費（依頼者指摘 2026-08-30）。
    // Arrange
    const p = presenter();
    p.accept(OK_A);
    // Act
    const plan = p.accept(OK_A);
    // Assert: 差分適用の版面（チャート）だけが受け取る。
    assert.equal(plan.hasKeyed, false);
    assert.equal(plan.hasAlways, true);
  });

  test('a_changed_response_rebuilds_the_keyed_panels', () => {
    const p = presenter();
    p.accept(OK_A);
    assert.equal(p.accept(OK_B).hasKeyed, true);
  });

  test('an_unchanged_response_touches_no_panel_but_replays_the_last_full_one', () => {
    // トークンだけ受け取り、表は触らない。チャートの差分描画は通す（右端余白アンカーの
    //   再試行が枯れないように）。
    // Arrange
    const p = presenter();
    p.accept(OK_A);
    // Act
    const plan = p.accept({ ok: true, unchanged: true, state: 's9' });
    // Assert
    assert.equal(plan.full, null);
    assert.equal(plan.hasKeyed, false);
    assert.equal(plan.always, OK_A);
    assert.equal(p.stateToken(), 's9');
  });

  test('an_unchanged_response_before_any_full_one_paints_nothing', () => {
    // 器を出し直した直後に unchanged が来ると、描く材料が無い（空の版面を「描いた」ことに
    //   しない）。
    const p = presenter();
    const plan = p.accept({ ok: true, unchanged: true, state: 's1' });
    assert.equal(plan.hasAlways, false);
    assert.equal(plan.always, null);
  });

  test('a_failed_response_is_painted_and_is_not_remembered_as_the_full_one', () => {
    // Arrange
    const p = presenter();
    p.accept(OK_A);
    // Act
    const plan = p.accept(FAIL);
    // Assert: 掲示は描く。だが「直近の完全応答」は差し替えない（材料は成功応答のまま）。
    assert.equal(plan.hasKeyed, true);
    assert.equal(plan.full, null);
  });

  test('a_new_day_repaints_even_when_the_response_is_identical', () => {
    // 到達時刻の「今日/昨日」表記が日替わりで確実に描き直される。
    // Arrange
    let day = 0;
    const p = presenter(() => day);
    p.accept(OK_A);
    // Act
    day = 1;
    // Assert
    assert.equal(p.accept(OK_A).hasKeyed, true);
  });
});

describe('sheet_presenter — 借用の反映', () => {
  test('a_new_generation_repaints_even_when_the_response_is_identical', () => {
    // 借りたら必ず 1 回描く。世代を鍵へ混ぜないと、応答が同一のとき MP 列だけ古いまま残る
    //   （借りたのに描かない＝作って捨てる計算になる）。
    // Arrange
    const p = presenter();
    p.accept(OK_A);
    assert.equal(p.accept(OK_A).hasKeyed, false);
    // Act
    p.bumpGeneration();
    // Assert
    assert.equal(p.accept(OK_A).hasKeyed, true);
  });

  test('the_repaint_target_is_the_last_full_response_while_it_is_on_screen', () => {
    const p = presenter();
    p.accept(OK_A);
    assert.equal(p.repaintTarget(), OK_A);
  });

  test('nothing_is_repainted_while_the_view_shows_a_failure', () => {
    // 最も危険な縮退の禁止: 無条件に描くと、シートが落ちている最中に古い行が復活し、
    //   理由の掲示も消える——ユーザーには復旧したように見える。
    // Arrange
    const p = presenter();
    p.accept(OK_A);
    // Act
    p.accept(FAIL);
    // Assert
    assert.equal(p.repaintTarget(), null);
  });

  test('nothing_is_repainted_before_the_first_full_response', () => {
    assert.equal(presenter().repaintTarget(), null);
  });

  test('syncing_the_key_stops_the_next_identical_response_from_painting_twice', () => {
    // Arrange
    const p = presenter();
    p.accept(OK_A);
    p.bumpGeneration();
    // Act: 借用の着弾でその場描き直し → 鍵を今描いた内容へ揃える。
    const target = p.repaintTarget();
    p.syncKey(target);
    // Assert: 直後の同一応答は描き直しを求めない。
    assert.equal(p.accept(OK_A).hasKeyed, false);
  });
});

describe('sheet_presenter — 器の出し直し', () => {
  test('reset_demands_a_full_response_because_unchanged_would_leave_a_blank_host', () => {
    // Arrange
    const p = presenter();
    p.accept(OK_A);
    // Act
    p.reset();
    // Assert: トークンも直近の完全応答も鍵も捨てる。
    assert.equal(p.stateToken(), null);
    assert.equal(p.repaintTarget(), null);
    assert.equal(p.accept({ ok: true, unchanged: true, state: 's1' }).hasAlways, false);
  });

  test('reset_clears_the_render_key_so_the_reopened_host_is_painted_again', () => {
    // 鍵が残っていると、畳んで開き直した直後に「内容が同じ」と判定されて版面が空のまま残る。
    // Arrange
    const p = presenter();
    p.accept(OK_A);
    // Act
    p.reset();
    // Assert
    assert.equal(p.accept(OK_A).hasKeyed, true);
  });
});

describe('sheet_presenter — 計算量（絶対命令 §4.1）', () => {
  test('the_render_key_is_composed_once_per_response', () => {
    // 鍵の合成は応答の直列化を含む（応答の大きさに比例）。1 応答につき 1 回であることを、
    //   日付印の読み取り回数で観測する（accept 1 回 = 1 回）。
    let stamps = 0;
    const p = createSheetPresenter({ dayStampOf: () => { stamps += 1; return 0; } });
    p.accept(OK_A);
    assert.equal(stamps, 1);
    p.accept(OK_B);
    assert.equal(stamps, 2);
  });

  test('an_unchanged_response_composes_no_key_at_all', () => {
    // unchanged は素材不変の極小応答である。ここで鍵を組むと、表を触らないと決めているのに
    //   直近の応答の直列化だけ毎秒走る（作って捨てる計算そのもの）。
    // Arrange
    let stamps = 0;
    const p = createSheetPresenter({ dayStampOf: () => { stamps += 1; return 0; } });
    p.accept(OK_A);
    const baseline = stamps;
    // Act
    p.accept({ ok: true, unchanged: true, state: 's9' });
    p.accept({ ok: true, unchanged: true, state: 's9' });
    // Assert
    assert.equal(stamps, baseline);
  });
});
