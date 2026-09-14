// view_rule_ownership — 「版面は規則を持たない / 規則は版面を持たない」を機械的に固定する。
//
// なぜ必要か（ISSUE-502 段階 4C・F-2 / F-3 の再発防止）:
//   reach_sheet_view.js は 1,170 行に 6 責務を抱えていた（頻度の統計・残光の予定表・表示範囲の
//   状態機械・窓の幾何・距離と差の式・DOM 構築）。分割しても、次に急ぐ人が「1 行だけだから」と
//   View へ定数や `Date.now()` を書き戻せば元へ戻る。**宣言（コメント）ではなく検査**で戻れなく
//   する（MEMORY: enforce-constraints-mechanically）。
//
//   同型の失敗は既に起きている: composition_root_front.js は冒頭で「本モジュールの責務は
//   結線だけである（SRP）」と自称しながら、tails spec の導出・描画鍵・MP 借用の文言・
//   player 8 台の生成を保持していた。自称は検査ではない。
//
// 本ファイルが固定するもの:
//   V-1 規則の所有者は 1 つ … 規則の定数は台帳が指すモジュールでだけ宣言される。
//   V-2 版面は時計も予約も持たない … `*_view.js` は現在時刻を読まず、タイマーも張らない
//       （時刻は注入・フェードの時間は CSS が唯一源）。
//   V-3 規則は DOM を持たない … `js/domain/` は DOM 語彙を 1 つも含まない（単体で検証できる）。
//   V-4 版面は規則を借りている … 第 1 表は規則モジュールを実際に import している
//       （規則を書き戻して import だけ残す退行は V-1 / V-2 が落とす）。
//
// 構造は AAA。テスト名は「対象_条件_期待結果」。

