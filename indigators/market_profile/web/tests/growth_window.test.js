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
import { sessionDayStart } from '../js/domain/session_day.js';

const DAY = 86400;

// --- normal: 絞った窓 from=min(当日始まり, formingStart)＋ bar-period forming ---
test('forCurrent(normal): 日中足は from=当日始まり（絞った窓・視認性）', () => {
  const cursor = DAY * 3 + 3600 * 10 + 123; // 3日目・10時台の途中
  const w = GrowthWindow.forCurrent('normal', '1h', cursor);

  // session_start=DAY*3 <= formingStart=DAY*3+3600*10 → min は当日始まり。
  assert.equal(w.from, sessionDayStart(cursor), '日中足(1h)はセッション日始端を base 下限にする（ISSUE-078）');
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

  assert.equal(w.from, sessionDayStart(cursor), 'sessions はセッション日始端（ISSUE-078）を base 下限にする');
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
  // ISSUE-078: 1D のバー周期はセッション日（NY17:00 ET 基準）＝UTC 深夜床でなくセッション始端。
  assert.equal(
    GrowthWindow.periodStart(DAY + 7200 + 1, '1D'), sessionDayStart(DAY + 7200 + 1),
    '1D はセッション日始端（ISSUE-078）',
  );
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

  // session_start=sessionDayStart(cursor)（1969-12-31 22:00 UTC＝EST 境界）<= formingStart → min は session 側。
  assert.equal(w.from, sessionDayStart(3600 * 10), '未知 mode（replay 等）は normal 扱い＝絞った窓');
});

// --- cursor 欠損: 窓を成さない（null 三つ組・呼び出し側で無効判定できる） ---
test('forCurrent(cursor=null): from/to/formingStart はすべて null（窓を成さない）', () => {
  const w = GrowthWindow.forCurrent('normal', '1h', null);

  assert.equal(w.from, null);
  assert.equal(w.to, null);
  assert.equal(w.formingStart, null);
});

// --- lockedBarw（ISSUE-047）: 成長 push 中の bins モードは窓レンジ拡大のたびに binw=(range/bins) が
//   再導出されプロファイル全体が再スケールする。成長開始前の因果履歴（from 直前・約 1 営業日ぶんの
//   確定足＝ceil(86400/barSec(tf)) 本）のレンジ / bins から barw を 1 回だけ導出して固定するための
//   domain 純関数。履歴不足・レンジ縮退は null（呼び出し側が bins モードへフォールバック）。 ---
test('lockedBarw: from 直前の因果履歴（1h→24本）レンジ / bins から barw を導出する', () => {
  const from = DAY * 10;
  // from 直前 24 本（1h）: low 100..123, high 200..223 → lo=100, hi=223, span=123。
  const candles = [];
  for (let i = 0; i < 24; i += 1) {
    candles.push({ time: from - 3600 * (24 - i), low: 100 + i, high: 200 + i });
  }
  const barw = GrowthWindow.lockedBarw(candles, from, '1h', 60);

  assert.equal(barw, 123 / 60, 'barw=(max(high)-min(low))/bins（因果履歴レンジ基準）');
});

test('lockedBarw: time>=from の足（未来側）はレンジへ含めない（未来リーク禁止）', () => {
  const from = DAY * 10;
  const candles = [
    { time: from - 3600, low: 100, high: 200 },
    { time: from, low: 0, high: 10000 },        // from 以後＝forming/未来側は除外
    { time: from + 3600, low: 0, high: 99999 },
  ];
  const barw = GrowthWindow.lockedBarw(candles, from, '1h', 50);

  assert.equal(barw, 100 / 50, 'from 以後の足はロック対象レンジに入らない');
});

test('lockedBarw: 履歴窓は直近 ceil(86400/barSec(tf)) 本のみ（古い極値は含めない）', () => {
  const from = DAY * 10;
  const candles = [{ time: from - 3600 * 100, low: 1, high: 9999 }]; // 窓外（1h→24本より古い）
  for (let i = 0; i < 24; i += 1) {
    candles.push({ time: from - 3600 * (24 - i), low: 100, high: 220 });
  }
  const barw = GrowthWindow.lockedBarw(candles, from, '1h', 60);

  assert.equal(barw, 120 / 60, '窓は直近 24 本（1h）＝古い足の極値はレンジへ入れない');
});

test('lockedBarw: 1D は直近 1 本（前日レンジ）を基準にする', () => {
  const from = DAY * 10;
  const candles = [
    { time: from - DAY * 2, low: 50, high: 500 },  // 前々日（窓外）
    { time: from - DAY, low: 100, high: 400 },     // 前日（窓＝1 本）
  ];
  const barw = GrowthWindow.lockedBarw(candles, from, '1D', 60);

  assert.equal(barw, 300 / 60, '1D の窓は ceil(86400/86400)=1 本＝前日レンジ');
});

test('lockedBarw: bins 非数/0 以下は既定 60、文字列 bins は数値化する', () => {
  const from = DAY * 10;
  const candles = [{ time: from - 3600, low: 100, high: 220 }];

  assert.equal(GrowthWindow.lockedBarw(candles, from, '1h', undefined), 120 / 60, 'bins 未指定は既定 60');
  assert.equal(GrowthWindow.lockedBarw(candles, from, '1h', '30'), 120 / 30, '文字列 bins は数値化');
  assert.equal(GrowthWindow.lockedBarw(candles, from, '1h', 0), 120 / 60, 'bins<=0 は既定 60');
});

test('lockedBarw: 履歴なし・レンジ縮退・from 欠損・low/high 非有限は null（bins モードへフォールバック）', () => {
  const from = DAY * 10;

  assert.equal(GrowthWindow.lockedBarw([], from, '1h', 60), null, '履歴なしは null');
  assert.equal(GrowthWindow.lockedBarw([{ time: from - 3600, low: 100, high: 100 }], from, '1h', 60), null, 'span=0 は null');
  assert.equal(GrowthWindow.lockedBarw([{ time: from - 3600, low: 100, high: 200 }], null, '1h', 60), null, 'from 欠損は null');
  assert.equal(GrowthWindow.lockedBarw([{ time: from - 3600, close: 1 }], from, '1h', 60), null, 'low/high 欠損足のみは null');
});
