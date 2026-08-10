// color_theme_controller.test.js — テーマ協働子（ColorThemeController）の振る舞い固定。
//
// 設計入力（唯一の仕様源）: .doc/indicator-management-ui/基本設計_指標カラーテーマ.md v0.3.1
//   §5.1（UC-C01 保存＝適用ではない）／§5.2（UC-C02 適用の手順順序）／§5.3（UC-C03 改名・削除の
//   非対称性）／§5.7（F-C6 dangling）／§3.4（ビュー自動介入の禁止）／§7.3 ISP（契約 4 メンバー）。
//
// 固定する不変条件:
//   (1) UC-C02 の手順順序 1→2→3→4（永続化 → クロム → 系列 → 凡例）。凡例は反復の外で 1 回。
//   (2) 色の書き手は host._applyStoredStyles ただ 1 つ（協働子は系列を走査しない・R-1）。
//   (3) 未描画（_meta 不在）インスタンスはスキップする。
//   (4) 保存・改名・削除はチャート上の色を変えない（§5.1 後条件・§5.3 の非対称性）。
//   (5) 契約は 4 メンバーちょうどで、射影（createHostView）が契約外アクセスを実行時に落とす。
//   (6) §3.4: /compute・setData・ビュー操作 API へ到達しない。
// 構造: Arrange-Act-Assert（AAA）。

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

import {
  COLOR_THEME_HOST_CONTRACT,
  ColorThemeController,
} from '../js/adapter/front/color_theme_controller.js';
import { CHROME_SLOTS } from '../js/usecase/chrome_tokens.js';
import { createHostView } from '../js/adapter/front/host_view.js';
import { LocalStorageThemeGateway } from '../js/adapter/front/local_storage_theme_gateway.js';
import { resolveAllChrome, resolveSeriesColor } from '../js/usecase/color_resolver.js';
import { list } from '../js/usecase/catalog.js';
import {
  CODE, PRESET_THEMES, isPresetThemeId, projectThemeForUse,
} from '../js/usecase/color_themes.js';

const THEMES_KEY = 'indicatorUi.themes.v1';
const ACTIVE_KEY = 'indicatorUi.activeTheme.v1';

// 同梱プリセット（§9 T-1）。**一覧に見える集合**と**永続層に書かれた集合**は別物である。
//   - 一覧 = `controller.themes()` … プリセットを合成した集合（先頭がプリセット）。
//   - 永続層 = `themes.v1` … 保存された原形のみ。プリセットは 1 件も書き込まれない。
//   この 2 つを混ぜて数えると「プリセットを永続層へ書いてしまった」実装でもテストが通る。
//   よって以下では必ず別々に表明する。
const PRESET = PRESET_THEMES[0];
const storedThemes = (storage) => JSON.parse(storage._map.get(THEMES_KEY) ?? '{"themes":[]}').themes;
const listedIds = (controller) => controller.themes().map((t) => t.themeId);

const THEME_A = Object.freeze({
  themeId: 'thm#1',
  name: 'Ocean',
  roleColors: Object.freeze({ surface: '#0a0b0c', bullish: '#00ff00' }),
  tfModifier: null,
  createdAt: 100,
  updatedAt: 100,
});

// 実 gateway（LocalStorageThemeGateway）＋メモリ storage。書き込みは呼び出し順を calls へ記録し、
//   手順 1（永続化）が手順 2（クロム）より先であることを順序で固定できるようにする。
function makeStorage(calls, seed = {}) {
  const map = new Map(Object.entries(seed));
  return {
    getItem: (k) => (map.has(k) ? map.get(k) : null),
    setItem: (k, v) => { map.set(k, v); calls.push(`put:${k}`); },
    removeItem: (k) => { map.delete(k); },
    _map: map,
  };
}

// host の実体。契約外の面（_renderer / _compute）を意図的に持たせ、射影が遮断することと
//   「協働子から一度も到達しない」ことの双方を検査できるようにする（§3.4 のスパイ固定）。
function makeHost(calls, { applied = [], drawn = null } = {}) {
  const raw = {
    _state: { applied },
    _meta: new Map((drawn ?? applied.map((i) => i.instanceId)).map((id) => [id, { def: {} }])),
    _applyStoredStyles: (instanceId) => calls.push(`style:${instanceId}`),
    _renderLegend: () => calls.push('legend'),
    // 契約外（到達したら 0 でなくなる／射影が例外にする）。
    _renderer: { setData: () => calls.push('VIOLATION:setData'), applySeriesStyle: () => calls.push('VIOLATION:applySeriesStyle') },
    _compute: { compute: async () => calls.push('VIOLATION:compute') },
    _persistAll: () => calls.push('VIOLATION:_persistAll'),
  };
  return { raw, view: createHostView(raw, COLOR_THEME_HOST_CONTRACT) };
}

