// catalog.js の仕様検証（node:test / node:assert）。
//
// 対象: usecase/catalog.js レジストリ（list / get）。
// 設計入力: 内部設計書 §3.1.3（IndicatorDef）、実在 4 バインディング
//   （tgp_btlm / profit_band global,robust / price_range_power）。
// 構造: Arrange-Act-Assert（AAA）。

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { list, get } from '../js/usecase/catalog.js';
import { IndicatorDef } from '../js/domain/domain_models.js';
import { ParamType } from '../js/domain/constraint_eval.js';

// パラメータ取得ヘルパ（params 配列から name で 1 件）。
function paramOf(def, name) {
  return def.params.find((p) => p.name === name);
}

test('catalog: list returns the 20 registered indicators (基本4 + profit_* 15 + market_profile)', () => {
  // Act
  const defs = list();
  // Assert: 既存4（tgp_btlm / profit_band / price_range_power / moving_averages）+ profit_* 15
  //   + market_profile（プロファイルタブ・アクター委譲型）= 20。
  const ids = defs.map((d) => d.id);
  for (const base of ['moving_averages', 'price_range_power', 'profit_band', 'tgp_btlm']) {
    assert.ok(ids.includes(base), `missing ${base}`);
  }
  assert.equal(defs.length, 20);
});

test('catalog: moving_averages is a single-MA indicator (種別/期間/ソース/オフセット + 平滑化 + 計算)', () => {
  const d = get('moving_averages');
  assert.equal(d.id, 'moving_averages');
  assert.equal(d.placement, 'overlay');
  assert.equal(d.category.nameKey, 'cat.technical');
  assert.equal(paramOf(d, 'ma_type').type, ParamType.ENUM);
  assert.equal(paramOf(d, 'ma_type').default, 'ema');
  assert.equal(paramOf(d, 'length').type, ParamType.INT);
  assert.equal(paramOf(d, 'length').default, 9);
  assert.equal(paramOf(d, 'source').type, ParamType.ENUM);
  assert.equal(paramOf(d, 'offset').default, 0);
  assert.equal(paramOf(d, 'smoothing_type').default, 'none');
  assert.equal(paramOf(d, 'wait_for_close').default, false);
});

// 回帰防止: wait_for_close の既定は false。true だと lwc_chart が最終足（未確定足）を
//   price[:-1] で除外し、MA が常に最新足の1本手前で止まる（最新足に指標が出ないバグ）。
//   確定足のみで計算したいユーザーはダイアログで ON にできる。
test('catalog: moving_averages wait_for_close defaults to false so the MA reaches the latest bar', () => {
  const d = get('moving_averages');
  assert.equal(paramOf(d, 'wait_for_close').default, false);
});

test('catalog: moving_averages localizes labels and enum options (日本語表示)', () => {
  const d = get('moving_averages');
  assert.equal(paramOf(d, 'ma_type').label, '種別');
  assert.equal(paramOf(d, 'source').label, 'ソース');
  assert.equal(paramOf(d, 'source').enumLabels.hl2, '(高値 + 安値)/2');
  assert.equal(paramOf(d, 'timeframe').enumLabels.chart, 'チャート');
});

test('catalog: moving_averages BB stddev is conditionally enabled only for sma_bb', () => {
  const bb = paramOf(get('moving_averages'), 'bb_stddev');
  assert.deepEqual(bb.conditionalEnable.when, { param: 'smoothing_type', equals: 'sma_bb' });
});

test('catalog: moving_averages declares 4 fixed series (MA/Smoothing/Upper/Lower)', () => {
  const d = get('moving_averages');
  assert.deepEqual(d.series.map((s) => s.seriesName), ['MA', 'Smoothing', 'Upper', 'Lower']);
  assert.ok(d.series.every((s) => s.dynamic === false));
});

test('catalog: list returns IndicatorDef instances with series>=1', () => {
  const defs = list();
  for (const d of defs) {
    assert.ok(d instanceof IndicatorDef);
    assert.ok(d.series.length >= 1);
  }
});

test('catalog: get returns the indicator by id', () => {
  const d = get('tgp_btlm');
  assert.equal(d.id, 'tgp_btlm');
});

