// js_layer_guard.mjs — フロント（JS）の依存方向ゲートの**唯一の実装**（ISSUE-479 Wave2 J-4）。
//
// なぜ共有モジュールなのか:
//   同じ走査を各 core のテストへ書き写すと、規則が 1 つで実装が複数ある状態になり、片方だけ
//   直された日に片方の core だけ検査が緩む。走査は本モジュールだけが持ち、各 core のテストは
//   「どの根を見るか」と「何を assert するか」だけを持つ。
//
// なぜ「走査対象の列挙」を持たないか（simulator/tests/unit/test_layer_dependency_direction.py
// :12-27 の規律をフロントへ写像）:
//   列挙に載っていない新規ディレクトリは、違反しても永久に検出されない。走査対象は名前の表
//   ではなく**構造**から導く——配信根 `web/js` の直下にあり、層名（domain / usecase / adapter /
//   public）を持つディレクトリはすべて層とみなす。層が増えても本ファイルの分岐は書き換わらない。
//
// なぜ import 走査だけでは足りないか（D-1 の実測）:
//   `const PATH = '/live/js/usecase/period_presets.js'; await import(PATH);` は **識別子渡しの
//   動的 import** であり、import 文の走査には原理的に現れない。他 core の内部階層を名指しする
//   文字列そのものを見ないと検出できない。これが G-3 の存在理由である。
//
// 依存: node 標準（fs / path）のみ。読取は注入可能（計算量テストが発行回数を数えられるように）。

import { readdirSync, readFileSync, statSync } from 'node:fs';
import path from 'node:path';

/** 層名 → その層が import してよい層の集合（外側ほど多くを見てよい）。 */
export const LAYER_RULES = Object.freeze({
  domain: Object.freeze(['domain']),
  usecase: Object.freeze(['usecase', 'domain']),
  adapter: Object.freeze(['adapter', 'usecase', 'domain', 'public']),
  // public は「他 core へ公開する面」＝最外周。自 core の内側を束ねて再輸出するのが仕事。
  public: Object.freeze(['public', 'adapter', 'usecase', 'domain']),
});

export const LAYER_NAMES = Object.freeze(Object.keys(LAYER_RULES));

/** 配信上の core 名（URL の第 1 セグメント）。 */
export const CORE_SEGMENTS = Object.freeze(['live', 'replay', 'sim', 'dashboard']);

/** 他 core を名指してよい唯一の場所（公開面）。 */
export const PUBLIC_URL_RE = new RegExp(
  `^/(?:${CORE_SEGMENTS.join('|')})/js/public/[^/]+\\.js$`,
);

// 他 core の**モジュール URL**（.js で終わる絶対パス文字列）。API パス（`/live/candles` 等）は
//   公開契約であって階層の名指しではないため対象外。末尾の否定先読みは `.json` を除くためで、
//   これが無いと `/live/data/trade_markers.json` を誤検出する（実測 2026-09-04）。
//
// 左端の否定後読みは**相対指定子**を除くためである（実測 2026-09-04）。
//   `import { t } from '../../replay/timing.js';` の指定子は `/replay/timing.js` を部分文字列として
//   含み、左端を縛らないと「他 core の名指し」に見える。相対指定子を解決して見るのは G-1
//   （layerDirectionOffenders の isForeignWebJsPath — 他コアの配信根 `web/js` 配下を offender に
//   する）の担当で、G-3 の担当は配信 URL（`/` 始まり）だけである。宣言だけ置いて G-1 側に実体が
//   無かった期間（ISSUE-479 Wave2b・JS レビュー 🟡-2）は、この型の越境がどの検査からも漏れていた。
//   除くのは直前が「パス断片の続き」に見える文字（英数字・`_`・`.`・`-`）のときのみ。
//   クォート直後（`'/live/...'`）とテンプレート補間直後（`` `${P}/live/...` ``）は残る
//   ＝合成された越境 URL を取りこぼさない。
export const CROSS_CORE_MODULE_URL_RE = new RegExp(
  `(?<![A-Za-z0-9_.\\-])/(?:${CORE_SEGMENTS.join('|')})/[A-Za-z0-9_\\-./]*\\.js(?![A-Za-z0-9])`,
  'g',
);

