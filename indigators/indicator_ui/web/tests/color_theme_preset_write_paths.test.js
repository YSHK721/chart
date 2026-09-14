// color_theme_preset_write_paths.test.js — 同梱プリセット（§9 T-1）に対する**書き込み 3 経路**
//   （改名・削除・保存／編集）を実利用点で固定する。
//
// 検出された欠陥（実測 2026-08-09・いずれも同一根）:
//   1. renameTheme('thm#0', …) が `{ok:false, code:'not_found'}` を返す（改名できない）
//   2. deleteTheme('thm#0') 後も一覧にプリセットが残る（削除できない・削除の記録先が無い）
//   3. saveTheme({name:'基本'}) が新規採番になり、一覧に `thm#0:基本` と `thm#1:基本` が二重に並ぶ
//   4. saveTheme({themeId:'thm#0', …}) も同様に新規採番になり、二重に並ぶ
//   根は 1 つ: 読み出し（一覧・適用）は**合成後の集合**で解決するのに、書き込み 3 経路は
//   **永続層だけ**で解決していた。見えている席に手が届かない＝「一度確定したテーマは変更できない」
//   という既に是正した不具合が、プリセットの席で再現していた。
//
// 直し方（症状の条件を避けるのではなく原因を除去する）:
//   書き込み 3 経路の**解決集合を読み出しと同じ合成後の集合に揃える**。プリセットが対象になったら
//   **同じ themeId のまま** `themes.v1` へ実体化する（新規採番しない）。削除はプリセット定義が
//   コード側に在るため行を消すだけでは復活するので、`removedPresets.v1` に削除を記録する。
//
// 設計入力（唯一の仕様源）: .doc/indicator-management-ui/基本設計_指標カラーテーマ.md
//   §4.9（永続化キーと値スキーマ）／§4.10（採番）／§4.11（上限）／§5.1（UC-C01 保存）／
//   §5.3（UC-C03 改名・削除）／§9 T-1（同梱プリセット）。
// 構造: Arrange-Act-Assert（AAA）。

import test from 'node:test';
import assert from 'node:assert/strict';

import { ColorThemeController } from '../js/adapter/front/color_theme_controller.js';
import { LocalStorageThemeGateway } from '../js/adapter/front/local_storage_theme_gateway.js';
import { PRESET_THEMES, isPresetThemeId } from '../js/usecase/color_themes.js';

const THEMES_KEY = 'indicatorUi.themes.v1';
const ACTIVE_KEY = 'indicatorUi.activeTheme.v1';
const REMOVED_KEY = 'indicatorUi.removedPresets.v1';

const PRESET = PRESET_THEMES[0];

// メモリ storage（tests/local_storage_theme_gateway.test.js と同作法）。**同一の storage** から
//   gateway と controller を作り直すことで「リロード相当」を再現する。
function makeStorage(seed = {}) {
  const map = new Map(Object.entries(seed));
  return {
    map,
    getItem: (k) => (map.has(k) ? map.get(k) : null),
    setItem: (k, v) => { map.set(k, String(v)); },
    removeItem: (k) => { map.delete(k); },
  };
}

// 契約 4 メンバーの最小 host。色の適用へ到達したら calls に載る（保存・改名・削除は色を変えない）。
function makeHost(calls) {
  return {
    _state: { applied: [] },
    _meta: new Map(),
    _applyStoredStyles: (id) => calls.push(`style:${id}`),
    _renderLegend: () => calls.push('legend'),
  };
}

// 同じ storage から協働子を作り直す＝リロード相当（起動時の復元経路をそのまま通す）。
function boot(storage, calls = []) {
  const chrome = [];
  const controller = new ColorThemeController(makeHost(calls), {
    gateway: new LocalStorageThemeGateway(storage),
    chromeApplier: { apply: (resolved) => { chrome.push(resolved); calls.push('chrome'); } },
    now: () => 777,
  });
  return { controller, chrome, calls };
}

const listed = (controller) => controller.themes().map((t) => [t.themeId, t.name]);
const storedThemes = (storage) => JSON.parse(storage.map.get(THEMES_KEY) ?? '{"themes":[]}').themes;
const storedRemoved = (storage) => JSON.parse(storage.map.get(REMOVED_KEY) ?? '{"ids":[]}').ids;

