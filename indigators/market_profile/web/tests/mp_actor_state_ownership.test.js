// mp_actor_state_ownership.test.js — MarketProfileActor から分離したロールの状態所有（ISSUE-181）の回帰固定。
//
// 対象の問題（ISSUE-181 実測）: market_profile_actor.js:92-852 は注入依存 14 個・6 アクター同居の
//   神クラスだった。分割しても「状態は host（actor）のまま」だと責務は分離されない（分割不全）ため、
//   「どのオブジェクトが状態を持つか」を構造で固定する。
//
// 固定する不変条件:
//   (1) 抽出済みロールは host（actor）の private フィールドへ再代入しない
//       （`host._x = / += / -=` が 0 件。host 所有オブジェクトのプロパティ更新は対象外）。
//   (2) リプレイ・スクラブ状態（_replay / _replayTo / _scrubRunning / _scrubQueued / リプレイバー）は
//       MpReplayScrubController が所有する（actor のインスタンスに own field として現れない）。
//   (3) チャートレイアウト状態（_attached / mainSeries）は MpChartLayout が所有する。
//   (4) 取得パラメータ（_params 実体）は MpFetchParams が所有する（actor 側は読み取り専用アクセサ）。
//   (5) tick 逐次成長状態（_growing / _accumulator / _formingStart / _lastSec / _formingClient /
//       _makeAccumulator）は MpTickGrowth が所有する。ただし replay subclass
//       （replay_market_profile_actor.js の push 戦略）は `a._accumulator = acc` 等で actor の当該
//       フィールドへ直接書き込む既存の読み書き面を持つため、actor 側に prototype アクセサを置いて
//       その面を維持する（own field ではない＝状態の所有は協働子）。
// 構造: Arrange-Act-Assert（AAA）。DOM/ネット非依存（全注入）。

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

import { MarketProfileActor } from '../js/adapter/front/market_profile_actor.js';

const FRONT_DIR = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', 'js', 'adapter', 'front');

// MarketProfileActor から抽出したロール（host を受け取り、そのロールを担うクラス）の一覧。
const ROLES = [
  'mp_replay_scrub.js',
  'mp_fetch_params.js',
  'mp_tick_growth.js',
  'mp_mode_transition.js',
  'mp_session_tiles.js',
];

// `host._x = ...` / `host._x += ...` / `host._x -= ...`（フィールド再代入）を抽出する。
//   `host._state.uiState = ...` のような host 所有オブジェクトのプロパティ更新は対象外＝
//   フィールドそのものの差し替えのみを違反とする。`==` / `===` の比較は除外する。
function hostFieldAssignments(source) {
  return [...source.matchAll(/host\._[A-Za-z0-9_]+\s*(?:\+=|-=|=(?!=))/g)].map((m) => m[0].trim());
}

function makeActor(overrides = {}) {
  const calls = [];
  const primitive = {
    setProfile: () => calls.push('setProfile'),
    setVisible: () => calls.push('setVisible'),
    setCursorTime: () => calls.push('setCursorTime'),
    setSnapshot: () => calls.push('setSnapshot'),
  };
  return new MarketProfileActor({
    client: { fetchProfile: async () => null },
    primitive,
    mainSeries: { attachPrimitive: () => calls.push('attach') },
    getContext: () => ({ datasetRef: 'jp225_tick', timeframe: '1D' }),
    getCandles: () => [],
    ...overrides,
  });
}

test('抽出ロールは host（actor）の private フィールドへ直接代入しない（ISSUE-181 分割不全の回帰固定）', () => {
  // Arrange
  const found = {};
  // Act
  for (const file of ROLES) {
    const hits = hostFieldAssignments(readFileSync(path.join(FRONT_DIR, file), 'utf8'));
    if (hits.length > 0) {
      found[file] = hits;
    }
  }
  // Assert
  assert.deepEqual(
    found,
    {},
    '抽出ロールが host のフィールドへ直接代入している（状態所有が actor のまま＝責務未分離）',
  );
});

test('リプレイ・スクラブ状態は MpReplayScrubController が所有する（actor は own field を持たない）', () => {
  // Arrange
  const actor = makeActor();
  // Act / Assert
  for (const f of ['_replay', '_replayTo', '_scrubRunning', '_scrubQueued', '_replayBar']) {
    assert.equal(
      Object.prototype.hasOwnProperty.call(actor, f), false,
      `actor に ${f} が own field として残っている（状態所有が actor のまま）`,
    );
  }
  assert.equal(typeof actor._replayScrub.setCursor, 'function', '協働子が scrub 面を持たない');
  assert.equal(actor._replayScrub.isReplay(), false, '初期状態は replay OFF');
});