test('catalog: get unknown id returns null', () => {
  // 未知 id は null（呼び出し側で扱う）
  const d = get('does_not_exist');
  assert.equal(d, null);
});

test('catalog: profit_band exposes global and robust variants', () => {
  const d = get('profit_band');
  assert.deepEqual([...d.compute.variants].sort(), ['global', 'robust']);
});

// 回帰防止: バンド値は価格水準（始値±分位点を復元）なので価格 pane(0) へ重畳する。
// placement!=='overlay' だと indicator_controller(pane:true)→専用 pane へ落ち、
// 下部の別 pane に表示されるバグ（別 pane 描画回帰）になる。
test('catalog: profit_band is overlaid on the price pane (not a separate pane)', () => {
  const d = get('profit_band');
  assert.equal(d.placement, 'overlay');
});

test('catalog: tgp_btlm has fitter backend_param', () => {
  const d = get('tgp_btlm');
  assert.equal(d.compute.backendParam, 'fitter');
});

test('catalog: each indicator carries display name, category and tab for the dialog', () => {
  for (const d of list()) {
    assert.ok(typeof d.displayNameKey === 'string' && d.displayNameKey.length > 0);
    assert.ok(d.category && typeof d.category.group === 'string');
    assert.ok(typeof d.tab === 'string' && d.tab.length > 0);
  }
});

// ============================================================================
// catalog パリティ是正（M-1/M-2/M-3/M-4・§1.2）。
// 期待値はすべて実コード（add_* シグネチャ）由来。コメントに根拠ファイル:行を明記。
// ============================================================================

test('catalog parity: tgp_btlm maxbars default is 100 (core.py:33 DEFAULT_MAXBARS=100, M-1)', () => {
  // Arrange/Act
  const p = paramOf(get('tgp_btlm'), 'maxbars');
  // Assert: 実コード既定 100（旧 catalog は 40）
  assert.equal(p.default, 100);
});

test('catalog: tgp_btlm exposes mcmc_samples ENUM [standard,high,max] default standard', () => {
  const p = paramOf(get('tgp_btlm'), 'mcmc_samples');
  assert.ok(p, 'mcmc_samples param が存在する');
  assert.equal(p.type, ParamType.ENUM);
  assert.equal(p.default, 'standard'); // 既定は現挙動維持
  assert.deepEqual(p.enumValues, ['standard', 'high', 'max']);
});

test('catalog parity: price_range_power top_n default is 5 (lwc_chart.py:43 top_n=5, M-3)', () => {
  const p = paramOf(get('price_range_power'), 'top_n');
  assert.equal(p.default, 5); // 旧 catalog は 2
});

test('catalog parity: price_range_power interval keeps ENUM with INTERVAL_CHOICES (core.py:41)', () => {
  const p = paramOf(get('price_range_power'), 'interval');
  assert.equal(p.type, ParamType.ENUM);
  assert.deepEqual(p.enumValues, [0.1, 0.01, 0.001]); // INTERVAL_CHOICES=(0.1,0.01,0.001)
});

test('catalog parity: profit_band probabilities default is the 7-level PROBABILITIES (core.py:19, M-4)', () => {
  const p = paramOf(get('profit_band'), 'probabilities');
  // PROBABILITIES=(0.51,0.80,0.85,0.90,0.95,0.98,0.99) 実 7 水準（旧 catalog は [0.95,0.99]）
  assert.deepEqual(p.default, [0.51, 0.8, 0.85, 0.9, 0.95, 0.98, 0.99]);
});

// ----------------------------------------------------------------------------
// profit_band 未定義パラメータの追加（§4.2/§4.3・実シグネチャ準拠）。
// ----------------------------------------------------------------------------

test('catalog params: profit_band defines buckets ENUM_LIST default DEFAULT_BUCKETS (lwc_chart.py:51)', () => {
  const p = paramOf(get('profit_band'), 'buckets');
  assert.equal(p.type, ParamType.ENUM_LIST);
  assert.deepEqual(p.default, ['nOH', 'pOL', 'pOH', 'nOL']); // DEFAULT_BUCKETS
});

