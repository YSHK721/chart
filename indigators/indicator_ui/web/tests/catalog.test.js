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

test('catalog: list returns the 26 registered indicators (基本4 + btlm_trail + btlm_trail_marod + ma_marod + cvfe + profit_* 15 + market_profile + tickvol_bands + tickvol)', () => {
  // Act
  const defs = list();
  // Assert: 既存4（tgp_btlm / profit_band / price_range_power / moving_averages）+ btlm_trail
  //   + btlm_trail_marod（新規・MAROD 別 pane オシレータ）+ profit_* 15 + market_profile = 22。
  const ids = defs.map((d) => d.id);
  for (const base of ['moving_averages', 'price_range_power', 'profit_band', 'tgp_btlm', 'btlm_trail', 'btlm_trail_marod', 'ma_marod']) {
    assert.ok(ids.includes(base), `missing ${base}`);
  }
  assert.equal(defs.length, 26);
});

test('catalog: btlm_trail_marod is a pane oscillator (source 8択 / maxbars min3 / color + 0% 基準線)', () => {
  const d = get('btlm_trail_marod');
  assert.equal(d.id, 'btlm_trail_marod');
  assert.equal(d.placement, 'pane');
  assert.equal(d.category.nameKey, 'cat.oscillator');
  // params は source / maxbars / q_low / q_high / q_out / k_events / event_agg / window_n /
  //   color（back golden 契約と対称・外れ値 3 パラメータは共有ビルダー EVQ_PARAMS）。
  //   timeframe（計算.時間足）は全指標共通のため REGISTRY 構築時に注入される（ISSUE-274）。
  assert.deepEqual(
    d.params.map((p) => p.name).sort(),
    ['color', 'event_agg', 'k_events', 'maxbars', 'q_high', 'q_low', 'q_out', 'source', 'timeframe', 'window_n'],
  );
  assert.equal(paramOf(d, 'source').type, ParamType.ENUM);
  assert.equal(paramOf(d, 'source').default, 'close');
  assert.deepEqual(paramOf(d, 'source').enumValues, ['close', 'open', 'high', 'low', 'hl2', 'hlc3', 'ohlc4', 'hlcc4']);
  assert.equal(paramOf(d, 'maxbars').type, ParamType.INT);
  assert.equal(paramOf(d, 'maxbars').default, 100);
  assert.equal(paramOf(d, 'q_low').default, 0.05);
  assert.equal(paramOf(d, 'q_high').default, 0.95);
  // 外れ値イベント分位 3 パラメータ（ma_marod と対称・共有ビルダー）。
  assert.equal(paramOf(d, 'q_out').default, 0.99);
  assert.equal(paramOf(d, 'k_events').default, 50);
  assert.equal(paramOf(d, 'event_agg').default, 'episode');
  assert.deepEqual(paramOf(d, 'event_agg').enumValues, ['episode', 'bar']);
  assert.equal(paramOf(d, 'window_n').type, ParamType.INT);
  assert.equal(paramOf(d, 'window_n').default, 500);
  assert.equal(paramOf(d, 'color').type, ParamType.COLOR);
  // 系列: MAROD line ＋ 0% 水平基準線 ＋ 分位バンド（動的）＋ イベント分位水準線 4 本。
  //   σ バンドは描画廃止（認知負荷削減・ユーザー裁定 2026-07-21）。
  const seriesNames = d.series.map((s) => s.seriesName);
  assert.deepEqual(seriesNames, [
    'btlm_trail_marod', 'btlm_trail_marod', null,
    'btlm_trail_marod_evq_med_hi', 'btlm_trail_marod_evq_med_lo',
    'btlm_trail_marod_evq_ext_hi', 'btlm_trail_marod_evq_ext_lo',
  ]);
  // 動的分位 SeriesDef（btlm_trail_marod_q{pct}）が存在する。
  const dyn = d.series.find((s) => s.dynamic && s.seriesNamePattern);
  assert.ok(dyn, '動的分位 SeriesDef が存在する');
  assert.equal(dyn.seriesNamePattern.template, 'btlm_trail_marod_q{pct}');
  assert.equal(d.compute.computeId, 'btlm_trail_marod');
});