/** 行コメント・ブロックコメントを落とす（宣言の走査に文章を混ぜない）。 */
export function stripComments(source) {
  return source.replace(/\/\*[\s\S]*?\*\//g, '').replace(/(^|[^:])\/\/[^\n]*/g, '$1');
}

const DEFAULT_IO = Object.freeze({
  readFile: (p) => readFileSync(p, 'utf8'),
  readDir: (p) => readdirSync(p),
  statOf: (p) => statSync(p),
});

/**
 * 走査根の配下の .js を **1 ファイル 1 回だけ** 読み、絶対パス → 本文の Map を返す。
 * 検査項目ごとに読み直さないための単一の読取点（計算量テストがこの発行を数える）。
 */
export function collectSources(roots, io = DEFAULT_IO) {
  const sources = new Map();
  const walk = (dir) => {
    for (const name of io.readDir(dir)) {
      if (name === 'node_modules' || name.startsWith('.')) {
        continue;
      }
      const full = path.join(dir, name);
      const st = io.statOf(full);
      if (st.isDirectory()) {
        walk(full);
      } else if (name.endsWith('.js') && !sources.has(full)) {
        sources.set(full, io.readFile(full));
      }
    }
  };
  for (const root of roots) {
    walk(root);
  }
  return sources;
}

/** `web/js` 直下にある層ディレクトリを**構造から**見つける（名前の表を持たない）。 */
export function discoverLayers(jsRoot, io = DEFAULT_IO) {
  const out = [];
  for (const name of io.readDir(jsRoot)) {
    if (!LAYER_NAMES.includes(name)) {
      continue;
    }
    if (io.statOf(path.join(jsRoot, name)).isDirectory()) {
      out.push(name);
    }
  }
  return out;
}

/** ファイルが属する層（`web/js` からの第 1 セグメント）。層外は null。 */
export function layerOf(absPath, jsRoot) {
  const rel = path.relative(jsRoot, absPath);
  if (rel.startsWith('..')) {
    return null;
  }
  const head = rel.split(path.sep)[0];
  return LAYER_NAMES.includes(head) ? head : null;
}

/** 静的 import / re-export / 文字列リテラルの動的 import から指定子を取り出す。 */
export function importSpecifiers(source) {
  const text = stripComments(source);
  const out = [];
  const patterns = [
    /(?:^|[\s;}])(?:import|export)\s[\s\S]*?from\s*['"]([^'"]+)['"]/g,
    /(?:^|[\s;}])import\s*['"]([^'"]+)['"]/g,
    /\bimport\s*\(\s*['"]([^'"]+)['"]\s*\)/g,
  ];
  for (const re of patterns) {
    for (const m of text.matchAll(re)) {
      out.push(m[1]);
    }
  }
  return out;
}

/** 配信根を示すパス断片（`.../web/js/...`）。 */
const WEB_JS_FRAGMENT = `${path.sep}web${path.sep}js${path.sep}`;

/**
 * 解決先が**他コアの配信根（`web/js`）配下**か。
 *
 * 判定はパス文字列だけで行う（fs を触らない）。存在確認を混ぜると検査項目ごとに I/O が増え、
 * 「検定 1 巡の読取 − 対象ファイル数 = 0」の計算量ゲートが崩れる。
 *
 * @param {string} target 解決済みの絶対パス。
 * @param {string} jsRoot 走査中の core の配信根。
 */
function isForeignWebJsPath(target, jsRoot) {
  if (!path.relative(jsRoot, target).startsWith('..')) {
    return false; // 自コアの配信根の内側は層の規則（LAYER_RULES）が見る。
  }
  return target.includes(WEB_JS_FRAGMENT);
}