function makeChrome(calls) {
  const payloads = [];
  return {
    applier: { apply: (resolved) => { payloads.push(resolved); calls.push('chrome'); } },
    payloads,
  };
}

// 既定は「state 未注入」＝協働子が gateway から自力で復元する経路（単体の既定）。
//   構築時の復旧書き込み（§4.10 lastSeq の引き上げ）は Act の観測を汚すため calls を切り直す。
//   now は既定で固定時刻 `() => 777`（決定論）。`now: undefined` を明示すると既定つき注入の
//   既定側（実時刻）を通せる＝本番経路の検証に使う。
const FIXED_NOW = () => 777;
function makeController(calls, {
  applied = [], drawn = null, seed = {}, themes = [], now = FIXED_NOW,
} = {}) {
  const storage = makeStorage(calls, { [THEMES_KEY]: JSON.stringify({ themes }), ...seed });
  const gateway = new LocalStorageThemeGateway(storage);
  const { raw, view } = makeHost(calls, { applied, drawn });
  const { applier, payloads } = makeChrome(calls);
  const controller = new ColorThemeController(view, {
    gateway, chromeApplier: applier, now,
  });
  calls.length = 0;
  return {
    controller, storage, gateway, raw, view, applier, payloads,
  };
}

const inst = (instanceId) => ({ instanceId, indicatorId: 'x', params: {}, styles: null });

// ---- 契約（§7.3 ISP・4 メンバーちょうど）------------------------------------

test('契約: COLOR_THEME_HOST_CONTRACT は ThemeHost の 4 メンバーちょうどを凍結公開する', () => {
  // Arrange / Act
  const c = COLOR_THEME_HOST_CONTRACT;
  // Assert
  assert.equal(c.role, 'ThemeHost');
  assert.deepEqual([...c.methods], ['_applyStoredStyles', '_renderLegend']);
  assert.deepEqual([...c.fields], ['_state', '_meta']);
  assert.equal(c.methods.length + c.fields.length, 4, '契約が 4 メンバーちょうどでない');
  assert.ok(Object.isFrozen(c) && Object.isFrozen(c.methods) && Object.isFrozen(c.fields));
});

test('契約: 射影は契約外の host メンバーへのアクセスを実行時に落とす', () => {
  // Arrange
  const calls = [];
  const { raw, view } = makeHost(calls, { applied: [] });
  // Act / Assert
  assert.doesNotThrow(() => view._state);
  assert.doesNotThrow(() => view._meta);
  assert.equal(typeof raw._renderer, 'object', '前提: host には契約外の面が実在する');
  assert.throws(() => view._renderer, /契約外の host メンバー/);
  assert.throws(() => view._compute, /契約外の host メンバー/);
  assert.throws(() => view._persistAll, /契約外の host メンバー/);
});

// ---- UC-C02 適用（§5.2）-----------------------------------------------------

test('UC-C02: 手順 1→2→3→4 の順（永続化 → クロム → 系列 → 凡例）で実行する', () => {
  // Arrange
  const calls = [];
  const { controller } = makeController(calls, {
    applied: [inst('a'), inst('b')], themes: [THEME_A],
  });
  // Act
  controller.applyTheme('thm#1');
  // Assert
  assert.deepEqual(calls, [`put:${ACTIVE_KEY}`, 'chrome', 'style:a', 'style:b', 'legend']);
});

test('UC-C02: クロムは選択したテーマで解決された値を配る', () => {
  // Arrange
  const calls = [];
  const { controller, payloads } = makeController(calls, {
    applied: [inst('a')], themes: [THEME_A],
  });
  // Act
  controller.applyTheme('thm#1');
  // Assert
  assert.equal(payloads.length, 1, 'クロム配信が 1 回でない');
  // 配るのは **消費のための射影**（activeTheme()）を解決した値である（_applyChrome の実体）。
  //   ここを原形 THEME_A で書くと、射影が恒等でない限り一致しない。段階 5-B（導出）で
  //   部分宣言テーマの射影が恒等でなくなったため、期待値を契約どおりの形へ直す。
  assert.deepEqual(payloads[0], resolveAllChrome(projectThemeForUse(THEME_A).theme));
  assert.equal(payloads[0].slots.layoutBackground, '#0a0b0c');
});

test('UC-C02: 未描画（_meta 不在）のインスタンスはスキップする', () => {
  // Arrange
  const calls = [];
  const { controller } = makeController(calls, {
    applied: [inst('a'), inst('b'), inst('c')], drawn: ['a', 'c'], themes: [THEME_A],
  });
  // Act
  controller.applyTheme('thm#1');
  // Assert
  assert.deepEqual(calls.filter((c) => c.startsWith('style:')), ['style:a', 'style:c']);
});

