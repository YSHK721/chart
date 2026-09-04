// min_bars_ledger.test.js — 最小バー数の学習台帳（ISSUE-479 Wave2 J-1 SRP 2/2）の抽出を固定する。
//
// 何を固定するか:
//   R1 構造: 「サーバが申告した必要バー数を学習し、窓が満たすまで発行を見送る」（ISSUE-283）の
//       本体は indicator_controller.js に無い（協働子への 1 行委譲だけが残る）。学習内容
//       （instanceId -> 必要バー数）も協働子が所有する（ISSUE-181「状態も一緒に移す」）。
//   C1 計算量: recomputeAllApplied 1 回で発行した /compute の数が、出力に使った数
//       （描画 job ＋ 失敗）と一致する（作って捨てる要求が無い）。見送り（deferred）は
//       **発行しない**＝要求そのものが消える（回数を間引くのではない・ISSUE-283 の趣旨）。
//   C4 計算量: 見送り判定の評価回数は適用済み件数で決まり、窓の長さでは変わらない。
//
// 回数そのものは焼き込まない（固定するのは無駄の不在・絶対命令 2026-08-28）。

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

import { IndicatorController } from '../js/adapter/front/indicator_controller.js';
import { get } from '../js/usecase/catalog.js';

const WEB = dirname(dirname(fileURLToPath(import.meta.url)));
const FRONT = join(WEB, 'js', 'adapter', 'front');
const CONTROLLER_SRC = readFileSync(join(FRONT, 'indicator_controller.js'), 'utf8');

const LEDGER_METHODS = ['_knownMinBars', '_forgetMinBars', '_computeWindowBars'];

function methodBody(name) {
  const lines = CONTROLLER_SRC.split('\n');
  const start = lines.findIndex((l) => new RegExp(`^  ${name}\\(`).test(l));
  assert.notEqual(start, -1, `${name} が indicator_controller.js に見つからない（公開面が消えている）`);
  const body = [];
  for (let i = start + 1; i < lines.length; i += 1) {
    if (lines[i] === '  }') break;
    const code = lines[i].trim();
    if (code === '' || code.startsWith('//') || code.startsWith('*') || code.startsWith('/*')) continue;
    body.push(code);
  }
  return body;
}

test('R1: 最小バー数台帳の本体は indicator_controller.js に無い（協働子への委譲だけが残る）', () => {
  const offenders = [];
  for (const name of LEDGER_METHODS) {
    const body = methodBody(name);
    if (body.length !== 1 || !body[0].includes('this._minBarsLedger')) {
      offenders.push(`${name}: ${body.length} 行 / ${body.join(' ')}`.slice(0, 160));
    }
  }
  assert.deepEqual(offenders, [],
    `台帳の本体が indicator_controller.js に残っています:\n  ${offenders.join('\n  ')}`);
});

test('R1: 学習内容（instanceId -> 必要バー数）は協働子が所有する（host の Map ではない）', () => {
  assert.equal(
    /this\._minBars\s*=/.test(CONTROLLER_SRC), false,
    'IndicatorController が学習 Map を自身のフィールドとして持っている（状態が host のまま）',
  );
  assert.equal(
    /this\._minBars\.(set|get|delete)\b/.test(CONTROLLER_SRC), false,
    'IndicatorController が学習 Map を直接操作している（台帳の入口が 2 つになる）',
  );
});

// ---------------------------------------------------------------------------
// 計算量ゲート（Test Spy＝/compute 発行回数と見送り判定の評価回数を数える）
// ---------------------------------------------------------------------------

const noop = () => {};

class ComputeError extends Error {
  constructor(message, payload) {
    super(message);
    Object.assign(this, payload);
  }
}

// 適用済み 2 件（うち 1 件は要件 1352 本を要求して失敗する）の構成を作る。
async function build({ recentBars = 1500 } = {}) {
  const spy = { compute: 0, drawn: new Set() };
  const controller = new IndicatorController({
    catalog: { listIndicators: () => [], get },
    compute: {
      compute: async (req) => {
        spy.compute += 1;
        if (req.indicatorId === 'cvfe' && (req.limit ?? 0) < 1352) {
          throw new ComputeError('E01_INSUFFICIENT_BARS: バー数が足りない', {
            error_type: 'validation',
            violations: [{ code: 'E01_INSUFFICIENT_BARS', requiredBars: 1352, actualBars: req.limit }],
          });
        }
        // 世代はサーバが要求のものを返す（echo）。ここで固定値を返すと世代不一致で
        //   job が非採用になり、測定の前提（発行が出力に使われる）が崩れる。
        return { ok: true, generation: req.generation ?? 0, series: [] };
      },
    },
    persistence: {
      loadApplied: () => [], saveApplied: noop, loadFavorites: () => [], saveFavorites: noop,
      loadUiState: () => ({}), saveUiState: noop, nextSeq: () => 1,
    },
    renderer: {
      renderLine: noop, renderHorizontal: noop, renderHistogram: noop, setData: noop,
      setVisible: noop, remove: noop, setCandles: noop,
    },
    document: null,
    recentBars,
  });
  await controller.applyIndicator('cvfe');
  await controller.applyIndicator('moving_averages');
  // 「出力に使った計算」= 描画へ回された job（_renderInstance は accepted job ごとに 1 回）。
  const renderInner = controller._renderInstance.bind(controller);
  controller._renderInstance = (job) => { spy.drawn.add(job.instanceId); return renderInner(job); };
  return { controller, spy };
}

