// host_role_contract.test.js — フロント front/adapter のロール契約（ISP・ISSUE-099 🟡-3/🟡-4・ISSUE-255）の回帰固定。
//
// 対象: IndicatorController の協働子 5 つ（描画振分 / 永続化復元 / 時間足 / MP / ダイアログ）が
//   host へ要求する面を、広い公開面（約 40 メソッド＋20 超フィールド）ではなくロール専用の狭い契約に
//   限定したことを **3 方向**で固定する:
//     (1) 協働子が実際に読む/呼ぶ host.X の集合 ⊆ ロール契約（依存面が契約を超えない）。
//     (2) 契約の必須面はすべて実際に使われる（契約が過大でない＝最小面）。
//     (3) IndicatorController（present 共有ベース）が契約の全メンバーを構造的に満たす（host 面 ⊇ 契約）。
//
// ISSUE-255 で加えた最重要点: 契約は**宣言だけ**では効かない。実際に渡すのが host 全体なら、
//   協働子は契約外へいつでも触れる（ソース走査テストは書き方を変えれば迂回できる）。
//   よって合成点は createHostView(host, 契約) の射影を渡し、契約外アクセスを**実行時に例外**にする。
//   本ファイルはその配線（射影を通していること）と実行時の遮断も固定する。
// 構造: Arrange-Act-Assert（AAA）。

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

import {
  IndicatorController,
  TIMEFRAME_HOST_CONTRACT,
  MARKET_PROFILE_HOST_CONTRACT,
} from '../js/adapter/front/indicator_controller.js';
import { SERIES_RENDER_HOST_CONTRACT } from '../js/adapter/front/series_render_router.js';
import { STATE_STORE_HOST_CONTRACT } from '../js/adapter/front/indicator_state_store.js';
import { DIALOG_HOST_CONTRACT } from '../js/adapter/front/indicator_dialog_controller.js';
import { STYLE_HOST_CONTRACT } from '../js/adapter/front/series_style_applier.js';
import { get } from '../js/usecase/catalog.js';

const noop = () => {};

// DOM/port を使わない純構造検証のため、ports は最小スタブで生成（既存 indicator_controller.test.js と同型）。
function makeController() {
  return new IndicatorController({
    catalog: { listIndicators: () => [], get },
    compute: { compute: async () => ({ ok: true, generation: 0, series: [] }) },
    persistence: { loadApplied: () => [], saveApplied: noop, loadFavorites: () => [], saveFavorites: noop, loadUiState: () => ({}), saveUiState: noop, nextSeq: () => 1 },
    renderer: { renderLine: noop, renderHorizontal: noop, setData: noop, setVisible: noop, remove: noop, setCandles: noop },
    document: null,
  });
}

function readFront(relPath) {
  return readFileSync(fileURLToPath(new URL(relPath, import.meta.url)), 'utf8');
}

// 協働子ソースから host.X / this._host.X で参照される host メンバー名の集合を抽出する。
function hostMemberUsage(relPath) {
  const names = new Set();
  for (const m of readFront(relPath).matchAll(/host\.([_a-zA-Z][_a-zA-Z0-9]*)/g)) {
    names.add(m[1]);
  }
  return names;
}

// 契約が宣言する全メンバー名（必須 method + 必須 field + optional field）。
function contractMembers(contract) {
  return new Set([...contract.methods, ...contract.fields, ...contract.optionalFields]);
}

//: ロール一覧（協働子・契約・host 上の保持先）。協働子を増やしたら本表へ 1 行足す。
//   足し忘れは下の「合成点の網羅」テストが検出する（構築点の走査と本表を突き合わせる）。
const ROLES = [
  {
    name: 'SeriesRenderRouter',
    source: '../js/adapter/front/series_render_router.js',
    contract: SERIES_RENDER_HOST_CONTRACT,
    slot: '_router',
  },
  {
    name: 'IndicatorStateStore',
    source: '../js/adapter/front/indicator_state_store.js',
    contract: STATE_STORE_HOST_CONTRACT,
    slot: '_store',
  },
  {
    name: 'TimeframeController',
    source: '../js/adapter/front/timeframe_controller.js',
    contract: TIMEFRAME_HOST_CONTRACT,
    slot: '_tf',
  },
  {
    name: 'MarketProfileController',
    source: '../js/adapter/front/market_profile_controller.js',
    contract: MARKET_PROFILE_HOST_CONTRACT,
    slot: '_mp',
  },
  {
    name: 'IndicatorDialogController',
    source: '../js/adapter/front/indicator_dialog_controller.js',
    contract: DIALOG_HOST_CONTRACT,
    slot: '_dialog',
  },
  {
    name: 'SeriesStyleApplier',
    source: '../js/adapter/front/series_style_applier.js',
    contract: STYLE_HOST_CONTRACT,
    slot: '_style',
  },
];

