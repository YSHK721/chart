// color_theme_derivation_wiring.test.js — 導出の結線と、その結線が壊してはならない不変条件
//   （段階 5-B の通過条件 3・4・7・9）。
//
// 結線は 1 点だけ: `projectThemeForUse`（消費のための射影）が `normalizeRoleColors` の結果を
//   `expandRoleColors` に通してから返す。ここに置く理由:
//   - 読み出し側の射影なので、**永続値（`this._themes` / `saveThemes` の入力）は原形のまま**に
//     なる（§4.9 前方互換・§5.3 改名不変を壊さない）。導出値を保存すると、係数を直したときに
//     既存テーマだけ古い導出値で固まる。
//   - 消費経路が 1 本（controller.activeTheme()）なので、指標側とクロム側で導出の有無が食い違わない。
//
// 構造: Arrange-Act-Assert（AAA）。

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

import {
  PRESET_THEMES, projectThemeForUse, saveTheme,
} from '../js/usecase/color_themes.js';
import { COLOR_ROLES } from '../js/domain/color_roles.js';
import { CHROME_CURRENT, CHROME_SLOTS } from '../js/usecase/chrome_tokens.js';
import { resolveAllChrome, resolveSeriesColor } from '../js/usecase/color_resolver.js';
import { list } from '../js/usecase/catalog.js';

const abs = (rel) => fileURLToPath(new URL(rel, import.meta.url));

// =========================================================================
// 結線: 消費のための射影が導出を通す
// =========================================================================

test('TC-DW01 projectThemeForUse: 宣言されていないトークンが導出で埋まる', () => {
  // Arrange: 地だけを宣言したテーマ（ユーザーが決める項目を減らす、の実体）。
  const theme = {
    themeId: 'thm#1', name: 't', roleColors: { surface: '#131722' }, tfModifier: null,
  };
  // Act
  const projected = projectThemeForUse(theme).theme;
  // Assert
  assert.equal(projected.roleColors.surface, '#131722', '宣言値は不変');
  assert.deepEqual(
    Object.keys(projected.roleColors).sort(),
    ['border', 'grid', 'highlight', 'level', 'muted', 'surface', 'text'],
    '地から到達できるトークンだけが埋まる（部分写像）',
  );
  assert.equal(projected.roleColors.grid, '#21242f');
  assert.equal(projected.roleColors.text, '#d5d5d7');
});

test('TC-DW02 projectThemeForUse: 未正規化の宣言も正規化 → 導出の順で通る', () => {
  // Arrange: 射影の既存責務（保存形への正規化）が導出より先に効くこと。3 桁 hex から導出できる。
  const theme = { themeId: 'thm#1', name: 't', roleColors: { surface: '#137' } };
  // Act
  const projected = projectThemeForUse(theme).theme;
  // Assert
  assert.equal(projected.roleColors.surface, '#113377', '正規化が先');
  assert.equal('grid' in projected.roleColors, true, '正規化後の値から導出できる');
});

test('TC-DW03 恒等（D-11）: 宣言 0 件のテーマは 1 キーも生えない', () => {
  // Arrange
  const theme = { themeId: 'thm#1', name: 't', roleColors: {} };
  // Act
  const projected = projectThemeForUse(theme).theme;
  // Assert
  assert.deepEqual(projected.roleColors, {}, '恒等テーマは恒等のまま');
});

// =========================================================================
// 通過条件 3（永続化の側）: 導出値を永続化しない
// =========================================================================

test('TC-DW04 saveTheme: 保存値は原形のまま（導出値は 1 つも混ざらない）', () => {
  // Arrange
  const roleColors = { surface: '#131722' };
  // Act
  const res = saveTheme({ themes: [], lastSeq: 0, name: 'n', roleColors, now: 1 });
  // Assert
  assert.equal(res.ok, true);
  assert.deepEqual(res.themes[0].roleColors, { surface: '#131722' },
    '永続層には宣言した 1 語だけが入る（導出は読み出し側の射影）');
});

test('TC-DW05 saveTheme: 射影した値を保存し直しても永続値が膨らまない（往復の安定）', () => {
  // Arrange: 編集ダイアログは原形（themes()）を初期値に開くが、万一射影値が戻っても
  //   永続値は宣言の集合として閉じている必要がある（往復で 1 語 → 14 語へ膨らませない）。
  const first = saveTheme({ themes: [], lastSeq: 0, name: 'n', roleColors: { surface: '#131722' }, now: 1 });
  // Act: 保存済みの**原形**をそのまま保存し直す。
  const second = saveTheme({
    themes: first.themes, lastSeq: first.lastSeq, name: 'n', roleColors: first.themes[0].roleColors, now: 2,
  });
  // Assert
  assert.deepEqual(second.themes[0].roleColors, { surface: '#131722' });
  assert.equal(second.themes.length, 1);
});

// =========================================================================
// 通過条件 4（恒等回帰）: テーマ未設定の見た目が 1 色も動かない
// =========================================================================

