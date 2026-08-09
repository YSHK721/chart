// local_storage_theme_gateway.js（ThemeStorePort 実装）のテスト。
//
// 設計入力（唯一の仕様源）: .doc/indicator-management-ui/基本設計_指標カラーテーマ.md v0.3.0
//   §4.9（2 論理キー `indicatorUi.themes.v1` / `indicatorUi.activeTheme.v1` と値スキーマ・
//        activeTheme は「選択中テーマ ＋ 採番カウンタ」の**単一原子**・破損時は当該キーのみ
//        空既定へ初期化し他キーを温存＋console 警告・QuotaExceeded は当該書き込み中止で
//        例外を投げない・接頭辞を自前で付けず注入 storage をそのまま使う）、
//   §4.10（lastSeq 単調・id の再利用禁止）、§5.7（F-C4 破損 / F-C5 Quota）。
// 参照実装（同型元）: js/adapter/front/local_storage_template_gateway.js ／
//   tests/local_storage_template_gateway.test.js（fakeStorage の作法）。
// 構造: Arrange-Act-Assert（AAA）。
//
// ThemeStorePort は 4 メソッドちょうど（lastSeq 専用メソッドに割らない＝§4.9 の原子性単位に一致）:
//   loadThemes() -> COLOR_THEME[]              saveThemes(themes) -> void
//   loadActiveTheme() -> { themeId, lastSeq }  saveActiveTheme({ themeId, lastSeq }) -> void

import { test } from 'node:test';
import assert from 'node:assert/strict';

async function load() {
  return import('../js/adapter/front/local_storage_theme_gateway.js');
}

const KEY_THEMES = 'indicatorUi.themes.v1';
const KEY_ACTIVE = 'indicatorUi.activeTheme.v1';

// Fake localStorage（tests/local_storage_template_gateway.test.js と同作法）。
function fakeStorage(initial = {}) {
  const map = new Map(Object.entries(initial));
  return {
    map,
    quotaExceeded: false,
    getItem(k) { return map.has(k) ? map.get(k) : null; },
    setItem(k, v) {
      if (this.quotaExceeded) { const e = new Error('quota'); e.name = 'QuotaExceededError'; throw e; }
      map.set(k, String(v));
    },
    removeItem(k) { map.delete(k); },
  };
}

function captureWarn(fn) {
  const original = console.warn;
  const seen = [];
  console.warn = (...args) => { seen.push(args.join(' ')); };
  try { fn(); } finally { console.warn = original; }
  return seen;
}

const THEME = {
  themeId: 'thm#1',
  name: 'ダーク',
  roleColors: { bullish: '#26a69a', bearish: '#ef5350' },
  tfModifier: { '1D': 0.2 },
  createdAt: 1000,
  updatedAt: 1000,
};

// ---------------------------------------------------------------------------
// themes.v1 の読み書き（§4.9）
// ---------------------------------------------------------------------------

test('TC-T01 themes: 往復し、物理キーは indicatorUi.themes.v1・値は { themes: [...] }（§4.9）', async () => {
  // Arrange
  const { LocalStorageThemeGateway } = await load();
  const storage = fakeStorage();
  const gw = new LocalStorageThemeGateway(storage);
  // Act
  gw.saveThemes([THEME]);
  // Assert
  assert.deepEqual(gw.loadThemes(), [THEME], '往復して同値が読み出せる');
  assert.deepEqual(JSON.parse(storage.map.get(KEY_THEMES)), { themes: [THEME] }, '値スキーマは { themes: [...] }');
});

test('TC-T02 themes: キー未設定は空既定 []（§4.9 空既定）', async () => {
  // Arrange
  const { LocalStorageThemeGateway } = await load();
  const gw = new LocalStorageThemeGateway(fakeStorage());
  // Act / Assert
  assert.deepEqual(gw.loadThemes(), []);
});

test('TC-T03 themes: 配列でない入力は空集合として書き込む（保存形を壊さない）', async () => {
  // Arrange
  const { LocalStorageThemeGateway } = await load();
  const storage = fakeStorage();
  const gw = new LocalStorageThemeGateway(storage);
  // Act
  gw.saveThemes(null);
  // Assert
  assert.deepEqual(JSON.parse(storage.map.get(KEY_THEMES)), { themes: [] });
});

test('TC-T04 注入された storage をそのまま使う＝接頭辞を自前で付けない（§4.9・E-17）', async () => {
  // Arrange
  const { LocalStorageThemeGateway } = await load();
  const storage = fakeStorage();
  const gw = new LocalStorageThemeGateway(storage);
  // Act
  gw.saveThemes([THEME]);
  gw.saveActiveTheme({ themeId: 'thm#1', lastSeq: 1 });
  // Assert: scopedStorage 側が付ける live: 等の接頭辞を gateway が二重に付けない
  assert.deepEqual([...storage.map.keys()].sort(), [KEY_ACTIVE, KEY_THEMES].sort());
});

// ---------------------------------------------------------------------------
// activeTheme.v1 の読み書き（§4.9 単一原子・§4.10 lastSeq）
// ---------------------------------------------------------------------------