/**
 * G-1: 層の依存方向。内側の層が外側を import している箇所を列挙する。
 *
 * 併せて**他コアの配信根への相対 import**も列挙する（ISSUE-479 Wave2b・JS レビュー 🟡-2）。
 *   `import { t } from '../../replay/web/js/usecase/timing.js';` は G-3（越境 URL）の担当外である
 *   ——G-3 は相対指定子を明示的に除いている（CROSS_CORE_MODULE_URL_RE の左端の否定後読み）。
 *   相対指定子を解決できるのは本関数だけなので、ここで見なければ**どの検査からも漏れる**。
 *   以前は「解決先が自コアの層でなければ無条件 skip」で、まさにその穴が空いていた。
 *   自コアの配信根の外（共有ツール等）への相対 import は従来どおり静か＝加法である。
 *
 * @returns {string[]} `<相対パス>: <層> → <層>（<指定子>）` /
 *   `<相対パス>: <層> → 他コアの配信根（<指定子>）`
 */
export function layerDirectionOffenders(sources, jsRoot, repoRoot) {
  const offenders = [];
  for (const [absPath, source] of sources) {
    const from = layerOf(absPath, jsRoot);
    if (from === null) {
      continue;
    }
    for (const spec of importSpecifiers(source)) {
      if (!spec.startsWith('.')) {
        continue; // 相対以外（bare / 絶対 URL）は層解決の対象外。URL は G-3 が見る。
      }
      const target = path.resolve(path.dirname(absPath), spec);
      const to = layerOf(target, jsRoot);
      if (to === null) {
        if (isForeignWebJsPath(target, jsRoot)) {
          offenders.push(
            `${path.relative(repoRoot, absPath)}: ${from} → 他コアの配信根（${spec}）`,
          );
        }
        continue;
      }
      if (LAYER_RULES[from].includes(to)) {
        continue;
      }
      offenders.push(
        `${path.relative(repoRoot, absPath)}: ${from} → ${to}（${spec}）`,
      );
    }
  }
  return offenders.sort();
}

/**
 * G-2: 指定した名前（他サブシステム）への参照。
 * @returns {string[]} `<相対パス>:<行>: <行内容>`
 */
export function foreignReferenceOffenders(sources, names, repoRoot) {
  const offenders = [];
  for (const [absPath, source] of sources) {
    const lines = stripComments(source).split('\n');
    lines.forEach((line, i) => {
      if (names.some((name) => line.includes(name))) {
        offenders.push(`${path.relative(repoRoot, absPath)}:${i + 1}: ${line.trim()}`);
      }
    });
  }
  return offenders.sort();
}

/** URL の第 1 セグメント（`/sim/report-js/chart.js` → `'sim'`）。core 名でなければ null。 */
export function coreSegmentOf(url) {
  const head = url.split('/')[1] ?? '';
  return CORE_SEGMENTS.includes(head) ? head : null;
}

/**
 * G-3: **他** core のモジュール URL は、その core の `public/` 配下だけを名指してよい。
 *
 * 識別子渡しの動的 import（`const P = '/live/js/usecase/x.js'; await import(P);`）は import 文の
 * 走査に現れないため、**文字列そのもの**を見るのが唯一の検出手段である。
 *
 * @param {Map<string,string>} sources 走査対象（絶対パス → 本文）。
 * @param {string} repoRoot 報告用の相対化根。
 * @param {?string} [ownCore] 走査している core 自身の配信名（`'live' | 'replay' | 'sim' | 'dashboard'`）。
 *   与えると**自 core を名指す URL**を対象外にする。自 core の名指しは越境ではない——配信根が
 *   同じで、public/ を経由する必要が無い（実測: sim の合成根は `/sim/report-js/chart.js` を
 *   配信 URL で読み込む）。**省略時の挙動は従来どおり**（core を問わず全て対象＝加法）。
 * @returns {string[]} `<相対パス>:<行>: <URL>`
 */
export function crossCoreModuleUrlOffenders(sources, repoRoot, ownCore = null) {
  const offenders = [];
  for (const [absPath, source] of sources) {
    const lines = stripComments(source).split('\n');
    lines.forEach((line, i) => {
      for (const m of line.matchAll(CROSS_CORE_MODULE_URL_RE)) {
        const url = m[0];
        if (PUBLIC_URL_RE.test(url)) {
          continue;
        }
        if (ownCore !== null && coreSegmentOf(url) === ownCore) {
          continue;
        }
        offenders.push(`${path.relative(repoRoot, absPath)}:${i + 1}: ${url}`);
      }
    });
  }
  return offenders.sort();
}