test('UC-C02: 凡例の再描画は反復の外で 1 回だけ（インスタンス数に依存しない）', () => {
  // Arrange
  const calls = [];
  const { controller } = makeController(calls, {
    applied: [inst('a'), inst('b'), inst('c')], themes: [THEME_A],
  });
  // Act
  controller.applyTheme('thm#1');
  // Assert
  assert.equal(calls.filter((c) => c === 'legend').length, 1);
  assert.equal(calls.at(-1), 'legend', '凡例が反復の外（最後）で呼ばれていない');
});

test('UC-C02: activeTheme.v1 へ themeId が永続化される（lastSeq は温存）', () => {
  // Arrange
  const calls = [];
  const { controller, storage } = makeController(calls, {
    applied: [], themes: [THEME_A],
    seed: { [ACTIVE_KEY]: JSON.stringify({ themeId: null, lastSeq: 7 }) },
  });
  // Act
  controller.applyTheme('thm#1');
  // Assert
  assert.deepEqual(JSON.parse(storage._map.get(ACTIVE_KEY)), { themeId: 'thm#1', lastSeq: 7 });
  assert.equal(controller.activeThemeId(), 'thm#1');
});

test('UC-C02: 「テーマなし」(null) の適用は themeId=null を永続化し既定クロムを配る', () => {
  // Arrange
  const calls = [];
  const { controller, storage, payloads } = makeController(calls, {
    applied: [inst('a')], themes: [THEME_A],
  });
  controller.applyTheme('thm#1');
  // Act
  controller.applyTheme(null);
  // Assert
  assert.deepEqual(JSON.parse(storage._map.get(ACTIVE_KEY)), { themeId: null, lastSeq: 1 });
  assert.deepEqual(payloads.at(-1), resolveAllChrome(null));
  assert.equal(controller.activeTheme(), null);
});

test('UC-C02 / §3.4: 適用は /compute・setData・契約外の面へ一切到達しない', () => {
  // Arrange
  const calls = [];
  const { controller } = makeController(calls, {
    applied: [inst('a'), inst('b')], themes: [THEME_A],
  });
  // Act
  controller.applyTheme('thm#1');
  // Assert
  assert.deepEqual(calls.filter((c) => c.startsWith('VIOLATION')), []);
});

// 一覧に見えるもの（＝ユーザーが選べるもの）は、すべて適用できなければならない。同梱プリセット
//   （§9 T-1）は永続層に無い＝合成でのみ見える要素なので、適用時の解決を永続層だけで行うと
//   「メニューに出ているのに押しても既定色のまま」という無言の死になる（F-C6 の誤発火）。
//   loadThemeState が合成集合で解決する（起動時にプリセット選択を保持できる）こととも対称。
test('UC-C02: 同梱プリセットも適用できる（一覧に見えるものは適用対象・§9 T-1）', () => {
  // Arrange
  const calls = [];
  const warned = [];
  const original = console.warn;
  console.warn = (m) => warned.push(String(m));
  try {
    const { controller, storage, payloads } = makeController(calls, { applied: [], themes: [] });
    // Act
    const applied = controller.applyTheme(PRESET.themeId);
    // Assert
    assert.equal(applied, PRESET.themeId, 'プリセットの適用が F-C6 で縮退している');
    assert.equal(controller.activeThemeId(), PRESET.themeId);
    assert.equal(controller.activeTheme().themeId, PRESET.themeId);
    assert.deepEqual(payloads.at(-1), resolveAllChrome(PRESET), '地はプリセットの色で配られる');
    assert.deepEqual(warned, [], 'プリセットの適用は不在警告を出さない');
    // 選択の記録は activeTheme.v1（id のみ）。テーマ実体は永続層へ書き込まれない。
    assert.deepEqual(
      JSON.parse(storage._map.get(ACTIVE_KEY)), { themeId: PRESET.themeId, lastSeq: 0 },
    );
    assert.deepEqual(storedThemes(storage), [], '適用でプリセットが themes.v1 へ実体化しない');
  } finally {
    console.warn = original;
  }
});

test('F-C6: テーマ集合に不在の themeId は「テーマ未選択」へ縮退し null を永続化する', () => {
  // Arrange
  const calls = [];
  const warned = [];
  const original = console.warn;
  console.warn = (m) => warned.push(String(m));
  try {
    const { controller, storage, payloads } = makeController(calls, {
      applied: [inst('a')], themes: [THEME_A],
    });
    // Act
    assert.doesNotThrow(() => controller.applyTheme('thm#404'));
    // Assert
    assert.equal(controller.activeThemeId(), null);
    assert.deepEqual(JSON.parse(storage._map.get(ACTIVE_KEY)), { themeId: null, lastSeq: 1 });
    assert.deepEqual(payloads.at(-1), resolveAllChrome(null));
    assert.equal(warned.length, 1, '警告が 1 回でない');
  } finally {
    console.warn = original;
  }
});

// ---- UC-C01 保存（§5.1）-----------------------------------------------------

