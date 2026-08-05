// MP actor が成長窓の期間始端規則を **複製していない**ことを検定する（ISSUE-267）。
//
// かつて market_profile_actor.js の `_sessionFrom()` は
//   GrowthWindow.forCurrent('normal', tf, now).from
// と同一規則を独自に算出していた。複製の理由として「TF_BAR_SEC の二重宣言で bundle が壊れる」
// と述べられていたが**その理由は誤り**で（宣言は tf_meta.js の 1 箇所のみ）、実際の阻害は
// A方式バンドル（build.mjs のフラット連結）が growth_window.js を取り込めないことだった。
// A方式の廃止（ISSUE-266）で制約が消えたため複製をやめ、GrowthWindow への委譲へ置換した。
//
// 本検定は「複製が戻ってこない」ことを構造的に固定する。式を actor へ書き戻したら落ちる。

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

import { GrowthWindow } from '../js/domain/growth_window.js';
import { TF_CODES } from '../js/domain/tf_meta.js';

const WEB = dirname(dirname(fileURLToPath(import.meta.url)));
const ACTOR = join(WEB, 'js', 'adapter', 'front', 'market_profile_actor.js');

test('_sessionFrom は GrowthWindow へ委譲する（規則を写さない）', () => {
  const src = readFileSync(ACTOR, 'utf8');
  const body = src.slice(src.indexOf('_sessionFrom()'), src.indexOf('_sessionFrom()') + 800);
  assert.match(body, /GrowthWindow\.forCurrent\('normal',/,
    '_sessionFrom が GrowthWindow.forCurrent へ委譲していません');
});

test('actor が期間始端の式を再実装していない（複製の再発防止）', () => {
  const src = readFileSync(ACTOR, 'utf8');
  const offenders = [];
  src.split('\n').forEach((line, i) => {
    const code = line.trim();
    if (code.startsWith('//')) return;
    // 「min(セッション始端, 周期始端)」と「floor(now / barSec) * barSec」は GrowthWindow の規則。
    if (/Math\.min\(\s*sessionStart/.test(code)) offenders.push(`${i + 1}: ${code.slice(0, 70)}`);
    if (/Math\.floor\([^)]*\/\s*\(?TF_BAR_SEC/.test(code)) offenders.push(`${i + 1}: ${code.slice(0, 70)}`);
  });
  assert.deepEqual(offenders, [],
    `期間始端の式が actor に書き戻されています:\n  ${offenders.join('\n  ')}\n`
    + '  GrowthWindow.forCurrent への委譲を使ってください。');
});

test('GrowthWindow が全時間足で期間始端を返す（委譲先の健全性）', () => {
  const now = Date.UTC(2026, 7, 5, 11, 0, 0) / 1000;
  for (const tf of TF_CODES) {
    const from = GrowthWindow.forCurrent('normal', tf, now).from;
    assert.equal(typeof from, 'number', `tf=${tf} の from が数値`);
    assert.ok(from <= now, `tf=${tf} の from は現在以下`);
  }
});
