// js_package_direction.test.js — フロント JS の**パッケージ間**依存方向ゲート（ISSUE-502 段階 3b/3e）。
//
// なぜ在るか（C-4 の実測形・2026-09-06〜09-07）:
//   indicator_ui（組み立て core）と market_profile（機能パッケージ）は相互に import していた。
//     - 正方向: indicator_ui → market_profile（symlink 27 本。組み立て → 機能で正当）
//     - 逆方向: market_profile → indicator_ui（3 本。`market_profile_primitive.js` の
//       chrome_tokens / series_primitive_lifecycle、`tf_period_tooltip.js` の chrome_css_var）
//   逆方向の 3 本が指していたのは「どちらの機能でもない共有カーネル」であり、その実体が
//   indicator_ui に置かれていたことだけが循環の原因だった。段階 3b で実体を中立パッケージ
//   `indigators/chart_kernel/web/js` へ移し、両者はそこへ**一方向**で依存する形になった。
//
// なぜ層ゲート（js_layer_direction.test.js）では足りないか:
//   層ゲート（G-1）が見るのは「走査中の配信根の**内側**の層方向」と「他コアの配信根への相対
//   import」である。共有カーネルは各パッケージの層ディレクトリへ symlink で現れるため、層としては
//   完全に正しい（domain → domain、adapter → usecase）。**実体がどのパッケージに属するか**は
//   symlink を辿らないと分からず、G-1 は fs を触らない設計（計算量ゲートを守るため）なので
//   原理的に見えない。ここが唯一の検出点である。
//
// なぜ「複製が無いこと」ではなく「向き」を測るか:
//   symlink による単一ソース共有は本リポジトリの既定手段であり、複製は元から 0 である。壊れるのは
//   常に**向き**（誰が誰の実体を持つか）であって、それは realpath を辿ってはじめて見える。
//
// なぜ chart_kernel が持つか:
//   測っている不変条件は「中立パッケージが中立であり続けること」であり、その主語は
//   chart_kernel である。consumer 側（indicator_ui / market_profile）へ置くと、同じ規則の
//   写しが 2 枚になるか、片方のスイートを消した日に静かに未実行になる。
//
// 走査と指定子抽出は tools/js_layer_guard.mjs だけが持つ（ここへ書き写さない）。
//
// 本ファイルが固定する不変条件:
//   P-1 chart_kernel の実体は chart_kernel の外を import しない（中立パッケージの閉包）。
//   P-2 market_profile の実体は indicator_ui の実体を import しない（逆方向の不在＝循環の不在）。
//   P-3 indicator_ui → market_profile は現に存在する（正方向。錨が恒真式に退化していない証拠）。
//   P-4 両者 → chart_kernel は現に存在する（移設が効いている証拠）。
// 併せて計算量（realpath の発行 − 相異なるパス数 = 0・項目を増やしても発行が増えない）。