test('catalog: ma_marod is a pane oscillator (source 8択 / ma_type 4択 / length min2 / color + 0% 基準線)', () => {
  const d = get('ma_marod');
  assert.equal(d.id, 'ma_marod');
  assert.equal(d.placement, 'pane');
  assert.equal(d.category.nameKey, 'cat.oscillator');
  // params は source / ma_type / length / q_low / q_high / q_out / k_events / event_agg /
  //   window_n / color（back golden 契約と対称）＋ 注入される timeframe（ISSUE-274）。
  assert.deepEqual(
    d.params.map((p) => p.name).sort(),
    ['color', 'event_agg', 'k_events', 'length', 'ma_type', 'q_high', 'q_low', 'q_out', 'source', 'timeframe', 'window_n'],
  );
  assert.equal(paramOf(d, 'source').type, ParamType.ENUM);
  assert.equal(paramOf(d, 'source').default, 'close');
  assert.deepEqual(paramOf(d, 'source').enumValues, ['close', 'open', 'high', 'low', 'hl2', 'hlc3', 'ohlc4', 'hlcc4']);
  // 基準線 MA: moving_averages と同一 4 択・既定 ema（計算の原子＝ソース解決も同期・§2.1）。
  assert.equal(paramOf(d, 'ma_type').type, ParamType.ENUM);
  assert.equal(paramOf(d, 'ma_type').default, 'ema');
  assert.deepEqual(paramOf(d, 'ma_type').enumValues, ['sma', 'ema', 'smma', 'lwma']);
  // source/ma_type のラベルは moving_averages と同一オブジェクト（同期の恒久固定）。
  const ma = get('moving_averages');
  assert.equal(paramOf(d, 'source').enumLabels, paramOf(ma, 'source').enumLabels);
  assert.equal(paramOf(d, 'ma_type').enumLabels, paramOf(ma, 'ma_type').enumLabels);
  assert.equal(paramOf(d, 'length').type, ParamType.INT);
  assert.equal(paramOf(d, 'length').default, 50);
  assert.equal(paramOf(d, 'q_low').default, 0.05);
  assert.equal(paramOf(d, 'q_high').default, 0.95);
  // 外れ値イベント分位（極端分位 q_out 既定 0.99・直近イベント K 既定 50・裁定 2026-07-21）。
  assert.equal(paramOf(d, 'q_out').type, ParamType.FLOAT);
  assert.equal(paramOf(d, 'q_out').default, 0.99);
  assert.equal(paramOf(d, 'k_events').type, ParamType.INT);
  assert.equal(paramOf(d, 'k_events').default, 50);
  // 集計単位（episode＝エピソード極値が既定・bar＝旧方式へ切替可能・裁定 2026-07-21）。
  assert.equal(paramOf(d, 'event_agg').type, ParamType.ENUM);
  assert.equal(paramOf(d, 'event_agg').default, 'episode');
  assert.deepEqual(paramOf(d, 'event_agg').enumValues, ['episode', 'bar']);
  assert.equal(paramOf(d, 'window_n').type, ParamType.INT);
  assert.equal(paramOf(d, 'window_n').default, 500);
  assert.equal(paramOf(d, 'color').type, ParamType.COLOR);
  assert.equal(paramOf(d, 'color').default, 'rgba(255, 152, 0, 1)');
  // 系列: MA_MAROD line ＋ 0% 水平基準線 ＋ 正常バンド（動的）＋ イベント分位水準線 4 本
  //   （{med|ext} × {hi|lo}）。σ バンド・_all 系列は描画廃止（認知負荷削減・裁定 2026-07-21）。
  const seriesNames = d.series.map((s) => s.seriesName);
  assert.deepEqual(seriesNames, [
    'ma_marod', 'ma_marod', null,
    'ma_marod_evq_med_hi', 'ma_marod_evq_med_lo',
    'ma_marod_evq_ext_hi', 'ma_marod_evq_ext_lo',
  ]);
  // 動的分位 SeriesDef（ma_marod_q{pct}）が存在する。
  const dyn2 = d.series.find((s) => s.dynamic && s.seriesNamePattern);
  assert.ok(dyn2, '動的分位 SeriesDef が存在する');
  assert.equal(dyn2.seriesNamePattern.template, 'ma_marod_q{pct}');
  assert.equal(d.compute.computeId, 'ma_marod');
});

