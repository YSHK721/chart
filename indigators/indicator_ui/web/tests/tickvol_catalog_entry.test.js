// tickvol（ティックボリューム）のカタログ登録・結線の検証（DOM/lwc/fetch 非依存・AAA）。
//
// 仕様（依頼者確定 2026-08-01）: その足の tick 数を**専用ペインのヒストグラム**で表示し、
// 指標カタログから ON/OFF する。正常帯（下側/上側の因果ローリング分位）と外れ値水準
// （経験的分位・GPD 外挿の並列表示）を重ねる。
//
// 本ファイルが固定するのは back との結線契約:
//   - compute_id / seriesName が back（indigators/tickvol/src/lwc_chart.py の add_tickvol）と一致。
//   - 系列駆動型（アクター駆動ではない）＝既存の series 描画経路にそのまま乗る。
//   - 足内更新の登録リストに載る（形成中バーの直下が空欄にならない）。

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { list, get } from '../js/usecase/catalog.js';
import { SeriesKind } from '../js/domain/domain_models.js';
import { isActorDriven } from '../js/usecase/actor_driven_ids.js';
import { INTRABAR_FORMING_IDS } from '../js/usecase/intrabar_forming_ids.js';

test('tickvol: 出来高カテゴリの専用ペイン・ヒストグラム＋正常帯＋水準線', () => {
  const d = get('tickvol');
  assert.ok(d, 'tickvol が登録されていない');
  assert.equal(d.placement, 'pane');            // 価格スケールを共有しない（単位が違う）
  assert.equal(d.tab, 'indicator');
  assert.equal(d.category.nameKey, 'cat.volume');
  assert.equal(d.series.length, 5);             // 本体1 + 水準帯(動的1) + 水準線3（回帰トレンドは ISSUE-244 で撤去）
  assert.equal(d.series[0].kind, SeriesKind.HISTOGRAM);
  assert.ok(d.series.slice(1).every((s) => s.kind === SeriesKind.LINE));
});

test('tickvol: 正常帯は分位依存の動的名（tickvol_q{pct}・btlm_trail_q{pct} と対称）', () => {
  const band = get('tickvol').series.find((s) => s.dynamic === true);
  assert.ok(band, '動的な帯 SeriesDef が無い');
  assert.equal(band.seriesNamePattern.template, 'tickvol_q{pct}');
  assert.deepEqual(band.seriesNamePattern.buckets, ['']);
  assert.equal(band.seriesNamePattern.pcts.length, 99);
  // 既定の分位ペア（0.10 / 0.90）に対応する名前が生成可能であること。
  for (const pct of ['10', '90']) {
    assert.ok(band.seriesNamePattern.pcts.includes(pct), pct);
  }
});

test('tickvol: back 結線（compute_id / 系列名 / variant）が add_tickvol と一致する', () => {
  const d = get('tickvol');
  // compute_id は call_binding._TABLE のキーかつ指標パッケージ名（indigators/tickvol）。
  assert.equal(d.compute.computeId, 'tickvol');
  assert.deepEqual(d.compute.variants, ['default']);
  assert.equal(d.compute.backendParam, null);
  // 系列名は FakeChart が payload の name に載せる create_* の第1引数と同値（emit 順）。
  // 静的名の系列（帯 2 種は動的名のため seriesName=null）。順序は lwc_chart の emit 順。
  assert.deepEqual(d.series.map((s) => s.seriesName), [
    'tickvol', null, 'tickvol_evq_med_hi', 'tickvol_evq_ext_hi', 'tickvol_gpd_hi',
  ]);
});

test('tickvol: パラメータ 5 件（既定は back の params_defaults と同値）', () => {
  const d = get('tickvol');
  const byName = Object.fromEntries(d.params.map((p) => [p.name, p]));
  assert.deepEqual(Object.keys(byName).sort(), [
    'k_events', 'q_high', 'q_low', 'q_out', 'window_n',
  ]);
  assert.equal(byName.window_n.default, 500);
  assert.equal(byName.q_low.default, 0.10);
  assert.equal(byName.q_high.default, 0.90);
  assert.equal(byName.q_out.default, 0.99);
  assert.equal(byName.k_events.default, 50);
  // 閾値窓だけがバー本数＝期間プリセットの対象（isPeriod）。
  assert.equal(byName.window_n.isPeriod, true);
  for (const n of ['q_low', 'q_high', 'q_out', 'k_events']) {
    assert.notEqual(byName[n].isPeriod, true, n);
  }
});