const USER_THEME = Object.freeze({
  themeId: 'thm#1',
  name: 'Ocean',
  roleColors: Object.freeze({ surface: '#0a0b0c', bullish: '#00ff00' }),
  tfModifier: null,
  createdAt: 100,
  updatedAt: 100,
});

const seedThemes = (themes) => ({ [THEMES_KEY]: JSON.stringify({ themes }) });

// ---------------------------------------------------------------------------
// 経路 1: プリセットの改名（欠陥 1）
// ---------------------------------------------------------------------------

test('経路 1 改名: プリセットを改名できる（一覧の名前が変わる）', () => {
  // Arrange
  const storage = makeStorage();
  const { controller } = boot(storage);
  // Act
  const res = controller.renameTheme(PRESET.themeId, ' 基本（改） ');
  // Assert
  assert.equal(res.ok, true, `プリセットを改名できない（code=${res.code}）`);
  assert.deepEqual(listed(controller), [[PRESET.themeId, '基本（改）']], '一覧の名前が変わっていない');
});

test('経路 1 改名: themes.v1 に同じ themeId の実体が 1 件だけ生まれる（新規採番しない）', () => {
  // Arrange
  const storage = makeStorage();
  const { controller } = boot(storage);
  // Act
  controller.renameTheme(PRESET.themeId, '基本（改）');
  // Assert
  const saved = storedThemes(storage);
  assert.equal(saved.length, 1, '実体化した行が 1 件でない');
  assert.equal(saved[0].themeId, PRESET.themeId, '新規採番されている（同じ席を直していない）');
  assert.equal(saved[0].name, '基本（改）');
  assert.deepEqual(saved[0].roleColors, PRESET.roleColors, '改名で色は変わらない（§5.3）');
  assert.equal(saved[0].createdAt, PRESET.createdAt, 'createdAt はプリセットの値を引き継ぐ');
  assert.equal(saved[0].updatedAt, 777, 'updatedAt を注入時刻で更新する');
  assert.deepEqual(
    JSON.parse(storage.map.get(ACTIVE_KEY) ?? '{"themeId":null,"lastSeq":0}').lastSeq ?? 0, 0,
    '実体化は採番を消費しない（§4.10）',
  );
});

test('経路 1 改名: 改名はリロードしても保たれる（一覧に元の名前が戻らない）', () => {
  // Arrange
  const storage = makeStorage();
  boot(storage).controller.renameTheme(PRESET.themeId, '基本（改）');
  // Act: 同じ storage で作り直す＝リロード相当
  const { controller } = boot(storage);
  // Assert
  assert.deepEqual(listed(controller), [[PRESET.themeId, '基本（改）']]);
});

test('経路 1 改名: 実体化してもメニューの並び（既定が先頭）は変わらない', () => {
  // Arrange: ユーザーのテーマが 1 件ある状態。
  const storage = makeStorage(seedThemes([USER_THEME]));
  const { controller } = boot(storage);
  assert.deepEqual(controller.themes().map((t) => t.themeId), [PRESET.themeId, 'thm#1'], '前提: 既定が先頭');
  // Act
  controller.renameTheme(PRESET.themeId, '基本（改）');
  // Assert
  assert.deepEqual(
    controller.themes().map((t) => t.themeId), [PRESET.themeId, 'thm#1'],
    '名前を直しただけで並びが変わっている（席が末尾へ移動した）',
  );
});

// ---------------------------------------------------------------------------
// 経路 2: プリセットの削除（欠陥 2）
// ---------------------------------------------------------------------------

test('経路 2 削除: プリセットを削除すると一覧から消え、removedPresets.v1 に id が入る', () => {
  // Arrange
  const storage = makeStorage();
  const { controller } = boot(storage);
  // Act
  controller.deleteTheme(PRESET.themeId);
  // Assert
  assert.deepEqual(listed(controller), [], '一覧からプリセットが消えていない');
  assert.deepEqual(storedRemoved(storage), [PRESET.themeId], '削除の記録が永続化されていない');
  assert.deepEqual(storedThemes(storage), [], '削除でプリセットが themes.v1 へ書き戻されてはならない');
});