// ma_marod 棒グラフ（btlm_trail_marod 案A と同一の非波及ゲート）: line 系列のみ barStyleEditable=true。
test('catalog: ma_marod の line 系列は barStyleEditable=true・水平線は false', () => {
  const d = get('ma_marod');
  assert.equal(d.series[0].kind, 'line');
  assert.equal(d.series[0].barStyleEditable, true, 'MA_MAROD line は棒スタイル編集可');
  assert.equal(d.series[1].barStyleEditable, false, '水平基準線は非対象');
});

// 案A（MAROD 棒グラフ）: MAROD line SeriesDef のみ barStyleEditable=true（スタイルタブで棒切替）。
//   0% 水平基準線・他指標系列は false（非波及ゲート・SeriesDef 既定 false）。
test('catalog: btlm_trail_marod の line 系列は barStyleEditable=true・水平線と他指標は false', () => {
  const marod = get('btlm_trail_marod');
  // series[0] = MAROD line（棒切替対象）、series[1] = 0% 水平基準線（非対象）。
  assert.equal(marod.series[0].kind, 'line');
  assert.equal(marod.series[0].barStyleEditable, true, 'MAROD line は棒スタイル編集可');
  assert.equal(marod.series[1].barStyleEditable, false, '水平基準線は非対象');
  // 他指標（moving_averages）は未付与＝既定 false（非波及）。
  const ma = get('moving_averages');
  for (const s of ma.series) {
    assert.equal(s.barStyleEditable, false, `${s.seriesName ?? '(dynamic)'} は barStyleEditable=false`);
  }
  // btlm_trail の mean/分位線は pointStyleEditable のみで barStyleEditable=false（棒対象外）。
  const trail = get('btlm_trail');
  for (const s of trail.series) {
    assert.equal(s.barStyleEditable, false);
  }
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
});

// market_profile に src（集計原子）ENUM を追加。candle=足レンジ TPO（既定・後方互換）/
//   dwell=実ティック滞在 / m1=tick数（試作 prototype_260630-01 の count 経路を移植）。
//   bins/va/limit と同じ group（プロパティダイアログの ENUM ドロップダウン）。
test('catalog: market_profile exposes src ENUM [dwell,zp] default zp（candle/m1 は非表示） with jp labels', () => {
  const d = get('market_profile');
  const src = paramOf(d, 'src');
  assert.ok(src, 'src param exists');
  assert.equal(src.type, ParamType.ENUM);
  assert.equal(src.default, 'zp');  // 依頼者指示 2026-07-12: zp を既定へ昇格
  assert.deepEqual(src.enumValues, ['dwell', 'zp']);  // candle/m1 は非表示（依頼者指示 2026-07-12・backend は温存）
  assert.equal(src.enumLabels.candle, undefined, 'candle ラベル撤去（非表示）');
  assert.equal(src.enumLabels.m1, undefined, 'm1 ラベル撤去（非表示）');
  assert.equal(src.enumLabels.dwell, '滞在時間(実ティック)');
  assert.equal(src.enumLabels.zp, '超過占有z(p)');
  // dispbp/va と同じ group（同一セクションに並ぶ）。
  assert.equal(src.group, paramOf(d, 'dispbp').group);
  // ISSUE-080: 日別×1m/5m の zp は**選択不可**（代替粒度を出さない・依頼者裁定 2026-07-15）。
  assert.ok(typeof src.tooltip === 'string' && src.tooltip.includes('選択不可'),
    'src tooltip が zp の非対応組合せ（日別×1m/5m）を明記する');
  // optionEnable 述語: zp は sessions×1m/5m でのみ無効。dwell は常に有効。
  assert.equal(typeof src.optionEnable, 'function');
  const oe = src.optionEnable;
  assert.equal(oe('zp', { mode: 'sessions' }, { timeframe: '1m' }), false, '日別×1m は zp 選択不可');
  assert.equal(oe('zp', { mode: 'sessions' }, { timeframe: '5m' }), false, '日別×5m は zp 選択不可');
  assert.equal(oe('zp', { mode: 'sessions' }, { timeframe: '15m' }), true, '日別×15m は可');
  assert.equal(oe('zp', { mode: 'normal' }, { timeframe: '1m' }), true, '通常×1m は可（全期間/当日 z）');
  assert.equal(oe('zp', { mode: 'sessions' }, null), true, 'ctx 不在（A方式/テスト）は制限しない');
  assert.equal(oe('dwell', { mode: 'sessions' }, { timeframe: '1m' }), true, 'dwell は常に可');
});

