// growth_window.test.js — GrowthWindow（domain）の MP セッション/成長窓 写像の検証。
//
// 設計入力: Model A 統一成長モデル Phase 3/4「新 domain 値 GrowthWindow（両 app 共有）。
//   forCurrent(mode,tf,cursor)→{from,to,formingStart}。normal=from:null(全期間)/to:cursor/
//   formingStart:period_start(cursor,tf)。sessions=from:session_start(cursor,tf)/to:cursor。
//   1D=86400 をここに隔離（sessions のみ暦日 anchor）。不変条件 to<=cursor（未来リーク禁止）・
//   formingStart<=to」。現状 replay_market_profile_actor._buildFormingArgs に散在する 1D 決め打ち
//   Math.floor(effNow/86400)*86400 を domain へ昇格し、tf/mode パラメータ化の単一源にする。
// 構造: Arrange-Act-Assert（AAA）。DOM/ネット非依存（純ロジック）。

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { GrowthWindow } from '../js/domain/growth_window.js';

const DAY = 86400;

// --- normal: 絞った窓 from=min(当日始まり, formingStart)＋ bar-period forming ---
test('forCurrent(normal): 日中足は from=当日始まり（絞った窓・視認性）', () => {
  const cursor = DAY * 3 + 3600 * 10 + 123; // 3日目・10時台の途中
  const w = GrowthWindow.forCurrent('normal', '1h', cursor);

  // session_start=DAY*3 <= formingStart=DAY*3+3600*10 → min は当日始まり。
  assert.equal(w.from, DAY * 3, '日中足(1h)は当日始まりを base 下限にする（全期間でない）');
});

test('forCurrent(normal): 上位足(1W)は from=当該バー期間始端（不変条件 from<=formingStart を保つ）', () => {
  const cursor = DAY * 3 + 3600 * 10; // 週の途中（週始端は 0）
  const w = GrowthWindow.forCurrent('normal', '1W', cursor);

  // 1W formingStart=floor(cursor/604800)*604800=0 < 当日始まり(DAY*3) → min は formingStart 側。
  assert.equal(w.formingStart, 0, '1W の formingStart は週始端');
  assert.equal(w.from, 0, '当日始まりが formingStart より後になる上位足は formingStart へ寄せる');
  assert.ok(w.from <= w.formingStart, '不変条件 from<=formingStart を保つ');
});

test('forCurrent(normal): to は cursor（as-seen-at-t）', () => {
  const w = GrowthWindow.forCurrent('normal', '1h', 3600 * 10 + 123);

  assert.equal(w.to, 3600 * 10 + 123);
});

test('forCurrent(normal): formingStart は period_start(cursor,tf)＝tf 境界 floor', () => {
  const cursor = 3600 * 10 + 123; // 1h 足の途中
  const w = GrowthWindow.forCurrent('normal', '1h', cursor);

  assert.equal(w.formingStart, 3600 * 10, '1h 足内 cursor は当該 1h 足始端へ floor');
});

// --- sessions: 当日セッション窓（from=暦日 anchor）。1D=86400 をここに隔離 ---
test('forCurrent(sessions): from は暦日 anchor＝floor(cursor/86400)*86400（1D 隔離）', () => {
  const cursor = DAY * 3 + 5000; // 3 日目の途中
  const w = GrowthWindow.forCurrent('sessions', '1h', cursor);

  assert.equal(w.from, DAY * 3, 'sessions は当日始まり（暦日 anchor）を base 下限にする');
});

test('forCurrent(sessions): to は cursor', () => {
  const cursor = DAY * 3 + 5000;
  const w = GrowthWindow.forCurrent('sessions', '1h', cursor);

  assert.equal(w.to, cursor);
});

test('forCurrent(sessions): formingStart は period_start(cursor,tf)（backend forming と同一 anchor）', () => {
  const cursor = DAY * 3 + 3600 * 2 + 77; // 3 日目 02:00 台
  const w = GrowthWindow.forCurrent('sessions', '1h', cursor);

  assert.equal(w.formingStart, DAY * 3 + 3600 * 2, 'forming 始端は当該 1h 足始端（backend period_start_unix と一致）');
});

// --- tf 写像（period_start）: 全時間足で bar-period 境界が効く ---
test('period_start: 1m は 60 秒床、5m は 300 秒床（全 tf tf化の核心）', () => {
  assert.equal(GrowthWindow.periodStart(125, '1m'), 120, '1m=60 秒床');
  assert.equal(GrowthWindow.periodStart(1000, '5m'), 900, '5m=300 秒床');
  assert.equal(GrowthWindow.periodStart(DAY + 7200 + 1, '1D'), DAY, '1D=86400 秒床');
});

test('period_start: 未知 tf は 1D（86400）相当へフォールバック（既存 actor TF_BAR_SEC 規約）', () => {
  assert.equal(GrowthWindow.periodStart(DAY + 100, undefined), DAY, '未知 tf は 86400 床');
});

// --- 不変条件: to<=cursor（未来リーク禁止）・formingStart<=to ---
test('不変条件: to<=cursor（未来リーク禁止・to は cursor で確定）', () => {
  const cursor = 123456;
  for (const mode of ['normal', 'sessions']) {
    const w = GrowthWindow.forCurrent(mode, '5m', cursor);
    assert.ok(w.to <= cursor, `${mode}: to<=cursor`);
  }
});

test('不変条件: formingStart<=to（forming 始端は現在時刻以下）', () => {
  const cursor = DAY * 2 + 12345;
  for (const mode of ['normal', 'sessions']) {
    const w = GrowthWindow.forCurrent(mode, '15m', cursor);
    assert.ok(w.formingStart <= w.to, `${mode}: formingStart<=to`);
  }
});

test('不変条件: sessions の from<=formingStart（base 窓 [from,formingStart) が有効・tf<=1D）', () => {
  const cursor = DAY * 2 + 3600 * 5 + 42;
  const w = GrowthWindow.forCurrent('sessions', '1h', cursor);

  assert.ok(w.from <= w.formingStart, 'sessions: 暦日 anchor<=bar-period anchor（tf<=1D）');
});

// --- 既定/未知 mode: normal 扱い（絞った窓 from=min(当日始まり,formingStart)） ---
test('forCurrent(未知 mode): normal 扱い（絞った窓・当日 base の安全側）', () => {
  const w = GrowthWindow.forCurrent('replay', '1h', 3600 * 10);

  // session_start=0・formingStart=3600*10 → min は 0（当日始まり）。normal と同じ絞った窓。
  assert.equal(w.from, 0, '未知 mode（replay 等）は normal 扱い＝絞った窓（min(当日,formingStart)）');
});

// --- cursor 欠損: 窓を成さない（null 三つ組・呼び出し側で無効判定できる） ---
test('forCurrent(cursor=null): from/to/formingStart はすべて null（窓を成さない）', () => {
  const w = GrowthWindow.forCurrent('normal', '1h', null);

  assert.equal(w.from, null);
  assert.equal(w.to, null);
  assert.equal(w.formingStart, null);
});