test('経路 2 削除: 同じ gateway で作り直した協働子でも復活しない（リロード相当）', () => {
  // Arrange
  const storage = makeStorage();
  boot(storage).controller.deleteTheme(PRESET.themeId);
  // Act
  const { controller } = boot(storage);
  // Assert
  assert.deepEqual(listed(controller), [], '次回起動でプリセットが復活している');
});

test('経路 2 削除: 実体化済み（編集済み）のプリセットも削除でき、復活しない', () => {
  // Arrange: 一度改名して themes.v1 へ実体化させる。
  const storage = makeStorage();
  boot(storage).controller.renameTheme(PRESET.themeId, '基本（改）');
  assert.equal(storedThemes(storage).length, 1, '前提: 実体化している');
  // Act
  boot(storage).controller.deleteTheme(PRESET.themeId);
  // Assert
  assert.deepEqual(storedThemes(storage), [], '永続層の行が消えていない');
  assert.deepEqual(storedRemoved(storage), [PRESET.themeId], '削除の記録が無いと定義から復活する');
  assert.deepEqual(listed(boot(storage).controller), [], 'リロードで復活している');
});

test('経路 2 削除: 削除済みプリセットは改名の対象にならない（not_found）', () => {
  // Arrange
  const storage = makeStorage();
  boot(storage).controller.deleteTheme(PRESET.themeId);
  const { controller } = boot(storage);
  // Act
  const res = controller.renameTheme(PRESET.themeId, '復活');
  // Assert
  assert.equal(res.ok, false, '削除した席が改名で復活している');
  assert.equal(res.code, 'not_found');
  assert.deepEqual(storedThemes(storage), []);
});

test('経路 2 削除: 削除済みプリセットと同名の新規保存は新しい id を採番する（席を再利用しない）', () => {
  // Arrange
  const storage = makeStorage();
  boot(storage).controller.deleteTheme(PRESET.themeId);
  const { controller } = boot(storage);
  // Act
  const res = controller.saveTheme({ name: PRESET.name, roleColors: { bullish: '#010203' } });
  // Assert
  assert.equal(res.ok, true);
  assert.equal(isPresetThemeId(res.themeId), false, `削除済みプリセットの id を再利用している（${res.themeId}）`);
  assert.equal(res.themeId, 'thm#1');
  assert.deepEqual(listed(controller), [['thm#1', PRESET.name]], '一覧が二重になっている');
});

// ---------------------------------------------------------------------------
// 経路 3: プリセットの保存・編集（欠陥 3・4）
// ---------------------------------------------------------------------------

test('経路 3 保存: プリセットと同名で保存しても一覧が二重にならない（同じ席が更新される）', () => {
  // Arrange
  const storage = makeStorage();
  const { controller } = boot(storage);
  // Act
  const res = controller.saveTheme({ name: PRESET.name, roleColors: { bullish: '#010203' } });
  // Assert
  assert.equal(res.ok, true);
  assert.equal(res.themeId, PRESET.themeId, '新規採番されている（同名の上書きになっていない）');
  assert.deepEqual(listed(controller), [[PRESET.themeId, PRESET.name]], '一覧に同名が 2 件並んでいる');
  const saved = storedThemes(storage);
  assert.equal(saved.length, 1);
  assert.deepEqual(saved[0].roleColors, { bullish: '#010203' }, 'roleColors は置換（§5.1 処理 2）');
  assert.equal(saved[0].createdAt, PRESET.createdAt);
  assert.equal(saved[0].updatedAt, 777);
});

test('経路 3 編集: themeId 指定の保存はプリセットの席を更新する（名前を変えても増えない）', () => {
  // Arrange
  const storage = makeStorage();
  const { controller } = boot(storage);
  // Act
  const res = controller.saveTheme({
    themeId: PRESET.themeId, name: '基本（編集）', roleColors: { alert: '#ffffff' },
  });
  // Assert
  assert.equal(res.ok, true);
  assert.equal(res.themeId, PRESET.themeId);
  assert.deepEqual(listed(controller), [[PRESET.themeId, '基本（編集）']]);
  assert.deepEqual(storedThemes(storage).map((t) => t.themeId), [PRESET.themeId], '席が 2 つに割れている');
  assert.deepEqual(
    JSON.parse(storage.map.get(ACTIVE_KEY) ?? '{"lastSeq":0}').lastSeq ?? 0, 0,
    '編集で採番を消費している（§4.10）',
  );
});