// market_profile の表示モード（mode）ENUM segmented トグル。旧 replay/sessions の 2 チェックを
//   1 つの排他トグルへ統合（解像度トグル resmode と同方式）。
//   既定 'normal'・label '表示モード'・controlType 'segmented'・表示系 group（bins と別）。
//   ISSUE-082: リプレイモードは present から撤去（replay_ui 専用へ）。ENUM は [通常｜日別プロファイル] の 2 モード。
test('catalog: market_profile exposes mode ENUM [normal,sessions] default normal as segmented toggle', () => {
  const d = get('market_profile');
  const mode = paramOf(d, 'mode');
  assert.ok(mode, 'mode param exists');
  assert.equal(mode.type, ParamType.ENUM);
  assert.equal(mode.default, 'normal');
  // Phase5（統一成長）: 旧 'ticklive' セグメント（表示選択肢）は撤去。足内 1tick 逐次成長は表示モードでなく
  //   成長軸（growing 信号）が担う（直交化）＝normal/sessions のいずれでも成長する。
  assert.deepEqual(mode.enumValues, ['normal', 'sessions']);
  assert.equal(mode.label, '表示モード');
  assert.equal(mode.controlType, 'segmented');
  assert.equal(mode.enumLabels.normal, '通常');
  assert.equal(mode.enumLabels.sessions, '日別プロファイル');
  assert.equal(mode.enumLabels.replay, undefined, 'リプレイセグメントは撤去（ISSUE-082）');
  assert.equal(mode.enumLabels.ticklive, undefined, 'ticklive セグメントは撤去（表示選択肢なし）');
  // 表示系 group（計算系 group.calc とは別＝dispbp と同じ group ではない）。
  assert.notEqual(mode.group, paramOf(d, 'dispbp').group);
});

// 統合により旧 replay / sessions の BOOL param は catalog から撤去された（mode に一本化）。
test('catalog: market_profile no longer exposes replay/sessions BOOL params (統合)', () => {
  const d = get('market_profile');
  assert.equal(paramOf(d, 'replay'), undefined, 'replay param は撤去された');
  assert.equal(paramOf(d, 'sessions'), undefined, 'sessions param は撤去された');
});

// market_profile に resmode（解像度）ENUM を追加。試作 prototype_260630-01 の解像度トグル
//   （ビン ⇄ レンジ）を移植。segmented（横並びセグメントボタン）で描画し、押した側の入力だけ表示する。


