// 全 core の配信根で相対 import が **実際に解決できる** ことの固定（ISSUE-504）。
//
// なぜ統合層が持つか:
//   不変条件の主語は「全 core を跨いだ配信面」であり、その主語に一番近いのは統合層である
//   （`js_package_direction.test.js` 冒頭の所有者論と同じ規律）。消費者 core 側へ置くと、
//   写しが 2 枚になるか、片方のスイートを消した日に静かに未実行になる。
//
// なぜ走査根を列挙しないか:
//   走査根は配信トポロジ台帳 `common/core_web_topology.json` から導く。是正前は
//   `indigators/indicator_ui/web/tests/served_import_resolution.test.js` が 2 根を配列で
//   列挙しており、**dashboard / sim / unified は無音で無検査**だった（列挙は新規を永久に
//   検出しない）。既存のその検定は無改変で残す（重複は次段の別裁定）。
//
// 2 段の不変条件（参照実装 `simulator/replay_ui/framework/static_file_server.py` の resolve）:
//   1. どちらの根でも解決できない相対 import が 0（＝ブラウザで 404 になる import が無い）。
//   2. 自根では解決できずフォールバック根に救われている辺の集合が、凍結台帳と**完全一致**。
//      増えても減っても Red にする——台帳は「今の残りを許す」ためのものであって、増やして
//      よいという意味ではない。到達する（実行グラフに乗る）辺は台帳に置かず、共有実体への
//      symlink を自根へ足して解消する（加法。ファイル削除・import 削除は別裁定）。
//
// 計算量（CLAUDE.md 絶対命令 §4.1）: 1 巡の走査で 1 ファイル 1 読取・1 パス 1 存在確認。
//   検査項目を増やしても I/O は増えない。数えるのは時間ではなく回数である。

