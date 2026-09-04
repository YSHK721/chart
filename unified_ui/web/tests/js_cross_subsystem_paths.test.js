// js_cross_subsystem_paths.test.js — 統合層が各 core を名指す形を固定する（ISSUE-479 Wave2 J-4a）。
//
// 統合層は各 core を束ねる**外側**であり、core のモジュールを URL で読み込むのが仕事である。
// ただし名指してよいのは各 core の**公開面**（`/<core>/js/public/*.js`）だけで、内部階層
// （`adapter/front/...`・`usecase/...`）を名指すと、core の内部配置が変わった瞬間に統合層が
// 無言で 404 になる。これらは識別子渡しの動的 import で読まれるため import 走査には
// **原理的に現れない**——文字列そのものを見るのが唯一の検出手段である。
//
// 対象外（本ゲートが見ないもの）:
//   - API パス（`/live/candles` 等）と JSON 資源（`/live/data/*.json`）は公開契約であり階層の
//     名指しではない。モジュール URL（`.js` で終わる絶対パス）だけを見る。
//
// 残件台帳（RATCHET）— **現在 0 件**:
//   J-4b で replay core の 4 経路を、J-5 段階 3 で sim / dashboard の合成根 2 経路を、
//   J-5 段階 5 で live core の 2 経路（合成根 `bootstrap` と `dataset_ref_query`）を、それぞれ
//   各 core の公開面へ集約した。sim / dashboard の入口 URL は `mode_table.js` の
//   displayLayerPath が持ち、統合層は表を走査して読む（unified_root には定数が残らない）。
//   **増やさない／直したら台帳から消す**ことを 2 本目の検定が機械的に強制する。
//   台帳は「今の違反を許す」ためのものであって、増やしてよいという意味ではない。

import { describe, test, expect } from 'vitest';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

import { collectSources, crossCoreModuleUrlOffenders } from '../../../tools/js_layer_guard.mjs';

const WEB = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const JS_ROOT = path.join(WEB, 'js');
const REPO_ROOT = path.resolve(WEB, '..', '..');

/** 行番号は編集で動くので、台帳の突合は「ファイル: URL」で行う。 */
function withoutLineNumber(offender) {
  const [file, , ...rest] = offender.split(':');
  return `${file}: ${rest.join(':').trim()}`;
}

//: 未解消の越境。**空である**（J-5 段階 5 で最後の 2 本を解消した）。
//   以降ここへ行を足すのは「新しい内部階層の名指しを増やす」ことに等しく、増やすなら
//   なぜ公開面に置けないのかを行ごとに書く義務がある。
const KNOWN_REMAINING = [];

describe('統合層 → core のモジュール URL', () => {
  const sources = collectSources([JS_ROOT]);
  const offenders = crossCoreModuleUrlOffenders(sources, REPO_ROOT).map(withoutLineNumber);

  test('走査が空振りしていない（統合層の JS を実際に読んでいる）', () => {
    expect(sources.size).toBeGreaterThan(5);
  });

  test('台帳にない越境が無い（新しい内部階層の名指しを増やさない）', () => {
    const unexpected = offenders.filter((o) => !KNOWN_REMAINING.includes(o));
    expect(unexpected).toEqual([]);
  });

  test('台帳の残件は実在する（解消済みの行を残さない＝ratchet が緩まない）', () => {
    const stale = KNOWN_REMAINING.filter((k) => !offenders.includes(k));
    expect(stale).toEqual([]);
  });

  test('公開面（public/）の名指しは越境として数えない', () => {
    const publicUrls = [...sources.values()]
      .join('\n')
      .match(/\/(?:live|replay|sim|dashboard)\/js\/public\/[^'"]+\.js/g) ?? [];
    for (const url of publicUrls) {
      expect(offenders.some((o) => o.endsWith(url))).toBe(false);
    }
  });
});
