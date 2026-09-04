// actor_controller_faces.test.js — アクターコントローラ契約（面の集合）の施行（ISSUE-479 Wave2b・JS レビュー 🟡-1）。
//
// なぜ在るか（レビュー指摘の実測）:
//   「アクター駆動指標の受け口」は computeId → コントローラのレジストリ 1 つに集約されたが、
//   その受け口が**要求する面の集合**はどこにも実体として無かった。あったのは
//   indicator_controller.js の未登録フォールバック（NULL_ACTOR_CONTROLLER）の**オブジェクトリテラル**と、
//   その直上のコメント「呼び出し口を足したら、ここにも足す（足し忘れは未登録経路でのみ TypeError になる）」
//   だけである。契約が宣言でなく散文で守られている状態は、必ず食い違う。
//   実際に食い違っていた: TickvolBandsController は 7 面のうち applyMpGrowth を持たず 6 面だった
//   （レビューが node で実測した TypeError。下の TC-B2 が同じ経路を再現する）。
//
// 何を固定するか — **3 点一致**（表・フォールバック・呼出口が同じ集合を指すこと）:
//   (a) 未登録フォールバックの面集合 == ACTOR_CONTROLLER_FACES
//   (b) 合成根が登録する全コントローラが FACES の全面を実装する
//   (c) FACES == ソース走査で得た `_actorControllerFor(...).X` 呼出口の全数
//       （indicator_controller / indicator_state_store / replay_indicator_controller）
//   どの 1 点がずれても落ちる。面を足すときに直す場所は表 1 箇所になる。
//
// 計算量（絶対命令 2026-08-28）: 面走査は対象ファイルを 1 本 1 回だけ読む。
//   検査項目を増やしても読取は増えない（発行 − 対象 = 0）。
//
// 構造: Arrange-Act-Assert（AAA）。

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdirSync, mkdtempSync, readFileSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

// 名前空間 import にする理由: 未定義の名前付き import は**リンク時**に落ち、ファイル全体が
//   読み込まれない（どの assert が何を要求しているのか報告に出ない）。表の不在は
//   「表が export されていない」という assert の失敗として出す。
import * as indicatorControllerModule from '../js/adapter/front/indicator_controller.js';
import { MarketProfileController } from '../js/adapter/front/market_profile_controller.js';
import { TickvolBandsController } from '../js/adapter/front/tickvol_bands_controller.js';
import { get } from '../js/usecase/catalog.js';
// コメント剥がしの実装は tools/js_layer_guard.mjs だけが持つ（同じ処理を書き写さない）。
import { stripComments } from '../../../../tools/js_layer_guard.mjs';

const { IndicatorController } = indicatorControllerModule;
const FACES = indicatorControllerModule.ACTOR_CONTROLLER_FACES;

const abs = (rel) => fileURLToPath(new URL(rel, import.meta.url));

/** (c) の走査対象 = レジストリの戻り値に対して面を呼ぶ実装ファイルの全数。 */
const CALL_SITE_FILES = Object.freeze([
  abs('../js/adapter/front/indicator_controller.js'),
  abs('../js/adapter/front/indicator_state_store.js'),
  abs('../../../../simulator/replay_ui/web/js/adapter/front/replay_indicator_controller.js'),
]);

const WIRING_FILE = abs('../js/adapter/front/chart_app_wiring.js');

// --------------------------------------------------------------------------- //
// 読取の単一点（計算量テストが発行を数えられるよう、読取だけを注入可能にする）
// --------------------------------------------------------------------------- //

const defaultRead = (p) => readFileSync(p, 'utf8');

/** 指定パスを **1 本 1 回だけ** 読み、絶対パス → 本文の Map を返す。 */
function collectFaceSources(paths, read = defaultRead) {
  const sources = new Map();
  for (const p of paths) {
    if (!sources.has(p)) {
      sources.set(p, read(p));
    }
  }
  return sources;
}

function countingRead(counter) {
  return (p) => { counter.reads.push(p); return readFileSync(p, 'utf8'); };
}

// --------------------------------------------------------------------------- //
// 走査器 — 呼出口の面 / 合成根が登録するコントローラ
// --------------------------------------------------------------------------- //

// `_actorControllerFor(...)` / `_actorControllerForInstance(...)` の**戻り値に対して**呼ぶメソッド名。
//   引数は 1 段のネスト（`this._catalog.get(x)`）まで許す。戻り値をそのまま返す形
//   （`return this._actorControllerFor(...)`）は `.` が続かないので拾わない＝面ではない。
const CALL_SITE_RE = /_actorControllerFor(?:Instance)?\((?:[^()]|\([^()]*\))*\)\s*\.\s*([A-Za-z_$][\w$]*)/g;