test('UC-C01: 保存は themes.v1 と activeTheme.v1（lastSeq）を永続化する', () => {
  // Arrange
  const calls = [];
  const { controller, storage } = makeController(calls, { applied: [], themes: [] });
  // Act
  const res = controller.saveTheme({ name: ' Ocean ', roleColors: { surface: '#0A0B0C', bogus: '#fff' } });
  // Assert
  assert.equal(res.ok, true);
  assert.equal(res.themeId, 'thm#1');
  // 永続層（themes.v1）に書かれる件数 = 保存した 1 件だけ。
  const saved = storedThemes(storage);
  assert.equal(saved.length, 1, '永続層に書かれるのは保存した 1 件だけ');
  assert.equal(saved[0].name, 'Ocean');
  assert.deepEqual(saved[0].roleColors, { surface: '#0a0b0c' });
  assert.equal(saved[0].createdAt, 777, '注入した時刻源が使われていない');
  assert.deepEqual(
    saved.map((t) => t.themeId).filter(isPresetThemeId), [],
    'プリセットが themes.v1 へ実体化している（合成ではなく書き込みになっている）',
  );
  assert.deepEqual(JSON.parse(storage._map.get(ACTIVE_KEY)), { themeId: null, lastSeq: 1 });
  // 一覧（controller.themes()）に見える件数 = プリセット（先頭）＋ 保存した 1 件。
  assert.deepEqual(listedIds(controller), [PRESET.themeId, 'thm#1']);
  assert.deepEqual(
    controller.themes().filter((t) => !isPresetThemeId(t.themeId)), saved,
    '一覧のうちプリセット以外は永続層の原形と一致する',
  );
});

test('UC-C01: 保存は適用ではない（クロムも系列も凡例も動かない）', () => {
  // Arrange
  const calls = [];
  const { controller } = makeController(calls, { applied: [inst('a')], themes: [] });
  // Act
  controller.saveTheme({ name: 'Ocean', roleColors: { surface: '#0a0b0c' } });
  // Assert
  assert.deepEqual(calls.filter((c) => c === 'chrome' || c.startsWith('style:') || c === 'legend'), []);
});

// F-C3 の警告主体は adapter（R-4）。usecase（純関数）は「無視した」事実を戻り値で返すだけで
//   console を持たない（tests/usecase_console_free.test.js が層全体で固定する）。
//   ここでは「警告が消えていない・回数が増えていない」ことを協働子側で固定する。
test('F-C3: 未知トークンは無視され、警告は協働子が 1 回だけ出す（R-4）', () => {
  // Arrange
  const calls = [];
  const warned = [];
  const original = console.warn;
  console.warn = (m) => warned.push(String(m));
  try {
    const { controller, storage } = makeController(calls, { applied: [], themes: [] });
    // Act
    const res = controller.saveTheme({
      name: 'Ocean',
      roleColors: { surface: '#0A0B0C', bogus_a: '#fff', bogus_b: '#000' },
    });
    // Assert
    assert.equal(res.ok, true);
    assert.deepEqual(JSON.parse(storage._map.get(THEMES_KEY)).themes[0].roleColors, { surface: '#0a0b0c' });
    assert.equal(warned.length, 1, '未知トークンが複数でも警告は 1 回（F-C3）');
    assert.match(warned[0], /bogus_a, bogus_b/, '無視したトークンが警告に載っていない');
  } finally {
    console.warn = original;
  }
});

test('F-C3: 未知トークンが無いときは警告を出さない（R-4・沈黙の保存）', () => {
  // Arrange
  const calls = [];
  const warned = [];
  const original = console.warn;
  console.warn = (m) => warned.push(String(m));
  try {
    const { controller } = makeController(calls, { applied: [], themes: [] });
    // Act
    controller.saveTheme({ name: 'Ocean', roleColors: { surface: '#0a0b0c', bullish: 'not-a-color' } });
    // Assert
    assert.deepEqual(warned, [], 'F-C9（解釈不能な色）は F-C3 の警告対象ではない');
  } finally {
    console.warn = original;
  }
});

test('F-C1: 名前が不正な保存は CODE を返し、例外を投げず、何も永続化しない', () => {
  // Arrange
  const calls = [];
  const { controller, storage } = makeController(calls, { applied: [], themes: [] });
  // Act
  const res = controller.saveTheme({ name: '   ', roleColors: {} });
  // Assert
  assert.equal(res.ok, false);
  assert.equal(res.code, CODE.empty);
  assert.deepEqual(calls.filter((c) => c.startsWith('put:')), [], '拒否された保存で永続化が起きている');
  assert.deepEqual(storedThemes(storage), [], '永続層は空のまま（プリセットも書き込まれない）');
  assert.deepEqual(listedIds(controller), [PRESET.themeId], '一覧に見えるのは同梱プリセットだけ');
});

