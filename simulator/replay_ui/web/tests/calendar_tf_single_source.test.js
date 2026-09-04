// 暦ラベル足（1W/1M 相当）の判定を手書きしない（ISSUE-278 #13 の再発防止）。
//
// 由来: リプレイ側が `new Set(['1W','1M'])`（replay_market_profile_actor.js）と
//   `timeframe === '1W' || timeframe === '1M'`（replay/stream.js）で暦足を手書き判定しており、
//   Python 台帳（marketdata/resample.py の TF_DESCRIPTORS）へ暦足を足しても追随しなかった。
//   追随しない側は (a) forming を取りに行って 400→null となり MP が前回描画のまま固まる、
//   (b) 足内 tick 窓の切り方が丸ごとずれる、という**エラーを出さない**壊れ方をする
//   （ISSUE-253／ISSUE-261 と同型）。判断材料は台帳の導出（tf_meta.CALENDAR_LABEL_TFS）だけにする。

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

import { CALENDAR_LABEL_TFS, isCalendarLabelTimeframe } from '../js/domain/tf_meta.js';
import { TF_LEDGER } from '../js/domain/tf_ledger_generated.js';

// 台帳から暦足の判定を持ち出しているファイル（手書きが再発しやすい場所）。
const SOURCES = [
  '../js/adapter/front/replay_market_profile_actor.js',
  '../js/replay/stream.js',
];

test('CALENDAR_LABEL_TFS は台帳からの導出（calendar かつ非 floorable）', () => {
  const expected = TF_LEDGER.filter((d) => d.calendar && !d.floorable).map((d) => d.code);
  assert.deepEqual([...CALENDAR_LABEL_TFS], expected);
  for (const tf of expected) {
    assert.equal(isCalendarLabelTimeframe(tf), true, `${tf} が暦ラベル足と判定されない`);
  }
  assert.equal(isCalendarLabelTimeframe('1h'), false);
});

test('リプレイ側に暦足コードの手書き判定が残っていない', () => {
  for (const rel of SOURCES) {
    const src = readFileSync(fileURLToPath(new URL(rel, import.meta.url)), 'utf8');
    // コメント行（説明のための引用）を除いた実コードだけを見る。
    const code = src.split('\n').filter((l) => !l.trim().startsWith('//')).join('\n');
    assert.ok(
      !/new Set\(\[\s*'1W'\s*,\s*'1M'\s*\]\)/.test(code),
      `${rel} に暦足の手書き Set が残っている`,
    );
    assert.ok(
      !/===\s*'1W'\s*\|\|.*===\s*'1M'/.test(code),
      `${rel} に暦足の手書き比較が残っている`,
    );
  }
});
