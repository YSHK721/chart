// chart_templates.js（usecase・純関数）の Red テスト。
//
// 設計入力（唯一の仕様源）: .doc/indicator-management-ui/基本設計_チャートテンプレート.md v0.1.1
//   §4.1（CHART_TEMPLATE / TEMPLATE_INSTANCE / 保存しない属性）、§4.3（templateId 採番）、
//   §5.1（UC-T01 保存・正規化名一致は上書き・上限 50）、§5.3（UC-T03 紐付け）、
//   §5.4（UC-T04 再適用判定）、§5.5（UC-T05 改名の重複不可）、§5.6（F-T1 / F-T3 / F-T6）、
//   §7.1（usecase は DOM・Storage 非依存の純関数）、§8.1 A-2（最大 50 件・名前最大 40 文字）。
// 構造: Arrange-Act-Assert（AAA）。DOM・Storage 非依存。
//
// ★ 本ファイルは Red フェーズ専用。対象モジュール js/usecase/chart_templates.js は未実装。
//
// ★ 仮名（設計書は責務のみ規定し、関数名・引数形・戻り値形を定義していない。実装フェーズで確定する）:
//     normalizeTemplateName(name) -> string
//     validateTemplateName(name, { templates, excludeTemplateId }) -> { ok, code }
//     toTemplateInstance(appliedInstance) -> TEMPLATE_INSTANCE
//     saveTemplate({ templates, lastSeq, name, applied, now }) -> { ok, code, templates, lastSeq, templateId }
//     resolveBinding({ bindings, templates, timeframe, validTimeframes, activeTemplateId })
//        -> { templateId, bindings }   // templateId === null は「適用しない」
//     nextTemplateId(lastSeq) -> { templateId, lastSeq }
//     recoverLastSeq(lastSeq, templates) -> number
//   code の語彙（'empty' / 'too_long' / 'duplicate' / 'limit'）も設計書に定義がない仮値のため、
//   本テストは code の具体値ではなく ok（真偽）と状態不変性のみを固定する。

import { test } from 'node:test';
import assert from 'node:assert/strict';

// 未実装モジュールの読み込み失敗をテスト単位で顕在化させる（ファイル全体の load 失敗にしない）。
async function load() {
  return import('../js/usecase/chart_templates.js');
}

// 有効な時間足集合（§4.2 / E-13: live composition root が install する既定 9 足）。
const VALID_TIMEFRAMES = ['1m', '5m', '15m', '30m', '1h', '4h', '1D', '1W', '1M'];

function template({ templateId, name, instances = [], createdAt = 1000, updatedAt = 1000 }) {
  return { templateId, name, instances, createdAt, updatedAt };
}

// AppliedInstance の保存形（indicator_state_store.js:43-55 toJson と同形）。
function applied({ instanceId, indicatorId, variant, params, visible = true, styles = null, generation = 3, seq = 1, createdAt = 't0' }) {
  return { instanceId, indicatorId, variant, params, visible, generation, seq, createdAt, styles };
}

// ---------------------------------------------------------------------------
// 名前検証（§4.1 name 1〜40 文字・§5.1 例外 F-T1・§5.5 改名）
// ---------------------------------------------------------------------------

test('TC-U01 名前検証: trim 後 0 文字は不正（§4.1「空は不可」・F-T1）', async () => {
  // Arrange
  const { validateTemplateName } = await load();
  // Act
  const empty = validateTemplateName('', { templates: [] });
  const blanks = validateTemplateName('   ', { templates: [] });
  // Assert
  assert.equal(empty.ok, false, '空文字は保存不可');
  assert.equal(blanks.ok, false, '空白のみは trim 後 0 文字＝保存不可');
});

test('TC-U02 名前検証: trim 後 40 文字は有効・41 文字は不正（境界値・§4.1・A-2）', async () => {
  // Arrange
  const { validateTemplateName } = await load();
  // Act
  const at40 = validateTemplateName('a'.repeat(40), { templates: [] });
  const at41 = validateTemplateName('a'.repeat(41), { templates: [] });
  const trimmedTo40 = validateTemplateName(`  ${'a'.repeat(40)}  `, { templates: [] });
  // Assert
  assert.equal(at40.ok, true, '上限 40 文字ちょうどは有効');
  assert.equal(at41.ok, false, '41 文字は不正');
  assert.equal(trimmedTo40.ok, true, '前後空白は trim してから長さを判定する');
});

test('TC-U03 正規化名は trim ＋小文字化である（§5.1 処理 2）', async () => {
  // Arrange
  const { normalizeTemplateName } = await load();
  // Act / Assert
  assert.equal(normalizeTemplateName('  Swing  '), 'swing');
  assert.equal(normalizeTemplateName('SWING'), 'swing');
});