// ---- UC-C03 改名・削除（§5.3）-----------------------------------------------

test('UC-C03: 改名は themes.v1 を更新するがチャート上の色は変えない', () => {
  // Arrange
  const calls = [];
  const { controller, storage } = makeController(calls, { applied: [inst('a')], themes: [THEME_A] });
  // Act
  const res = controller.renameTheme('thm#1', 'Sunset');
  // Assert
  assert.equal(res.ok, true);
  assert.equal(storedThemes(storage)[0].name, 'Sunset');
  assert.deepEqual(
    storedThemes(storage).map((t) => t.themeId), ['thm#1'],
    '改名で永続層に書かれるのは対象テーマだけ（プリセットが themes.v1 へ実体化しない）',
  );
  assert.deepEqual(listedIds(controller), [PRESET.themeId, 'thm#1'], '一覧は合成後（件数は変わらない）');
  assert.deepEqual(calls.filter((c) => c === 'chrome' || c.startsWith('style:') || c === 'legend'), []);
});

test('UC-C03: 削除は activeThemeId を null にするがチャート上の色は変えない', () => {
  // Arrange
  const calls = [];
  const { controller, storage } = makeController(calls, { applied: [inst('a')], themes: [THEME_A] });
  controller.applyTheme('thm#1');
  const marker = calls.length;
  // Act
  controller.deleteTheme('thm#1');
  // Assert
  assert.deepEqual(storedThemes(storage), [], '永続層は空（削除でプリセットが書き戻されることもない）');
  assert.deepEqual(listedIds(controller), [PRESET.themeId], '一覧に残るのは同梱プリセットだけ');
  assert.equal(controller.activeThemeId(), null);
  assert.deepEqual(JSON.parse(storage._map.get(ACTIVE_KEY)), { themeId: null, lastSeq: 1 });
  assert.deepEqual(calls.slice(marker).filter((c) => c === 'chrome' || c.startsWith('style:') || c === 'legend'), []);
});

// ---- 選択中テーマの供給（provider の値源）------------------------------------

test('provider: activeTheme() は選択中テーマの実体を返す（未選択は null）', () => {
  // Arrange
  const calls = [];
  const { controller } = makeController(calls, { applied: [], themes: [THEME_A] });
  // Act / Assert
  assert.equal(controller.activeTheme(), null);
  controller.applyTheme('thm#1');
  assert.equal(controller.activeTheme().themeId, 'thm#1');
});

test('起動: state 未注入なら gateway から自力で復元する（dangling は null へ縮退）', () => {
  // Arrange
  const calls = [];
  const warned = [];
  const original = console.warn;
  console.warn = (m) => warned.push(String(m));
  try {
    const storage = makeStorage(calls, {
      [THEMES_KEY]: JSON.stringify({ themes: [THEME_A] }),
      [ACTIVE_KEY]: JSON.stringify({ themeId: 'thm#404', lastSeq: 5 }),
    });
    const { view } = makeHost(calls, { applied: [] });
    // Act
    const controller = new ColorThemeController(view, {
      gateway: new LocalStorageThemeGateway(storage), chromeApplier: null,
    });
    // Assert
    assert.deepEqual(storedThemes(storage), [THEME_A], '永続層は復元しても原形 1 件のまま');
    assert.deepEqual(listedIds(controller), [PRESET.themeId, 'thm#1'], '一覧はプリセット合成後');
    assert.equal(controller.activeThemeId(), null);
    assert.deepEqual(JSON.parse(storage._map.get(ACTIVE_KEY)), { themeId: null, lastSeq: 5 });
  } finally {
    console.warn = original;
  }
});

test('起動: state を注入されたら gateway を読み直さない（起動時の二重読みを作らない）', () => {
  // Arrange: storage は空。state だけがテーマ集合の出所になる（配線が渡す経路）。
  const calls = [];
  const storage = makeStorage(calls, {});
  const { view } = makeHost(calls, { applied: [] });
  // Act
  const controller = new ColorThemeController(view, {
    gateway: new LocalStorageThemeGateway(storage),
    chromeApplier: null,
    state: {
      themes: [THEME_A], activeThemeId: 'thm#1', lastSeq: 4, theme: THEME_A,
    },
  });
  // Assert
  assert.deepEqual(listedIds(controller), [PRESET.themeId, 'thm#1'], '一覧はプリセット合成後');
  assert.equal(controller.activeThemeId(), 'thm#1');
  // activeTheme() は「消費のための射影」を返す（§4.9: 永続値は原形・消費値は §4.4 の形）。
  //   段階 5-B（導出）以降、射影は正規化に加えて未宣言トークンの導出も行うため恒等ではない。
  //   固定すべきは「消費値は射影である」ことであって、射影が恒等であることではない。
  assert.deepEqual(controller.activeTheme(), projectThemeForUse(THEME_A).theme);
  assert.equal(controller.activeTheme().roleColors.surface, '#0a0b0c', '宣言値は不変');
  assert.deepEqual(
    controller.themes().find((t) => t.themeId === 'thm#1'), THEME_A,
    '注入された値は原形のまま（合成は永続値を書き換えない）',
  );
  assert.deepEqual(storedThemes(storage), [], '永続層へは 1 件も書かない（プリセットも書かない）');
  assert.deepEqual(calls, [], '構築だけで永続化が走っている（起動時の二重書き込み）');
});