/** 走査対象（絶対パス → 本文）から呼出口の面をソート済み一意配列で返す。 */
function callSiteFaces(sources) {
  const faces = new Set();
  for (const source of sources.values()) {
    for (const m of stripComments(source).matchAll(CALL_SITE_RE)) {
      faces.add(m[1]);
    }
  }
  return [...faces].sort();
}

/**
 * 合成根が `registerActorController` へ渡すコントローラの**クラス名**を列挙する。
 *
 * なぜ名前の表を書かないか: 3 つ目のアクター駆動指標を登録した日に、この検定が
 *   「知らないコントローラが登録されている」で**落ちる**必要がある。手書きの表は黙って古びる。
 */
function registeredControllerClassNames(wiringSource) {
  const text = stripComments(wiringSource);
  const names = new Set();
  const marker = 'registerActorController(';
  for (let at = text.indexOf(marker); at !== -1; at = text.indexOf(marker, at + 1)) {
    // 対応する閉じ括弧まで（引数内の括弧を数える）。
    let depth = 0;
    let end = -1;
    for (let i = at + marker.length - 1; i < text.length; i += 1) {
      if (text[i] === '(') { depth += 1; }
      if (text[i] === ')') { depth -= 1; if (depth === 0) { end = i; break; } }
    }
    if (end === -1) {
      continue;
    }
    const args = text.slice(at + marker.length, end);
    // 深さ 0 の最初のカンマで computeId と controller を割る。
    let depth2 = 0;
    let comma = -1;
    for (let i = 0; i < args.length; i += 1) {
      if (args[i] === '(' || args[i] === '{' || args[i] === '[') { depth2 += 1; }
      if (args[i] === ')' || args[i] === '}' || args[i] === ']') { depth2 -= 1; }
      if (args[i] === ',' && depth2 === 0) { comma = i; break; }
    }
    if (comma === -1) {
      continue;
    }
    const expr = args.slice(comma + 1).trim();
    const direct = /^new\s+([A-Za-z_$][\w$]*)/.exec(expr);
    if (direct) {
      names.add(direct[1]);
      continue;
    }
    // 変数渡し: 同じソース内の `const <ident> = new X(` を引く。
    const ident = /^([A-Za-z_$][\w$]*)/.exec(expr);
    if (!ident) {
      continue;
    }
    const bound = new RegExp(`\\b(?:const|let|var)\\s+${ident[1]}\\s*=\\s*new\\s+([A-Za-z_$][\\w$]*)`)
      .exec(text);
    if (bound) {
      names.add(bound[1]);
    }
  }
  return [...names].sort();
}

/** クラス名 → 実体（(b) が面を検査する対象）。走査で出た名前がここに無ければ落とす。 */
const CONTROLLER_CLASSES = Object.freeze({
  MarketProfileController,
  TickvolBandsController,
});

const noop = () => {};

function makeController() {
  return new IndicatorController({
    catalog: { listIndicators: () => [], get },
    compute: { compute: async () => ({ ok: true, generation: 0, series: [] }) },
    persistence: {
      loadApplied: () => [], saveApplied: noop, loadFavorites: () => [], saveFavorites: noop,
      loadUiState: () => ({}), saveUiState: noop, nextSeq: () => 1,
    },
    renderer: {
      renderLine: noop, renderHorizontal: noop, renderHistogram: noop, setData: noop,
      setVisible: noop, remove: noop, setCandles: noop,
    },
    document: null,
  });
}

// --------------------------------------------------------------------------- //
// 表そのもの
// --------------------------------------------------------------------------- //

test('TC-0: アクターコントローラの面は表（ACTOR_CONTROLLER_FACES）として実体で在る', () => {
  // Arrange / Act: 表は indicator_controller.js が export する凍結配列。
  // Assert
  assert.ok(Array.isArray(FACES),
    'ACTOR_CONTROLLER_FACES が export されていません（面の集合が実体として存在せず、'
    + '散文コメントでしか守られていない状態です）');
  assert.ok(Object.isFrozen(FACES), '面の表が凍結されていない（実行時に書き換えられる）');
  assert.equal(new Set(FACES).size, FACES.length, `面の表に重複がある: ${FACES}`);
  assert.ok(FACES.length > 0, '面の表が空（検定が空振りする）');
});

// --------------------------------------------------------------------------- //
// (a) 未登録フォールバックは表から導出される
// --------------------------------------------------------------------------- //

