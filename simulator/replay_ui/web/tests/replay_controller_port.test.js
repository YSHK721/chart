// replay_controller_port.test.js — 宣言 port の契約と、実行時能力探査の再発防止（ISSUE-502 段階 5B）。
//
// 是正前（台帳 .doc/solid_audit_20260906.md・実測 2026-09-06）:
//   `js/replay.js` は `typeof controller.<name> === 'function'` を **19 箇所**（10 メソッド）に
//   散らし、呼ぶ直前に毎回「その面を持つか」を問い合わせていた。要求する面はどこにも宣言されず、
//   面の**部分実装**も無言で受理していた（ISP 違反・宣言 port の代用）。
//
// 本スイートが固定する規則:
//   1. `js/replay.js` に実行時能力探査が 1 つも無い（再発を Red で止める）。
//   2. driver が port 経由で呼ぶ名は、すべて契約が宣言している（宣言外の面を勝手に生やさない）。
//   3. 必須メンバーの欠落・面の部分実装は**接続時**に落ちる（実行時まで隠れない）。
//   4. 面が無いときの振る舞いは契約が宣言する（旧 else 側と 1 対 1 同値）。
//   5. 面の判定は**接続時 1 回**で、フレーム数を増やしても増えない（計算量検定）。
//
// 計算量検定（絶対命令 2026-08-28）の測り方:
//   測るのは時間ではなく**回数**である。旧実装は「不在の面」を毎フレーム問い合わせ直しており
//   （バー送りのたびに 9 面ぶんの探査を発行して結果を捨てる）、これが除去した浪費そのものである。
//   よって「面を持たない controller に対する、契約メンバーの読み取り回数」を数え、
//   **バー送り 1 回と 3 回の 2 点**で「読み取り − 宣言した任意メンバー数 = 0」を固定する。
//   期待値に回数リテラルは焼き込まない（契約宣言そのものから導出する）。

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

import {
  CONTRACT_MEMBERS,
  OPTIONAL_FACES,
  OPTIONAL_METHODS,
  REQUIRED_METHODS,
  RESOLVED_OPTIONAL_MEMBERS,
  createReplayControllerPort,
} from '../js/replay/replay_controller_port.js';
import { setupReplay } from '../js/replay.js';
import { fakeChart, fakeController, fakeDoc } from './_fakes.js';

const REPLAY_SOURCE = readFileSync(
  fileURLToPath(new URL('../js/replay.js', import.meta.url)),
  'utf8',
);

// --------------------------------------------------------------------------------------
// 1. 実行時能力探査の再発防止（Red）
// --------------------------------------------------------------------------------------

//: driver が探査を書き戻したことを検出する走査（コメント中の再掲も等しく検出する）。
const PROBE_PATTERNS = Object.freeze({
  controller: /typeof\s+controller\./g,
  view: /typeof\s+view\./g,
});

test('replay.js に controller / view の実行時能力探査が 1 つも無い', () => {
  // Act
  const found = Object.entries(PROBE_PATTERNS).map(
    ([target, re]) => [target, (REPLAY_SOURCE.match(re) || []).length],
  );
  // Assert
  for (const [target, count] of found) {
    assert.equal(count, 0, (
      `replay.js に ${target} の実行時能力探査が ${count} 件ある。`
      + ' 要求する面は js/replay/replay_controller_port.js の宣言 1 箇所が持ち、'
      + ' 突き合わせは接続時 1 回だけ行う規約である。'
    ));
  }
});

test('探査の走査が空振りでない（検出力の自己検定）', () => {
  // Arrange: 是正前の replay.js に実在した 2 行（行 174 / 613 の形）。
  const mutated = [
    "      && typeof controller.windowTokenOf === 'function')",
    "    if (typeof view.getCandles !== 'function') {",
  ].join('\n');
  // Act / Assert: 走査は変異を必ず見つける（見つけられない走査で 0 件を主張しない）。
  assert.equal((mutated.match(PROBE_PATTERNS.controller) || []).length, 1);
  assert.equal((mutated.match(PROBE_PATTERNS.view) || []).length, 1);
});

// --------------------------------------------------------------------------------------
// 2. 契約の網羅（driver が呼ぶ名 ⊆ 宣言）
// --------------------------------------------------------------------------------------

/** `port.<name>` を拾う（import パス中の `..._port.js` は直前が `\w` なので拾わない）。 */
function portMembersUsedInDriver() {
  return [...REPLAY_SOURCE.matchAll(/(?<![\w$/])port\.([A-Za-z_$][\w$]*)/g)].map((m) => m[1]);
}

/** `port.supports.<name>` を拾う。 */
function supportsKeysUsedInDriver() {
  return [...REPLAY_SOURCE.matchAll(/(?<![\w$/])port\.supports\.([A-Za-z_$][\w$]*)/g)].map((m) => m[1]);
}