// ---- ライブプレビュー（段階 5-C-3）------------------------------------------
//
// 目的: 編集ダイアログの操作結果を、保存する前にチャート上で見られるようにする。
//
// 規律（ISSUE-357 で既に踏んだ失敗を繰り返さない）:
//   (1) **色の書き手を 2 本に増やさない**。適用（applyTheme）とプレビューは同じ `_repaint` を通る。
//       書き手を増やすと、経路ごとに結果が食い違う（ISSUE-357 の 3 症状はすべてこれが原因）。
//   (2) **復元用のスナップショットを持たない**。解除は「元のテーマで塗り直す」だけでよい。
//       根拠は 3 つとも既存コードで確認できる: 系列色は不変の baseColor から毎回作り直される／
//       resolveAllChrome は 20 slot を全数返す（＝全上書き＝可逆）／クロム出力は保持色 × 表示
//       モードからの導出 1 本。スナップショットを持つと真の状態が 2 つに割れる。
//   (3) プレビューは**保存ではない**（永続化 0 回）し、**ビューへの介入でもない**（§3.4）。

const DRAFT = Object.freeze({ roleColors: Object.freeze({ surface: '#131722' }), tfModifier: null });

test('TC-CP01 previewTheme: 適用と同じ手順（クロム → 系列 → 凡例）を 1 回ずつ通る', () => {
  // Arrange
  const calls = [];
  const { controller } = makeController(calls, { applied: [inst('i1'), inst('i2')] });
  // Act
  controller.previewTheme(DRAFT);
  // Assert
  assert.deepEqual(calls, ['chrome', 'style:i1', 'style:i2', 'legend'],
    '手順・回数が適用と同一（凡例は反復の外で 1 回）');
});

test('TC-CP02 previewTheme: 永続化を 1 回も行わない（保存ではない）', () => {
  // Arrange
  const calls = [];
  const { controller, storage } = makeController(calls, { applied: [inst('i1')] });
  const before = new Map(storage._map);
  // Act
  controller.previewTheme(DRAFT);
  controller.previewTheme({ roleColors: { surface: '#ffffff' }, tfModifier: null });
  controller.previewTheme(null);
  // Assert
  assert.deepEqual(calls.filter((c) => c.startsWith('put:')), [], '永続層への書き込みが起きた');
  assert.deepEqual([...storage._map.entries()], [...before.entries()], '永続層の中身が動いた');
});

test('TC-CP03 previewTheme: 選択中テーマ id は動かず、activeTheme() だけが下書きを返す', () => {
  // Arrange
  const calls = [];
  const { controller } = makeController(calls, { themes: [THEME_A] });
  controller.applyTheme('thm#1');
  // Act
  controller.previewTheme(DRAFT);
  // Assert
  assert.equal(controller.activeThemeId(), 'thm#1', '選択は動かない（保存も適用もしていない）');
  assert.equal(controller.activeTheme().roleColors.surface, '#131722', '下書きが見えている');
  assert.equal(controller.activeTheme().roleColors.text, '#d5d5d7',
    '下書きも射影を通る（導出込みで見える＝ユーザーが実際に見る色）');
});

test('TC-CP04 previewTheme(null): 元のテーマへ戻る（クロム全 slot が文字列一致）', () => {
  // Arrange
  const calls = [];
  const { controller, payloads } = makeController(calls, { themes: [THEME_A] });
  controller.applyTheme('thm#1');
  const before = payloads[payloads.length - 1];
  // Act
  controller.previewTheme(DRAFT);
  controller.previewTheme(null);
  // Assert
  const after = payloads[payloads.length - 1];
  assert.deepEqual(after.slots, before.slots, 'クロム全 slot がプレビュー前と一致しない');
  assert.deepEqual(after.tokens, before.tokens, 'CSS トークンがプレビュー前と一致しない');
  // 可逆性の前提は「一部だけ返す」ことが無いこと＝台帳の全数を返すこと。件数の逐語固定は
  //   台帳テスト（chrome_tokens.test.js）が持つので、ここは台帳との一致で見る。
  assert.equal(Object.keys(after.slots).length, CHROME_SLOTS.length,
    '前提: 台帳の全 slot を返す（＝全上書き＝可逆）');
  assert.equal(controller.activeTheme().roleColors.surface, '#0a0b0c', '元のテーマへ戻っている');
});