import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  mkdirSync, readdirSync, readFileSync, realpathSync, statSync, symlinkSync, writeFileSync,
} from 'node:fs';
import { mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

import { collectSources, importSpecifiers } from '../../../../tools/js_layer_guard.mjs';

const TESTS_DIR = path.dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = path.resolve(TESTS_DIR, '..', '..', '..', '..');

/** 依存方向を測る対象パッケージ（`<pkg>/web/js` の絶対パス）。 */
const PACKAGE_ROOTS = Object.freeze({
  chart_kernel: path.join(REPO_ROOT, 'indigators', 'chart_kernel', 'web', 'js'),
  indicator_ui: path.join(REPO_ROOT, 'indigators', 'indicator_ui', 'web', 'js'),
  market_profile: path.join(REPO_ROOT, 'indigators', 'market_profile', 'web', 'js'),
});

const DEFAULT_IO = Object.freeze({
  readFile: (p) => readFileSync(p, 'utf8'),
  readDir: (p) => readdirSync(p),
  statOf: (p) => statSync(p),
  realPathOf: (p) => realpathSync(p),
});

/**
 * realpath の解決を **1 パスにつき 1 回だけ** 発行する解決子。
 * 検査項目ごとに辿り直さないための単一の解決点（計算量テストがこの発行を数える）。
 */
function realPathResolver(io = DEFAULT_IO) {
  const cache = new Map();
  return (p) => {
    if (!cache.has(p)) {
      let value;
      try {
        value = io.realPathOf(p);
      } catch {
        value = null; // 解決不能（切れた symlink・不在）。
      }
      cache.set(p, value);
    }
    return cache.get(p);
  };
}

/** 実パスが属するパッケージ名。どの対象にも属さなければ null。 */
export function packageOfRealPath(realPath, roots) {
  for (const [name, root] of Object.entries(roots)) {
    if (!path.relative(root, realPath).startsWith('..')) {
      return name;
    }
  }
  return null;
}

/**
 * 走査根の各ファイルが張る**パッケージ間の辺**を列挙する。
 *
 * 判定は 2 段:
 *   1. 指定子は**走査上のパス**（symlink のまま）から解決する。ESM の解決は URL 基準であり、
 *      配信根から見た解決先がそれだからである（実体の置き場から解決すると、配信時に成立して
 *      いる参照を「壊れている」と誤判定する）。
 *   2. 属するパッケージは**解決先の実体**（realpath）で決める。symlink を辿らないと「誰の実体か」
 *      は分からず、それこそが本ゲートの測る対象である。
 *
 * @returns {{from: ?string, to: ?string, at: string, spec: string, broken: boolean}[]}
 */
export function packageEdges(sources, roots, resolveReal) {
  const edges = [];
  for (const [absPath, source] of sources) {
    const fromReal = resolveReal(absPath);
    const from = fromReal === null ? null : packageOfRealPath(fromReal, roots);
    for (const spec of importSpecifiers(source)) {
      if (!spec.startsWith('.')) {
        continue; // 相対以外（bare / 絶対 URL）はパッケージ解決の対象外。
      }
      const target = path.resolve(path.dirname(absPath), spec);
      const toReal = resolveReal(target);
      edges.push({
        from,
        to: toReal === null ? null : packageOfRealPath(toReal, roots),
        at: path.relative(REPO_ROOT, fromReal ?? absPath),
        spec,
        broken: toReal === null,
      });
    }
  }
  return edges;
}

/** `<from> → <to>` の辺だけを人が読める行にして返す。 */
function describe(edges) {
  return edges.map((e) => `${e.at}: ${e.from} → ${e.to}（${e.spec}）`).sort();
}

// --------------------------------------------------------------------------- //
// 実リポジトリに対する回帰錨
// --------------------------------------------------------------------------- //
const RESOLVE = realPathResolver();

/**
 * 走査根が消えている場合に **module 評価時に throw しない**（実測 2026-09-07）。
 *
 * 素の `collectSources` は根が無いと ENOENT を投げる。それが module のトップレベルで起きると、
 * テストファイルは 1 件も登録されないまま落ち、出力は「どのパッケージの根が無いか」ではなく
 * fs のスタックトレースになる。パッケージが移動・改名されたときに読むべきなのは前者である。
 * 空の走査結果を返し、下の「走査が空振りしていない」が根の不在として報告する。
 */
function collectIfPresent(root) {
  try {
    statSync(root);
  } catch {
    return new Map();
  }
  return collectSources([root]);
}

const EDGES = Object.freeze(Object.fromEntries(
  Object.keys(PACKAGE_ROOTS).map((name) => [
    name, packageEdges(collectIfPresent(PACKAGE_ROOTS[name]), PACKAGE_ROOTS, RESOLVE),
  ]),
));

/** ある走査結果から `from`/`to` を指定して辺を絞る。 */
function edgesFromTo(edges, from, to) {
  return edges.filter((e) => e.from === from && e.to === to);
}

test('走査が空振りしていない（3 パッケージとも実体が見つかり、辺が張られている）', () => {
  for (const [name, root] of Object.entries(PACKAGE_ROOTS)) {
    let present = false;
    try {
      present = statSync(root).isDirectory();
    } catch { /* 不在は下の assert が名前付きで報告する */ }
    assert.ok(present, `${name} の走査根が無い（移動・改名したなら PACKAGE_ROOTS を直すこと）: ${root}`);
    assert.ok(EDGES[name].length > 0, `${name} から辺が 1 本も出ていない（走査の破綻）`);
  }
});

test('解決できない相対 import が無い（切れた symlink・移設の取り残しの検出）', () => {
  const broken = Object.values(EDGES).flat().filter((e) => e.broken);
  assert.deepEqual(describe(broken), [],
    `解決できない相対 import（実体が消えている）:\n${describe(broken).join('\n')}`);
});

test('P-1 chart_kernel の実体は chart_kernel の外を import しない（中立パッケージの閉包）', () => {
  const escaping = EDGES.chart_kernel.filter((e) => e.from === 'chart_kernel' && e.to !== 'chart_kernel');
  assert.deepEqual(describe(escaping), [],
    '共有カーネルが消費者側を参照している（中立でなくなり循環が復活する）:\n'
    + `${describe(escaping).join('\n')}`);
  // 錨の非空振り: カーネル内部の辺は現に存在する（chrome_tokens → color_roles 等）。
  assert.ok(edgesFromTo(EDGES.chart_kernel, 'chart_kernel', 'chart_kernel').length > 0,
    'chart_kernel 内部の辺が 0 本＝走査または解決が壊れている');
});

test('P-2 market_profile の実体は indicator_ui の実体を import しない（C-4 の逆方向の不在）', () => {
  const reverse = edgesFromTo(EDGES.market_profile, 'market_profile', 'indicator_ui');
  assert.deepEqual(describe(reverse), [],
    'market_profile が indicator_ui の実体を参照している（C-4 の再発。共有物なら '
    + `chart_kernel へ移すこと）:\n${describe(reverse).join('\n')}`);
});

test('P-3 indicator_ui → market_profile の正方向は現に存在する（錨が恒真式でない証拠）', () => {
  const forward = edgesFromTo(EDGES.indicator_ui, 'indicator_ui', 'market_profile');
  assert.ok(forward.length > 0,
    '組み立て core → 機能パッケージの辺が 0 本。P-2 が「そもそも辺が無い」ことで通っている疑い');
});

test('P-4 indicator_ui / market_profile はいずれも chart_kernel へ依存する（移設が効いている）', () => {
  assert.ok(edgesFromTo(EDGES.indicator_ui, 'indicator_ui', 'chart_kernel').length > 0,
    'indicator_ui → chart_kernel が 0 本（移設が届いていない）');
  assert.ok(edgesFromTo(EDGES.market_profile, 'market_profile', 'chart_kernel').length > 0,
    'market_profile → chart_kernel が 0 本（張り替えが届いていない）');
});

// --------------------------------------------------------------------------- //
// 検出器の自己検定 — 合成ツリーで「symlink 経由の逆 import」を実際に捕捉する
// --------------------------------------------------------------------------- //
/**
 * 合成の 2 パッケージを作る。`linkBack` が真なら pkg_a 側に pkg_b の実体を指す symlink を張り、
 * pkg_a のファイルがそれを import する（＝ C-4 の旧形そのもの）。
 */
function syntheticPackages({ linkBack }) {
  const root = mkdtempSync(path.join(tmpdir(), 'jspkgdir-'));
  const roots = {
    pkg_a: path.join(root, 'pkg_a', 'web', 'js'),
    pkg_b: path.join(root, 'pkg_b', 'web', 'js'),
  };
  for (const js of Object.values(roots)) {
    mkdirSync(path.join(js, 'domain'), { recursive: true });
  }
  writeFileSync(path.join(roots.pkg_b, 'domain', 'shared.js'), 'export const shared = 1;\n');
  writeFileSync(path.join(roots.pkg_a, 'domain', 'own.js'), 'export const own = 1;\n');
  if (linkBack) {
    symlinkSync(
      path.join(roots.pkg_b, 'domain', 'shared.js'),
      path.join(roots.pkg_a, 'domain', 'shared.js'),
    );
    writeFileSync(
      path.join(roots.pkg_a, 'domain', 'model.js'),
      "export { shared } from './shared.js';\n",
    );
  } else {
    writeFileSync(
      path.join(roots.pkg_a, 'domain', 'model.js'),
      "export { own } from './own.js';\n",
    );
  }
  return roots;
}

test('検出器は symlink 経由の逆 import を捕捉する（C-4 の旧形を逐語で再現）', () => {
  const roots = syntheticPackages({ linkBack: true });
  const edges = packageEdges(collectSources([roots.pkg_a]), roots, realPathResolver());
  const crossing = edges.filter((e) => e.from === 'pkg_a' && e.to === 'pkg_b');
  assert.equal(crossing.length, 1,
    `symlink 経由の越境を捕捉できていない（symlink を辿らない実装の疑い）: ${describe(edges)}`);
  assert.equal(crossing[0].spec, './shared.js');
});

test('検出器は自パッケージ内の import を越境にしない（誤検出の側も測る）', () => {
  const roots = syntheticPackages({ linkBack: false });
  const edges = packageEdges(collectSources([roots.pkg_a]), roots, realPathResolver());
  assert.deepEqual(edges.filter((e) => e.from !== e.to), [],
    `自パッケージ内の import を越境と誤判定している: ${describe(edges)}`);
  assert.ok(edges.length > 0, '合成ツリーから辺が出ていない（自己検定が空振り）');
});

test('検出器は解決不能な指定子を broken として報告する（黙って落とさない）', () => {
  const roots = syntheticPackages({ linkBack: false });
  writeFileSync(
    path.join(roots.pkg_a, 'domain', 'dangling.js'),
    "export { gone } from './not_there.js';\n",
  );
  const edges = packageEdges(collectSources([roots.pkg_a]), roots, realPathResolver());
  assert.equal(edges.filter((e) => e.broken).length, 1,
    `解決不能な指定子が報告されていない: ${describe(edges)}`);
});

// --------------------------------------------------------------------------- //
// 計算量: 解決の発行 − 相異なるパス数 = 0（測るのは時間ではなく回数）
// --------------------------------------------------------------------------- //
function countingIo(counter) {
  return {
    readFile: (p) => { counter.reads.push(p); return readFileSync(p, 'utf8'); },
    readDir: (p) => readdirSync(p),
    statOf: (p) => statSync(p),
    realPathOf: (p) => { counter.resolves.push(p); return realpathSync(p); },
  };
}

/** 合成パッケージへ `count` 本の葉モジュールを足す（走査量を振る 2 点目を作るため）。 */
function syntheticPackagesWithLeaves(count) {
  const roots = syntheticPackages({ linkBack: true });
  for (let i = 0; i < count; i += 1) {
    writeFileSync(
      path.join(roots.pkg_a, 'domain', `m${i}.js`),
      "export { shared } from './shared.js';\n",
    );
  }
  return roots;
}

for (const leafCount of [10, 20]) {
  test(`検定 1 巡の読取 − 対象ファイル数 = 0（葉 ${leafCount} 本）`, () => {
    const roots = syntheticPackagesWithLeaves(leafCount);
    const counter = { reads: [], resolves: [] };
    const io = countingIo(counter);
    const sources = collectSources([roots.pkg_a], io);
    packageEdges(sources, roots, realPathResolver(io));
    assert.equal(counter.reads.length - sources.size, 0,
      `同じファイルを読み直している: reads=${counter.reads.length} files=${sources.size}`);
  });

  test(`解決の発行 − 相異なるパス数 = 0（葉 ${leafCount} 本）`, () => {
    const roots = syntheticPackagesWithLeaves(leafCount);
    const counter = { reads: [], resolves: [] };
    const io = countingIo(counter);
    const sources = collectSources([roots.pkg_a], io);
    packageEdges(sources, roots, realPathResolver(io));
    const unique = new Set(counter.resolves).size;
    assert.equal(counter.resolves.length - unique, 0,
      `同じパスを辿り直している: resolves=${counter.resolves.length} unique=${unique}`);
  });
}

for (const itemCount of [3, 6]) {
  test(`検査項目を ${itemCount} 件に増やしても解決は増えない（オーダーの表明）`, () => {
    const roots = syntheticPackagesWithLeaves(10);
    const counter = { reads: [], resolves: [] };
    const io = countingIo(counter);
    const sources = collectSources([roots.pkg_a], io);
    const resolve = realPathResolver(io);
    for (let i = 0; i < itemCount; i += 1) {
      packageEdges(sources, roots, resolve);
    }
    const unique = new Set(counter.resolves).size;
    assert.equal(counter.resolves.length - unique, 0,
      `項目ごとに辿り直している: resolves=${counter.resolves.length} unique=${unique}`);
  });
}

test('計算量ゲートの検出力: 解決を共有しない変異で赤になる', () => {
  const roots = syntheticPackagesWithLeaves(10);
  const counter = { reads: [], resolves: [] };
  const io = countingIo(counter);
  const sources = collectSources([roots.pkg_a], io);
  // 項目ごとに解決子を作り直す浪費の再現（捨てられる解決が混ざる）。
  packageEdges(sources, roots, realPathResolver(io));
  packageEdges(sources, roots, realPathResolver(io));
  const unique = new Set(counter.resolves).size;
  assert.notEqual(counter.resolves.length - unique, 0,
    '変異を検出できていない（検査が空振り）');
});