test('チャートレイアウト状態（attach 済み・attach 先）は MpChartLayout が所有する', () => {
  // Arrange
  const actor = makeActor();
  // Act / Assert
  for (const f of ['_attached', '_mainSeries']) {
    assert.equal(
      Object.prototype.hasOwnProperty.call(actor, f), false,
      `actor に ${f} が own field として残っている（状態所有が actor のまま）`,
    );
  }
  assert.equal(actor._layout._attached, false, '協働子が attach 済みフラグを所有していない');
});

test('取得パラメータの実体は MpFetchParams が所有し、actor 側は読み取り専用の写しを返す', () => {
  // Arrange
  const actor = makeActor();
  // Act
  actor.setParams({ bins: 40, src: 'zp', ignored: 1 });
  // Assert: own field ではなくアクセサ経由（実体は協働子）。受理キーのみ通す。
  assert.equal(Object.prototype.hasOwnProperty.call(actor, '_params'), false,
    'actor に _params が own field として残っている（状態所有が actor のまま）');
  assert.deepEqual(actor._params, { bins: 40, src: 'zp' });
  assert.equal(actor._params, actor._fetchParams.values(), '実体が協働子の所有物でない');
  assert.equal(actor.srcParam(), 'zp');
});

test('tick 逐次成長状態（累積器・形成足・尾部秒・成長フラグ・注入）は MpTickGrowth が所有する', () => {
  // Arrange
  const factory = () => ({ init: () => {}, addTick: () => {}, snapshot: () => ({}) });
  const formingClient = { fetchForming: async () => null };
  const actor = makeActor({ formingClient, makeAccumulator: factory });
  // Act / Assert: own field として actor に残っていない（状態所有が actor のまま＝責務未分離）。
  for (const f of ['_growing', '_accumulator', '_formingStart', '_lastSec',
    '_formingClient', '_makeAccumulator']) {
    assert.equal(
      Object.prototype.hasOwnProperty.call(actor, f), false,
      `actor に ${f} が own field として残っている（状態所有が actor のまま）`,
    );
  }
  // 実体は協働子が持つ（初期値は抽出前と同一）。
  assert.equal(actor._growth._growing, false, '協働子が成長フラグを所有していない');
  assert.equal(actor._growth._accumulator, null, '協働子が累積器を所有していない');
  assert.equal(actor._growth._formingStart, null, '協働子が形成足始端を所有していない');
  assert.equal(actor._growth._lastSec, null, '協働子が尾部秒を所有していない');
  assert.equal(actor._growth._formingClient, formingClient, '協働子が forming 取得口を所有していない');
  assert.equal(actor._growth._makeAccumulator, factory, '協働子が累積器 factory を所有していない');
});

test('replay subclass の直接書き込み面（a._accumulator= 等）は協働子へ透過する（読み書き両方向）', () => {
  // Arrange: replay_market_profile_actor.js:149,150,154,158,180,181 が使う読み書き面を固定する。
  const actor = makeActor();
  const acc = { marker: 'acc' };
  // Act: host 側フィールドへ直接代入（subclass の push 戦略と同じ書き方）。
  actor._accumulator = acc;
  actor._formingStart = 1200;
  actor._lastSec = 1234;
  // Assert: 実体は協働子に届き、host 経由の読み取りも一致する（own field は生えない）。
  assert.equal(actor._growth._accumulator, acc, '_accumulator の代入が協働子へ届かない');
  assert.equal(actor._growth._formingStart, 1200, '_formingStart の代入が協働子へ届かない');
  assert.equal(actor._growth._lastSec, 1234, '_lastSec の代入が協働子へ届かない');
  assert.equal(actor._accumulator, acc, 'host からの読み取りが協働子の値を返さない');
  assert.equal(actor._formingStart, 1200);
  assert.equal(actor._lastSec, 1234);
  assert.equal(Object.prototype.hasOwnProperty.call(actor, '_accumulator'), false,
    '代入で actor に own field が生えた（アクセサではなく実体を持ってしまっている）');
});