test('TC-CP05 previewTheme(null): テーマ未選択の状態へも正しく戻る（既定色へ復帰）', () => {
  // Arrange: 「テーマなし」でプレビューして解除する経路（スナップショットが無くても戻れること）。
  const calls = [];
  const { controller, payloads } = makeController(calls);
  controller.applyTheme(null);
  const before = payloads[payloads.length - 1];
  // Act
  controller.previewTheme(DRAFT);
  controller.previewTheme(null);
  // Assert
  assert.deepEqual(payloads[payloads.length - 1].slots, before.slots);
  assert.equal(controller.activeTheme(), null, 'テーマ未選択へ戻る');
});

test('TC-CP06 previewTheme: 全系列色がプレビュー前と文字列一致まで戻る', () => {
  // Arrange: 実描画色の解決入力（§4.5）で、解除後の色が前と 1 文字も違わないことを全数で見る。
  const calls = [];
  const { controller } = makeController(calls, { themes: [THEME_A] });
  controller.applyTheme('thm#1');
  const sample = (theme) => list().flatMap((def) => def.series.map((s) => resolveSeriesColor({
    styles: null, seriesName: s.seriesName, role: s.colorRole, theme, payloadColor: '#123456',
  })));
  const before = sample(controller.activeTheme());
  // Act
  controller.previewTheme(DRAFT);
  const during = sample(controller.activeTheme());
  controller.previewTheme(null);
  const after = sample(controller.activeTheme());
  // Assert
  assert.equal(before.length, 97, '前提: SeriesDef 総数');
  assert.notDeepEqual(during, before, '前提: プレビュー中は実際に色が変わっている');
  assert.deepEqual(after, before, '解除後の系列色がプレビュー前と一致しない');
});

test('TC-CP07 previewTheme: §3.4 ビュー API・再計算へ 1 度も到達しない', () => {
  // Arrange
  const calls = [];
  const { controller } = makeController(calls, { applied: [inst('i1')] });
  // Act
  controller.previewTheme(DRAFT);
  controller.previewTheme(null);
  // Assert
  assert.deepEqual(calls.filter((c) => c.startsWith('VIOLATION')), []);
});

test('TC-CP08 previewTheme: 未描画インスタンスは適用と同じくスキップする（経路が 1 本の帰結）', () => {
  // Arrange: i2 だけ描画済み。適用とプレビューで挙動が分かれないこと。
  const calls = [];
  const { controller } = makeController(calls, {
    applied: [inst('i1'), inst('i2')], drawn: ['i2'],
  });
  // Act
  controller.previewTheme(DRAFT);
  // Assert
  assert.deepEqual(calls, ['chrome', 'style:i2', 'legend']);
});

test('TC-CP11 結線: 編集ダイアログの onPreview が previewTheme まで届く（作成・編集の両方）', () => {
  // Arrange: ダイアログは注入で受ける（DIP）。ここでは opts を捕まえるだけの偽ダイアログを使う。
  const calls = [];
  const opened = [];
  const dialogs = { openEdit: (opts) => opened.push(opts), openManage: () => {} };
  const storage = makeStorage(calls, { [THEMES_KEY]: JSON.stringify({ themes: [THEME_A] }) });
  const gateway = new LocalStorageThemeGateway(storage);
  const { view } = makeHost(calls, { applied: [inst('i1')] });
  const { applier, payloads } = makeChrome(calls);
  const controller = new ColorThemeController(view, {
    gateway, chromeApplier: applier, dialogs, now: FIXED_NOW,
  });
  controller.applyTheme('thm#1'); // 「元のテーマ」を確定させてから下書きを重ねる。
  calls.length = 0; // 適用時の永続化は Act の観測に含めない。
  // Act
  controller.openCreateDialog();
  controller.openEditDialog('thm#1');
  // Assert
  assert.equal(opened.length, 2, '前提: 2 枚とも開いた');
  for (const opts of opened) {
    assert.equal(typeof opts.onPreview, 'function', 'onPreview が渡っていない');
  }
  const before = payloads.length;
  opened[0].onPreview({ roleColors: { surface: '#131722' }, tfModifier: null });
  assert.equal(controller.activeTheme().roleColors.surface, '#131722', '下書きが効いていない');
  assert.equal(payloads.length, before + 1, 'プレビューで塗り直されていない');
  opened[0].onPreview(null);
  assert.equal(controller.activeTheme().roleColors.surface, '#0a0b0c', '解除で元へ戻らない');
  assert.deepEqual(calls.filter((c) => c.startsWith('put:')), [], 'プレビューで永続化が起きた');
});