// market_profile の period（期間・ISSUE-071 (b)案）ENUM [all,day] 既定 all。src=zp のときのみ表示し、
//   通常モード×固定周期 tf でのみ有効（conditionalEnable は関数述語）。
test('catalog: market_profile exposes period ENUM [all,day] default all, visible only for src=zp', () => {
  const d = get('market_profile');
  const period = paramOf(d, 'period');
  assert.ok(period, 'period param exists');
  assert.equal(period.type, ParamType.ENUM);
  assert.equal(period.default, 'all');
  assert.deepEqual(period.enumValues, ['all', 'day']);
  assert.equal(period.label, '期間');
  assert.equal(period.enumLabels.all, '全期間');
  assert.equal(period.enumLabels.day, '当日');
  // ISSUE-081: zp×通常×対応 tf のときだけ**表示**（旧: src で表示＋mode/tf でグレーアウト）。
  assert.equal(period.conditionalEnable, null, 'グレーアウト述語は廃止');
  const vis = period.conditionalVisible;
  assert.equal(typeof vis, 'function');
  assert.equal(vis({ src: 'zp', mode: 'normal' }, { timeframe: '1m' }), true, 'zp×通常×1m は表示');
  assert.equal(vis({ src: 'dwell', mode: 'normal' }, { timeframe: '1m' }), false, 'dwell は非表示');
  assert.equal(vis({ src: 'zp', mode: 'sessions' }, { timeframe: '1h' }), false, '日別は非表示');
  // ISSUE-086: 全時間足統一＝1W/1M でも期間を表示（「当日」窓はチャート tf と独立に定義できる）。
  assert.equal(vis({ src: 'zp', mode: 'normal' }, { timeframe: '1W' }), true, '1W も表示（統一）');
  assert.equal(vis({ src: 'zp', mode: 'normal' }, { timeframe: '1M' }), true, '1M も表示（統一）');
  assert.equal(vis({ src: 'zp', mode: 'normal' }, null), true, 'ctx 不在（A方式/テスト）は mode/src 条件のみ');
  assert.equal(period.group, paramOf(d, 'dispbp').group);
});

// ISSUE-070→081: 表示幅(bp) は tf-period が日別列を描くとき（列は固定生解像度＝bp が効かない）
//   **行ごと非表示**（グレーアウト廃止・依頼者指示）。
test('catalog: market_profile dispbp は tf-period描画時に非表示（ISSUE-081）', () => {
  const d = get('market_profile');
  const dispbp = paramOf(d, 'dispbp');
  assert.equal(dispbp.conditionalEnable, null, 'グレーアウト述語は廃止');
  assert.equal(typeof dispbp.conditionalVisible, 'function');
  const fn = dispbp.conditionalVisible;
  assert.equal(fn({ mode: 'sessions', src: 'dwell' }, { timeframe: '1h' }), false);
  assert.equal(fn({ mode: 'sessions', src: 'zp' }, { timeframe: '1h' }), false);
  assert.equal(fn({ mode: 'normal', src: 'dwell' }, { timeframe: '1h' }), true);
  // ISSUE-086: 1W/1M もバケット列を描くため日別では非表示（他 tf と統一）。
  assert.equal(fn({ mode: 'sessions', src: 'dwell' }, { timeframe: '1W' }), false);
  assert.equal(fn({ mode: 'sessions', src: 'dwell' }, { timeframe: '1M' }), false);
  assert.equal(fn({ mode: 'sessions', src: 'zp' }, { timeframe: '5m' }), true);
  assert.equal(fn({ mode: 'sessions', src: 'dwell' }, {}), true);
});

// 回帰防止（ISSUE-286）: 「時間足の確定を待つ」は撤去した。最終足の除外は offset=1 と同義で
//   概念が重複し、上位足計算は投影側が期間で使い分けるようになったため選択自体が不要になった。
test('catalog: moving_averages は撤去済みの wait_for_close を持たない', () => {
  const d = get('moving_averages');
  assert.equal(d.params.some((p) => p.name === 'wait_for_close'), false);
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
});


// 日別プロファイルは mode='sessions' へ統合済み（旧 sessions BOOL は撤去）。
//   mode ENUM の enumLabels.sessions='日別プロファイル' 検証は上位の mode テストで担保する。

// calc 群の表示順（依頼者指示 2026-07-15）: ソース → バリューエリア → 期間 → 表示幅(bp)。
test('catalog: market_profile calc 群の order は ソース<バリューエリア<期間<表示幅(bp)', () => {
  const d = get('market_profile');
  const o = (n) => paramOf(d, n).order;
  assert.ok(o('src') < o('va') && o('va') < o('period') && o('period') < o('dispbp'),
    `src=${o('src')} va=${o('va')} period=${o('period')} dispbp=${o('dispbp')}`);
});