test('TC-U04 改名の重複判定: 他テンプレートと正規化名一致は不正・自分自身との一致は許容（§5.5）', async () => {
  // Arrange
  const { validateTemplateName } = await load();
  const templates = [template({ templateId: 'tpl#1', name: 'Swing' })];
  // Act
  const other = validateTemplateName(' swing ', { templates, excludeTemplateId: 'tpl#2' });
  const self = validateTemplateName(' swing ', { templates, excludeTemplateId: 'tpl#1' });
  // Assert
  assert.equal(other.ok, false, '別テンプレートと正規化名が衝突する改名は不可');
  assert.equal(self.ok, true, '自分自身の正規化名との一致は重複ではない');
});

// ---------------------------------------------------------------------------
// AppliedInstance -> TEMPLATE_INSTANCE 写像（§4.1）
// ---------------------------------------------------------------------------

test('TC-U05 写像: instanceId/seq/createdAt/generation/datasetRef/timeframe が落ちる（§4.1 保存しない属性）', async () => {
  // Arrange
  const { toTemplateInstance } = await load();
  const src = {
    ...applied({ instanceId: 'tgp_btlm#7', indicatorId: 'tgp_btlm', variant: 'default', params: { window: 25 } }),
    datasetRef: 'jp225:1m',
    timeframe: '1D',
  };
  // Act
  const mapped = toTemplateInstance(src);
  // Assert
  assert.deepEqual(
    Object.keys(mapped).sort(),
    ['indicatorId', 'params', 'styles', 'variant', 'visible'],
    'TEMPLATE_INSTANCE の属性は §4.1 の 5 属性のみ',
  );
  for (const dropped of ['instanceId', 'seq', 'createdAt', 'generation', 'datasetRef', 'timeframe']) {
    assert.equal(Object.hasOwn(mapped, dropped), false, `${dropped} は保存しない（§4.1）`);
  }
});

test('TC-U06 写像: params のペア配列はオブジェクトへ正規化される（§4.1・_paramsObject と同一）', async () => {
  // Arrange
  const { toTemplateInstance } = await load();
  const pairs = applied({ instanceId: 'i#1', indicatorId: 'tgp_btlm', variant: 'default', params: [['window', 25], ['k', 2]] });
  const obj = applied({ instanceId: 'i#2', indicatorId: 'tgp_btlm', variant: 'default', params: { window: 25 } });
  // Act / Assert
  assert.deepEqual(toTemplateInstance(pairs).params, { window: 25, k: 2 });
  assert.deepEqual(toTemplateInstance(obj).params, { window: 25 }, 'オブジェクト形はそのまま');
});

test('TC-U07 写像: styles / visible は現状値を保持する（§4.1・null 可）', async () => {
  // Arrange
  const { toTemplateInstance } = await load();
  const styles = { btlm_mean: { color: '#f00', width: 2 } };
  const withStyles = applied({ instanceId: 'i#1', indicatorId: 'tgp_btlm', variant: 'default', params: {}, visible: false, styles });
  const noStyles = applied({ instanceId: 'i#2', indicatorId: 'tgp_btlm', variant: 'default', params: {}, styles: null });
  // Act
  const a = toTemplateInstance(withStyles);
  const b = toTemplateInstance(noStyles);
  // Assert
  assert.deepEqual(a.styles, styles, 'styles は現状値をそのまま保存する');
  assert.equal(a.visible, false, 'visible は現状値を保存する');
  assert.equal(b.styles, null, 'styles 未設定は null');
});

// ---------------------------------------------------------------------------
// 保存（§5.1 UC-T01）
// ---------------------------------------------------------------------------

test('TC-U08 保存: 正規化名一致は既存を上書き（templateId 保持・name は入力表記・updatedAt 更新・createdAt 不変）（§5.1 処理 2）', async () => {
  // Arrange
  const { saveTemplate } = await load();
  const templates = [template({ templateId: 'tpl#1', name: 'Swing', createdAt: 1000, updatedAt: 1000 })];
  const bindings = { '1D': 'tpl#1' };
  const list = [applied({ instanceId: 'i#1', indicatorId: 'tgp_btlm', variant: 'default', params: { window: 25 } })];
  // Act
  const res = saveTemplate({ templates, lastSeq: 1, name: '  SWING  ', applied: list, now: 2000 });
  // Assert
  assert.equal(res.ok, true);
  assert.equal(res.templates.length, 1, '正規化名一致は新規追加ではなく上書き');
  assert.equal(res.templateId, 'tpl#1', 'templateId は保持する');
  assert.equal(res.templates[0].name, 'SWING', 'name は入力の表記（trim 済み）を採用する');
  assert.equal(res.templates[0].createdAt, 1000, 'createdAt は不変');
  assert.equal(res.templates[0].updatedAt, 2000, 'updatedAt を更新する');
  assert.equal(res.lastSeq, 1, '上書きでは採番しない');
  assert.deepEqual(bindings, { '1D': 'tpl#1' }, 'templateId 保持により紐付けは維持される（純関数は bindings を破壊しない）');
});

