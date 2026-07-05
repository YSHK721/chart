// temporal_cursor.test.js — TemporalCursor（domain）の因果ルール検証。
//
// 設計入力: Model A 統一成長モデル Phase 0「因果性は新 domain 値 TemporalCursor（canFold(sec)=sec<=asOf）で
//   単一定義」。現状 replay.js/actor に散在する「now は必ず secs[i]・sec<=now（未来リーク禁止）」を domain へ昇格する。
//   domain 値＝自内 import のみ・副作用なし・純関数境界（SRP）。
// 構造: Arrange-Act-Assert（AAA）。DOM/ネット非依存（純ロジック）。

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { TemporalCursor } from '../js/domain/temporal_cursor.js';

test('canFold: sec < asOf は畳み込み可（過去の tick）', () => {
  const cursor = new TemporalCursor(1000);

  assert.equal(cursor.canFold(999), true);
});

test('canFold: sec === asOf は畳み込み可（境界＝now は必ず含む）', () => {
  const cursor = new TemporalCursor(1000);

  assert.equal(cursor.canFold(1000), true, '境界 sec===asOf は fold 可（sec<=asOf）');
});

test('canFold: sec > asOf は畳み込み不可（未来リーク禁止）', () => {
  const cursor = new TemporalCursor(1000);

  assert.equal(cursor.canFold(1001), false, '未来の tick は fold できない');
});

test('asOf: 構築時の as-of 秒を読み取れる', () => {
  const cursor = new TemporalCursor(1234);

  assert.equal(cursor.asOf, 1234);
});

test('canFold: asOf=null（最新＝全期間）は全 sec を畳み込み可（上限なし）', () => {
  const cursor = new TemporalCursor(null);

  assert.equal(cursor.canFold(0), true);
  assert.equal(cursor.canFold(9_999_999_999), true, 'null=最新は上限なし＝全 tick fold 可');
});

test('asOf: null は最新を表す（asOf===null）', () => {
  const cursor = new TemporalCursor(null);

  assert.equal(cursor.asOf, null);
});