test('TC-A1: 未登録フォールバックの面集合が表と一致する（表からの導出）', () => {
  // Arrange: 何も登録していない controller の未登録解決先＝共有 no-op。
  const c = makeController();
  // Act
  const nullActor = c._actorControllerFor({ compute: { computeId: 'unregistered_actor' } });
  // Assert
  assert.deepEqual(Object.keys(nullActor).sort(), [...FACES].sort(),
    '未登録フォールバックの面が表とずれています（表から導出していない＝2 箇所に書いてある）');
  for (const face of FACES) {
    assert.equal(typeof nullActor[face], 'function', `フォールバックの ${face} が関数でない`);
  }
});

test('TC-A2: 未登録フォールバックは全面が例外にならない（呼び出し側に分岐を作らせない）', () => {
  const c = makeController();
  const nullActor = c._actorControllerFor({ compute: { computeId: 'unregistered_actor' } });
  for (const face of FACES) {
    assert.doesNotThrow(() => nullActor[face]({}, null, {}), `フォールバックの ${face} が投げる`);
  }
});

// --------------------------------------------------------------------------- //
// (b) 登録される全コントローラが全面を実装する
// --------------------------------------------------------------------------- //

test('TC-B1: 合成根が登録する全コントローラが表の全面を実装する', () => {
  // Arrange: 登録されるクラスは合成根（chart_app_wiring.js）の実体から導く。
  const registered = registeredControllerClassNames(readFileSync(WIRING_FILE, 'utf8'));
  // Assert: 走査が空振りしていない＋知らないコントローラが増えていない。
  assert.ok(registered.length >= 2, `登録コントローラの走査が空振りしている: ${registered}`);
  assert.deepEqual(registered, Object.keys(CONTROLLER_CLASSES).sort(),
    '合成根の登録コントローラが本検定の対象と食い違っています'
    + '（増えたコントローラを CONTROLLER_CLASSES へ足すこと）');
  // Act / Assert: 各コントローラが全面を持つ。
  const missing = [];
  for (const name of registered) {
    const proto = CONTROLLER_CLASSES[name].prototype;
    for (const face of FACES) {
      if (typeof proto[face] !== 'function') {
        missing.push(`${name}.${face}`);
      }
    }
  }
  assert.deepEqual(missing, [],
    `登録コントローラに欠落した面があります（未登録経路でのみ TypeError になる形）:\n  ${missing.join('\n  ')}`);
});

test('TC-B2: レジストリ経由で解決した TickvolBandsController の全面が TypeError にならない', () => {
  // なぜ在るか: レビューが node で実測した壊れ方の再現。表に applyMpGrowth が在るのに
  //   TickvolBandsController だけ持っておらず、レジストリ経由の呼出が TypeError になっていた。
  // Arrange: 本番と同じ登録口へ、本番と同じ協働子を入れる（host / actor は最小スタブ）。
  const c = makeController();
  const host = {
    _state: { applied: [] }, _meta: new Map(), _datasetRef: 'sample', _document: null,
    _timeframe: '1D',
    _paramsObject: (p) => (p ?? {}), _defaultVariant: () => null, _defaultParams: () => ({}),
    _withParams: (s) => s, _renderLegend: noop, _persistAll: noop, _commitState: noop,
  };
  const actor = {
    setParams: noop, setEnabled: async () => {}, refresh: async () => {},
    onCandlesChanged: noop, isEnabled: () => false,
  };
  c.registerActorController('tickvol_bands', new TickvolBandsController(host, actor));
  // Act
  const resolved = c._actorControllerFor({ compute: { computeId: 'tickvol_bands' } });
  // Assert: 面の呼出そのものが TypeError にならない（非同期の中身は問わない）。
  for (const face of FACES) {
    assert.equal(typeof resolved[face], 'function',
      `解決した TickvolBandsController に ${face} が無い（レジストリ経由で TypeError になる）`);
  }
  assert.doesNotThrow(() => resolved.applyMpGrowth(),
    'applyMpGrowth の呼出が TypeError になる（レビュー実測の再現）');
});

// --------------------------------------------------------------------------- //
// (c) 表 == 呼出口の全数
// --------------------------------------------------------------------------- //

const SOURCES = collectFaceSources(CALL_SITE_FILES);

test('TC-C1: 表が呼出口の全数と一致する（足し忘れ・使われない面の両方を落とす）', () => {
  // Arrange / Act
  const faces = callSiteFaces(SOURCES);
  // Assert
  assert.deepEqual(faces, [...FACES].sort(),
    '表とレジストリ経由の呼出口がずれています。'
    + `\n  呼出口: ${faces.join(', ')}\n  表:     ${[...FACES].sort().join(', ')}`
    + '\n  （呼出口を足したら表にも足す／表にあって呼ばれない面は未登録経路から到達しない）');
});