test('TC-U09 保存: 正規化名が不一致なら新規採番して追加する（§5.1 処理 2・§4.3）', async () => {
  // Arrange
  const { saveTemplate } = await load();
  const templates = [template({ templateId: 'tpl#1', name: 'Swing' })];
  const list = [applied({ instanceId: 'i#1', indicatorId: 'tgp_btlm', variant: 'default', params: {} })];
  // Act
  const res = saveTemplate({ templates, lastSeq: 1, name: 'デイトレ', applied: list, now: 2000 });
  // Assert
  assert.equal(res.ok, true);
  assert.equal(res.templates.length, 2);
  assert.equal(res.templateId, 'tpl#2', '新規は lastSeq + 1 で採番（§4.3）');
  assert.equal(res.lastSeq, 2, 'lastSeq を進める');
});

test('TC-U10 上限: テンプレート 50 件時の新規保存は拒否し既存を変更しない（§5.1 例外・A-2）', async () => {
  // Arrange
  const { saveTemplate } = await load();
  const templates = Array.from({ length: 50 }, (_, i) => template({ templateId: `tpl#${i + 1}`, name: `t${i + 1}` }));
  const list = [applied({ instanceId: 'i#1', indicatorId: 'tgp_btlm', variant: 'default', params: {} })];
  // Act
  const res = saveTemplate({ templates, lastSeq: 50, name: '51 件目', applied: list, now: 2000 });
  // Assert
  assert.equal(res.ok, false, '上限 50 件に達した状態での新規保存は拒否');
  assert.equal(res.templates.length, 50, '既存データは不変（F-T1）');
  assert.equal(res.lastSeq, 50, '拒否時は採番しない');
});

test('TC-U11 上限: 50 件時でも正規化名一致の上書きは成功する（§5.1 例外「上書き更新は可」）', async () => {
  // Arrange
  const { saveTemplate } = await load();
  const templates = Array.from({ length: 50 }, (_, i) => template({ templateId: `tpl#${i + 1}`, name: `t${i + 1}`, updatedAt: 1000 }));
  const list = [applied({ instanceId: 'i#1', indicatorId: 'tgp_btlm', variant: 'default', params: {} })];
  // Act
  const res = saveTemplate({ templates, lastSeq: 50, name: 'T7', applied: list, now: 2000 });
  // Assert
  assert.equal(res.ok, true, '上限到達でも上書きは可');
  assert.equal(res.templates.length, 50);
  assert.equal(res.templateId, 'tpl#7');
  assert.equal(res.templates[6].updatedAt, 2000);
});

// ---------------------------------------------------------------------------
// 紐付け解決（§5.3・§5.4・F-T3・F-T6）
// ---------------------------------------------------------------------------

test('TC-U12 紐付け解決: dangling（参照先不在）は不適用かつ当該紐付けを削除する（F-T3）', async () => {
  // Arrange
  const { resolveBinding } = await load();
  const templates = [template({ templateId: 'tpl#1', name: 'Swing' })];
  const bindings = { '1D': 'tpl#9', '5m': 'tpl#1' };
  // Act
  const res = resolveBinding({ bindings, templates, timeframe: '1D', validTimeframes: VALID_TIMEFRAMES, activeTemplateId: null });
  // Assert
  assert.equal(res.templateId, null, '参照先が不在なら自動適用しない');
  assert.equal(Object.hasOwn(res.bindings, '1D'), false, 'dangling 紐付けは削除する（遅延クリーンアップ）');
  assert.equal(res.bindings['5m'], 'tpl#1', '他の紐付けは温存する');
  assert.deepEqual(bindings, { '1D': 'tpl#9', '5m': 'tpl#1' }, '純関数は入力 bindings を破壊しない');
});

test('TC-U13 紐付け解決: 有効時間足集合外のキーは無視するが削除しない（F-T6）', async () => {
  // Arrange
  const { resolveBinding } = await load();
  const templates = [template({ templateId: 'tpl#1', name: 'Swing' })];
  const bindings = { '2h': 'tpl#1', '1D': 'tpl#1' };
  // Act
  const res = resolveBinding({ bindings, templates, timeframe: '2h', validTimeframes: VALID_TIMEFRAMES, activeTemplateId: null });
  // Assert
  assert.equal(res.templateId, null, '集合外キーは解決対象にしない（無視）');
  assert.equal(res.bindings['2h'], 'tpl#1', '集合外キーは削除しない（将来足の温存・F-T6）');
});