test('表示モード状態（ticklive / sessions トグル）は MpModeTransition が所有する', () => {
  // Arrange
  const actor = makeActor();
  // Act / Assert: own field として actor に残っていない。
  for (const f of ['_ticklive', '_sessions']) {
    assert.equal(
      Object.prototype.hasOwnProperty.call(actor, f), false,
      `actor に ${f} が own field として残っている（状態所有が actor のまま）`,
    );
  }
  assert.equal(actor._mode._ticklive, false, '協働子が ticklive トグルを所有していない');
  assert.equal(actor._mode._sessions, false, '協働子が sessions トグルを所有していない');
});

test('mode 遷移は協働子の状態を動かし、host の読み取り面（_sessions / isSessions / isTicklive）と一致する', () => {
  // Arrange
  const actor = makeActor();
  // Act / Assert: normal → sessions → replay → ticklive → normal の排他遷移。
  actor.setParams({ mode: 'sessions' });
  assert.equal(actor._mode._sessions, true, '協働子の sessions が立たない');
  assert.equal(actor._sessions, true, 'host 読み取り面が協働子の値を返さない');

  actor.setParams({ mode: 'replay' });
  assert.equal(actor._mode._sessions, false, 'replay 遷移で sessions が落ちない（排他崩れ）');
  assert.equal(actor._mode._ticklive, false);

  actor.setParams({ mode: 'ticklive' });
  assert.equal(actor._mode._ticklive, true, 'ticklive トグルが協働子に立たない');
  assert.equal(actor._mode._sessions, false);

  actor.setParams({ mode: 'normal' });
  assert.equal(actor._mode._ticklive, false, 'normal 遷移で ticklive が落ちない');
  assert.equal(actor._mode._sessions, false);
  assert.equal(Object.prototype.hasOwnProperty.call(actor, '_sessions'), false,
    '遷移で actor に _sessions が own field として生えた');
});

test('日別タイルの初回オートズーム pending は MpSessionTiles が所有する（ISSUE-164: 発火条件は不変）', () => {
  // Arrange
  const actor = makeActor();
  // Act / Assert: own field として actor に残っていない。
  assert.equal(
    Object.prototype.hasOwnProperty.call(actor, '_sessionsFocusPending'), false,
    'actor に _sessionsFocusPending が own field として残っている（状態所有が actor のまま）',
  );
  assert.equal(actor._tiles._sessionsFocusPending, false, '協働子が pending フラグを所有していない');

  // 非 sessions → sessions の新規入場でのみ pending が立つ（ISSUE-164 の裁定条件そのもの）。
  actor.setParams({ mode: 'sessions' });
  assert.equal(actor._tiles._sessionsFocusPending, true, '新規入場で pending が協働子に立たない');

  // 既に sessions のままの再適用では立て直さない（手動ズームのリセット防止）。
  actor._tiles._sessionsFocusPending = false;
  actor.setParams({ mode: 'sessions' });
  assert.equal(actor._tiles._sessionsFocusPending, false, '再適用で pending が再セットされた（ISSUE-164 違反）');

  // sessions を抜けると pending はクリアされる（_applySessions の off 経路）。
  actor._tiles._sessionsFocusPending = true;
  actor.setParams({ mode: 'normal' });
  assert.equal(actor._tiles._sessionsFocusPending, false, 'off 遷移で pending がクリアされない');
});

test('legacy トグル（mode 未指定の sessions:true）も協働子の状態を動かす（後方互換面）', () => {
  // Arrange
  const actor = makeActor();
  // Act
  actor.setParams({ sessions: true });
  // Assert
  assert.equal(actor._mode._sessions, true, 'legacy sessions トグルが協働子へ届かない');
  assert.equal(actor.isSessions(), false, 'MP 無効時は isSessions が false（_enabled ゲート・不変）');
});

test('applyGrowthState(false) は協働子の累積器・形成足・尾部秒を破棄する（static 復帰）', () => {
  // Arrange
  const actor = makeActor();
  actor.applyGrowthState({ growing: true });
  actor._accumulator = { marker: 'acc' };
  actor._formingStart = 1200;
  actor._lastSec = 1234;
  // Act
  actor.applyGrowthState({ growing: false });
  // Assert: 破棄先は協働子の所有状態（host に残骸が生えない）。
  assert.equal(actor._growth._growing, false);
  assert.equal(actor._growth._accumulator, null, 'static 復帰で累積器が破棄されていない');
  assert.equal(actor._growth._formingStart, null);
  assert.equal(actor._growth._lastSec, null);
});
