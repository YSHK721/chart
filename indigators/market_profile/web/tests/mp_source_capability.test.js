// mp_source_capability.js — MP ソース能力記述子（domain 単一情報源）の回帰検証。
//
// 目的（ISSUE-097 🟡-8/🟡-9/🔵-20・ISSUE-099 起点）: 各ファイルに散在していた
//   `src === 'zp'` 直書き述語を単一記述子へ集約する。本テストは「記述子から導出した
//   述語結果が、集約前の各定数（_MP_ZP_TF / MP_ZP_SESSIONS_BLOCKED_TFS / incremental /
//   period / poc / labels）と完全一致する」ことを固定し、挙動不変を担保する。
// 構造: Arrange-Act-Assert。

import { test } from 'node:test';
import assert from 'node:assert/strict';

import {
  mpSourceCapability,
  mpSupportsTf,
  mpTfPeriodSrc,
  MP_SELECTABLE_SOURCES,
  MP_DEFAULT_SOURCE,
  mpSourceEnumLabels,
  MP_ZP_SESSIONS_BLOCKED_TFS,
} from '../js/domain/mp_source_capability.js';

// 集約前の参照定義（catalog_entry.js / composition_root_front.js の旧リテラル）。
const OLD_ZP_TF = new Set(['15m', '30m', '1h', '4h', '1D', '1W', '1M']);
const OLD_ZP_BLOCKED = new Set(['1m', '5m']);
const ALL_PLAYER_TF = ['1m', '5m', '15m', '30m', '1h', '4h', '1D', '1W', '1M'];

test('zp 記述子: 各能力フィールドが集約前の zp 挙動と一致する', () => {
  const c = mpSourceCapability('zp');
  assert.equal(c.id, 'zp');
  assert.equal(c.label, '超過占有z(p)');
  assert.equal(c.incremental, false, 'zp は per-tick 増分不可（_isIncremental の src!==zp）');
  assert.equal(c.hasPeriodWindow, true, 'zp は当日窓 period を持つ（_periodExtra / period 表示）');
  assert.equal(c.poc, 'star', 'zp は POC*（黄星）描画');
  assert.equal(c.showLabels, true, 'zp は POC*/VAH/VAL ラベルを描く');
  assert.equal(c.tfPeriodSrc, 'zp', 'zp の tf-period 取得 src は zp');
});

test('dwell 記述子: 既定挙動（非 zp）と一致する', () => {
  const c = mpSourceCapability('dwell');
  assert.equal(c.id, 'dwell');
  assert.equal(c.label, '滞在時間(実ティック)');
  assert.equal(c.incremental, true, 'dwell は forming 増分可');
  assert.equal(c.hasPeriodWindow, false);
  assert.equal(c.supportedTfs, null, 'dwell は tf 制限なし（全 player tf）');
  assert.equal(c.blockedSessionTfs.size, 0);
  assert.equal(c.poc, 'line');
  assert.equal(c.showLabels, false);
  assert.equal(c.tfPeriodSrc, null);
});

test('未知 src（candle/m1/undefined/null）は既定記述子＝非 zp 挙動へ縮退する', () => {
  for (const s of ['candle', 'm1', undefined, null]) {
    const c = mpSourceCapability(s);
    assert.equal(c.incremental, true, `${s}: 既定は増分可（旧 src!==zp）`);
    assert.equal(c.hasPeriodWindow, false, `${s}: 期間窓なし`);
    assert.equal(c.supportedTfs, null, `${s}: tf 制限なし`);
    assert.equal(c.blockedSessionTfs.size, 0, `${s}: session ブロックなし`);
    assert.equal(c.poc, 'line', `${s}: POC は通常線`);
    assert.equal(c.showLabels, false, `${s}: ラベルなし`);
    assert.equal(c.tfPeriodSrc, null, `${s}: tf-period src なし`);
  }
});

test('mpSupportsTf: 全 player tf で旧 _MP_ZP_TF / ZP_TF_ALLOWED 述語と完全一致する', () => {
  for (const tf of ALL_PLAYER_TF) {
    // zp: 旧「src==='zp' && !_MP_ZP_TF.has(tf) は不可」＝ mpSupportsTf は _MP_ZP_TF 集合
    assert.equal(mpSupportsTf('zp', tf), OLD_ZP_TF.has(tf), `zp × ${tf}`);
    // dwell / 未知 / null: 常に対応（旧 src!=='zp' は無制限）
    assert.equal(mpSupportsTf('dwell', tf), true, `dwell × ${tf}`);
    assert.equal(mpSupportsTf(null, tf), true, `null × ${tf}`);
  }
});

test('blockedSessionTfs: zp は 1m/5m のみ・全 player tf で旧集合と一致', () => {
  for (const tf of ALL_PLAYER_TF) {
    assert.equal(mpSourceCapability('zp').blockedSessionTfs.has(tf), OLD_ZP_BLOCKED.has(tf), `zp block × ${tf}`);
    assert.equal(mpSourceCapability('dwell').blockedSessionTfs.has(tf), false, `dwell block × ${tf}`);
  }
});

test('mpTfPeriodSrc: zp のみ zp を返し他は null（旧 getSrc と一致）', () => {
  assert.equal(mpTfPeriodSrc('zp'), 'zp');
  assert.equal(mpTfPeriodSrc('dwell'), null);
  assert.equal(mpTfPeriodSrc(null), null);
  assert.equal(mpTfPeriodSrc('candle'), null);
});

test('選択可能ソース一覧・既定・ラベルが catalog の enum と一致する', () => {
  assert.deepEqual(MP_SELECTABLE_SOURCES, ['dwell', 'zp']);
  assert.equal(MP_DEFAULT_SOURCE, 'zp');
  assert.deepEqual(mpSourceEnumLabels(), { dwell: '滞在時間(実ティック)', zp: '超過占有z(p)' });
});

test('MP_ZP_SESSIONS_BLOCKED_TFS 後方互換エクスポートは 1m/5m の 2 要素', () => {
  assert.equal(MP_ZP_SESSIONS_BLOCKED_TFS.size, 2);
  assert.ok(MP_ZP_SESSIONS_BLOCKED_TFS.has('1m'));
  assert.ok(MP_ZP_SESSIONS_BLOCKED_TFS.has('5m'));
});