import { test, describe } from 'node:test';
import assert from 'node:assert/strict';
import { readdirSync, readFileSync, statSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const WEB = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const JS_ROOT = path.join(WEB, 'js');

/** 行コメント・ブロックコメントを落とす（宣言の走査に文章を混ぜない）。 */
function stripComments(source) {
  return source.replace(/\/\*[\s\S]*?\*\//g, '').replace(/(^|[^:])\/\/[^\n]*/g, '$1');
}

/** 配信根の配下の .js を全部読む（対象の列挙を持たない＝新しいファイルも自動で入る）。 */
function collect(dir, out = new Map()) {
  for (const name of readdirSync(dir)) {
    const full = path.join(dir, name);
    if (statSync(full).isDirectory()) collect(full, out);
    else if (name.endsWith('.js')) out.set(path.relative(JS_ROOT, full), readFileSync(full, 'utf8'));
  }
  return out;
}

const SOURCES = collect(JS_ROOT);
const CODE = new Map([...SOURCES].map(([rel, src]) => [rel, stripComments(src)]));

/**
 * 規則の定数 → 唯一の所有者。
 *
 * ここに並ぶのは「版面の都合ではなく規則である」と裁定した数値である。第 2 の宣言が
 * 現れた瞬間に落ちる（片方だけ直された日に版面と規則が静かにずれるのを防ぐ）。
 */
const RULE_OWNERS = Object.freeze({
  TICK_RATE_WINDOW_SECONDS: 'domain/tick_rate.js',
  TICK_FULL_RATE: 'domain/tick_rate.js',
  TICK_MIN_STRENGTH: 'domain/tick_rate.js',
  NEXT_MOVE_FADE_SECONDS: 'domain/next_target_glow.js',
  NEXT_MOVE_DELAY_SECONDS: 'domain/next_target_glow.js',
  WINDOW_RADIUS: 'domain/ladder_window.js',
});

/** 版面のモジュール（構造から拾う＝新しい View も自動で対象になる）。 */
const VIEW_FILES = [...CODE.keys()].filter((rel) => rel.endsWith('_view.js'));

describe('view_rule_ownership — V-1 規則の所有者は 1 つ', () => {
  test('the_scan_actually_sees_the_front_layer', () => {
    // 検定の検定: 走査が空振りしていれば以下は全部無条件に通ってしまう。
    assert.ok(SOURCES.size > 15, `走査対象が少なすぎます（前提崩壊）: ${SOURCES.size}`);
    assert.ok(VIEW_FILES.length >= 3, `View が見つかりません: ${VIEW_FILES}`);
  });

  test('every_rule_constant_is_declared_in_exactly_one_module', () => {
    for (const [name, owner] of Object.entries(RULE_OWNERS)) {
      const declaredIn = [...CODE].filter(
        ([, code]) => new RegExp(`(?:^|\\n)\\s*(?:export\\s+)?const\\s+${name}\\s*=`).test(code),
      ).map(([rel]) => rel);
      assert.deepEqual(declaredIn, [owner],
        `規則 ${name} の宣言が唯一源からずれています: ${declaredIn.join(' / ')}`);
    }
  });
});

describe('view_rule_ownership — V-2 版面は時計も予約も持たない', () => {
  test('no_view_reads_the_wall_clock_because_time_is_injected', () => {
    // 版面が自前で現在時刻を読むと、時間に依る挙動（頻度・残光・更新時刻）が検定から
    //   決定的に動かせなくなる。時計は注入する規約（View は時計を持たない）。
    for (const rel of VIEW_FILES) {
      const hits = CODE.get(rel).match(/Date\.now|new\s+Date\s*\(\s*\)|performance\.now/g) ?? [];
      assert.deepEqual(hits, [], `${rel} が現在時刻を自分で読んでいます: ${hits.join(', ')}`);
    }
  });

  test('no_view_schedules_work_because_the_fade_belongs_to_css', () => {
    // タイマーを持つと CSS のフェード時間と第 2 の時間源が生まれ、片方だけ変えた日にずれる。
    //   後始末を落としたクラスは次の再構築で発光を再生してしまう（実測 2026-08-31）。
    for (const rel of VIEW_FILES) {
      const hits = CODE.get(rel).match(/setInterval|setTimeout|requestAnimationFrame/g) ?? [];
      assert.deepEqual(hits, [], `${rel} が予約を張っています: ${hits.join(', ')}`);
    }
  });

  test('no_view_talks_to_the_network_itself', () => {
    // 描画と発行が混ざると「描くたびに発行する」欠陥が入り込み、出力は正しいまま無駄が増える
    //   （ISSUE-450 と同型）。発行判定は poller、HTTP は client の責務。
    for (const rel of VIEW_FILES) {
      const hits = CODE.get(rel).match(/\bfetch\b|XMLHttpRequest|WebSocket/g) ?? [];
      assert.deepEqual(hits, [], `${rel} が自分で発行しています: ${hits.join(', ')}`);
    }
  });
});

describe('view_rule_ownership — V-3 規則は DOM を持たない', () => {
  test('no_domain_module_touches_the_dom_so_the_rules_stay_unit_testable', () => {
    // domain が DOM を触ると、規則を確かめるのに版面を組む必要が戻る（分割前の状態）。
    const domain = [...CODE.keys()].filter((rel) => rel.startsWith('domain/'));
    assert.ok(domain.length >= 5, `domain が見つかりません: ${domain}`);
    for (const rel of domain) {
      const hits = CODE.get(rel).match(
        /\bdocument\b|createElement|appendChild|classList|addEventListener|getBoundingClientRect/g,
      ) ?? [];
      assert.deepEqual(hits, [], `${rel} が DOM を触っています: ${hits.join(', ')}`);
    }
  });
});

describe('view_rule_ownership — V-4 版面は規則を借りている', () => {
  test('the_ladder_view_imports_the_rule_modules_instead_of_owning_them', () => {
    // 6 責務が戻っていないことの表明。import が消えていれば、規則がどこかへ書き戻されている
    //   （その書き戻し自体は V-1 / V-2 が落とす）。
    const src = CODE.get('adapter/front/reach_sheet_view.js');
    for (const owner of [
      '../../domain/tick_rate.js',
      '../../domain/next_target_glow.js',
      '../../domain/ladder_scope.js',
      '../../domain/ladder_window.js',
      '../../domain/smooth_ladder.js',
      '../../domain/ladder_row.js',
    ]) {
      assert.ok(src.includes(owner), `第 1 表が ${owner} を借りていません`);
    }
  });

  test('the_composition_root_delegates_its_policies_instead_of_holding_them', () => {
    // 合成根は「結線だけ」を自称していた期間に 4 つの方針を抱えていた（F-3 の実測）。
    //   方針モジュールを借りていることを表明で固定する。
    const src = CODE.get('adapter/front/composition_root_front.js');
    for (const owner of [
      '../../usecase/sheet_presenter.js',
      '../../usecase/tail_specs.js',
      './mp_borrow.js',
      './live_tick_players.js',
    ]) {
      assert.ok(src.includes(owner), `合成根が ${owner} へ委ねていません`);
    }
    // 方針が書き戻された形（描画鍵の合成・params_key の復元）が根に残っていないこと。
    assert.equal(/JSON\.parse/.test(src), false, '合成根が契約の復元を抱えています');
  });
});

describe('view_rule_ownership — V-5 表示系統の追加は台帳 1 行（OCP）', () => {
  const ROOT = 'adapter/front/composition_root_front.js';

  test('no_panel_is_mounted_or_unmounted_by_name_so_adding_one_is_a_declaration', () => {
    // 分割前は表示系統を 1 つ足すのに 4 箇所（生成 / mount / render / unmount）の改変が要り、
    //   どれか 1 つを落とすと「モードを出ても残る版面」「更新されない版面」になった。
    //   どちらも版面は表示され続けるので、出力の検査では落ちにくい。
    //
    //   版面の名前は**走査で拾う**（一覧をここへ書き写すと、View が増えた日に検査だけが
    //   古くなる——本ファイルが V-1 で禁じているのと同じ形になる）。
    const src = CODE.get(ROOT);
    const views = [...src.matchAll(/const\s+(\w*[Vv]iew)\s*=\s*create\w+\(/g)].map((m) => m[1]);
    assert.ok(views.length >= 3, `合成根から版面が見つかりません: ${views}`);
    for (const name of views) {
      const called = new RegExp(`\\b${name}\\s*\\.\\s*(?:mount|unmount)\\s*\\(`).test(src);
      assert.equal(called, false,
        `${name} を名指しで mount / unmount しています（台帳 panels から回すこと）`);
    }
  });

  test('the_root_drives_every_panel_from_the_single_ledger', () => {
    // 検定の検定: 台帳が無い（あるいは回していない）状態で上のテストは無条件に通る。
    const src = CODE.get(ROOT);
    assert.ok(/const\s+panels\s*=/.test(src), '表示系統の台帳（panels）がありません');
    const loops = src.match(/for\s*\(\s*const\s+panel\s+of\s+panels\s*\)/g) ?? [];
    assert.ok(loops.length >= 3,
      `台帳から回している箇所が足りません（mount / render / unmount）: ${loops.length}`);
  });
});