test('driver が port 経由で呼ぶ名は、すべて契約が宣言している', () => {
  // Arrange
  const declared = new Set([...CONTRACT_MEMBERS, 'supports']);
  // Act
  const used = portMembersUsedInDriver();
  const undeclared = [...new Set(used.filter((name) => !declared.has(name)))];
  // Assert
  assert.deepEqual(undeclared, [], (
    `契約に無い面を port から呼んでいる: ${undeclared.join(', ')}。`
    + ' 面を増やすときは replay_controller_port.js の宣言へ足すこと。'
  ));
  assert.ok(used.length > 0, '走査が空振り（port の呼び出しを 1 つも拾えていない）');
});

test('driver が読む supports の鍵は、面名または独立任意メンバー名である', () => {
  // Arrange
  const validKeys = new Set([...Object.keys(OPTIONAL_FACES), ...Object.keys(OPTIONAL_METHODS)]);
  // Act
  const used = supportsKeysUsedInDriver();
  const unknown = [...new Set(used.filter((key) => !validKeys.has(key)))];
  // Assert
  assert.deepEqual(unknown, [], `supports に存在しない鍵を読んでいる: ${unknown.join(', ')}`);
  assert.ok(used.length > 0, '走査が空振り（supports の読み取りを 1 つも拾えていない）');
});

// --------------------------------------------------------------------------------------
// 3. 接続時の fail-stop
// --------------------------------------------------------------------------------------

test('必須メンバーを欠く controller は接続時に落ちる（欠落名がメッセージに出る）', () => {
  // Arrange: 必須 2 つのうち setUntilTime だけを落とす。
  const broken = { async recomputeAllApplied() {} };
  // Act / Assert
  assert.throws(
    () => createReplayControllerPort(broken),
    (err) => {
      assert.ok(err instanceof TypeError, `TypeError でない: ${err}`);
      assert.match(err.message, /setUntilTime/);
      return true;
    },
  );
});

test('面の部分実装は接続時に落ちる（旧実装は無言で受理していた）', () => {
  // Arrange: revealStore の 9 面のうち 1 つだけを持つ controller。
  const faceNames = Object.keys(OPTIONAL_FACES.revealStore);
  const partial = {
    setUntilTime() {},
    async recomputeAllApplied() {},
    [faceNames[0]]() { return null; },
  };
  // Act / Assert
  assert.throws(
    () => createReplayControllerPort(partial),
    (err) => {
      assert.ok(err instanceof TypeError, `TypeError でない: ${err}`);
      assert.match(err.message, /revealStore/);
      // 欠落した残り全部が名指しされる（どれが足りないかを呼び出し時まで探さない）。
      for (const missing of faceNames.slice(1)) {
        assert.match(err.message, new RegExp(missing));
      }
      return true;
    },
  );
});

test('null は接続時に落ちる', () => {
  assert.throws(() => createReplayControllerPort(null), TypeError);
});

// --------------------------------------------------------------------------------------
// 4. 不在時実装（旧 else 側との同値）
// --------------------------------------------------------------------------------------

test('面を持たない controller では、契約が宣言した不在時実装が入る', async () => {
  // Arrange
  const port = createReplayControllerPort(fakeController());
  // Assert: 旧 else 側が作っていた値と 1 対 1（null / false / 何もしない）。
  assert.equal(port.supports.revealStore, false);
  assert.equal(port.windowTokenOf('1D', [{ time: 1 }]), null);
  assert.equal(port.storedInstanceIds('tok'), null);
  assert.equal(port.renderStored('tok'), null);
  assert.equal(port.revealNeedsBuild(), false);
  assert.equal(port.hasRevealFor('ma#1'), false);
  assert.equal(port.revealTo(123), undefined);
  assert.equal(port.seedRevealFromStore('tok'), undefined);
  assert.equal(port.clearRevealCache(), undefined);
  assert.equal(await port.buildRevealBase(1, 2), undefined);
  // 独立任意（購読スロット）も同様。
  assert.equal(port.supports.setAppliedObserver, false);
  assert.equal(port.supports.setTimeframeApplier, false);
  assert.equal(port.setAppliedObserver(() => {}), undefined);
  assert.equal(port.setTimeframeApplier(null), undefined);
});

test('面をすべて持つ controller では supports が真になり、実装がそのまま呼ばれる', () => {
  // Arrange
  const seen = [];
  const complete = { setUntilTime() {}, async recomputeAllApplied() {} };
  for (const name of Object.keys(OPTIONAL_FACES.revealStore)) {
    complete[name] = (...args) => { seen.push([name, ...args]); return `${name}:ok`; };
  }
  // Act
  const port = createReplayControllerPort(complete);
  const token = port.windowTokenOf('1D', []);
  // Assert
  assert.equal(port.supports.revealStore, true);
  assert.equal(token, 'windowTokenOf:ok');
  assert.deepEqual(seen, [['windowTokenOf', '1D', []]]);
});

test('port は凍結されている（driver が面を後付けできない）', () => {
  const port = createReplayControllerPort(fakeController());
  assert.ok(Object.isFrozen(port));
  assert.ok(Object.isFrozen(port.supports));
});