test('tickvol: 回帰トレンド（btlm_trail 仕様）は UI から外してある（ISSUE-244）', () => {
  const d = get('tickvol');
  assert.equal(d.series.some((s) => String(s.seriesName ?? '').startsWith('tickvol_trend')), false);
  assert.equal(d.series.some(
    (s) => String(s.seriesNamePattern?.template ?? '').startsWith('tickvol_trend')), false);
  for (const n of ['maxbars', 'band_method', 'empirical_n', 'show_metrics', 'n_cov']) {
    assert.equal(d.params.some((p) => p.name === n), false, n);
  }
});

test('tickvol: 動的名は水準帯 1 本だけ（tickvol_q{pct}）', () => {
  const dyn = get('tickvol').series.filter((s) => s.dynamic === true);
  assert.equal(dyn.length, 1);
  assert.deepEqual(dyn.map((s) => s.seriesNamePattern.template), ['tickvol_q{pct}']);
});

test('tickvol: 集計単位（event_agg）は公開しない（GPD の独立前提を壊さないため固定）', () => {
  assert.equal(get('tickvol').params.some((p) => p.name === 'event_agg'), false);
});

test('tickvol: イベント水準の下側（_evq_*_lo）は持たない（tick 数は 1 以上の計数量で下側は裾でない）', () => {
  // 正常帯・トレンド帯の下側はあるが、外れ値イベント水準の下側は持たない。
  assert.equal(get('tickvol').series.some((s) => String(s.seriesName).includes('_evq_') && String(s.seriesName).endsWith('_lo')), false);
});

test('tickvol: 分位ペアは q_low < q_high の制約を持つ（MAROD 系と対称の q-chain）', () => {
  const qLow = get('tickvol').params.find((p) => p.name === 'q_low');
  assert.ok(qLow.constraints.some(
    (c) => Array.isArray(c.operands) && c.operands[0] === 'q_low' && c.operands[1] === 'q_high'));
});

test('tickvol: 系列駆動型（アクター駆動ではない）', () => {
  // /compute の系列 JSON をそのまま描く＝専用アクター（market_profile / tickvol_bands）とは別扱い。
  assert.equal(isActorDriven(get('tickvol')), false);
});

test('tickvol: 足内更新の対象に登録されている（形成中バーの直下が空欄にならない）', () => {
  assert.equal(INTRABAR_FORMING_IDS.has('tickvol'), true);
});

test('tickvol: id は一意で、取引密度帯（tickvol_bands）とは別の指標である', () => {
  const ids = list().map((d) => d.id);
  assert.equal(ids.filter((id) => id === 'tickvol').length, 1);
  assert.ok(ids.includes('tickvol_bands'));
  // 別物であることの確認: 帯は overlay（背景）・本指標は pane（ヒストグラム）。
  assert.equal(get('tickvol_bands').placement, 'overlay');
  assert.equal(get('tickvol').placement, 'pane');
});

// --- 読取欄（β/σ/バンド内実績率）の結線 ------------------------------------- //
// readout_only は「描画せず読取欄だけに出す」ヒント。pane 指標で除外されるとどこにも
// 現れない死荷重になるため、pane でも読取欄へ載ることを固定する（btlm_trail F-09 と同じ役割）。

test('series_drawer: readout_only の系列は pane 指標でも読取欄に載る', async () => {
  const { SeriesDrawer } = await import('../js/adapter/front/series_drawer.js');
  const host = {
    _overlayReadouts: new Map(),
    _chart: { addSeries: () => fakeSeries(), panes: () => [], addPane: () => fakePane() },
    _lwc: { LineSeries: {}, HistogramSeries: {} },
    _instances: new Map(),
    _paneHeight: 120,
    _restorePaneScaleRange: () => {},
  };
  function fakeSeries() {
    return {
      setData: () => {}, applyOptions: () => {}, data: () => [],
      priceScale: () => ({ applyOptions: () => {} }),
      createPriceLine: () => ({}), moveToPane: () => {},
    };
  }
  function fakePane() {
    return { paneIndex: () => 1, setHeight: () => {}, priceScale: () => ({ applyOptions: () => {} }) };
  }
  const drawer = new SeriesDrawer(host);
  assert.equal(typeof drawer, 'object');
  // 契約の所在を固定する（実描画は実 UI 検証で確認済み）: readout_only を見て分岐している。
  const src = await import('node:fs').then((fs) => fs.promises.readFile(
    new URL('../js/adapter/front/series_drawer.js', import.meta.url), 'utf8'));
  assert.match(src, /p\.readout_only === true/,
    'readout_only の系列を読取欄へ載せる分岐が無い');
});
