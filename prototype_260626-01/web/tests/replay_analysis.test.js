// replay_analysis.js（usecase/replay_analysis.js）— 分析純関数群の検証。
//
// 設計入力（plan §新設（純ロジック・usecase）/ §3 提供価値 A/B/C / §テスト）:
//   - readoutAt(seriesByInstance, candle_t) → 決定点リードアウト用の整形（能力A）。
//   - repaintDelta(asSeenSeries, finalSeries) → 過去区間の最大/平均乖離・リペイント判定（能力B）。
//   - forwardOutcome(mark, candlesUpToCurrent) → Δ価格・経過足・最大順行/逆行（能力C・
//       現在フレームまでのみ。将来足を見ない＝因果一貫）。
//
// DOM/lwc/timer/fetch 非依存（純ロジック・node:test 対象）。AAA 構造。
//
// ★この時点で web/js/usecase/replay_analysis.js は未実装（Red）。import 解決失敗 or
//   関数未定義により失敗することを確認する（実装は後続 programmer-executor 担当）。
//
// 引き渡し契約（programmer-executor への想定シグネチャ）:
//   - readoutAt(seriesByInstance, candle_t) -> {
//         candle: {time, open, high, low, close} | null,
//         values: [{ instanceId, name, value } ...]   // 各系列の t 時点の点
//     }
//     seriesByInstance: { [instanceId]: { meta?, series: [{name, data:[{time,value}]}] } }
//   - repaintDelta(asSeenSeries, finalSeries, { threshold=? }) -> {
//         maxAbs, maxRel, repainted: bool   // 過去区間（共通 time）での最大乖離
//     }
//     asSeenSeries/finalSeries: { data: [{time, value}] }
//   - forwardOutcome(mark, candlesUpToCurrent) -> {
//         deltaPrice, barsElapsed, maxFavorable, maxAdverse
//     }
//     mark: { time, price }; candlesUpToCurrent: [{time,open,high,low,close} ...]
//        （★現在フレームまで＝末尾が現在足。将来足は配列に含まれない前提）

import { test } from 'node:test';
import assert from 'node:assert/strict';

import {
  readoutAt,
  repaintDelta,
  forwardOutcome,
} from '../js/usecase/replay_analysis.js';

// --------------------------------------------------------------------------- //
// readoutAt — t 時点の各系列値 + OHLC を抽出（能力A）
// --------------------------------------------------------------------------- //
function seriesByInstanceFixture() {
  return {
    inst_tgp: {
      series: [
        { name: 'btlm_mean', data: [{ time: 100, value: 10 }, { time: 200, value: 20 }, { time: 300, value: 30 }] },
        { name: 'btlm_q5', data: [{ time: 100, value: 5 }, { time: 200, value: 15 }, { time: 300, value: 25 }] },
      ],
    },
    inst_band: {
      series: [
        { name: 'pOL 99%', data: [{ time: 100, value: 1 }, { time: 200, value: 2 }, { time: 300, value: 3 }] },
      ],
    },
  };
}

const CANDLE_T = { time: 200, open: 19, high: 22, low: 18, close: 21 };

test('readoutAt extracts the OHLC candle at t into the readout', () => {
  const out = readoutAt(seriesByInstanceFixture(), CANDLE_T);
  assert.deepEqual(out.candle, CANDLE_T);
});

test('readoutAt extracts the value of each series at the candle time t', () => {
  const out = readoutAt(seriesByInstanceFixture(), CANDLE_T);
  // t=200 の各系列点の値を抽出（btlm_mean=20, btlm_q5=15, pOL 99%=2）。
  const byName = Object.fromEntries(out.values.map((v) => [v.name, v.value]));
  assert.equal(byName['btlm_mean'], 20);
  assert.equal(byName['btlm_q5'], 15);
  assert.equal(byName['pOL 99%'], 2);
});

test('readoutAt carries the instanceId for each extracted value', () => {
  const out = readoutAt(seriesByInstanceFixture(), CANDLE_T);
  const tgp = out.values.filter((v) => v.instanceId === 'inst_tgp');
  assert.equal(tgp.length, 2); // inst_tgp の 2 系列ぶん
});

test('readoutAt yields null value for a series that has no point at t', () => {
  // t に点が無い系列（horizontal_line 等で t 未満しか持たない）は値 null を返す。
  const sbi = {
    inst_x: { series: [{ name: 'sparse', data: [{ time: 100, value: 7 }] }] },
  };
  const out = readoutAt(sbi, CANDLE_T); // t=200 に点が無い
  const v = out.values.find((x) => x.name === 'sparse');
  assert.equal(v.value, null);
});