test('TC-U14 再適用判定: activeTemplateId と一致する templateId は適用しない（§5.4 発火条件 3）', async () => {
  // Arrange
  const { resolveBinding } = await load();
  const templates = [template({ templateId: 'tpl#1', name: 'Swing' })];
  const bindings = { '1D': 'tpl#1' };
  // Act
  const same = resolveBinding({ bindings, templates, timeframe: '1D', validTimeframes: VALID_TIMEFRAMES, activeTemplateId: 'tpl#1' });
  const diff = resolveBinding({ bindings, templates, timeframe: '1D', validTimeframes: VALID_TIMEFRAMES, activeTemplateId: 'tpl#2' });
  // Assert
  assert.equal(same.templateId, null, '同一テンプレート由来のままなら適用しない（決定論性）');
  assert.deepEqual(same.bindings, { '1D': 'tpl#1' }, '不適用でも紐付けは変更しない');
  assert.equal(diff.templateId, 'tpl#1', '異なるテンプレートが紐付いた足への切替では適用する');
});

// ---------------------------------------------------------------------------
// templateId 採番（§4.3）
// ---------------------------------------------------------------------------

test('TC-U15 採番: 形式は tpl#{seq}・seq は lastSeq + 1（§4.3）', async () => {
  // Arrange
  const { nextTemplateId } = await load();
  // Act
  const first = nextTemplateId(0);
  const next = nextTemplateId(7);
  // Assert
  assert.equal(first.templateId, 'tpl#1');
  assert.equal(first.lastSeq, 1);
  assert.equal(next.templateId, 'tpl#8');
  assert.equal(next.lastSeq, 8);
});

test('TC-U16 採番: 全テンプレート削除後も lastSeq を減算しない＝id を再利用しない（§4.3）', async () => {
  // Arrange
  const { saveTemplate, nextTemplateId } = await load();
  const templates = [template({ templateId: 'tpl#1', name: 'Swing' }), template({ templateId: 'tpl#2', name: 'Day' })];
  const list = [applied({ instanceId: 'i#1', indicatorId: 'tgp_btlm', variant: 'default', params: {} })];
  // Act: 2 件を全削除した状態（templates 空・lastSeq は 2 のまま）で新規保存する
  const afterDeleteAll = saveTemplate({ templates: [], lastSeq: 2, name: 'New', applied: list, now: 2000 });
  // Assert
  assert.equal(afterDeleteAll.templateId, 'tpl#3', '削除後も採番は継続する（id 再利用禁止）');
  assert.equal(nextTemplateId(2).templateId, 'tpl#3');
  assert.equal(templates.length, 2, '純関数は入力配列を破壊しない');
});

test('TC-U17 採番: templateSeq 破損時は既存 tpl#N の最大 N 以上へ復旧し id が衝突しない（§4.3）', async () => {
  // Arrange: templateSeq.v1 が破損して lastSeq=0 に初期化された状態、templates には tpl#5 が在席
  const { recoverLastSeq, nextTemplateId } = await load();
  const templates = [template({ templateId: 'tpl#2', name: 'a' }), template({ templateId: 'tpl#5', name: 'b' })];
  // Act
  const recovered = recoverLastSeq(0, templates);
  const issued = nextTemplateId(recovered).templateId;
  // Assert
  assert.ok(recovered >= 5, `復旧後の lastSeq は既存最大 N（5）以上（実際: ${recovered}）`);
  assert.equal(templates.some((t) => t.templateId === issued), false, `復旧後に発行する id が既存と衝突しない（実際: ${issued}）`);
});

test('TC-U18 純関数: saveTemplate は入力 templates 配列・要素を破壊しない（§7.1）', async () => {
  // Arrange
  const { saveTemplate } = await load();
  const templates = [template({ templateId: 'tpl#1', name: 'Swing', updatedAt: 1000 })];
  const snapshot = JSON.parse(JSON.stringify(templates));
  const list = [applied({ instanceId: 'i#1', indicatorId: 'tgp_btlm', variant: 'default', params: {} })];
  // Act
  saveTemplate({ templates, lastSeq: 1, name: 'swing', applied: list, now: 2000 });
  // Assert
  assert.deepEqual(templates, snapshot, '入力は不変（DOM・Storage 非依存の純関数）');
});