// ---- 契約記述オブジェクトの健全性 ----

for (const { name, contract } of ROLES) {
  test(`${name} の契約は role/methods/fields/optionalFields を凍結公開する`, () => {
    assert.equal(typeof contract.role, 'string');
    assert.ok(contract.role.length > 0);
    assert.ok(Array.isArray(contract.methods));
    assert.ok(Array.isArray(contract.fields));
    assert.ok(Array.isArray(contract.optionalFields));
    assert.ok(Object.isFrozen(contract));
  });
}

// ---- (1) 依存面 ⊆ 契約: 協働子が host から読む/呼ぶ面が契約を超えない ----

for (const { name, source, contract } of ROLES) {
  test(`${name} が参照する host.X 集合は ${contract.role} 契約の部分集合（広依存の遮断）`, () => {
    const used = hostMemberUsage(source);
    const allowed = contractMembers(contract);
    const leaks = [...used].filter((n) => !allowed.has(n));
    assert.deepEqual(leaks, [], `契約外の host 参照: ${leaks.join(', ')}`);
  });
}

// ---- (2) 契約が過大でない（ISP: 最小面） ----

for (const { name, source, contract } of ROLES) {
  test(`${contract.role} 契約の必須メンバーは全て ${name} が実際に使用する`, () => {
    const used = hostMemberUsage(source);
    const required = [...contract.methods, ...contract.fields];
    const unused = required.filter((n) => !used.has(n));
    assert.deepEqual(unused, [], `契約に含むが未使用の面: ${unused.join(', ')}`);
  });
}

// ---- (3) host 面 ⊇ 契約: IndicatorController が構造的に契約を満たす ----

for (const { contract } of ROLES) {
  test(`IndicatorController は ${contract.role} 契約を構造的に満たす（method=function / field=在席）`, () => {
    const c = makeController();
    for (const m of contract.methods) {
      assert.equal(typeof c[m], 'function', `method 欠落: ${m}`);
    }
    for (const f of contract.fields) {
      assert.ok(f in c, `field 欠落: ${f}`);
    }
  });
}

// ---- (4) 合成点が host 全体ではなく射影を渡す（ISSUE-255 の本体） ----

test('協働子の構築点は host 全体（this）ではなく createHostView の射影を渡す', () => {
  const src = readFront('../js/adapter/front/indicator_controller.js');
  // `new Xxx(this` / `new Xxx(this,` の形（host 丸ごと注入）が 1 つも無いこと。
  const raw = [...src.matchAll(/new\s+([A-Z][A-Za-z0-9_]*)\s*\(\s*this\s*[,)]/g)].map((m) => m[1]);
  assert.deepEqual(raw, [], `host 全体を渡している構築点: ${raw.join(', ')}`);
});

test('合成点の網羅: createHostView を通す構築点と ROLES 表が一致する', () => {
  const src = readFront('../js/adapter/front/indicator_controller.js');
  const wired = [...src.matchAll(/new\s+([A-Z][A-Za-z0-9_]*)\s*\(\s*createHostView\(/g)].map((m) => m[1]);
  assert.deepEqual(
    wired.sort(),
    ROLES.map((r) => r.name).sort(),
    '協働子を増やしたら ROLES 表にも追加してください（契約なしの host 注入を許さない）',
  );
});

// ---- (5) 実行時に契約外が遮断される（宣言ではなく実体） ----

for (const { name, slot, contract } of ROLES) {
  test(`${name} が受け取る host は ${contract.role} 契約外へ触れると例外になる`, () => {
    const c = makeController();
    const view = c[slot] && c[slot]._host;
    assert.ok(view, `${slot}._host が取得できません（協働子の host 保持名が変わった可能性）`);

    // 契約面は読める（代表 1 つ）。
    const sample = contract.methods[0] || contract.fields[0];
    assert.doesNotThrow(() => view[sample]);

    // host には実在するが契約に無い面（_scheduler は controller 内部の持ち物）。
    assert.equal(typeof c._scheduler, 'object');
    if (!contractMembers(contract).has('_scheduler')) {
      assert.throws(() => view._scheduler, /契約外の host メンバー/);
    }
  });
}