test('TC-CP09 構造: 色を書く手順は 1 本だけ（applyTheme と previewTheme が同じ _repaint を通る）', () => {
  // Arrange: ISSUE-357 の再発防止を規約ではなく構造で示す。凡例の再描画（手順 4）は塗り直し 1 回に
  //   つき 1 回なので、その呼び出し**点**の数がそのまま「色を書く手順」の本数になる。
  const src = readFileSync(
    fileURLToPath(new URL('../js/adapter/front/color_theme_controller.js', import.meta.url)),
    'utf8',
  );
  // Act
  const legendSites = (src.match(/_renderLegend\(\)/g) ?? []).length;
  const styleSites = (src.match(/_applyStoredStyles\(/g) ?? []).length;
  const chromeSites = (src.match(/this\._applyChrome\(/g) ?? []).length;
  // Assert
  assert.equal(legendSites, 1, '凡例の再描画点が 1 つでない＝塗り直しの手順が複数ある');
  assert.equal(styleSites, 1, '系列再スタイルの呼び出し点が 1 つでない');
  assert.equal(chromeSites, 1, 'クロム配信の呼び出し点が 1 つでない');
});

test('TC-CP10 構造: 復元用スナップショットを持たない（真の状態を 2 つに割らない）', () => {
  // Arrange: 「プレビュー前の色を控えておいて戻す」実装は、保持色という真の状態のコピーを作る。
  //   コピーは必ずずれる（ISSUE-357 で踏んだのと同型の失敗）。解除は元のテーマで塗り直すだけでよい。
  const src = readFileSync(
    fileURLToPath(new URL('../js/adapter/front/color_theme_controller.js', import.meta.url)),
    'utf8',
  );
  // Act / Assert
  for (const needle of ['_snapshot', '_restore', '_savedSlots', '_beforePreview', '_previousChrome']) {
    assert.equal(src.includes(needle), false, `復元用の控えを持っている: ${needle}`);
  }
});

// ---- §3.4 ビュー自動介入の禁止（静的固定）-----------------------------------

test('時刻源: now を注入したら協働子はそれだけを使う（決定論の担保）', () => {
  // Arrange: 固定時刻を注入する。
  const calls = [];
  const { controller } = makeController(calls, { now: () => 777 });
  // Act
  controller.saveTheme({ name: 'A', roleColors: {} });
  const saved = controller.themes().find((t) => t.name === 'A');
  // Assert: 実時刻ではなく注入値が使われる。
  assert.equal(saved.createdAt, 777);
  assert.equal(saved.updatedAt, 777);
});

test('時刻源: now 未注入なら実時刻（UNIX 秒）を使う（本番で createdAt が 0 にならない）', () => {
  // Arrange: 既定つき注入の**既定側**を通すため、now を渡さずに協働子を直接組む
  //   （makeController は既定で固定時刻を注入するため、そこからは既定側へ到達できない）。
  const calls = [];
  const storage = makeStorage(calls, { [THEMES_KEY]: JSON.stringify({ themes: [] }) });
  const gateway = new LocalStorageThemeGateway(storage);
  const { view } = makeHost(calls, { applied: [], drawn: null });
  const { applier } = makeChrome(calls);
  const controller = new ColorThemeController(view, { gateway, chromeApplier: applier });
  // Act
  controller.saveTheme({ name: 'B', roleColors: {} });
  const saved = controller.themes().find((t) => t.name === 'B');
  // Assert: 0 ではなく妥当な UNIX 秒（2020-01-01 以降）であること。
  assert.ok(saved.createdAt > 1577836800, `createdAt=${saved.createdAt} が実時刻でない`);
  assert.equal(saved.updatedAt, saved.createdAt);
});

test('§3.4: 協働子のソースにビュー操作・再計算 API が 1 つも現れない', () => {
  // Arrange
  const src = readFileSync(
    fileURLToPath(new URL('../js/adapter/front/color_theme_controller.js', import.meta.url)),
    'utf8',
  );
  // §3.4 が禁じるのは「ビューへの自動介入」と「再計算」。時刻源はこれに含まれない。
  //   `Date.now()` は**既定つき注入**（`now` 未注入時のみ実時刻を読む）であり、参照実装
  //   `chart_template_controller.js:86` と同一の idiom。既定を 0 にすると本番の createdAt /
  //   updatedAt が全テーマで 0 になり §4.4 が意味を失うため、禁止対象に含めてはならない。
  //   テストの決定論は「`now` を注入したとき協働子がそれを使うこと」を振る舞いで固定して担保する
  //   （下のテスト参照）。乱数は使わないので引き続き禁止する。
  const forbidden = [
    'setData(', 'fitContent(', 'setVisibleLogicalRange(', 'scrollToPosition(',
    'autoScale', 'applyOptions(', 'recomputeAllApplied(', 'compute(',
    'Math.random(',
  ];
  // Act
  const hits = forbidden.filter((needle) => src.includes(needle));
  // Assert
  assert.deepEqual(hits, [], `協働子が呼んではならない API を含む: ${hits.join(', ')}`);
});
