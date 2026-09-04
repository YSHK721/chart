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
} from '../js/adapter/front/indicator_controller.js';
import { MARKET_PROFILE_HOST_CONTRACT } from '../js/adapter/front/market_profile_controller.js';
import { TICKVOL_BANDS_HOST_CONTRACT } from '../js/adapter/front/tickvol_bands_controller.js';
import { TEMPLATE_HOST_CONTRACT } from '../js/adapter/front/chart_template_controller.js';
import { COLOR_THEME_HOST_CONTRACT } from '../js/adapter/front/color_theme_controller.js';
import { SERIES_RENDER_HOST_CONTRACT } from '../js/adapter/front/series_render_router.js';
import { STATE_STORE_HOST_CONTRACT } from '../js/adapter/front/indicator_state_store.js';
import { DIALOG_HOST_CONTRACT } from '../js/adapter/front/indicator_dialog_controller.js';
import { STYLE_HOST_CONTRACT } from '../js/adapter/front/series_style_applier.js';
import { MIN_BARS_HOST_CONTRACT } from '../js/adapter/front/min_bars_ledger.js';
import { get } from '../js/usecase/catalog.js';
// コメント剥がしの実装は tools/js_layer_guard.mjs だけが持つ（同じ処理を書き写さない）。
import { stripComments } from '../../../../tools/js_layer_guard.mjs';

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
//   **コメントは除く**: 契約の説明文（「以前は host._mpModeResolver を読んでいた」等）を
//   参照と数えると、説明を書くほど契約が広がるという逆立ちした検査になる。
function hostMemberUsage(relPath) {
  const names = new Set();
  for (const m of stripComments(readFront(relPath)).matchAll(/host\.([_a-zA-Z][_a-zA-Z0-9]*)/g)) {
    names.add(m[1]);
  }
  return names;
}

// 契約が宣言する全メンバー名（必須 method + 必須 field + optional field）。
function contractMembers(contract) {
  return new Set([...contract.methods, ...contract.fields, ...contract.optionalFields]);
}

// 合成点（協働子を組み立てるファイル）。ISSUE-479 Wave2b: **走査対象を 1 ファイルに固定しない**。
//   協働子の構築は controller の ctor だけでなく共有配線（chart_app_wiring）でも起きる。片方しか
//   見ない検査は、もう片方で生 host が渡っても緑のままになる（本 Wave が自ら作りかけた穴）。
const COMPOSITION_SITES = Object.freeze([
  '../js/adapter/front/indicator_controller.js',
  '../js/adapter/front/chart_app_wiring.js',
]);

