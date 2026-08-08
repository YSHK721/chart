// ISSUE-221: カテゴリ絞り込みからの到達不能を防ぐ回帰検証。
//
// 従来はサイドバーのカテゴリボタンが index.html へ 3 件だけ直書きされており、
//   cat.oscillator(10) と cat.band(2) が欠落して 24 指標中 12 件が到達不能だった。
//   categories() を単一情報源にし、全カテゴリが必ず現れることを構造的に固定する。

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { CATEGORY_LABELS, categories, list } from '../js/usecase/catalog.js';
import { listForView } from '../js/usecase/facade.js';

test('ISSUE-221 categories() はカタログの全カテゴリを網羅する', () => {
  const fromRegistry = new Set(list().map((d) => d.category.nameKey));
  const fromApi = new Set(categories().map((c) => c.key));
  assert.deepEqual([...fromApi].sort(), [...fromRegistry].sort());
});

test('ISSUE-221 全カテゴリに表示名が定義されている（key 露出を防ぐ）', () => {
  for (const c of categories()) {
    assert.ok(CATEGORY_LABELS[c.key], `${c.key} の表示名が未定義`);
    assert.equal(c.label, CATEGORY_LABELS[c.key]);
  }
});

test('ISSUE-221 どのカテゴリで絞り込んでも 1 件以上到達できる', () => {
  for (const c of categories()) {
    const rows = listForView({ category: c.key });
    assert.ok(rows.length > 0, `${c.key} が 0 件`);
    assert.equal(rows.length, c.count, `${c.key} の件数が一致しない`);
  }
});

test('ISSUE-221 カテゴリ絞り込みの合計が全指標数と一致する（取りこぼしゼロ）', () => {
  const total = categories().reduce((a, c) => a + c.count, 0);
  assert.equal(total, list().length, 'どのカテゴリからも到達できない指標がある');
});

// 配信される全ページ。1 つでも直書きが残ると、動的生成分と重複してサイドバーが二重になる
//   （実際に unified_ui/web/index.html の取り残しで重複が発生した）。
const SERVED_PAGES = [
  '../index.html',                                   // indicator_ui（ライブ core 8001）
  '../../../../unified_ui/web/index.html',           // 統合 UI（公開 8000・実際に配信される）
  '../../../../simulator/replay_ui/web/index.html',  // リプレイ（8281）
];

test('ISSUE-221 配信される全ページでカテゴリボタンを直書きしていない（再発防止）', async () => {
  const { readFile } = await import('node:fs/promises');
  for (const rel of SERVED_PAGES) {
    const html = await readFile(new URL(rel, import.meta.url), 'utf8');
    const hardcoded = [...html.matchAll(/data-category="(cat\.[^"]+)"/g)].map((m) => m[1]);
    assert.deepEqual(hardcoded, [], `${rel} に直書きが残っている: ${hardcoded.join(', ')}`);
  }
});

// ---- 計算.時間足の位置（依頼者指示 2026-08-08） ----
//
// 設定ダイアログのグループ順は form_model が「param の初出順」で決める。よって
// 「時間足」を一番上に出すことは **withCalcTimeframe が先頭へ置く**ことと同値であり、
// 決定点は 1 箇所（各指標定義は無改変＝新指標にも自動で適用される）。

test('計算.時間足は全対象指標で params の先頭にある（ダイアログの最上段）', async () => {
  const { get } = await import('../js/usecase/catalog.js');
  const { isActorDriven } = await import('../js/usecase/actor_driven_ids.js');
  const { listIndicators } = await import('../js/usecase/catalog.js').then((m) => ({
    listIndicators: m.listIndicators ?? (() => []),
  }));

  const ids = ['profit_rsi', 'cvfe', 'moving_averages', 'tickvol', 'profit_band', 'tgp_btlm'];
  for (const id of ids) {
    const def = get(id);
    assert.ok(def, `未登録: ${id}`);
    if (isActorDriven(def)) {
      continue;   // アクター駆動は /compute を持たない＝時間足を出さない（効かない設定を見せない）
    }
    assert.equal(def.params[0].name, 'timeframe', `${id}: 時間足が先頭にない`);
    assert.equal(def.params[0].group, 'group.calc');
  }
  void listIndicators;
});

test('アクター駆動指標には計算.時間足を付けない（従来どおり）', async () => {
  const { get } = await import('../js/usecase/catalog.js');
  for (const id of ['market_profile', 'tickvol_bands']) {
    const def = get(id);
    assert.equal(def.params.some((p) => p.name === 'timeframe'), false, `${id}: 効かない設定が出ている`);
  }
});