import { describe, test, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

import {
  DEFAULT_IO,
  edgeLabel,
  fallbackEdges,
  loadServedRoots,
  reachableFiles,
  scanServedRoots,
  unresolvedEdges,
} from '../../../tools/js_served_resolution.mjs';

const TESTS_DIR = path.dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = path.resolve(TESTS_DIR, '..', '..', '..');
const LEDGER_PATH = path.join(TESTS_DIR, 'served_import_fallback_ledger.json');

/** フォールバック依存の凍結台帳（実測で書く。憶測で行を足さない）。 */
const FROZEN = JSON.parse(readFileSync(LEDGER_PATH, 'utf8'));

const ROOTS = loadServedRoots(REPO_ROOT);
const SCAN = scanServedRoots(ROOTS);

/** 発行回数を数える I/O（実体は既定 I/O・数えるのは回数であって時間ではない）。 */
function countingIo(inner = DEFAULT_IO) {
  const reads = [];
  const dirReads = [];
  const existsCalls = [];
  return {
    reads,
    dirReads,
    existsCalls,
    io: {
      readFile: (p) => { reads.push(p); return inner.readFile(p); },
      // ディレクトリ列挙は走査コストの主体である。数えないとゲートを素通りする
      //   （是正レビュー Y-7）。
      readDir: (p) => { dirReads.push(p); return inner.readDir(p); },
      statOf: (p) => inner.statOf(p),
      exists: (p) => { existsCalls.push(p); return inner.exists(p); },
    },
  };
}

/** 発行 − 相異なる対象 = 0（同じ相手を二度問い合わせていない）。 */
function duplicates(counting) {
  return [
    counting.reads.length - new Set(counting.reads).size,
    counting.dirReads.length - new Set(counting.dirReads).size,
    counting.existsCalls.length - new Set(counting.existsCalls).size,
  ];
}

/** 合成ツリー上の I/O（実 fs を触らずに検出器そのものを検定する）。 */
function memoryIo(entries) {
  const files = new Map(Object.entries(entries));
  const dirs = new Set();
  for (const file of files.keys()) {
    let dir = path.dirname(file);
    while (dir !== path.dirname(dir)) {
      dirs.add(dir);
      dir = path.dirname(dir);
    }
  }
  return {
    readFile: (p) => {
      if (!files.has(p)) throw new Error(`合成ツリーに無いファイル: ${p}`);
      return files.get(p);
    },
    readDir: (p) => {
      const names = new Set();
      for (const entry of [...files.keys(), ...dirs]) {
        if (path.dirname(entry) === p) names.add(path.basename(entry));
      }
      return [...names];
    },
    statOf: (p) => ({ isDirectory: () => dirs.has(p) && !files.has(p) }),
    exists: (p) => files.has(p) || dirs.has(p),
  };
}

/** 合成ツリーの台帳＋走査根（core 1 つ＋共有のフォールバック根）。 */
function syntheticRoots(repoRoot) {
  return [{
    name: 'core_a',
    web: path.join(repoRoot, 'core_a', 'web'),
    fallbacks: [path.join(repoRoot, 'shared', 'web')],
  }];
}

describe('配信規則で全 core の相対 import が解決できる', () => {
  test('走査根_台帳の全配信面_どの根でも解決できない import が 0 件', () => {
    const offenders = unresolvedEdges(SCAN).map((edge) => edgeLabel(edge, REPO_ROOT));

    expect(offenders, '配信規則で解決できない import があります'
      + '（node は通るがブラウザは 404 になる）:\n  ' + offenders.join('\n  ')).toEqual([]);
  });

  test('走査根_台帳の全配信面_各根から辺が 1 本以上出ている', () => {
    // 空振り防止: 根の綴り違い・走査の早期打ち切りで 0 件になっても「違反 0」で緑になる。
    const perRoot = Object.fromEntries(
      ROOTS.map((root) => [root.name, SCAN.edges.filter((e) => e.core === root.name).length]),
    );

    expect(ROOTS.length).toBeGreaterThan(0);
    for (const [name, count] of Object.entries(perRoot)) {
      expect(count, `${name} の配信根から相対 import が 1 本も出ていない`).toBeGreaterThan(0);
    }
  });

  test('走査根_台帳に在って実配置に無い根_名前付きで報告する', () => {
    const roots = [{ name: 'ghost', web: path.join(REPO_ROOT, '__no_such_core__', 'web'),
      fallbacks: [] }];

    expect(() => scanServedRoots(roots)).toThrowError(/ghost/);
  });
});

describe('フォールバック依存の凍結台帳', () => {
  test('フォールバック辺_実測集合_凍結台帳と完全一致する', () => {
    const measured = fallbackEdges(SCAN).map((edge) => edgeLabel(edge, REPO_ROOT)).sort();
    const frozen = FROZEN.map((row) => row.edge).sort();

    expect(measured, '自根で解決できない辺の集合が台帳と食い違います'
      + '（増えたら symlink を足す・解消したら台帳から消す）').toEqual(frozen);
  });

  test('フォールバック辺_実行グラフに到達する辺_台帳に 1 件も無い', () => {
    // 到達する脆さは凍結しない（共有実体への symlink を自根へ足して加法で解消する）。
    const reached = reachableFiles(SCAN);
    const reachable = fallbackEdges(SCAN)
      .filter((edge) => reached.has(edge.at))
      .map((edge) => edgeLabel(edge, REPO_ROOT));

    expect(reachable, '実行グラフに乗るフォールバック依存が残っています'
      + '（自根に symlink を張って解消してください）:\n  ' + reachable.join('\n  '))
      .toEqual([]);
    expect(FROZEN.filter((row) => row.reachable === true)).toEqual([]);
  });

  test('凍結台帳_全行_到達しないことが記録されている', () => {
    expect(FROZEN.length).toBeGreaterThanOrEqual(0);
    for (const row of FROZEN) {
      expect(Object.keys(row).sort()).toEqual(['edge', 'reachable']);
      expect(row.reachable).toBe(false);
    }
  });
});

describe('検出器の自己検定（合成ツリーで 3 形を逐語再現）', () => {
  const REPO = path.join(path.sep, 'synthetic');
  const CORE_JS = path.join(REPO, 'core_a', 'web', 'js');
  const SHARED_JS = path.join(REPO, 'shared', 'web', 'js');

  test('解決先がどこにも無い_offender 1 件', () => {
    const io = memoryIo({
      [path.join(CORE_JS, 'a.js')]: "import x from './missing.js';\n",
    });

    const scan = scanServedRoots(syntheticRoots(REPO), io);

    expect(unresolvedEdges(scan).map((e) => e.spec)).toEqual(['./missing.js']);
    expect(fallbackEdges(scan)).toEqual([]);
  });

  test('自根に無くフォールバックに在る_offender 0 件かつ台帳候補 1 件', () => {
    const io = memoryIo({
      [path.join(CORE_JS, 'a.js')]: "import x from './shared_only.js';\n",
      [path.join(SHARED_JS, 'shared_only.js')]: 'export default 1;\n',
    });

    const scan = scanServedRoots(syntheticRoots(REPO), io);

    expect(unresolvedEdges(scan)).toEqual([]);
    expect(fallbackEdges(scan).map((e) => e.spec)).toEqual(['./shared_only.js']);
  });

  test('落ち先が 2 つで 2 番目に在る_配信元は当たった根になる', () => {
    // 判定（どの段で当たったか）と配信元（どの根の実体か）を別々に導くと、ここで食い違う。
    //   台帳が 1 core に落ち先を 2 つ与えた日に、到達性が実在しないファイルを辿り始める。
    const SECOND_JS = path.join(REPO, 'shared_2', 'web', 'js');
    const io = memoryIo({
      [path.join(CORE_JS, 'a.js')]: "import x from './second_only.js';\n",
      [path.join(SECOND_JS, 'second_only.js')]: 'export default 1;\n',
    });
    const roots = [{
      name: 'core_a',
      web: path.join(REPO, 'core_a', 'web'),
      fallbacks: [path.join(REPO, 'shared', 'web'), path.join(REPO, 'shared_2', 'web')],
    }];

    const scan = scanServedRoots(roots, io);

    expect(unresolvedEdges(scan)).toEqual([]);
    expect(fallbackEdges(scan).map((e) => e.servedFrom))
      .toEqual([path.join(SECOND_JS, 'second_only.js')]);
  });

  test('自根に在る_何も出ない', () => {
    const io = memoryIo({
      [path.join(CORE_JS, 'a.js')]: "import x from './there.js';\n",
      [path.join(CORE_JS, 'there.js')]: 'export default 1;\n',
    });

    const scan = scanServedRoots(syntheticRoots(REPO), io);

    expect(unresolvedEdges(scan)).toEqual([]);
    expect(fallbackEdges(scan)).toEqual([]);
  });
});

describe('計算量（無駄の不在）', () => {
  test('1 巡の走査_実配信面_1 ファイル 1 読取かつ 1 パス 1 存在確認', () => {
    const counting = countingIo();

    const scan = scanServedRoots(loadServedRoots(REPO_ROOT, counting.io), counting.io);

    expect(scan.sources.size).toBeGreaterThan(0);
    expect(counting.dirReads.length).toBeGreaterThan(0);
    // 読取・ディレクトリ列挙・存在確認のいずれも「発行 − 相異なる対象 = 0」。
    expect(duplicates(counting)).toEqual([0, 0, 0]);
  });

  test('葉モジュール 10 本 / 20 本_いずれも発行の重複が 0（オーダーの表明）', () => {
    const extra = {};
    for (const leaves of [10, 20]) {
      const REPO = path.join(path.sep, `order_${leaves}`);
      const coreJs = path.join(REPO, 'core_a', 'web', 'js');
      const entries = { [path.join(coreJs, 'hub.js')]: 'export const hub = 1;\n' };
      for (let i = 0; i < leaves; i += 1) {
        entries[path.join(coreJs, `leaf_${i}.js`)] = "import { hub } from './hub.js';\n";
      }
      const counting = countingIo(memoryIo(entries));
      const scan = scanServedRoots(syntheticRoots(REPO), counting.io);
      expect(scan.edges.length).toBe(leaves);
      extra[leaves] = duplicates(counting);
    }

    expect(extra[10]).toEqual([0, 0, 0]);
    expect(extra[20]).toEqual([0, 0, 0]);
  });

  test('検査項目 3 件 / 6 件_発行は 1 巡ぶんのまま増えない（オーダーの表明）', () => {
    const REPO = path.join(path.sep, 'items');
    const coreJs = path.join(REPO, 'core_a', 'web', 'js');
    const entries = {
      [path.join(coreJs, 'a.js')]: "import x from './b.js';\n",
      [path.join(coreJs, 'b.js')]: 'export default 1;\n',
    };
    const issued = {};
    for (const items of [3, 6]) {
      const counting = countingIo(memoryIo(entries));
      const scan = scanServedRoots(syntheticRoots(REPO), counting.io);
      for (let i = 0; i < items; i += 1) {
        unresolvedEdges(scan);
        fallbackEdges(scan);
        reachableFiles(scan);
      }
      issued[items] = [
        counting.reads.length, counting.dirReads.length, counting.existsCalls.length,
      ];
    }

    expect(issued[6]).toEqual(issued[3]);
  });

  test('検出力_項目ごとに走査し直す変異_発行が増える', () => {
    // 上のゲートが空虚でないことの証拠。解決子（走査結果）を項目ごとに作り直すと、
    //   出力は 1 文字も変わらないまま発行だけが項目数に比例して増える。
    const REPO = path.join(path.sep, 'mutation');
    const coreJs = path.join(REPO, 'core_a', 'web', 'js');
    const entries = {
      [path.join(coreJs, 'a.js')]: "import x from './b.js';\n",
      [path.join(coreJs, 'b.js')]: 'export default 1;\n',
    };

    const shared = countingIo(memoryIo(entries));
    const scan = scanServedRoots(syntheticRoots(REPO), shared.io);
    for (let i = 0; i < 3; i += 1) {
      unresolvedEdges(scan);
    }

    const mutant = countingIo(memoryIo(entries));
    for (let i = 0; i < 3; i += 1) {
      unresolvedEdges(scanServedRoots(syntheticRoots(REPO), mutant.io));
    }

    expect(mutant.reads.length).toBeGreaterThan(shared.reads.length);
    expect(mutant.dirReads.length).toBeGreaterThan(shared.dirReads.length);
    expect(mutant.existsCalls.length).toBeGreaterThan(shared.existsCalls.length);
  });
});
