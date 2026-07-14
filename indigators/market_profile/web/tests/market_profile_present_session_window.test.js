// market_profile_present_session_window.test.js
//   present（通常チャート・ライブ・FOLLOW=growing）の normal forming が base 累積窓を
//   全期間 → 当日（現在セッション）へ絞る回帰。
//
// 設計入力（ユーザー確定）: present の normal（FOLLOW ライブ成長）プロファイル窓が「全期間」だと
//   1 本ぶんの成長が数年に対して極小で視認不能になる。よって base 累積の下限を
//   from=session_start=floor(now/86400)*86400（当日始端）へ絞る（古典的セッション Market Profile）。
//   now = 最新ローソク time（_getCandles 末尾・sessions の getContext().to 源と同一）。
//   規則は domain GrowthWindow.forCurrent('normal',tf,now).from（=min(当日始端, formingStart)）と一致
//   （日中足＝当日始端／上位足 1W/1M＝当該バー期間で from<=formingStart 不変条件を保つ）。
//
// 不変（本テストで固定する非退行）:
//   - static（ANALYSIS＝growing=false）は forming 経路に入らず（refresh 委譲）、from を載せない。
//   - sessions（_sessions=true）は forming 経路に from を載せない（sessions は refresh(to) で育てる）。
//   - 最新ローソク不在（getCandles 空）は窓を成さず from を載せない（安全側・全期間へ縮退）。
//
// 構造: Arrange-Act-Assert。_buildFormingArgs を直接検証（replay 側 test と同型の unit 検証）。

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { sessionDayStart } from '../js/domain/session_day.js';
import { MarketProfileActor } from '../js/adapter/front/market_profile_actor.js';
import { GrowthWindow } from '../js/domain/growth_window.js';

const DAY = 86400;

// 最小 actor（_buildFormingArgs は getContext/params/_growing/_sessions/_getCandles のみ参照）。
function makeActor({ timeframe = '1h', candles = null } = {}) {
  return new MarketProfileActor({
    getContext: () => ({ datasetRef: 'jp225_tick', timeframe, limit: 1500 }),
    getCandles: () => candles ?? [],
  });
}

// --- 正常系: present normal + growing（FOLLOW）は当日始端を base 下限 from に載せる ---
test('present normal growing: _buildFormingArgs adds from=session_start (当日始端) from latest candle time', () => {
  // Arrange: 日途中の最新ローソク time。mode=normal・growing=true（FOLLOW 相当）。
  const now = 1782985000; // 日途中
  const daySt = sessionDayStart(now); // ISSUE-078: セッション日始端。
  const actor = makeActor({ timeframe: '1h', candles: [{ time: now - 3600, close: 1 }, { time: now, close: 1 }] });
  actor.setParams({ mode: 'normal' });
  actor.applyGrowthState({ growing: true });
  // Act
  const args = actor._buildFormingArgs({ base: 1, since: null });
  // Assert: 基底 src=dwell/base/since ＋ from=当日始端（全期間→当日）。now は present では載せない
  //   （backend がライブ現在時刻から formingStart を導出＝from のみ絞る）。
  assert.equal(args.src, 'dwell');
  assert.equal(args.base, 1);
  assert.equal(args.from, daySt, 'present normal 成長は当日始端を base 下限に載せる（視認性・全期間撤廃）');
});

// --- 上位足（1W）: from=週始端（=formingStart）で min(当日,formingStart) 不変条件を保つ ---
test('present normal growing on 1W: from clamps to the bar-period start (min(当日, formingStart))', () => {
  // Arrange: 1W は formingStart（週始端）が当日始端より前。from は formingStart 側へ寄せる。
  //   epoch 週境界（木曜 00:00 UTC 基準）から +3.5 日の週央・日途中を選び、週始端 < 当日始端 を確実にする。
  const now = 1782950400 + 3 * DAY + 12 * 3600; // 既知の週境界 +3.5 日
  const weekSt = Math.floor(now / (7 * DAY)) * (7 * DAY);
  const daySt = sessionDayStart(now); // ISSUE-078。
  assert.ok(weekSt < daySt, '前提: 週始端 < 当日始端');
  const actor = makeActor({ timeframe: '1W', candles: [{ time: now, close: 1 }] });
  actor.setParams({ mode: 'normal' });
  actor.applyGrowthState({ growing: true });
  // Act
  const args = actor._buildFormingArgs({ base: 1, since: null });
  // Assert: from=min(当日始端, 週始端)=週始端（from<=formingStart 不変条件）。
  assert.equal(args.from, weekSt, '上位足は当該バー期間（週始端）を base 下限にする（from<=formingStart）');
});

