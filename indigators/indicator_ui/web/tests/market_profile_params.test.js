// market_profile_params.test.js — MP パラメータ・スキーマ写像（純関数）の単体テスト。
//
// 対象: js/adapter/front/market_profile_params.js（ISSUE-094 🔴-4 抽出）。
//   indicator_controller.js に混在していた MP（A7）のパラメータ・スキーマ知識
//   （_mpParams / _deriveMode / _deriveResmode 相当）を純関数へ外出しした対象。
//   挙動は抽出前の controller メソッドと byte 等価（既存 mp_mode_migration /
//   mp_resmode_migration / market_profile_menu テストの意味を純関数側で再固定する）。

import { test } from 'node:test';
import assert from 'node:assert/strict';

import {
  buildMpParams,
  deriveMpMode,
  deriveMpResmode,
} from '../js/adapter/front/market_profile_params.js';

// --- deriveMpResmode -------------------------------------------------------
test('deriveMpResmode: 明示 resmode をそのまま返す（上書きしない）', () => {
  assert.equal(deriveMpResmode({ resmode: 'bins', range: '100' }), 'bins');
  assert.equal(deriveMpResmode({ resmode: 'range', range: '50' }), 'range');
});

test('deriveMpResmode: resmode 欠落かつ range が barw 数値集合 → range', () => {
  assert.equal(deriveMpResmode({ range: '100' }), 'range');
  assert.equal(deriveMpResmode({ range: '10' }), 'range');
  assert.equal(deriveMpResmode({ range: '500' }), 'range');
});

test('deriveMpResmode: resmode 欠落かつ range=auto → bins', () => {
  assert.equal(deriveMpResmode({ range: 'auto' }), 'bins');
});

test('deriveMpResmode: resmode も range も無い → null（付与しない）', () => {
  assert.equal(deriveMpResmode({}), null);
  assert.equal(deriveMpResmode({ bins: 80 }), null);
});

// --- deriveMpMode ----------------------------------------------------------
test('deriveMpMode: 明示 mode をそのまま返す', () => {
  assert.equal(deriveMpMode({ mode: 'sessions' }), 'sessions');
  assert.equal(deriveMpMode({ mode: 'normal' }), 'normal');
});

test('deriveMpMode: 保存済み mode=replay は normal へ正規化（ISSUE-082）', () => {
  assert.equal(deriveMpMode({ mode: 'replay' }), 'normal');
});

test('deriveMpMode: legacy sessions:true → sessions（mode 欠落時）', () => {
  assert.equal(deriveMpMode({ sessions: true }), 'sessions');
});

test('deriveMpMode: legacy replay:true → normal（リプレイ撤去）', () => {
  assert.equal(deriveMpMode({ replay: true }), 'normal');
});

test('deriveMpMode: 両 true は sessions 優先', () => {
  assert.equal(deriveMpMode({ replay: true, sessions: true }), 'sessions');
});

test('deriveMpMode: legacy 明示 false（両 OFF）→ normal', () => {
  assert.equal(deriveMpMode({ replay: false, sessions: false }), 'normal');
});

test('deriveMpMode: mode も legacy キーも無い → null（付与しない）', () => {
  assert.equal(deriveMpMode({}), null);
  assert.equal(deriveMpMode({ bins: 60 }), null);
});

// --- buildMpParams ---------------------------------------------------------
test('buildMpParams: va/src は常に転送する', () => {
  const out = buildMpParams({ va: 0.7, src: 'candle' });
  assert.equal(out.va, 0.7);
  assert.equal(out.src, 'candle');
});

test('buildMpParams: limit は転送しない（全期間集計固定）', () => {
  const out = buildMpParams({ va: 0.7, src: 'candle', limit: 500 });
  assert.equal('limit' in out, false);
});

test('buildMpParams: bins/period/dispbp は非 null のときのみ転送する', () => {
  const withNone = buildMpParams({ va: 0.7, src: 'candle' });
  assert.equal('bins' in withNone, false);
  assert.equal('period' in withNone, false);
  assert.equal('dispbp' in withNone, false);
  const withAll = buildMpParams({ va: 0.7, src: 'candle', bins: 30, period: 'day', dispbp: 12 });
  assert.equal(withAll.bins, 30);
  assert.equal(withAll.period, 'day');
  assert.equal(withAll.dispbp, 12);
});

test('buildMpParams: legacy barw（数値 range・resmode 無し）は resmode=range を導出し range を載せる', () => {
  const out = buildMpParams({ bins: 30, va: 0.7, limit: 500, src: 'candle', range: '100' });
  assert.equal(out.resmode, 'range');
  assert.equal(out.range, '100');
});

test('buildMpParams: range=auto は resmode=bins を導出し range を載せない', () => {
  const out = buildMpParams({ va: 0.7, src: 'candle', range: 'auto' });
  assert.equal(out.resmode, 'bins');
  assert.equal('range' in out, false);
});

test('buildMpParams: legacy replay/sessions キー自体は actor へ送らない（mode に一本化）', () => {
  const out = buildMpParams({ va: 0.7, src: 'candle', sessions: true });
  assert.equal(out.mode, 'sessions');
  assert.equal('sessions' in out, false);
  assert.equal('replay' in out, false);
});

test('buildMpParams: mode/resmode 未確定の旧インスタンスはキーを付与しない', () => {
  const out = buildMpParams({ va: 0.65, src: 'dwell', bins: 80 });
  assert.equal('mode' in out, false);
  assert.equal('resmode' in out, false);
  assert.equal('range' in out, false);
});
