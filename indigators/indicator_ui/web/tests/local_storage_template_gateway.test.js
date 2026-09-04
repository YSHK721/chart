// local_storage_template_gateway.js（TemplateStorePort 実装）の Red テスト。
//
// 設計入力（唯一の仕様源）: .doc/indicator-management-ui/基本設計_チャートテンプレート.md v0.1.1
//   §4.2（3 論理キーと値スキーマ・破損時は当該キーのみ空既定へ初期化し他キー温存＋console 警告・
//        QuotaExceeded は当該書き込み中止で例外を投げない・接頭辞を自前で付けず注入 storage を
//        そのまま使う）、§4.3（templateSeq 単調性）、§5.6（F-T2 / F-T5）、
//   §7.1（既存 LocalStorageGateway は無改変・ISP）。
// 参照実装（既存規約の同型）: js/adapter/front/local_storage_gateway.js（storage 注入・破損時初期化・
//   Quota 中止）／tests/local_storage_gateway.test.js（fakeStorage の作法）。
// 構造: Arrange-Act-Assert（AAA）。
//
// ★ 本ファイルは Red フェーズ専用。対象モジュール js/adapter/front/local_storage_template_gateway.js は未実装。
//
// ★ 仮名（設計書は責務と論理キーのみ規定し、クラス名・メソッド名・戻り値形を定義していない。
//   実装フェーズで確定する）:
//     class LocalStorageTemplateGateway(storage)
//       loadTemplates() -> CHART_TEMPLATE[]        saveTemplates(list)
//       loadBindings()  -> { [timeframe]: id }     saveBindings(obj)
//       loadTemplateSeq() -> int                   saveTemplateSeq(n)
//   物理キー文字列（indicatorUi.templates.v1 / indicatorUi.templateBindings.v1 /
//   indicatorUi.templateSeq.v1）と値スキーマは §4.2 が定義済みのため固定する。

import { test } from 'node:test';
import assert from 'node:assert/strict';

async function load() {
  return import('../js/adapter/front/local_storage_template_gateway.js');
}

const KEY_TEMPLATES = 'indicatorUi.templates.v1';
const KEY_BINDINGS = 'indicatorUi.templateBindings.v1';
const KEY_SEQ = 'indicatorUi.templateSeq.v1';

// Fake localStorage（tests/local_storage_gateway.test.js と同作法）。quota フラグで setItem を失敗させる。
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

// console.warn を捕捉する（F-T2 / F-T5 の「console 警告」検証用）。
function captureWarn(fn) {
  const original = console.warn;
  const seen = [];
  console.warn = (...args) => { seen.push(args.join(' ')); };
  try { fn(); } finally { console.warn = original; }
  return seen;
}

const TEMPLATE = {
  templateId: 'tpl#1',
  name: 'スイング',
  instances: [{ indicatorId: 'tgp_btlm', variant: 'default', params: { window: 25 }, visible: true, styles: null }],
  createdAt: 1000,
  updatedAt: 1000,
};

// ---------------------------------------------------------------------------
// 3 キーの読み書き（§4.2）
// ---------------------------------------------------------------------------

test('TC-G01 templates: 往復し、物理キーは indicatorUi.templates.v1・値は { templates: [...] }（§4.2）', async () => {
  // Arrange
  const { LocalStorageTemplateGateway } = await load();
  const storage = fakeStorage();
  const gw = new LocalStorageTemplateGateway(storage);
  // Act
  gw.saveTemplates([TEMPLATE]);
  // Assert
  assert.deepEqual(gw.loadTemplates(), [TEMPLATE], '往復して同値が読み出せる');
  assert.deepEqual(JSON.parse(storage.map.get(KEY_TEMPLATES)), { templates: [TEMPLATE] }, '値スキーマは { templates: [...] }');
});

test('TC-G02 bindings: 往復し、物理キーは indicatorUi.templateBindings.v1・値は { bindings: {...} }（§4.2）', async () => {
  // Arrange
  const { LocalStorageTemplateGateway } = await load();
  const storage = fakeStorage();
  const gw = new LocalStorageTemplateGateway(storage);
  const bindings = { '1D': 'tpl#1', '5m': 'tpl#2' };
  // Act
  gw.saveBindings(bindings);
  // Assert
  assert.deepEqual(gw.loadBindings(), bindings);
  assert.deepEqual(JSON.parse(storage.map.get(KEY_BINDINGS)), { bindings });
});

test('TC-G03 templateSeq: 往復し、物理キーは indicatorUi.templateSeq.v1・値は { lastSeq: int }（§4.2・§4.3）', async () => {
  // Arrange
  const { LocalStorageTemplateGateway } = await load();
  const storage = fakeStorage();
  const gw = new LocalStorageTemplateGateway(storage);
  // Act
  gw.saveTemplateSeq(7);
  // Assert
  assert.equal(gw.loadTemplateSeq(), 7);
  assert.deepEqual(JSON.parse(storage.map.get(KEY_SEQ)), { lastSeq: 7 });
});