// --- 非退行: static（ANALYSIS＝growing=false）は from を載せない ---
test('present static (growing=false): _buildFormingArgs does NOT add from (全期間・不変)', () => {
  // Arrange: growing=false（ANALYSIS 静止）。既定で _growing=false。
  const now = 1782985000;
  const actor = makeActor({ timeframe: '1h', candles: [{ time: now, close: 1 }] });
  actor.setParams({ mode: 'normal' });
  // growing は立てない（static）。
  // Act
  const args = actor._buildFormingArgs({ base: 1, since: null });
  // Assert: from を載せない（全期間 base＝従来挙動を保持）。
  assert.ok(!('from' in args), 'static は from を載せない（ANALYSIS 静止は全期間・不変）');
});

// --- 非退行: sessions（_sessions=true）は forming 経路に from を載せない ---
test('present sessions growing: _buildFormingArgs does NOT add from (sessions は refresh(to) で育てる)', () => {
  // Arrange: sessions ON + growing。sessions は forming 単一プロファイルではなく refresh(to) 経路で育てる。
  const now = 1782985000;
  const actor = makeActor({ timeframe: '1h', candles: [{ time: now, close: 1 }] });
  actor.setParams({ mode: 'sessions' });
  actor.applyGrowthState({ growing: true });
  // Act
  const args = actor._buildFormingArgs({ base: 1, since: null });
  // Assert: sessions では forming from を載せない（当日窓は refresh(to,sessions) が backend で担う）。
  assert.ok(!('from' in args), 'sessions は forming 経路に from を載せない（refresh(to) で育てる）');
});

// --- 安全側: 最新ローソク不在（getCandles 空）は from を載せない（全期間へ縮退） ---
test('present normal growing with no candles: _buildFormingArgs omits from (窓を成さず全期間へ縮退)', () => {
  // Arrange: growing だが getCandles が空（now を確定できない）。
  const actor = makeActor({ timeframe: '1h', candles: [] });
  actor.setParams({ mode: 'normal' });
  actor.applyGrowthState({ growing: true });
  // Act
  const args = actor._buildFormingArgs({ base: 1, since: null });
  // Assert: now 不明なら from を載せない（安全側・既存 fetch と同じ全期間）。
  assert.ok(!('from' in args), 'ローソク不在は窓を成さず from を載せない（全期間へ縮退）');
});

// --- DRY ドリフト検知: _sessionFrom（present インライン）は domain GrowthWindow と写像一致すること ---
//   アーキ精査の改善提案。present の絞った窓規則はビルド制約（バンドル単一 IIFE の TF_BAR_SEC 二重宣言衝突・
//   growth_window.js の symlink 不在）ゆえ domain import せずインライン複製している。唯一の弱点＝サイレント
//   ドリフト（一方だけ規則変更）を、両者を実 import する本テスト（Node ESM＝別スコープで衝突しない）で固定する。
test('_sessionFrom は GrowthWindow.forCurrent(normal) と写像一致する（インライン複製のドリフト検知）', () => {
  const nows = [
    1782985000,            // 日途中
    Math.floor(1782985000 / DAY) * DAY,          // 当日始端ちょうど
    1782950400 + 3 * DAY + 12 * 3600,            // 週央
    1730000000, 1700000123, 1782950400 + 6 * DAY, // 各種
  ];
  for (const tf of ['1m', '5m', '15m', '30m', '1h', '4h', '1D', '1W', '1M']) {
    for (const now of nows) {
      const actor = makeActor({ timeframe: tf, candles: [{ time: now, close: 1 }] });
      const inlineFrom = actor._sessionFrom();
      const domainFrom = GrowthWindow.forCurrent('normal', tf, now).from;
      assert.equal(
        inlineFrom, domainFrom,
        `_sessionFrom(tf=${tf}, now=${now})=${inlineFrom} が GrowthWindow(normal).from=${domainFrom} と一致（複製ドリフト検知）`,
      );
    }
  }
});