test('catalog params: profit_band defines require_full BOOL default true (lwc_chart.py:96)', () => {
  const p = paramOf(get('profit_band'), 'require_full');
  assert.equal(p.type, ParamType.BOOL);
  assert.equal(p.default, true);
});

test('catalog params: profit_band defines legend BOOL default false (lwc_chart.py:97)', () => {
  const p = paramOf(get('profit_band'), 'legend');
  assert.equal(p.type, ParamType.BOOL);
  assert.equal(p.default, false);
});

test('catalog params: profit_band defines normalize ENUM [return,atr] default return (lwc_chart.py:164)', () => {
  const p = paramOf(get('profit_band'), 'normalize');
  assert.equal(p.type, ParamType.ENUM);
  assert.deepEqual(p.enumValues, ['return', 'atr']);
  assert.equal(p.default, 'return');
});

test('catalog params: profit_band defines window default expanding (lwc_chart.py:165)', () => {
  const p = paramOf(get('profit_band'), 'window');
  assert.equal(p.default, 'expanding');
});

test('catalog params: profit_band defines atr_period INT default 14 enabled when normalize==atr (lwc_chart.py:166, robust_bands.py:135-138)', () => {
  const p = paramOf(get('profit_band'), 'atr_period');
  assert.equal(p.type, ParamType.INT);
  assert.equal(p.default, 14);
  // 条件付き有効化メタデータ（§3.5・normalize==atr のみ有効）
  assert.deepEqual(p.conditionalEnable, { when: { param: 'normalize', equals: 'atr' } });
});

test('catalog params: profit_band defines min_obs INT default 30 (lwc_chart.py:167)', () => {
  const p = paramOf(get('profit_band'), 'min_obs');
  assert.equal(p.type, ParamType.INT);
  assert.equal(p.default, 30);
});

// ----------------------------------------------------------------------------
// price_range_power 未定義パラメータの追加（§4.4・実シグネチャ準拠）。
// ----------------------------------------------------------------------------

test('catalog params: price_range_power defines range_from FLOAT default null (lwc_chart.py:41)', () => {
  const p = paramOf(get('price_range_power'), 'range_from');
  assert.equal(p.type, ParamType.FLOAT);
  assert.equal(p.default, null);
});

test('catalog params: price_range_power defines range_to FLOAT default null (lwc_chart.py:42)', () => {
  const p = paramOf(get('price_range_power'), 'range_to');
  assert.equal(p.type, ParamType.FLOAT);
  assert.equal(p.default, null);
});

test('catalog params: price_range_power defines width INT default 2 (lwc_chart.py:46)', () => {
  const p = paramOf(get('price_range_power'), 'width');
  assert.equal(p.type, ParamType.INT);
  assert.equal(p.default, 2);
});

test('catalog params: price_range_power defines bull_color/bear_color (COLOR) params (lwc_chart.py:44-45)', () => {
  const bull = paramOf(get('price_range_power'), 'bull_color');
  const bear = paramOf(get('price_range_power'), 'bear_color');
  assert.equal(bull.type, ParamType.COLOR);
  assert.equal(bear.type, ParamType.COLOR);
  // 既定は _BULL_COLOR / _BEAR_COLOR（lwc_chart.py:27-28）
  assert.equal(bull.default, 'rgba(46, 158, 91, 0.9)');
  assert.equal(bear.default, 'rgba(210, 67, 58, 0.9)');
});

// ----------------------------------------------------------------------------
// UI メタデータ後方互換: evaluate 挙動不変（既存制約は影響なし）。
// ----------------------------------------------------------------------------

test('catalog params: existing q-chain constraints survive UI-metadata extension (evaluate unaffected)', () => {
  const def = get('tgp_btlm');
  // q_low<q_high<1 の既存制約が残存（range_open ×2 + lt）
  const qlow = paramOf(def, 'q_low');
  const kinds = qlow.constraints.map((c) => c.kind);
  assert.ok(kinds.includes('range_open'));
  assert.ok(kinds.includes('lt'));
  // 既定値検証は緑（evaluate 単一定義・挙動不変）
  assert.deepEqual(def.validateParams({ fitter: 'ols', price: 'open', maxbars: 100, q_low: 0.05, q_high: 0.95 }), []);
});
