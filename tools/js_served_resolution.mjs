// js_served_resolution.mjs — 配信規則（URL → 実ファイル）の JS 側の**共有実装**（ISSUE-504）。
//
// なぜ共有モジュールなのか:
//   規則の参照実装は Python 側の `StaticFileServer.resolve`（自根で解決 → miss なら共有根へ
//   同一 rel でフォールバック）である。JS 側はブラウザの解決を検定するためにその写しを
//   持たざるを得ないが、写しが増えるほど片方だけ直された日に片方の core だけ検査が緩む。
//   新しい検定はここを import し、「どの根を見るか」と「何を assert するか」だけを持つ。
//
// **唯一の実装ではない（現状の正確な記述）**:
//   `indigators/indicator_ui/web/tests/served_import_resolution.test.js` が同じ規則の写しを
//   `servedExists` として持ち続けている（走査根も 2 根の列挙のまま）。本モジュールへの集約は
//   別裁定であり、それまでは JS 側に規則の実装が 2 枚ある。ここへ「唯一」と書くと、実際には
//   残っている写しが宣言によって見えなくなる——是正しようとしている欠陥（同じ事実の複数所有）
//   を、文章の側で覆い隠すことになる（是正レビュー Y-2）。
//
// なぜ走査と指定子抽出を自前で書かないか:
//   `js_layer_guard.mjs` の `collectSources` / `importSpecifiers` が既にそれを持つ。書き写すと
//   同じ規則の所有者が 2 人になり、しかも読取が 2 度発行される（計算量ゲートが壊れる）。
//
// なぜ走査根を列挙しないか:
//   走査根の一覧は配信トポロジ台帳 `common/core_web_topology.json` が単独で持つ。ここで
//   列挙すると、core を 1 つ足した日にその core だけ無音で無検査になる——列挙は新規を
//   永久に検出しない、という `js_layer_guard.mjs` 冒頭の規律と同型である。
//
// 計算量の規律（CLAUDE.md 絶対命令 §4.1）:
//   1 巡の走査で **1 ファイルにつき読取 1 回**・**1 パスにつき存在確認 1 回**。検査項目
//   （解決不能の列挙 / フォールバック依存の列挙 / 到達性）は 1 つの走査結果を読むだけで、
//   項目を増やしても I/O は増えない。読取と存在確認は注入可能（Test Spy が数えられる）。

import { existsSync } from 'node:fs';
import path from 'node:path';

import {
  DEFAULT_IO as GUARD_IO,
  collectSources,
  importSpecifiers,
} from './js_layer_guard.mjs';

/**
 * 実 I/O の既定（すべて注入可能・計算量テストはここを Test Spy へ差し替える）。
 *
 * 走査 3 種は層ガードの既定をそのまま**展開して再利用**する。書き写すと読取口の定義が
 * 2 か所になり、片方だけ直された日に計算量ゲートが数える先と実際に読む先がずれる
 * （是正レビュー Y-6）。本モジュールが足すのは存在確認 1 フィールドだけである。
 */
export const DEFAULT_IO = Object.freeze({
  ...GUARD_IO,
  exists: (p) => existsSync(p),
});

/** 配信トポロジ台帳の位置（Python 側 common/core_web_topology.py と同じ実体を読む）。 */
export const LEDGER_RELATIVE_PATH = path.join('common', 'core_web_topology.json');

/**
 * 台帳から走査根の並びを導く（core ＋ 統合層）。
 *
 * @returns {{name: string, web: string, fallbacks: string[]}[]}
 */
export function loadServedRoots(repoRoot, io = DEFAULT_IO) {
  const data = JSON.parse(io.readFile(path.join(repoRoot, LEDGER_RELATIVE_PATH)));
  const rows = { ...(data.cores ?? {}), ...(data.integration ?? {}) };
  const names = Object.keys(rows);
  if (names.length === 0) {
    // 空を黙って返すと、走査が 0 根で「違反 0」になり、検定が在るのに何も守らない。
    throw new Error(`配信トポロジ台帳に配信面が 1 件も無い: ${LEDGER_RELATIVE_PATH}`);
  }
  return names.map((name) => ({
    name,
    web: path.join(repoRoot, rows[name].web),
    fallbacks: (rows[name].fallbacks ?? []).map((rel) => path.join(repoRoot, rel)),
  }));
}

/**
 * 存在確認の発行点（同じパスは 1 回しか問い合わせない）。
 * 検査項目ごとに作り直すと発行が項目数に比例して増える——それを禁じるのが本関数の役目。
 */
export function createExistence(io = DEFAULT_IO) {
  const known = new Map();
  return (target) => {
    if (!known.has(target)) {
      known.set(target, io.exists(target));
    }
    return known.get(target);
  };
}

/** フォールバック根における同一 URL の実ファイル位置（参照実装の同一 rel フォールバック）。 */
export function fallbackPathOf(fallbackWeb, jsRoot, target) {
  return path.join(fallbackWeb, 'js', path.relative(jsRoot, target));
}

/**
 * 配信規則でその解決先が **どの実ファイルから配られるか**（参照実装 resolve の写像）。
 *
 * 返すのは判定だけでなく配信元そのものである。判定（どの段で当たったか）と配信元（どの根の
 * 実体か）を別々に導くと、落ち先が 2 つ以上ある行で「2 番目の根で当たったのに 1 番目の根の
 * ファイルを配信元として記録する」という食い違いが起きる。参照実装
 * （static_file_server.py の resolve）も真偽ではなく解決先そのものを返す。
 *
 * @returns {{by: 'self'|'fallback'|null, servedFrom: ?string}} 当たった段と配信元
 *   （どちらの根でも見つからなければ `{by: null, servedFrom: null}`＝ブラウザで 404）。
 */