test('TC-T05 activeTheme: 選択中テーマと lastSeq を 1 キーで往復する（§4.9 単一原子）', async () => {
  // Arrange
  const { LocalStorageThemeGateway } = await load();
  const storage = fakeStorage();
  const gw = new LocalStorageThemeGateway(storage);
  // Act
  gw.saveActiveTheme({ themeId: 'thm#3', lastSeq: 3 });
  // Assert
  assert.deepEqual(gw.loadActiveTheme(), { themeId: 'thm#3', lastSeq: 3 });
  assert.deepEqual(JSON.parse(storage.map.get(KEY_ACTIVE)), { themeId: 'thm#3', lastSeq: 3 }, '値スキーマは { themeId, lastSeq }');
  assert.equal(storage.map.size, 1, '採番カウンタを別キーへ割らない（原子性単位を割らない）');
});

test('TC-T06 activeTheme: キー未設定は空既定 { themeId: null, lastSeq: 0 }（§4.9・§4.10 初回発行 thm#1）', async () => {
  // Arrange
  const { LocalStorageThemeGateway } = await load();
  const gw = new LocalStorageThemeGateway(fakeStorage());
  // Act / Assert
  assert.deepEqual(gw.loadActiveTheme(), { themeId: null, lastSeq: 0 });
});

test('TC-T07 activeTheme: テーマ未選択（themeId = null）を保存・復元できる（§5.3 削除後・F-C6 解決後）', async () => {
  // Arrange
  const { LocalStorageThemeGateway } = await load();
  const storage = fakeStorage();
  const gw = new LocalStorageThemeGateway(storage);
  // Act
  gw.saveActiveTheme({ themeId: null, lastSeq: 12 });
  // Assert
  let active;
  const warns = captureWarn(() => { active = gw.loadActiveTheme(); });
  assert.deepEqual(active, { themeId: null, lastSeq: 12 }, '全テーマ削除後も lastSeq は減算しない（§4.10）');
  assert.equal(warns.length, 0, 'テーマ未選択は正常系であり破損ではない（警告を出さない）');
});

test('TC-T08 activeTheme: 保存形を満たさない入力は空既定へ倒して書き込む（themeId は string|null・lastSeq は 0 以上の整数）', async () => {
  // Arrange
  const { LocalStorageThemeGateway } = await load();
  const storage = fakeStorage();
  const gw = new LocalStorageThemeGateway(storage);
  // Act
  gw.saveActiveTheme({ themeId: 7, lastSeq: -1 });
  // Assert
  assert.deepEqual(JSON.parse(storage.map.get(KEY_ACTIVE)), { themeId: null, lastSeq: 0 });
  // Act
  gw.saveActiveTheme(undefined);
  // Assert
  assert.deepEqual(JSON.parse(storage.map.get(KEY_ACTIVE)), { themeId: null, lastSeq: 0 }, '引数なしでも例外を投げない');
});

// ---------------------------------------------------------------------------
// 破損時挙動（F-C4・§4.9）
// ---------------------------------------------------------------------------

test('TC-T09 破損（JSON パース不能）は当該キーのみ空既定へ初期化し他キーを温存する（F-C4）', async () => {
  // Arrange: themes が破損、activeTheme は正常
  const { LocalStorageThemeGateway } = await load();
  const storage = fakeStorage({
    [KEY_THEMES]: '{ broken json',
    [KEY_ACTIVE]: JSON.stringify({ themeId: 'thm#2', lastSeq: 2 }),
  });
  const gw = new LocalStorageThemeGateway(storage);
  // Act
  let themes;
  const warns = captureWarn(() => { themes = gw.loadThemes(); });
  // Assert
  assert.deepEqual(themes, [], '破損キーは空既定へ初期化する');
  assert.deepEqual(gw.loadActiveTheme(), { themeId: 'thm#2', lastSeq: 2 }, '他キーは温存する（全消去しない）');
  assert.ok(warns.length > 0, 'console に警告する（F-C4）');
});

test('TC-T10 破損（activeTheme 側）も当該キーのみ初期化し themes を温存する（F-C4）', async () => {
  // Arrange
  const { LocalStorageThemeGateway } = await load();
  const storage = fakeStorage({
    [KEY_THEMES]: JSON.stringify({ themes: [THEME] }),
    [KEY_ACTIVE]: 'not json at all',
  });
  const gw = new LocalStorageThemeGateway(storage);
  // Act
  let active;
  const warns = captureWarn(() => { active = gw.loadActiveTheme(); });
  // Assert
  assert.deepEqual(active, { themeId: null, lastSeq: 0 }, '空既定へ初期化する（呼び出し側が recoverLastSeq で引き上げる・§4.10）');
  assert.deepEqual(gw.loadThemes(), [THEME], 'テーマ集合は温存する');
  assert.ok(warns.length > 0);
});