test('TC-G04 キー未設定時は空既定を返す（templates=[] / bindings={} / lastSeq=0）（§4.2 空既定・§4.3 seq=lastSeq+1）', async () => {
  // Arrange
  const { LocalStorageTemplateGateway } = await load();
  const gw = new LocalStorageTemplateGateway(fakeStorage());
  // Act / Assert
  assert.deepEqual(gw.loadTemplates(), []);
  assert.deepEqual(gw.loadBindings(), {});
  assert.equal(gw.loadTemplateSeq(), 0, '初回発行が tpl#1 になる（§4.3 seq = lastSeq + 1）');
});

test('TC-G05 注入された storage をそのまま使う＝接頭辞を自前で付けない（§4.2）', async () => {
  // Arrange
  const { LocalStorageTemplateGateway } = await load();
  const storage = fakeStorage();
  const gw = new LocalStorageTemplateGateway(storage);
  // Act
  gw.saveTemplates([TEMPLATE]);
  gw.saveBindings({ '1D': 'tpl#1' });
  gw.saveTemplateSeq(1);
  // Assert: scopedStorage 側が付ける live: 等の接頭辞を gateway が二重に付けない
  assert.deepEqual([...storage.map.keys()].sort(), [KEY_BINDINGS, KEY_SEQ, KEY_TEMPLATES].sort());
});

// ---------------------------------------------------------------------------
// 破損時挙動（F-T2・§4.2）
// ---------------------------------------------------------------------------

test('TC-G06 破損（JSON パース不能）は当該キーのみ空既定へ初期化し他キーを温存する（F-T2）', async () => {
  // Arrange: templates が破損、bindings / seq は正常
  const { LocalStorageTemplateGateway } = await load();
  const storage = fakeStorage({
    [KEY_TEMPLATES]: '{ broken json',
    [KEY_BINDINGS]: JSON.stringify({ bindings: { '1D': 'tpl#1' } }),
    [KEY_SEQ]: JSON.stringify({ lastSeq: 3 }),
  });
  const gw = new LocalStorageTemplateGateway(storage);
  // Act
  let templates;
  const warns = captureWarn(() => { templates = gw.loadTemplates(); });
  // Assert
  assert.deepEqual(templates, [], '破損キーは空既定へ初期化する');
  assert.deepEqual(gw.loadBindings(), { '1D': 'tpl#1' }, '他キーは温存する（全消去しない）');
  assert.equal(gw.loadTemplateSeq(), 3, '他キーは温存する');
  assert.ok(warns.length > 0, 'console に警告する（F-T2）');
});

test('TC-G07 スキーマ不一致も当該キーのみ空既定へ初期化し他キーを温存する（F-T2）', async () => {
  // Arrange: JSON としては妥当だがスキーマ不一致（templates が配列でない / bindings がオブジェクトでない）
  const { LocalStorageTemplateGateway } = await load();
  const storage = fakeStorage({
    [KEY_TEMPLATES]: JSON.stringify({ templates: 'not-an-array' }),
    [KEY_BINDINGS]: JSON.stringify({ bindings: [1, 2, 3] }),
    [KEY_SEQ]: JSON.stringify({ lastSeq: 'NaN' }),
  });
  const gw = new LocalStorageTemplateGateway(storage);
  // Act / Assert
  assert.deepEqual(gw.loadTemplates(), [], 'templates が配列でなければ空既定');
  assert.deepEqual(gw.loadBindings(), {}, 'bindings がオブジェクトでなければ空既定');
  assert.equal(gw.loadTemplateSeq(), 0, 'lastSeq が整数でなければ空既定');
});

// ---------------------------------------------------------------------------
// QuotaExceeded（F-T5・§4.2）
// ---------------------------------------------------------------------------

test('TC-G08 QuotaExceeded は当該書き込みを中止し例外を投げない（F-T5）', async () => {
  // Arrange
  const { LocalStorageTemplateGateway } = await load();
  const storage = fakeStorage();
  const gw = new LocalStorageTemplateGateway(storage);
  storage.quotaExceeded = true;
  // Act / Assert
  const warns = captureWarn(() => {
    assert.doesNotThrow(() => gw.saveTemplates([TEMPLATE]), 'templates 書き込みで例外を投げない');
    assert.doesNotThrow(() => gw.saveBindings({ '1D': 'tpl#1' }), 'bindings 書き込みで例外を投げない');
    assert.doesNotThrow(() => gw.saveTemplateSeq(1), 'templateSeq 書き込みで例外を投げない');
  });
  assert.equal(storage.map.has(KEY_TEMPLATES), false, '当該書き込みは中止される');
  assert.ok(warns.length > 0, 'console に警告する（F-T5）');
});

test('TC-G09 QuotaExceeded 後も既存キーは温存され、以後の読み出しはメモリ外の永続値を壊さない（F-T5）', async () => {
  // Arrange: 既に永続化済みの bindings がある状態で templates 書き込みが Quota で失敗する
  const { LocalStorageTemplateGateway } = await load();
  const storage = fakeStorage({ [KEY_BINDINGS]: JSON.stringify({ bindings: { '1D': 'tpl#1' } }) });
  const gw = new LocalStorageTemplateGateway(storage);
  storage.quotaExceeded = true;
  // Act
  captureWarn(() => gw.saveTemplates([TEMPLATE]));
  storage.quotaExceeded = false;
  // Assert
  assert.deepEqual(gw.loadBindings(), { '1D': 'tpl#1' }, '失敗した書き込みは他キーを壊さない');
});