//: ロール一覧（協働子・契約・host 上の保持先・構築するファイル）。協働子を増やしたら本表へ 1 行足す。
//   足し忘れは下の「合成点の網羅」テストが検出する（構築点の走査と本表を突き合わせる）。
//
//   slot: fresh な IndicatorController から協働子へ辿れるフィールド名。**合成根が組む協働子は
//     controller のフィールドに載らない**ため null を置く（実行時遮断の検定はその協働子自身の
//     テストが担う。例: chart_app_wiring_market_profile_registration.test.js の 🟡-1 群）。
const ROLES = [
  {
    name: 'SeriesRenderRouter',
    source: '../js/adapter/front/series_render_router.js',
    contract: SERIES_RENDER_HOST_CONTRACT,
    slot: '_router',
    site: '../js/adapter/front/indicator_controller.js',
  },
  {
    name: 'IndicatorStateStore',
    source: '../js/adapter/front/indicator_state_store.js',
    contract: STATE_STORE_HOST_CONTRACT,
    slot: '_store',
    site: '../js/adapter/front/indicator_controller.js',
  },
  {
    name: 'TimeframeController',
    source: '../js/adapter/front/timeframe_controller.js',
    contract: TIMEFRAME_HOST_CONTRACT,
    slot: '_tf',
    site: '../js/adapter/front/indicator_controller.js',
  },
  {
    // S3: 構築点は合成根へ移った（controller の ctor はもう MP を組まない）。
    name: 'MarketProfileController',
    source: '../js/adapter/front/market_profile_controller.js',
    contract: MARKET_PROFILE_HOST_CONTRACT,
    slot: null,
    site: '../js/adapter/front/chart_app_wiring.js',
  },
  {
    name: 'TickvolBandsController',
    source: '../js/adapter/front/tickvol_bands_controller.js',
    contract: TICKVOL_BANDS_HOST_CONTRACT,
    slot: null,
    site: '../js/adapter/front/chart_app_wiring.js',
  },
  {
    name: 'ChartTemplateController',
    source: '../js/adapter/front/chart_template_controller.js',
    contract: TEMPLATE_HOST_CONTRACT,
    slot: null,
    site: '../js/adapter/front/chart_app_wiring.js',
  },
  {
    name: 'ColorThemeController',
    source: '../js/adapter/front/color_theme_controller.js',
    contract: COLOR_THEME_HOST_CONTRACT,
    slot: null,
    site: '../js/adapter/front/chart_app_wiring.js',
  },
  {
    name: 'IndicatorDialogController',
    source: '../js/adapter/front/indicator_dialog_controller.js',
    contract: DIALOG_HOST_CONTRACT,
    slot: '_dialog',
    site: '../js/adapter/front/indicator_controller.js',
  },
  {
    name: 'SeriesStyleApplier',
    source: '../js/adapter/front/series_style_applier.js',
    contract: STYLE_HOST_CONTRACT,
    slot: '_style',
    site: '../js/adapter/front/indicator_controller.js',
  },
  {
    name: 'MinBarsLedger',
    source: '../js/adapter/front/min_bars_ledger.js',
    contract: MIN_BARS_HOST_CONTRACT,
    slot: '_minBarsLedger',
    site: '../js/adapter/front/indicator_controller.js',
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

// 生 host（射影を通さない host 実体）を渡している構築点を導く（純関数＝検出器自身を検定できる）。
//   host 実体の識別子は合成点ごとに違う: controller の ctor では `this`、共有配線では `controller`。
//   **両方**を見る（片方だけ見る検査は、もう片方で契約が失われても緑のままになる）。
//   `indicator_controller.js` のような import パスを `controller.` の参照と取り違えないよう、
//   識別子の左端も縛る。
function rawHostInjections(source) {
  const re = /new\s+([A-Z][A-Za-z0-9_]*)\s*\(\s*(?:this|(?<![A-Za-z0-9_])controller)\s*[,)]/g;
  return [...source.matchAll(re)].map((m) => m[1]).sort();
}

// createHostView の射影を通している構築点を導く（純関数）。
function projectedInjections(source) {
  const re = /new\s+([A-Z][A-Za-z0-9_]*)\s*\(\s*createHostView\(/g;
  return [...source.matchAll(re)].map((m) => m[1]).sort();
}

test('検出器そのものの検定（生 host / 射影を取り違えない）', () => {
  assert.deepEqual(rawHostInjections('this._x = new FooController(this, opts);'), ['FooController']);
  assert.deepEqual(rawHostInjections('const a = new BarController(controller, deps);'), ['BarController']);
  assert.deepEqual(rawHostInjections("import { X } from './indicator_controller.js';"), [],
    'import パスを生 host 注入と取り違えている');
  assert.deepEqual(rawHostInjections('new BazController(createHostView(controller, C), d);'), [],
    '射影を生 host 注入と取り違えている');
  assert.deepEqual(projectedInjections('new BazController(createHostView(controller, C), d);'),
    ['BazController']);
});

for (const site of COMPOSITION_SITES) {
  test(`${site} の構築点は host 全体ではなく createHostView の射影を渡す`, () => {
    const raw = rawHostInjections(readFront(site));
    assert.deepEqual(raw, [], `host 全体を渡している構築点: ${raw.join(', ')}`);
  });
}

test('合成点の網羅: 全合成点で createHostView を通す構築点と ROLES 表が一致する', () => {
  const wired = COMPOSITION_SITES.flatMap((site) => projectedInjections(readFront(site))).sort();
  assert.deepEqual(
    wired,
    ROLES.map((r) => r.name).sort(),
    '協働子を増やしたら ROLES 表にも追加してください（契約なしの host 注入を許さない）',
  );
});

test('ROLES の site は、その協働子を実際に構築しているファイルを指す', () => {
  // 表の site 欄が実体とずれると、上の網羅テストは通るのに「どこで組まれるか」の記述だけが
  //   古くなる（宣言と実体の乖離＝本リポジトリが繰り返し潰してきた失敗型）。
  for (const { name, site } of ROLES) {
    assert.ok(projectedInjections(readFront(site)).includes(name),
      `${name} は ${site} で構築されていません（ROLES の site 欄が実体とずれています）`);
  }
});

// ---- (5) 実行時に契約外が遮断される（宣言ではなく実体） ----

for (const { name, slot, contract } of ROLES.filter((r) => r.slot !== null)) {
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