test('TC-T11 スキーマ不一致も当該キーのみ空既定へ倒す（themes 非配列 / themeId 非文字列 / lastSeq 非整数）（F-C4）', async () => {
  // Arrange: JSON としては妥当だがスキーマ不一致
  const { LocalStorageThemeGateway } = await load();
  const storage = fakeStorage({
    [KEY_THEMES]: JSON.stringify({ themes: 'not-an-array' }),
    [KEY_ACTIVE]: JSON.stringify({ themeId: 7, lastSeq: 'NaN' }),
  });
  const gw = new LocalStorageThemeGateway(storage);
  // Act / Assert
  assert.deepEqual(gw.loadThemes(), [], 'themes が配列でなければ空既定');
  assert.deepEqual(gw.loadActiveTheme(), { themeId: null, lastSeq: 0 });
});

test('TC-T11b スキーマ不一致でも console に警告する（§4.9「JSON パース不能・スキーマ不一致 … console に警告」）', async () => {
  // Arrange
  const { LocalStorageThemeGateway } = await load();
  const gwThemes = new LocalStorageThemeGateway(fakeStorage({ [KEY_THEMES]: JSON.stringify({ themes: 'not-an-array' }) }));
  const gwActive = new LocalStorageThemeGateway(fakeStorage({ [KEY_ACTIVE]: JSON.stringify({ themeId: 7, lastSeq: 'NaN' }) }));
  const gwClean = new LocalStorageThemeGateway(fakeStorage());
  // Act
  const themeWarns = captureWarn(() => gwThemes.loadThemes());
  const activeWarns = captureWarn(() => gwActive.loadActiveTheme());
  const cleanWarns = captureWarn(() => { gwClean.loadThemes(); gwClean.loadActiveTheme(); });
  // Assert
  assert.equal(themeWarns.length, 1, 'themes のスキーマ不一致を黙って捨てない');
  assert.equal(activeWarns.length, 1, 'activeTheme のスキーマ不一致も 1 回だけ警告する');
  assert.equal(cleanWarns.length, 0, 'キー未設定（初回起動）は破損ではないので警告しない');
});

test('TC-T12 部分的にしか解釈できない activeTheme は解釈できた領域を残す（§4.9 前方互換）', async () => {
  // Arrange: lastSeq は妥当・themeId だけ壊れている
  const { LocalStorageThemeGateway } = await load();
  const storage = fakeStorage({ [KEY_ACTIVE]: JSON.stringify({ themeId: { bad: true }, lastSeq: 9 }) });
  const gw = new LocalStorageThemeGateway(storage);
  // Act
  const active = gw.loadActiveTheme();
  // Assert
  assert.deepEqual(active, { themeId: null, lastSeq: 9 }, 'lastSeq を捨てると id が再利用され衝突する（§4.10）');
});

// ---------------------------------------------------------------------------
// QuotaExceeded（F-C5・§4.9）
// ---------------------------------------------------------------------------

test('TC-T13 QuotaExceeded は当該書き込みを中止し例外を投げない（F-C5）', async () => {
  // Arrange
  const { LocalStorageThemeGateway } = await load();
  const storage = fakeStorage();
  const gw = new LocalStorageThemeGateway(storage);
  storage.quotaExceeded = true;
  // Act / Assert
  const warns = captureWarn(() => {
    assert.doesNotThrow(() => gw.saveThemes([THEME]), 'themes 書き込みで例外を投げない');
    assert.doesNotThrow(() => gw.saveActiveTheme({ themeId: 'thm#1', lastSeq: 1 }), 'activeTheme 書き込みで例外を投げない');
  });
  assert.equal(storage.map.has(KEY_THEMES), false, '当該書き込みは中止される');
  assert.equal(storage.map.has(KEY_ACTIVE), false);
  assert.ok(warns.length > 0, 'console に警告する（F-C5）');
});

test('TC-T14 QuotaExceeded で失敗した書き込みは既存の他キーを壊さない（F-C5）', async () => {
  // Arrange: 既に永続化済みの activeTheme がある状態で themes 書き込みが Quota で失敗する
  const { LocalStorageThemeGateway } = await load();
  const storage = fakeStorage({ [KEY_ACTIVE]: JSON.stringify({ themeId: 'thm#1', lastSeq: 1 }) });
  const gw = new LocalStorageThemeGateway(storage);
  storage.quotaExceeded = true;
  // Act
  captureWarn(() => gw.saveThemes([THEME]));
  storage.quotaExceeded = false;
  // Assert
  assert.deepEqual(gw.loadActiveTheme(), { themeId: 'thm#1', lastSeq: 1 });
});

test('TC-T15 storage の読み取り自体が失敗しても空既定を返し例外を投げない（全域性）', async () => {
  // Arrange: プライバシー設定等で getItem が throw する storage
  const { LocalStorageThemeGateway } = await load();
  const gw = new LocalStorageThemeGateway({
    getItem() { throw new Error('SecurityError'); },
    setItem() { throw new Error('SecurityError'); },
    removeItem() { throw new Error('SecurityError'); },
  });
  // Act / Assert
  assert.deepEqual(gw.loadThemes(), []);
  assert.deepEqual(gw.loadActiveTheme(), { themeId: null, lastSeq: 0 });
  captureWarn(() => {
    assert.doesNotThrow(() => gw.saveThemes([THEME]));
    assert.doesNotThrow(() => gw.saveActiveTheme({ themeId: null, lastSeq: 0 }));
  });
});