async function measureBatch(rig) {
  const before = rig.spy.compute;
  rig.spy.drawn.clear();
  const batch = await rig.controller.recomputeAllApplied({ mode: 'full' });
  return { issued: rig.spy.compute - before, drawn: rig.spy.drawn.size, batch };
}

test('C1: 発行した /compute は、描画 job と失敗のどちらかとして必ず使われる（捨てる要求が無い）', async () => {
  // Arrange: 窓が cvfe の要件（1352）を下回る＝1 件は失敗する。
  const rig = await build();
  rig.controller._recentBars = 1234;
  // Act
  const m = await measureBatch(rig);
  // Assert
  const used = m.drawn + m.batch.failures.length;
  assert.equal(m.issued - used, 0,
    `発行した計算が出力に使われていない（発行 ${m.issued} / 使用 ${used}）`);
  assert.equal(m.batch.deferred.length, 0, '初回は未学習なので見送りは発生しない');
});

test('C1: 学習後の見送り（deferred）は 1 件も発行しない（要求そのものが消える）', async () => {
  // Arrange: 1 回失敗させて要件を学習させる。
  const rig = await build();
  rig.controller._recentBars = 1234;
  await measureBatch(rig);
  // Act: 学習済みの状態でもう 1 回。
  const m = await measureBatch(rig);
  // Assert: 見送った件は発行ゼロ。発行したぶんは全て使われている。
  assert.equal(m.batch.deferred.length, 1, '見送りとして可視化されていない（測定の前提が崩れている）');
  const used = m.drawn + m.batch.failures.length;
  assert.equal(m.issued - used, 0,
    `見送りに対応する要求が発行されている（発行 ${m.issued} / 使用 ${used}）`);
});

test('C4: 見送り判定の評価回数は適用件数で決まり、窓の長さでは変わらない', async () => {
  // Arrange: 窓の長さだけが違う 2 点（どちらも要件 1352 に届かない＝判定は同じ回数走る）。
  const short = await build();
  const long = await build();
  short.controller._recentBars = 500;
  long.controller._recentBars = 1300;
  await measureBatch(short);   // 学習させる（以後は毎回 shouldDefer が評価される）
  await measureBatch(long);
  const countEvaluations = (rig) => {
    const ledger = rig.controller._minBarsLedger;
    let calls = 0;
    const inner = ledger.shouldDefer.bind(ledger);
    ledger.shouldDefer = (...args) => { calls += 1; return inner(...args); };
    return { get: () => calls };
  };
  const a = countEvaluations(short);
  const b = countEvaluations(long);
  // Act
  await measureBatch(short);
  await measureBatch(long);
  // Assert: 適用件数（2 件）ぶんだけ評価する。窓が 500 でも 1300 でも同じ。
  assert.equal(a.get(), 2, '適用件数と評価回数が一致しない');
  assert.equal(b.get(), a.get(), '窓の長さで評価回数が変わっている（オーダーが崩れた）');
});

test('C5: 見送りを無視して発行する変異を入れると C1 が赤になる（検出力の実測）', async () => {
  // Arrange: 「学習していても必ず発行する」＝ISSUE-283 以前の挙動へ戻す変異（負の対照）。
  const rig = await build();
  rig.controller._recentBars = 1234;
  await measureBatch(rig);                 // 学習させる
  rig.controller._minBarsLedger.shouldDefer = () => null;
  // Act
  const m = await measureBatch(rig);
  // Assert: 発行したのに使われない（失敗にも job にもならない）ぶんが出る…のではなく、
  //   見送るはずの 1 件が失敗として現れる＝deferred が消える。どちらにせよ上の
  //   「deferred は 1 件も発行しない」assert が成立しなくなる。
  assert.equal(m.batch.deferred.length, 0,
    '変異を入れても見送りが残っている（ゲートが空振りしている）');
  assert.ok(m.issued > 0, '変異を入れたのに発行が増えていない（ゲートが空振りしている）');
});