test('TC-DW06 恒等回帰: クロム全 slot はテーマ未設定で現行リテラルと文字列一致する', () => {
  // Arrange / Act
  const { slots } = resolveAllChrome(null);
  // Assert
  // 件数の逐語固定は台帳テスト（chrome_tokens.test.js）が持つ。ここでは台帳と現行値写像が
  //   同じ濃度であること＝走査に穴が無いことだけを確かめ、恒等は全 slot について見る。
  assert.equal(CHROME_SLOTS.length, Object.keys(CHROME_CURRENT).length, '台帳と現行値写像の濃度が一致');
  assert.ok(CHROME_SLOTS.length >= 20, 'チャート本体の 20 点が失われていない');
  for (const slot of CHROME_SLOTS) {
    assert.equal(slots[slot.id], CHROME_CURRENT[slot.id], `${slot.id}: 現行リテラルから動いた`);
  }
});

test('TC-DW07 恒等回帰: 宣言 0 件のテーマでもクロム 20 slot が現行リテラルのまま', () => {
  // Arrange: 導出が「宣言が無いのに値を作る」経路になっていないことの固定。
  const theme = projectThemeForUse({ themeId: 'thm#1', name: 't', roleColors: {} }).theme;
  // Act
  const { slots } = resolveAllChrome(theme);
  // Assert
  for (const slot of CHROME_SLOTS) {
    assert.equal(slots[slot.id], CHROME_CURRENT[slot.id], `${slot.id}: 恒等テーマで動いた`);
  }
});

test('TC-DW08 恒等回帰: 全 26 指標の全系列色がテーマ未設定・恒等テーマで payload 色のまま', () => {
  // Arrange: 実描画色の解決入力（§4.5）。テーマが無ければ payload 色（backend 既定）が返る。
  const defs = list();
  assert.equal(defs.length, 26, '指標は 26 件');
  const identity = projectThemeForUse({ themeId: 'thm#1', name: 't', roleColors: {} }).theme;
  // Act / Assert
  let checked = 0;
  for (const def of defs) {
    for (const s of def.series) {
      // 系列ごとに異なる番兵色を渡し、「返ってきた色 = 渡した色」を全数で確かめる
      //   （既定色 #2962ff への捏造・意味色の漏れの双方が落ちる）。
      const payloadColor = `#${String(checked % 1000).padStart(3, '0')}abc`;
      for (const theme of [null, identity]) {
        const got = resolveSeriesColor({
          styles: null, seriesName: s.seriesName, role: s.colorRole, theme, payloadColor,
        });
        assert.equal(got, payloadColor,
          `${def.id} / ${s.seriesName} / role=${s.colorRole}: 色が動いた`);
      }
      checked += 1;
    }
  }
  assert.equal(checked, 97, 'SeriesDef 総数（§4.1.5 合計）');
});

// =========================================================================
// 通過条件 7（構造）: 警告は保存を妨げない
// =========================================================================

test('TC-DW09 走査: color_themes.js は color_diagnostics を 1 本も import しない', () => {
  // Arrange: 「警告は保存を妨げない」を規約ではなく構造で示す。保存の単一情報源が診断を知らない
  //   なら、診断結果が保存の可否に影響する経路は存在し得ない。
  const src = readFileSync(abs('../js/usecase/color_themes.js'), 'utf8');
  // Act / Assert
  assert.equal(src.includes('color_diagnostics'), false,
    'color_themes.js に color_diagnostics への参照が現れた');
  assert.equal(src.includes('diagnose'), false, '診断関数名への参照も現れない');
});

test('TC-DW10 走査: color_resolver.js は導出・診断のいずれにも依存しない', () => {
  // Arrange: 適用（色の決定）は本段階で 1 行も変えない。導出は射影で済んでおり、resolver が
  //   導出を知る必要は無い（知らせると導出の有無が 2 箇所で決まる）。
  const src = readFileSync(abs('../js/usecase/color_resolver.js'), 'utf8');
  // Act / Assert
  assert.equal(src.includes('color_derivation'), false);
  assert.equal(src.includes('color_diagnostics'), false);
});

// =========================================================================
// 通過条件 9: プリセット「基本」は 14 トークン全数を明示宣言したまま
// =========================================================================

test('TC-DW11 PRESET_THEMES「基本」は語彙 14 語を全数、明示宣言している', () => {
  // Arrange: プリセットを基点 5 語へ縮約すると、導出の検証がユーザー操作でしか行われなくなる。
  //   また現行の見た目（依頼者が確定した配色）が導出値へ置き換わってしまう。
  const preset = PRESET_THEMES[0];
  // Act / Assert
  assert.equal(preset.name, '基本');
  assert.deepEqual(Object.keys(preset.roleColors).sort(), [...COLOR_ROLES].sort());
});

test('TC-DW12 プリセット「基本」は射影を通しても 1 色も変わらない（全数宣言の帰結）', () => {
  // Arrange / Act
  const projected = projectThemeForUse(PRESET_THEMES[0]).theme;
  // Assert
  assert.deepEqual(projected.roleColors, { ...PRESET_THEMES[0].roleColors },
    '全数宣言なので導出が 1 件も起きない');
});