// --------------------------------------------------------------------------- //
// repaintDelta — as-seen-at-t と最終確定の乖離（能力B）
// --------------------------------------------------------------------------- //
test('repaintDelta returns zero and not-repainted for identical series', () => {
  const s = { data: [{ time: 100, value: 10 }, { time: 200, value: 20 }] };
  const out = repaintDelta(s, { data: [...s.data.map((p) => ({ ...p }))] }, { threshold: 0.01 });
  assert.equal(out.maxAbs, 0);
  assert.equal(out.repainted, false); // 非リペイント（robust 因果窓相当）
});

test('repaintDelta computes absolute and relative max divergence for a known repaint', () => {
  // 過去区間で final が as-seen から描き直された（global 分位点のリペイント相当）。
  const asSeen = { data: [{ time: 100, value: 100 }, { time: 200, value: 100 }] };
  const final = { data: [{ time: 100, value: 110 }, { time: 200, value: 100 }] };
  const out = repaintDelta(asSeen, final, { threshold: 0.01 });
  // t=100 で |110-100|=10、相対 10/100=0.10（10%）。
  assert.equal(out.maxAbs, 10);
  assert.ok(Math.abs(out.maxRel - 0.10) < 1e-9);
});

test('repaintDelta flags repainted=true when max relative divergence exceeds the threshold', () => {
  const asSeen = { data: [{ time: 100, value: 100 }] };
  const final = { data: [{ time: 100, value: 110 }] }; // 相対 10%
  // threshold=5% → 超過 → repainted。
  const out = repaintDelta(asSeen, final, { threshold: 0.05 });
  assert.equal(out.repainted, true);
});

test('repaintDelta flags repainted=false when divergence stays within the threshold', () => {
  const asSeen = { data: [{ time: 100, value: 100 }] };
  const final = { data: [{ time: 100, value: 101 }] }; // 相対 1%
  // threshold=5% → 閾値内 → 非リペイント（許容範囲）。
  const out = repaintDelta(asSeen, final, { threshold: 0.05 });
  assert.equal(out.repainted, false);
});

// --------------------------------------------------------------------------- //
// forwardOutcome — マーク後の先送り観察（能力C・現在フレームまでに限定）
// --------------------------------------------------------------------------- //
test('forwardOutcome computes delta price and bars elapsed from the mark to the current frame', () => {
  const mark = { time: 100, price: 10 };
  // 現在フレームまでの足（末尾=現在足 time=300, close=13）。
  const candles = [
    { time: 100, open: 10, high: 11, low: 9, close: 10 },
    { time: 200, open: 10, high: 14, low: 8, close: 12 },
    { time: 300, open: 12, high: 15, low: 11, close: 13 },
  ];
  const out = forwardOutcome(mark, candles);
  // Δ価格 = 現在 close - mark.price = 13 - 10 = 3。経過足 = mark 以降の足数（200,300）= 2。
  assert.equal(out.deltaPrice, 3);
  assert.equal(out.barsElapsed, 2);
});

test('forwardOutcome reports max favorable and max adverse excursion within the observed window', () => {
  const mark = { time: 100, price: 10 };
  const candles = [
    { time: 100, open: 10, high: 11, low: 9, close: 10 },
    { time: 200, open: 10, high: 14, low: 8, close: 12 }, // high 14, low 8
    { time: 300, open: 12, high: 15, low: 11, close: 13 }, // high 15
  ];
  const out = forwardOutcome(mark, candles);
  // 最大順行 = 観測窓の最高 high - mark.price = 15 - 10 = 5。
  // 最大逆行 = mark.price - 観測窓の最安 low = 10 - 8 = 2。
  assert.equal(out.maxFavorable, 5);
  assert.equal(out.maxAdverse, 2);
});

test('forwardOutcome does not look at bars beyond the current frame (causal limitation)', () => {
  // 因果一貫: candlesUpToCurrent に「現在フレームまで」しか含まれない前提を固定する。
  //   同じ mark でも、現在フレームが手前（time=200 まで）なら将来足(300)の値を一切見ない。
  const mark = { time: 100, price: 10 };
  const upTo200 = [
    { time: 100, open: 10, high: 11, low: 9, close: 10 },
    { time: 200, open: 10, high: 14, low: 8, close: 12 }, // 現在足=200
  ];
  const out = forwardOutcome(mark, upTo200);
  // 経過足=1（200 のみ）。Δ=12-10=2。最大順行=14-10=4（300 の high 15 は見ない）。
  assert.equal(out.barsElapsed, 1);
  assert.equal(out.deltaPrice, 2);
  assert.equal(out.maxFavorable, 4); // 将来足(15)を見ていない証拠
});