test('経路 3 編集: 編集後の値がリロード後の一覧・適用に反映される', () => {
  // Arrange
  const storage = makeStorage();
  boot(storage).controller.saveTheme({
    themeId: PRESET.themeId, name: '基本（編集）', roleColors: { bullish: '#010203' },
  });
  // Act
  const { controller } = boot(storage);
  const applied = controller.applyTheme(PRESET.themeId);
  // Assert
  assert.equal(applied, PRESET.themeId, '実体化した席が適用できない');
  assert.equal(controller.activeTheme().roleColors.bullish, '#010203', '編集した色が適用に届いていない');
  assert.deepEqual(listed(controller), [[PRESET.themeId, '基本（編集）']]);
});

test('経路 3 保存: プリセットと同名のユーザーテーマを新規に作れない（重複として拒否する）', () => {
  // Arrange: 既にユーザーのテーマが 1 件ある状態で、プリセットの名前へ改名しようとする。
  const storage = makeStorage(seedThemes([USER_THEME]));
  const { controller } = boot(storage);
  // Act
  const res = controller.renameTheme('thm#1', PRESET.name);
  // Assert
  assert.equal(res.ok, false, '一覧に同名が 2 件並ぶ改名が通っている');
  assert.equal(res.code, 'duplicate');
});

// ---------------------------------------------------------------------------
// 回帰: ユーザー作成テーマは従来どおり／既定状態の見た目は不変
// ---------------------------------------------------------------------------

test('回帰: ユーザーテーマの保存・改名・削除はプリセット導入で挙動が変わらない', () => {
  // Arrange
  const storage = makeStorage();
  const { controller } = boot(storage);
  // Act: 新規保存 → 改名 → 削除
  const saved = controller.saveTheme({ name: 'Ocean', roleColors: { surface: '#0a0b0c' } });
  const renamed = controller.renameTheme('thm#1', 'Sunset');
  const beforeDelete = storedThemes(storage);
  controller.deleteTheme('thm#1');
  // Assert
  assert.equal(saved.themeId, 'thm#1', '採番は従来どおり lastSeq + 1');
  assert.equal(renamed.ok, true);
  assert.deepEqual(beforeDelete.map((t) => [t.themeId, t.name]), [['thm#1', 'Sunset']]);
  assert.deepEqual(storedThemes(storage), [], '削除で永続層から消える');
  assert.deepEqual(listed(controller), [[PRESET.themeId, PRESET.name]], 'プリセットは無傷で残る');
  assert.deepEqual(storedRemoved(storage), [], 'ユーザーテーマの削除で削除記録を汚さない');
});

test('回帰: プリセットへの書き込みは適用ではない（クロムも系列も凡例も動かない・§5.1/§5.3）', () => {
  // Arrange
  const storage = makeStorage();
  const calls = [];
  const { controller, chrome } = boot(storage, calls);
  calls.length = 0;
  // Act
  controller.saveTheme({ themeId: PRESET.themeId, name: PRESET.name, roleColors: { bullish: '#010203' } });
  controller.renameTheme(PRESET.themeId, '基本（改）');
  controller.deleteTheme(PRESET.themeId);
  // Assert
  assert.deepEqual(calls.filter((c) => c === 'chrome' || c.startsWith('style:') || c === 'legend'), []);
  assert.deepEqual(chrome, [], 'クロムが配られている（既定状態の見た目が変わる）');
  assert.equal(controller.activeThemeId(), null, 'テーマ未選択のまま（保存・改名・削除は適用ではない）');
});

test('回帰: 既定状態（テーマ未設定）では removedPresets.v1 を書かない（キーを増やさない）', () => {
  // Arrange / Act
  const storage = makeStorage();
  const { controller } = boot(storage);
  controller.saveTheme({ name: 'Ocean', roleColors: {} });
  controller.applyTheme('thm#1');
  // Assert
  assert.equal(storage.map.has(REMOVED_KEY), false, 'プリセットを削除していないのに削除記録が書かれている');
});