export function servedSourceOf(target, jsRoot, fallbacks, exists) {
  if (exists(target)) {
    return { by: 'self', servedFrom: target };
  }
  for (const fallbackWeb of fallbacks) {
    const candidate = fallbackPathOf(fallbackWeb, jsRoot, target);
    if (exists(candidate)) {
      return { by: 'fallback', servedFrom: candidate };
    }
  }
  return { by: null, servedFrom: null };
}

/**
 * その配信面の入口（index.html のモジュールと公開面）。到達性の起点。
 *
 * 公開面は**既に集めた走査結果から引く**（是正レビュー Y-7）。ここでディレクトリを
 * 列挙し直すと、`js/public` だけ 2 回列挙され、走査コストの主体である列挙の発行が
 * 対象より多くなる（実測: 公開面を持つ 4 根で重複 4 件）。
 *
 * @param {Map<string,string>} collected その配信面の走査結果（絶対パス → 本文）。
 */
function entryFilesOf(root, io, exists, collected) {
  const entries = [];
  const indexPath = path.join(root.web, 'index.html');
  if (exists(indexPath)) {
    const html = io.readFile(indexPath);
    for (const spec of importSpecifiers(html)) {
      if (spec.startsWith('.')) {
        entries.push(path.resolve(root.web, spec));
      }
    }
    for (const m of html.matchAll(/<script\b[^>]*>/g)) {
      const tag = m[0];
      const src = /\bsrc\s*=\s*"([^"]+)"/.exec(tag);
      if (src && /type\s*=\s*"module"/.test(tag) && src[1].endsWith('.js')) {
        entries.push(path.join(root.web, src[1].replace(/^\//, '')));
      }
    }
  }
  // 公開面（`js/public/*.js`）は他 core・統合層がここから入ると宣言している面である。
  //   index.html を持たない配信面（sim の Phase 1 ページ等）はここだけが入口になる。
  const publicDir = path.join(root.web, 'js', 'public');
  for (const file of collected.keys()) {
    if (path.dirname(file) === publicDir) {
      entries.push(file);
    }
  }
  return entries;
}

/**
 * 走査根の全 JS を 1 巡し、相対 import の辺と解決結果を返す。
 *
 * @returns {{roots, edges, sources, entries, exists}} 1 巡ぶんの走査結果。検査項目は
 *   この結果を読むだけで、項目を増やしても I/O は増えない。
 */
export function scanServedRoots(roots, io = DEFAULT_IO) {
  const exists = createExistence(io);
  const edges = [];
  const sources = new Map();
  const entries = new Map();
  for (const root of roots) {
    const jsRoot = path.join(root.web, 'js');
    if (!exists(jsRoot)) {
      // 台帳に在って実配置に無い根は、名前を出して落とす。fs のスタックトレースのままだと
      //   「どの core の話か」が読み取れず、走査が静かに 0 件になる形と見分けがつかない。
      throw new Error(
        `配信根が実在しない: ${root.name} → ${jsRoot}`
        + `（台帳 ${LEDGER_RELATIVE_PATH} と実配置が食い違う）`,
      );
    }
    const collected = collectSources([jsRoot], io);
    for (const [file, source] of collected) {
      sources.set(file, source);
      for (const spec of importSpecifiers(source)) {
        if (!spec.startsWith('.')) {
          continue; // bare / 絶対 URL は配信規則の解決対象ではない。
        }
        const target = path.resolve(path.dirname(file), spec);
        const served = servedSourceOf(target, jsRoot, root.fallbacks, exists);
        // 辺が持つのは検査側が実際に読む項目だけにする（走査中の根や解決前のパスは
        //   `at` と `spec` から一意に導けるので、持たせると同じ事実の写しが増える）。
        edges.push({
          core: root.name,
          at: file,
          spec,
          resolvedBy: served.by,
          servedFrom: served.servedFrom,
        });
      }
    }
    entries.set(root.name, entryFilesOf(root, io, exists, collected));
  }
  return { roots, edges, sources, entries, exists };
}

/** どの根でも解決できない辺（＝ブラウザで 404 になる import）。 */
export function unresolvedEdges(scan) {
  return scan.edges.filter((edge) => edge.resolvedBy === null);
}

/** 自根では解決できず、フォールバック根に救われている辺。 */
export function fallbackEdges(scan) {
  return scan.edges.filter((edge) => edge.resolvedBy === 'fallback');
}

/**
 * 入口から実際に辿り着くファイルの集合（配信規則で解決しながらの推移閉包）。
 * フォールバックで解決した辺は、その解決先（共有根の実体）へ進む。
 */
export function reachableFiles(scan) {
  const outgoing = new Map();
  for (const edge of scan.edges) {
    const list = outgoing.get(edge.at) ?? [];
    list.push(edge);
    outgoing.set(edge.at, list);
  }
  const reached = new Set();
  const queue = [];
  for (const files of scan.entries.values()) {
    queue.push(...files);
  }
  while (queue.length > 0) {
    const file = queue.pop();
    if (reached.has(file)) {
      continue;
    }
    reached.add(file);
    for (const edge of outgoing.get(file) ?? []) {
      if (edge.servedFrom !== null) {
        queue.push(edge.servedFrom);
      }
    }
  }
  return reached;
}

/** 報告用の 1 行（行番号は編集で動くので、ファイルと指定子だけで台帳を作る）。 */
export function edgeLabel(edge, repoRoot) {
  return `${path.relative(repoRoot, edge.at)} → ${edge.spec}`;
}
