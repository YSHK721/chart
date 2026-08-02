// 期間フラグ（isPeriod）の付与範囲とコントロール既定の検証（node:test / node:assert）。
//
// 対象: usecase/catalog.js（isPeriod 付与）／usecase/form_model.js（FieldDesc への透過・controlType 既定）。
// 設計入力: 基本設計_期間プリセット.md v0.1.0 §5.1 判定規則 / §5.2 対象一覧 / §5.3 対象外。
// 構造: Arrange-Act-Assert（AAA）。

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { get, list } from '../js/usecase/catalog.js';
import { buildFormModel } from '../js/usecase/form_model.js';
import { ParamType } from '../js/domain/constraint_eval.js';

// 設計書 §5.2 の対象一覧（指標 → 期間パラメータ名）。ここが仕様の固定点。
const EXPECTED_PERIOD_PARAMS = {
  tgp_btlm: ['maxbars'],
  btlm_trail: ['maxbars', 'empirical_n', 'n_cov'],
  btlm_trail_marod: ['maxbars', 'window_n'],
  ma_marod: ['length', 'window_n'],
  // cvfe: 公開する窓は n_har のみ（認知負荷の最小化・2026-07-30 で 14→6 パラメータへ削減）。
  //   window_n は外れ値判定の内部しきい値として非公開化した（対応する線を持たないため）。
  cvfe: ['n_har'],
  moving_averages: ['length', 'smoothing_length'],
  profit_band: ['atr_period'],
  profit_adx_needle: ['period', 'window'],
  profit_arctan: ['period', 'window'],
  profit_mfi: ['mfi_period', 'ma_period'],
  profit_rsi: ['rsi_period'],   // ma_period は削除済み（承認 2026-08-02）
  profit_stc: ['period'],
  profit_oscillator: ['period_a', 'period_b', 'window'],
  profit_oscillator2: ['osc_period', 'stc_slow', 'ma_period', 'rci_period'],
  profit_osi_ma: ['ma_period'],
  profit_rmm: ['osc_period', 'ma_period', 'window'],
  profit_volatility: ['period', 'window'],
  profit_hl_band: ['window'],
  profit_hlband: [],
  profit_mfi_macd: ['mfi_period', 'fast', 'slow', 'signal'],
  profit_rmm_macd: ['osc_period', 'ma_period', 'fast', 'slow', 'signal', 'window'],
  profit_rsi_macd: ['rsi_period', 'fast', 'slow', 'signal'],
  price_range_power: [],
  market_profile: [],
  // 取引密度帯: sessions は「参照セッション日数」でバー本数ではない＝期間パラメータではない。
  tickvol_bands: [],
  // ティックボリューム: バー本数を意味する窓が期間パラメータ（水準の閾値窓のみ。回帰トレンドは
  //   ISSUE-244 で UI から外した）。分位（q_low/q_high/q_out）と観測件数（k_events）は対象外。
  tickvol: ['window_n'],
};

function periodParamsOf(id) {
  const def = get(id);
  assert.ok(def, `カタログに ${id} が無い`);
  return (def.params ?? []).filter((p) => p.isPeriod === true).map((p) => p.name);
}

test('isPeriod の付与範囲が設計書 §5.2 の一覧と完全一致する', () => {
  for (const [id, expected] of Object.entries(EXPECTED_PERIOD_PARAMS)) {
    assert.deepEqual(periodParamsOf(id), expected, `${id} の期間パラメータが設計書と不一致`);
  }
});

test('カタログの全指標が §5.2 の一覧に列挙されている（指標追加時の付け忘れ検出）', () => {
  const known = new Set(Object.keys(EXPECTED_PERIOD_PARAMS));
  const missing = list().map((d) => d.id).filter((id) => !known.has(id));
  assert.deepEqual(missing, [], '新しい指標が期間フラグの棚卸し対象から漏れている');
});

test('対象外パラメータ（§5.3）に isPeriod が付かない', () => {
  // 件数・線幅・最小観測数・イベント件数・オフセットは期間ではない。
  const cases = [
    ['price_range_power', 'top_n'],
    ['price_range_power', 'width'],
    ['profit_band', 'min_obs'],
    ['ma_marod', 'k_events'],
    ['btlm_trail_marod', 'k_events'],
    ['moving_averages', 'offset'],
  ];
  for (const [id, name] of cases) {
    const p = get(id).params.find((x) => x.name === name);
    assert.ok(p, `${id}.${name} が見つからない`);
    assert.notEqual(p.isPeriod, true, `${id}.${name} に isPeriod が付いている`);
  }
});

test('FLOAT 系パラメータに isPeriod が付かない', () => {
  for (const def of list()) {
    for (const p of def.params ?? []) {
      if (p.isPeriod === true) {
        assert.equal(p.type, ParamType.INT, `${def.id}.${p.name} は INT ではないのに isPeriod が付いている`);
      }
    }
  }
});

test('buildFormModel: isPeriod のパラメータは controlType が period になる', () => {
  const model = buildFormModel(get('ma_marod'), {});
  const length = model.fields.find((f) => f.name === 'length');
  assert.equal(length.controlType, 'period');
  assert.equal(length.isPeriod, true);
  // min は FieldDesc へ透過している（プリセット絞り込みに使う）。
  assert.equal(length.min, 2);
});

test('buildFormModel: isPeriod でない INT は従来どおり number のまま', () => {
  const model = buildFormModel(get('price_range_power'), {});
  const topN = model.fields.find((f) => f.name === 'top_n');
  assert.equal(topN.controlType, 'number');
  assert.equal(topN.isPeriod, false);
});

test('buildFormModel: 明示 controlType は isPeriod より優先される', () => {
  const def = { params: [{ name: 'x', type: ParamType.INT, default: 1, isPeriod: true, controlType: 'number' }] };
  const model = buildFormModel(def, {});
  assert.equal(model.fields[0].controlType, 'number');
});