test('呼び出し先は遅延解決される（接続後に差し替えた実装を観測する）', () => {
  // 旧実装 `controller.X(...)` と同一の解決規則。焼き付けると、setupReplay 後に
  //   recomputeAllApplied を差し替える既存検定（replay_timeframe_applier.test.js）が壊れる。
  // Arrange
  const controller = { setUntilTime() {}, recomputeAllApplied() { return 'before'; } };
  const port = createReplayControllerPort(controller);
  // Act
  controller.recomputeAllApplied = () => 'after';
  // Assert
  assert.equal(port.recomputeAllApplied(), 'after');
});

test('必須・任意の宣言が重複なく CONTRACT_MEMBERS へ畳まれている', () => {
  // 宣言表そのものの健全性（走査・検定が空集合を比べていないことの担保）。
  assert.ok(REQUIRED_METHODS.length > 0);
  assert.ok(RESOLVED_OPTIONAL_MEMBERS.length > 0);
  assert.equal(
    CONTRACT_MEMBERS.length - new Set(CONTRACT_MEMBERS).size, 0,
    `契約メンバー名が重複している: ${CONTRACT_MEMBERS.join(', ')}`,
  );
  assert.equal(
    CONTRACT_MEMBERS.length - (REQUIRED_METHODS.length + RESOLVED_OPTIONAL_MEMBERS.length), 0,
  );
});

// --------------------------------------------------------------------------------------
// 5. 計算量検定（Test Spy・発行 − 使用 = 0）
// --------------------------------------------------------------------------------------

const CANDLES = [
  { time: 100, open: 1, high: 2, low: 0.5, close: 1.5 },
  { time: 200, open: 1.5, high: 2.5, low: 1, close: 2 },
  { time: 300, open: 2, high: 3, low: 1.5, close: 2.5 },
  { time: 400, open: 2.5, high: 3.5, low: 2, close: 3 },
];

/** 契約メンバー名の**読み取り回数**を数える controller ラッパ（面の問い合わせを観測する）。 */
function countingController(counts) {
  const target = fakeController();
  return new Proxy(target, {
    get(obj, prop, receiver) {
      if (typeof prop === 'string' && RESOLVED_OPTIONAL_MEMBERS.includes(prop)) {
        counts[prop] = (counts[prop] || 0) + 1;
      }
      return Reflect.get(obj, prop, receiver);
    },
  });
}

async function bootAndStep(steps) {
  globalThis.window = globalThis.window || {};
  const counts = {};
  const doc = fakeDoc('math');   // math＝足内更新なし（計測対象を面の問い合わせだけに絞る）
  await setupReplay({
    chart: fakeChart(),
    mainSeries: { attachPrimitive() {}, update() {} },
    controller: countingController(counts),
    renderer: { setCandles() {} },
    datasetRef: 'jp225_tick',
    recentBars: 1500,
    document: doc,
    fetchImpl: async (url) => ({
      ok: true,
      async json() {
        if (String(url).startsWith('/candles')) return { ok: true, candles: CANDLES };
        if (String(url).startsWith('/intraday')) return { ok: true, m1: [], ticks: [], tick_secs: [] };
        return { ok: true, days: [] };
      },
    }),
    marketProfile: null,
  });
  for (let i = 0; i < steps; i++) {
    await doc._els['rp-prev']._onclick();   // バー送り（1 フレーム描画）
  }
  return counts;
}

test('spy が実際に読み取りを数える（計測器の自己検定）', () => {
  // 計測器が死んでいると、下の 2 点検定は常に緑になる（ガードが空虚になる）。
  const counts = {};
  const controller = countingController(counts);
  const probed = RESOLVED_OPTIONAL_MEMBERS[0];
  // Act
  void controller[probed];
  void controller[probed];
  // Assert
  assert.equal(counts[probed], 2);
});

for (const steps of [1, 3]) {
  test(`面の問い合わせは接続時 1 回だけ（バー送り ${steps} 回でも増えない）`, async () => {
    // Arrange / Act
    const counts = await bootAndStep(steps);
    // Assert: 宣言した任意メンバーを 1 回ずつ、それ以上は 1 度も問い合わせない。
    //   （回数リテラルは焼き込まず、契約宣言から導出する）
    const total = Object.values(counts).reduce((a, b) => a + b, 0);
    assert.equal(total - RESOLVED_OPTIONAL_MEMBERS.length, 0, (
      `任意メンバーの問い合わせが ${total} 回発行された`
      + `（宣言は ${RESOLVED_OPTIONAL_MEMBERS.length} 件・接続時 1 回のはず）: ${JSON.stringify(counts)}`
    ));
    for (const name of RESOLVED_OPTIONAL_MEMBERS) {
      assert.equal(counts[name], 1, `${name} の問い合わせ回数が 1 でない（${counts[name]}）`);
    }
  });
}
