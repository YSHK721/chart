// py_parity_golden.test.js — Python 実装との生成同期検定（ISSUE-087 🔴-3）。
//
// 権威は Python（marketdata.session_day / _value_area / marketdata.tf_meta）。fixture
// （fixtures/py_parity_golden.json＝tools/gen_js_parity_golden.py が生成）に対し、JS 側の
// 二重実装（domain/session_day.js・dwell_accumulator.valueArea・domain/tf_meta.js）の一致を
// 網羅検定する（旧: ハードコード 2 値の弱同期を置換）。規則変更時は fixture を再生成する。

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

import {
  sessionDayStart, nextSessionDayStart, sessionDateLabel, sessionBarTime,
} from '../js/domain/session_day.js';
import { TF_BAR_SEC, TF_CODES, FLOOR_TFS, CALENDAR_TFS } from '../js/domain/tf_meta.js';
import { valueArea } from '../js/domain/market_profile_dwell_accumulator.js';
import { mpSupportsTf, MP_ZP_SESSIONS_BLOCKED_TFS } from '../js/domain/mp_source_capability.js';

const golden = JSON.parse(
  readFileSync(join(dirname(fileURLToPath(import.meta.url)), 'fixtures', 'py_parity_golden.json'), 'utf8'),
);

test('session_day.js は Python 実装と全境界ケースで一致する（DST 切替・週/月/年跨ぎ・160点）', () => {
  assert.ok(golden.session_day.length >= 100, 'fixture が境界網羅されている');
  for (const c of golden.session_day) {
    assert.equal(sessionDayStart(c.t), c.dayStart, `sessionDayStart(${c.t})`);
    assert.equal(nextSessionDayStart(c.dayStart), c.nextDayStart, `nextSessionDayStart(${c.dayStart})`);
    assert.equal(sessionDateLabel(c.t), c.label, `sessionDateLabel(${c.t})`);
    assert.equal(sessionBarTime(c.t), c.barTime, `sessionBarTime(${c.t})`);
  }
});

test('tf_meta.js の TF_BAR_SEC は Python marketdata.tf_meta と一致する', () => {
  assert.deepEqual({ ...TF_BAR_SEC }, golden.tf_bar_sec);
});

// ISSUE-254: 値（barSec）だけでなく**派生属性**（floorable / calendar）まで一致させる。
//   派生属性は検定の対象外だったため JS 側の手書き配列が静かにずれ、ライブの更新粒度が
//   時間足で割れた（ISSUE-253）。判断に使う属性を残らず固定する。
test('tf_meta.js の派生属性（floorable/calendar）と順序は Python 台帳と一致する', () => {
  assert.ok(Array.isArray(golden.tf_ledger) && golden.tf_ledger.length > 0, 'fixture に台帳がある');
  assert.deepEqual([...TF_CODES], golden.tf_ledger.map((d) => d.code), '時間足コードと順序');
  assert.deepEqual(
    [...FLOOR_TFS],
    golden.tf_ledger.filter((d) => d.floorable).map((d) => d.code),
    'floorable（ここがずれると更新経路が時間足で割れる）',
  );
  assert.deepEqual(
    [...CALENDAR_TFS],
    golden.tf_ledger.filter((d) => d.calendar).map((d) => d.code),
    'calendar（セッション日集計の対象）',
  );
});

// 生成物が手で編集されていない／再生成漏れが無いことの検出。tf_meta.js は生成台帳からの
//   導出しか行わないため、導出結果が fixture と一致すれば生成物も一致している。
test('tf_ledger_generated.js は fixture と同一の台帳（手編集・再生成漏れの検出）', () => {
  const derived = TF_CODES.map((code) => ({
    code,
    barSec: TF_BAR_SEC[code],
    floorable: FLOOR_TFS.includes(code),
    calendar: CALENDAR_TFS.includes(code),
  }));
  assert.deepEqual(derived, golden.tf_ledger);
});

test('valueArea は Python _value_area と一致する（整数 TPO・float z の両系）', () => {
  for (const c of golden.value_area) {
    const [lo, hi] = valueArea(c.centers, c.tpo, c.pct);
    assert.deepEqual([lo, hi], c.expected, `VA(${JSON.stringify(c.tpo)})`);
  }
});

// ISSUE-261: zp 対応 tf は台帳から導出できない「能力宣言」で、Python（唯一源）と JS の両方に
//   手書きで存在する。同期手段が無いと、ずれた瞬間にサーバは 400・フロントは選択可能のまま
//   ＝無言の機能不全になる（ISSUE-253 と同型の失敗）。写しは残すが、ずれたら落ちる状態にする。
test('mp_source_capability.js の zp 対応 tf は Python _ZP_TF_ALLOWED と一致する', () => {
  assert.ok(Array.isArray(golden.zp_supported_tfs) && golden.zp_supported_tfs.length > 0,
    'fixture に zp 対応 tf がある');
  for (const tf of golden.zp_supported_tfs) {
    assert.equal(mpSupportsTf('zp', tf), true, `zp は ${tf} に対応する`);
  }
  // 台帳の全 tf のうち、Python が非対応と宣言したものは JS でも非対応であること。
  const supported = new Set(golden.zp_supported_tfs);
  for (const { code } of golden.tf_ledger) {
    if (!supported.has(code)) {
      assert.equal(mpSupportsTf('zp', code), false, `zp は ${code} に非対応`);
    }
  }
});

test('日別モードの zp 非選択 tf は「対応 tf の補集合」と一致する', () => {
  const supported = new Set(golden.zp_supported_tfs);
  const expected = golden.tf_ledger.map((d) => d.code).filter((c) => !supported.has(c));
  assert.deepEqual([...MP_ZP_SESSIONS_BLOCKED_TFS].sort(), expected.sort());
});
