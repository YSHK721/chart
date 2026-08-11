// 統合エントリ（index.html）のモード別 CSS が 3 値化されていることの固定（§11.1 裁定 4 = L-2）。
//
// なぜ要るか: 旧 CSS は `.um-mode-live #replay-bar { display: none }`＝**ライブのときだけ隠す**
//   という「2 値の裏返し」で書かれていた。モードが 3 つになると、live でも replay でもない
//   sim モードで**リプレイ操作バーが出たままになる**（隠す条件に当てはまらないため）。
//   条件を「replay のときだけ出す」へ反転させると、表に無いモード・将来のモードでも既定は非表示になり、
//   モードを増やしても本 CSS を直さなくてよくなる（＝表駆動と同じ性質を CSS 側にも持たせる）。
//
// 併せて、モードトグルの点灯（aria-pressed）は既存の #enter-replay と同流儀で各モード分を置く。
// 構造は AAA。

import { describe, test, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

import {
  MODE_TOGGLE_BUTTONS, MODE_IDS, DEFAULT_MODE, bodyClassOf, hasChartApi, CHART_API_BODY_CLASS,
} from '../js/mode_table.js';

const HTML = readFileSync(fileURLToPath(new URL('../index.html', import.meta.url)), 'utf8');
// 規則の**不在**を見る検定はコメント本文に反応してはならない（旧規則を注記として残せなくなる）。
const HTML_NO_COMMENTS = HTML.replace(/\/\*[\s\S]*?\*\//g, '').replace(/<!--[\s\S]*?-->/g, '');

describe('index.html — モード別 CSS の 3 値化', () => {
  test('replay_bar_is_hidden_by_default_and_shown_only_in_replay_mode', () => {
    // Assert: 既定は非表示。replay モードのときだけ出す（＝条件の反転）。
    expect(HTML).toMatch(/#replay-bar\s*\{\s*display:\s*none;?\s*\}/);
    expect(HTML).toMatch(/\.um-mode-replay\s+#replay-bar\s*\{\s*display:\s*flex;?\s*\}/);
  });

  test('replay_bar_is_not_hidden_by_the_old_live_only_rule', () => {
    // Assert: 「ライブのときだけ隠す」という 2 値の裏返しが残っていない（sim で出っぱなしになる）。
    expect(HTML_NO_COMMENTS).not.toMatch(/\.um-mode-live\s+#replay-bar/);
  });

  // --- 裁定 2026-08-11: ライブ追従トグルは live モードでのみ有効 -----------------------
  //
  // 根拠（実測）: `#live-follow-toggle` の意味は「LiveUpdater 稼働＋新足で右端追従」であり
  //   （`live_follow_controller.js:5-9`）、live 以外への遷移は必ず `stopPollers()` で
  //   LiveUpdater を止める（`unified_root.js` の enterReplay / enterSim）。つまり live 以外の
  //   モードでは押しても何も起きない。replay の既存挙動（グレーアウト＋操作不可）が参照実装。
  //
  // 書き方は L-2 と同流儀に寄せる: 「replay のときだけ無効」という**モードの列挙**ではなく、
  //   「live のとき以外は無効」という反転で書く。モードが増えても本 CSS を直さなくてよくなる。
  test('live_follow_toggle_is_disabled_in_every_non_live_mode', () => {
    // Assert: live 以外を一括で無効化する規則が在る（グレーアウト＋操作不可は replay と同値）。
    expect(HTML).toMatch(
      /body:not\(\.um-mode-live\)\s+#live-follow-toggle\s*\{[^}]*opacity:\s*\.4;[^}]*pointer-events:\s*none;[^}]*\}/,
    );
  });

  test('live_follow_toggle_rule_does_not_enumerate_modes', () => {
    // Assert: モードを列挙する旧形（.um-mode-replay #live-follow-toggle）が残っていない。
    //   残すと第 4 モードのたびにセレクタを足す義務が復活する（＝L-2 が撤去した状態への逆戻り）。
    expect(HTML_NO_COMMENTS).not.toMatch(/\.um-mode-replay\s+#live-follow-toggle/);
    expect(HTML_NO_COMMENTS).not.toMatch(/\.um-mode-sim\s+#live-follow-toggle/);
  });

  // --- 🟡-5: チャート API を持たない core では、チャート操作系を触らせない -----------------
  //
  // sim core は Phase 1 で `/candles` `/compute` を持たない。時間足や指標を操作すると要求は
  //   飛ぶが 404 で、**画面には何も起きない**（無音の失敗）。操作できないことを見た目で示す。
  //   記法は #live-follow-toggle と同じ反転側: モード名ではなく「chartApi を持つか」の状態
  //   クラスの有無で判定する（第 4 モードを表へ足しても本 CSS は変わらない）。
  test('chart_operations_are_disabled_when_the_core_has_no_chart_api', () => {
    // Assert: 状態クラスが無いときだけ無効化する反転記法。
    expect(HTML).toMatch(
      /body:not\(\.um-chart-api\)[^{]*\{[^}]*opacity:\s*\.4;[^}]*pointer-events:\s*none;[^}]*\}/,
    );
  });

  test('chart_operation_rule_covers_the_operations_that_hit_the_chart_api', () => {
    // Assert: チャート API を実際に叩く操作が対象に入っている（実測で確認した 4 経路）。
    //   #tf-menu            … indicator_controller.js:1224 → setTimeframe → candles + compute
    //   #tpl-menu           … chart_template_controller.js:364-372 が compute を並列発行
    //   #indicator-open-btn … indicator_dialog_controller.js:108 → applyIndicator → compute
    //   .pane-legend-gear   … indicator_controller.js:1295 → _onGear:1313 → プロパティ OK →
    //                         _applyDialogResult:1375 → recomputeInstance:688 → _computeInstance
    //                         （DOM 不在時は _gearRecompute:1385-1393 経由で同じく recomputeInstance）
    const rule = HTML.match(/(body:not\(\.um-chart-api\)[^{]*)\{[^}]*pointer-events:\s*none;[^}]*\}/);
    expect(rule).not.toBeNull();
    for (const sel of ['#tf-menu', '#tpl-menu', '#indicator-open-btn', '.pane-legend-gear']) {
      expect(rule[1]).toContain(sel);
    }
  });

  test('chart_operation_rule_excludes_the_legend_controls_that_never_compute', () => {
    // Assert: ペイン別凡例の目（表示切替）と ✕（削除）は対象外のまま。
    //   目 … indicator_controller.js:1292 → toggleVisible:808-816（setVisible / persist / 再描画のみ）
    //   ✕ … 同:1296 → removeInstance:838-848（renderer.remove / facadeRemove / persist のみ）
    //   どちらも compute を発行しないので core が何であっても動く。動くものを塞がない。
    const rule = HTML.match(/(body:not\(\.um-chart-api\)[^{]*)\{[^}]*pointer-events:\s*none;[^}]*\}/);
    expect(rule[1]).not.toContain('.pane-legend-visibility');
    expect(rule[1]).not.toContain('.pane-legend-remove');
  });

  test('chart_operation_rule_excludes_the_color_theme_menu', () => {
    // Assert: 指標カラーテーマは対象外。適用は再計算を伴わず `/compute` を呼ばない
    //   （color_theme_controller.js:25-28 の明示契約）ので、core が何であっても動く。
    //   動くものを塞ぐと「押せない理由が説明できない UI」になる。
    //   （当初この検定は #color-theme-menu を対象に**含める**ことを要求していたが、上記の
    //     実測により期待値そのものが誤りと判明したため訂正した。）
    const rule = HTML.match(/(body:not\(\.um-chart-api\)[^{]*)\{[^}]*pointer-events:\s*none;[^}]*\}/);
    expect(rule[1]).not.toContain('#color-theme-menu');
  });

  test('chart_operation_rule_never_disables_the_mode_switch_buttons', () => {
    // Assert: モード切替ボタンを巻き込むと sim から出られなくなる（🔴-1 と同じ壊れ方）。
    const rule = HTML.match(/(body:not\(\.um-chart-api\)[^{]*)\{[^}]*pointer-events:\s*none;[^}]*\}/);
    for (const b of MODE_TOGGLE_BUTTONS) {
      expect(rule[1]).not.toContain(`#${b.id}`);
    }
    // ツールバー全体を一括で潰す書き方も禁止（モード切替ボタンを巻き込むため）。
    expect(rule[1]).not.toMatch(/\.toolbar\s*\{?$/);
  });

  test('chart_operation_rule_does_not_enumerate_modes', () => {
    // Assert: モード名で書くと第 4 モードのたびにセレクタを足す義務が生まれる。
    const rule = HTML_NO_COMMENTS.match(/body:not\(\.um-chart-api\)[^{]*\{[^}]*\}/);
    expect(rule).not.toBeNull();
    expect(rule[0]).not.toMatch(/um-mode-/);
  });

  test('initial_body_classes_match_the_default_mode_state', () => {
    // Arrange: applyModeUi が走るのは bootstrap 完了後。それまでの間、body の初期クラスが
    //   状態を正しく表していないと、ツールバーが出てから初期化が終わるまでのあいだだけ
    //   チャート操作がグレーアウトして見える（`body:not(.um-chart-api)` に当たるため）。
    //   初期状態は既定モード（live）と一致していなければならない。
    const body = HTML.match(/<body class="([^"]*)"/);
    expect(body).not.toBeNull();
    const classes = body[1].split(/\s+/).filter(Boolean);
    // Assert
    expect(classes).toContain(bodyClassOf(DEFAULT_MODE));
    expect(classes.includes(CHART_API_BODY_CLASS)).toBe(hasChartApi(DEFAULT_MODE));
    // 既定モード以外のモードクラスは付けない（相互排他）。
    for (const id of MODE_IDS.filter((m) => m !== DEFAULT_MODE)) {
      expect(classes).not.toContain(bodyClassOf(id));
    }
  });

  test('every_mode_toggle_has_a_pressed_highlight_rule', () => {
    // Assert: 表に載っている全トグルが #enter-replay と同流儀で点灯する。
    for (const b of MODE_TOGGLE_BUTTONS) {
      expect(HTML).toContain(`#${b.id}[aria-pressed="true"]`);
    }
  });
});