test('TC-C2: 呼出口の走査器が空振りしていない（検出器そのものの検定）', () => {
  assert.deepEqual(
    callSiteFaces(new Map([['x.js', 'await this._actorControllerFor(meta.def).onLiveRecompute(i);']])),
    ['onLiveRecompute'],
  );
  assert.deepEqual(
    callSiteFaces(new Map([['x.js', 'this._actorControllerForInstance(inst).toggleVisible(inst);']])),
    ['toggleVisible'],
  );
  // 戻り値をそのまま返す形は面ではない（`.` が続かない）。
  assert.deepEqual(
    callSiteFaces(new Map([['x.js', 'return this._actorControllerFor(this._catalog.get(id));']])),
    [],
  );
  // コメント中の言及は拾わない。
  assert.deepEqual(
    callSiteFaces(new Map([['x.js', '// this._actorControllerFor(def).ghostFace();']])),
    [],
  );
});

test('TC-C3: 登録コントローラの走査器が空振りしていない（検出器そのものの検定）', () => {
  assert.deepEqual(
    registeredControllerClassNames("c.registerActorController('a', new AlphaController(h, x));"),
    ['AlphaController'],
  );
  assert.deepEqual(
    registeredControllerClassNames(
      'const beta = new BetaController(h, { actor });\n'
      + "c.registerActorController('b', beta);\n",
    ),
    ['BetaController'],
  );
  assert.deepEqual(
    registeredControllerClassNames("// c.registerActorController('z', new GhostController(h));"),
    [],
  );
});

// --------------------------------------------------------------------------- //
// 計算量: 面走査の読取 − 対象ファイル数 = 0
// --------------------------------------------------------------------------- //

test('計算量: 面走査 1 巡の読取 − 対象ファイル数 = 0（実対象）', () => {
  // なぜ在るか（絶対命令 2026-08-28）: 検査項目ごとにファイルを読み直す実装は、出力（面集合）が
  //   正しいまま読取だけが増える。状態検証では原理的に落ちない浪費である。
  //   固定するのは回数そのものではなく **無駄の不在**（読取 − 対象 = 0）。
  const counter = { reads: [] };
  const scanned = collectFaceSources(CALL_SITE_FILES, countingRead(counter));
  callSiteFaces(scanned);
  callSiteFaces(scanned);
  assert.equal(counter.reads.length - scanned.size, 0,
    `同じファイルを読み直している: reads=${counter.reads.length} files=${scanned.size}`);
  assert.equal(scanned.size, CALL_SITE_FILES.length);
});

const syntheticFiles = (count) => {
  const root = mkdtempSync(path.join(tmpdir(), 'actorfaces-'));
  mkdirSync(path.join(root, 'src'), { recursive: true });
  const paths = [];
  for (let i = 0; i < count; i += 1) {
    const p = path.join(root, 'src', `m${i}.js`);
    writeFileSync(p, `this._actorControllerFor(d${i}).onGear(a, b);\n`);
    paths.push(p);
  }
  return paths;
};

for (const fileCount of [4, 8]) {
  test(`計算量: 対象を ${fileCount} 本に増やしても読取 − 対象 = 0（オーダーの表明）`, () => {
    const counter = { reads: [] };
    const scanned = collectFaceSources(syntheticFiles(fileCount), countingRead(counter));
    callSiteFaces(scanned);
    assert.equal(counter.reads.length - scanned.size, 0);
    assert.equal(scanned.size, fileCount);
  });
}

for (const itemCount of [3, 6]) {
  test(`計算量: 検査項目を ${itemCount} 件に増やしても読取は増えない`, () => {
    const counter = { reads: [] };
    const scanned = collectFaceSources(CALL_SITE_FILES, countingRead(counter));
    for (let i = 0; i < itemCount; i += 1) {
      callSiteFaces(scanned);
    }
    assert.equal(counter.reads.length - scanned.size, 0);
  });
}

test('計算量ゲートの検出力: 項目ごとに読み直す変異で赤になる', () => {
  const counter = { reads: [] };
  const read = countingRead(counter);
  const first = collectFaceSources(CALL_SITE_FILES, read);
  collectFaceSources(CALL_SITE_FILES, read); // 捨てられる読取（項目ごとの読み直しの再現）。
  assert.notEqual(counter.reads.length - first.size, 0, '変異を検出できていない（検査が空振り）');
});
