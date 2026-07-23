// 指標レジストリ（usecase/catalog.js）。
//
// 実在 4 バインディング（tgp_btlm / profit_band global,robust / price_range_power）の
// IndicatorDef を最低限定義し、list() / get(id) を提供する（§3.1.3）。
// 検索・フィルタ（UC-01）は id/display_name を対象とし series/params は読まない（§4.6）。
// 架空指標は足さない（実在バインディング中心）。

import { ConstraintKind, ParamType } from '../domain/constraint_eval.js';
import { IndicatorDef, SeriesDef, SeriesKind } from '../domain/domain_models.js';
import { makeMarketProfileDef } from './catalog_entry.js';

const OHLC = ['open', 'high', 'low', 'close'];

// ParamDef（JS plain object）を生成する。
//
// 必須引数 name/type/def は従来どおり。constraints/enumValues も従来位置を維持。
// 第 6 引数 ui は UI 向けメタデータ（純 UI 情報・後方互換）。省略時は従来同値で、
// ConstraintEvaluator.evaluate は ui フィールドを参照しない（constraint_eval.js:53-67）
// ため evaluate 挙動は不変（§3.3.3 移行方針・C-3）。
//
// ui のオプション: group / controlType / tooltip / unit / step / min / max /
//                  conditionalEnable / conditionalVisible / order / uiVisible。すべて末尾 default 付き。
// ui メタデータの既定値（省略キーは従来挙動）。pick() で一括解決し ?? null 連鎖を集約する。
const UI_DEFAULTS = Object.freeze({
  group: null,
  controlType: null,
  tooltip: null,
  unit: null,
  step: null,
  min: null,
  max: null,
  conditionalEnable: null,
  // optionEnable: ENUM option 単位の有効述語 (value, values, ctx)->bool（ISSUE-080・select の
  //   option を個別に disabled にする。行全体の conditionalEnable と直交）。
  optionEnable: null,
  // conditionalVisible: 条件付き“表示”（トグル）。conditionalEnable（グレーアウト）と対称で、
  //   { when: { param, equals } } が偽のとき当該フィールド行を非表示にする（form_model.computeVisible）。
  conditionalVisible: null,
  order: null,
  uiVisible: true,
  // label: フィールド表示名の直接指定（日本語ラベル。省略時は labelKey 末尾を表示）。
  label: null,
  // enumLabels: enum 値 → 表示名のマップ（select の選択肢を日本語表示する。省略時は値をそのまま表示）。
  enumLabels: null,
});

// ui オブジェクトから UI_DEFAULTS のキーのみを既定フォールバック付きで抽出する。
function pickUi(ui) {
  const out = {};
  for (const key of Object.keys(UI_DEFAULTS)) {
    out[key] = ui[key] ?? UI_DEFAULTS[key];
  }
  return out;
}

function param(name, type, def, constraints = [], enumValues = null, ui = {}) {
  return {
    name,
    labelKey: `label.${name}`,
    type,
    default: def,
    enumValues,
    constraints,
    // --- UI 向けメタデータ（任意・省略時は従来挙動・pickUi で ?? 連鎖を集約）---
    ...pickUi(ui),
  };
}

// --- tgp_btlm（回帰チャネル・OVERLAY）-------------------------------------
const TGP_BTLM = new IndicatorDef({
  id: 'tgp_btlm',
  displayNameKey: 'ind.tgp_btlm',
  category: { group: 'builtin', nameKey: 'cat.technical' },
  tab: 'indicator',
  placement: 'overlay',
  params: [
    param('fitter', ParamType.ENUM, 'ols', [], ['ols', 'tgp'], { group: 'group.calc', order: 1 }),
    // price（ソース）: add_btlm の price="open"（lwc_chart.py:68・core.py 既定 open）。
    // 8 択化（kind-twirling-hollerith.md §4）: 既存 4 択（open/high/low/close・既定 open・byte 不変）に
    // 合成 4 択（hl2/hlc3/ohlc4/hlcc4）を追加拡張。合成ソースは結線層（call_binding._resolve_btlm_price）が
    // 共有 applied_price で解決する（tgp_btlm src は無改変）。moving_averages の source と同一写像。
    param('price', ParamType.ENUM, 'open', [], ['open', 'high', 'low', 'close', 'hl2', 'hlc3', 'ohlc4', 'hlcc4'], { group: 'group.calc', order: 2 }),
    // maxbars 既定 40→100 是正（M-1・core.py:33 DEFAULT_MAXBARS=100）。
    param('maxbars', ParamType.INT, 100, [{ kind: ConstraintKind.MIN_VALUE, operands: ['maxbars', 1], messageKey: 'err.maxbars' }], null, { group: 'group.calc', order: 3, step: 1, min: 1, unit: 'unit.bars' }),
    param('q_low', ParamType.FLOAT, 0.05, [
      { kind: ConstraintKind.RANGE_OPEN, operands: [0, 'q_low', 1], messageKey: 'err.q_low.range' },
      { kind: ConstraintKind.LT, operands: ['q_low', 'q_high'], messageKey: 'err.q_order' },
    ], null, { group: 'group.calc', order: 4, step: 0.01, min: 0, max: 1 }),
    param('q_high', ParamType.FLOAT, 0.95, [
      { kind: ConstraintKind.RANGE_OPEN, operands: [0, 'q_high', 1], messageKey: 'err.q_high.range' },
    ], null, { group: 'group.calc', order: 5, step: 0.01, min: 0, max: 1 }),
    // MCMC サンプル（帯の安定性）: fitter='tgp'(btlm) のみ有効。サンプル増で分位帯が収束し
    // 安定するが計算は重くなる。standard=既定(BTE Total 15000)/high(30000)/max(60000)。
    // fitter='ols' は解析解のため無視される（backend _fitter_factory で吸収）。
    // 既定 'standard' は backend call_binding._DEFAULT_SAMPLES と一致必須（乖離防止）。
    param('mcmc_samples', ParamType.ENUM, 'standard', [], ['standard', 'high', 'max'], { group: 'group.calc', order: 6 }),
    // color は COLOR＝スタイルタブへ移譲（§4.1）。既定は実コード add_btlm の
    // color=_COLOR（MediumSlateBlue・lwc_chart.py:33,73）。
    param('color', ParamType.COLOR, 'rgba(123, 104, 238, 1)', [], null, { group: 'group.style', order: 1 }),
  ],
  // 平均線 btlm_mean ＋ 分位線。分位線名は core.py quantile_column(q)=`btlm_q{round(q*100)}`
  // で q_low/q_high に依存して変わる（既定 0.05/0.95 → btlm_q5/btlm_q95、0.25 → btlm_q25）。
  // よって F3（_validateSeriesNames）が任意の分位を受理するよう、分位線は動的パターン
  // `btlm_q{pct}`（pct=1..99）で表現する（§3.3.6 dynamic 展開）。
  series: [
    new SeriesDef({ kind: SeriesKind.LINE, sourceColumn: 'btlm_mean', seriesName: 'btlm_mean', dynamic: false }),
    new SeriesDef({
      kind: SeriesKind.LINE, sourceColumn: null, seriesName: null, dynamic: true,
      seriesNamePattern: {
        template: 'btlm_q{pct}', buckets: [''],
        pcts: Array.from({ length: 99 }, (_, i) => String(i + 1)),
      },
    }),
  ],
  compute: { computeId: 'tgp_btlm', requiredColumns: OHLC, timeRequired: true, backendParam: 'fitter', variants: ['default'] },
});

// --- btlm_trail（トレンド現在位置トレイル・OVERLAY）----------------------
// 新インジケーター（kind-twirling-hollerith.md）。各バーで直近 maxbars 本に ols を当てはめ、
// 窓末尾の btlm_mean/q_low/q_high を当日値とする（確定バー不変＝非リペイント）。ソースは
// moving_averages と同一 8 択（applied_price 参照・既定 close）。バンドは名目 ols / 経験分位の
// 2 方式（band_method）。分位ペアは q_low/q_high（0<q_low<q_high<1）。
// 実バインディング add_btlm_trail（indigators/btlm_trail/src/lwc_chart.py）。
// 系列名: btlm_trail_mean（静的）＋ btlm_trail_q{pct}（動的・q_low/q_high に依存）。
const BTLM_TRAIL_SOURCE_LABELS = {
  close: '終値', open: '始値', high: '高値', low: '安値',
  hl2: '(高値 + 安値)/2', hlc3: '(高値 + 安値 + 終値)/3',
  ohlc4: '(始値 + 高値 + 安値 + 終値)/4', hlcc4: '(高値 + 安値 + 終値 + 終値)/4',
};
const BTLM_TRAIL_METHOD_LABELS = { ols: '名目 ols バンド', empirical: '経験分位バンド' };
const BTLM_TRAIL = new IndicatorDef({
  id: 'btlm_trail',
  displayNameKey: 'ind.btlm_trail',
  category: { group: 'builtin', nameKey: 'cat.technical' },
  tab: 'indicator',
  placement: 'overlay',
  params: [
    // ソース: moving_averages と同一 8 択（applied_price 参照・既定 close）。
    param('source', ParamType.ENUM, 'close', [], ['close', 'open', 'high', 'low', 'hl2', 'hlc3', 'ohlc4', 'hlcc4'], { group: 'group.calc', order: 1, label: 'ソース', enumLabels: BTLM_TRAIL_SOURCE_LABELS }),
    // maxbars: 回帰窓（既定 100・core DEFAULT_MAXBARS）。
    param('maxbars', ParamType.INT, 100, [{ kind: ConstraintKind.MIN_VALUE, operands: ['maxbars', 3], messageKey: 'err.maxbars' }], null, { group: 'group.calc', order: 2, step: 1, min: 3, unit: 'unit.bars' }),
    // 分位ペア（0<q_low<q_high<1）。tgp_btlm と対称の q-chain 制約。
    param('q_low', ParamType.FLOAT, 0.05, [
      { kind: ConstraintKind.RANGE_OPEN, operands: [0, 'q_low', 1], messageKey: 'err.q_low.range' },
      { kind: ConstraintKind.LT, operands: ['q_low', 'q_high'], messageKey: 'err.q_order' },
    ], null, { group: 'group.calc', order: 3, step: 0.01, min: 0, max: 1 }),
    param('q_high', ParamType.FLOAT, 0.95, [
      { kind: ConstraintKind.RANGE_OPEN, operands: [0, 'q_high', 1], messageKey: 'err.q_high.range' },
    ], null, { group: 'group.calc', order: 4, step: 0.01, min: 0, max: 1 }),
    // バンド方式: ols（名目・norm_ppf(q)·pred_sd）/ empirical（経験分位・因果ウォークフォワード）。
    param('band_method', ParamType.ENUM, 'ols', [], ['ols', 'empirical'], { group: 'group.calc', order: 5, label: 'バンド方式', enumLabels: BTLM_TRAIL_METHOD_LABELS }),
    // 経験分位バンドの参照本数（既定 500・band_method==empirical のときのみ有効）。
    param('empirical_n', ParamType.INT, 500, [{ kind: ConstraintKind.MIN_VALUE, operands: ['empirical_n', 2], messageKey: 'err.empirical_n' }], null, {
      group: 'group.calc', order: 6, step: 1, min: 2, unit: 'unit.bars',
      conditionalEnable: { when: { param: 'band_method', equals: 'empirical' } },
    }),
    // --- 表示 ---
    // 系列表示（ドット/ライン）はパラメーターから移設（案A・2026-07-19）。設定ダイアログの
    //   「スタイル」タブで系列単位に切替（既定ドット）。ゲート = 下記 SeriesDef の pointStyleEditable。
    // 外れ値分位: 上側 q_out／下側 1-q_out に補助線を上下対称で描画。空/無効はオフ（既定）。
    //   有効条件 q_high < q_out < 1。範囲外・q_out<=q_high は黙って無効化（補助線なし）。
    param('q_out', ParamType.FLOAT, null, [], null, {
      group: 'group.display', order: 2, label: '外れ値分位', step: 0.01, min: 0, max: 1,
      tooltip: 'バンドより外側の極端分位（例 0.99＝片側1%の極値水準）に補助線を引く。上側は分位 q_out、下側は 1-q_out。選択中のバンド方式（ols／経験分位）と同じ規約で算出する。ストップ位置の目安。空欄・q_high 以下・範囲外はオフ。',
    }),
    // 数値表示（β・バンド内実績率〔実現被覆率〕・残差 σ）を読取欄に出す。
    param('show_metrics', ParamType.BOOL, true, [], null, {
      group: 'group.display', order: 3, label: 'β・バンド内実績率・σ を表示',
      tooltip: 'β＝回帰直線の傾き（トレンド方向の正式判定値。符号が向き・大きさが勢い）／バンド内実績率＝直近N本で確定バー終値が帯に収まった実測割合（帯の信頼度の実績。名目との乖離を監視）／σ＝回帰直線まわりの価格の散らばり（σの倍数でボラ追随型ストップ幅を設計する物差し）。3値とも読取欄への表示専用で、チャート描画・帯の計算には影響しない。',
    }),
    // バンド内実績率（実現被覆率）のローリング本数（既定 250）。
    param('n_cov', ParamType.INT, 250, [{ kind: ConstraintKind.MIN_VALUE, operands: ['n_cov', 2], messageKey: 'err.n_cov' }], null, {
      group: 'group.display', order: 4, label: 'バンド内実績率の本数', step: 1, min: 2, unit: 'unit.bars',
      conditionalEnable: { when: { param: 'show_metrics', equals: true } },
    }),
    // color は btlm_mean（トレンド現在位置）の色。スタイルタブへ移譲。
    param('color', ParamType.COLOR, 'rgba(123, 104, 238, 1)', [], null, { group: 'group.style', order: 1 }),
  ],
  // 系列: btlm_trail_mean（静的）＋ 動的分位線 btlm_trail_q{pct}＋オフセット/数値（読取欄）系列。
  //   数値系列（beta/sigma/band_hit_rate）は不可視 line（表示層が readout オーバーレイへ載せる）。
  series: [
    // pointStyleEditable（案A）: mean と分位線のみスタイルタブで「系列表示（ドット/ライン）」編集可。
    new SeriesDef({ kind: SeriesKind.LINE, sourceColumn: 'btlm_trail_mean', seriesName: 'btlm_trail_mean', dynamic: false, pointStyleEditable: true }),
    new SeriesDef({
      kind: SeriesKind.LINE, sourceColumn: null, seriesName: null, dynamic: true, pointStyleEditable: true,
      seriesNamePattern: {
        template: 'btlm_trail_q{pct}', buckets: [''],
        pcts: Array.from({ length: 99 }, (_, i) => String(i + 1)),
      },
    }),
    new SeriesDef({ kind: SeriesKind.LINE, sourceColumn: 'btlm_trail_off_hi', seriesName: 'btlm_trail_off_hi', dynamic: false }),
    new SeriesDef({ kind: SeriesKind.LINE, sourceColumn: 'btlm_trail_off_lo', seriesName: 'btlm_trail_off_lo', dynamic: false }),
    new SeriesDef({ kind: SeriesKind.LINE, sourceColumn: 'btlm_trail_beta', seriesName: 'btlm_trail_beta', dynamic: false }),
    new SeriesDef({ kind: SeriesKind.LINE, sourceColumn: 'btlm_trail_sigma', seriesName: 'btlm_trail_sigma', dynamic: false }),
    new SeriesDef({ kind: SeriesKind.LINE, sourceColumn: 'btlm_trail_band_hit_rate', seriesName: 'btlm_trail_band_hit_rate', dynamic: false }),
  ],
  compute: { computeId: 'btlm_trail', requiredColumns: OHLC, timeRequired: true, backendParam: null, variants: ['default'] },
});

// --- 外れ値イベント分位（共有ビルダー・ユーザー裁定 2026-07-21）------------
// 採用指標（MAROD 系を初出とする別 pane オシレータ）で共通の 3 パラメータ
//   （q_out / k_events / event_agg）と水準線 SeriesDef 4 本
//   （{prefix}_evq_{med|ext}_{hi|lo}）を生成する単一情報源。計算・表示規約の正は
//   common/event_quantiles.py（back）。仕様変更は本ビルダーと back 共有層のみで完結する。
const EVQ_EVENT_AGG_LABELS = { episode: 'エピソード極値', bar: 'バー値（旧方式）' };
const EVQ_PARAMS = (orderStart) => [
  // q_out: イベントの極端分位（有効条件 max(q_high, 0.5) < q_out < 1・空欄/範囲外は極端線のみ黙ってオフ）。
  param('q_out', ParamType.FLOAT, 0.99, [], null, {
    group: 'group.calc', order: orderStart, label: '外れ値の極端分位', step: 0.01, min: 0, max: 1,
    tooltip: '正常バンド（下側/上側分位）を超えた「外れ値イベント」値の集合に対する極端分位（既定 0.99・上側は分位 q_out／下側は 1-q_out・赤破線）。典型深度（イベント中央値・赤実線）と併せ、「外れたら典型的に／極端にどこまで行くか」の水準を描く。水準は当該バーより前のイベントのみから計算（因果・非リペイント）＝事前に把握できる。空欄・上側分位以下・範囲外は極端線のみオフ。',
  }),
  // k_events: イベント分位ローリングの直近観測件数（分散非定常対策・実測 2026-07-20）。
  param('k_events', ParamType.INT, 50, [{ kind: ConstraintKind.MIN_VALUE, operands: ['k_events', 1], messageKey: 'err.k_events' }], null, {
    group: 'group.calc', order: orderStart + 1, step: 1, min: 1, label: '外れ値イベント数 K',
    tooltip: '外れ値イベント分位（中央値・極端分位）を直近何件の観測から計算するか（既定 50。集計単位がエピソードのときはエピソード数）。乖離率は分散非定常のため直近観測に限定して推定する。',
  }),
  // event_agg: episode＝連続超過を 1 エピソード＝極値 1 点に declustering（既定。バー単位は
  //   持続時間の重み付けで典型深度を歪める実測 +24.6% vs +19.1%）。bar＝旧方式（復帰用に保持）。
  param('event_agg', ParamType.ENUM, 'episode', [], ['episode', 'bar'], {
    group: 'group.calc', order: orderStart + 2, label: '外れ値の集計単位',
    enumLabels: EVQ_EVENT_AGG_LABELS,
    tooltip: '外れ値イベントの数え方。エピソード極値＝連続して外れている区間を 1 回と数え、その極値（上側は最大・下側は最小）を 1 観測にする（推奨。「1 回の外れでどこまで行くか」を直接測る）。バー値＝外れているバー 1 本ごとに 1 観測（旧方式。長引いた外れが重複カウントされ典型深度が深めに歪む）。',
  }),
];
// 水準線 SeriesDef 4 本（中央値 hi/lo ＋ 極端 hi/lo・静的名）。表示順は emit 順と同一。
const EVQ_SERIES_DEFS = (prefix) => ['med_hi', 'med_lo', 'ext_hi', 'ext_lo'].map(
  (k) => new SeriesDef({ kind: SeriesKind.LINE, sourceColumn: `${prefix}_evq_${k}`, seriesName: `${prefix}_evq_${k}`, dynamic: false }),
);

// --- btlm_trail_marod（MAROD＝移動平均乖離率・別 pane オシレータ）----------
// 新指標（btlm-trail-marod-concurrent-aho.md）。btlm_trail core の OLS 窓末尾トレンド（基準線）と
//   8 択ソース合成価格を参照し、基準線からの相対偏差（%）= (source - mean)/mean*100 を別 pane の
//   line オシレータとして描く（0% 基準線付き）。確定バー不変（非リペイント・btlm_trail core 由来）。
//   独立インスタンス（自前の source/maxbars で OLS トレンドを算出＝チャート上の btlm_trail に非依存）。
//   実バインディング add_btlm_trail_marod（indigators/btlm_trail_marod/src/lwc_chart.py）。
//   系列名: btlm_trail_marod（line）＋ btlm_trail_marod（0% 水平基準線群 payload・compute_id 一致）。
const BTLM_TRAIL_MAROD = new IndicatorDef({
  id: 'btlm_trail_marod',
  displayNameKey: 'ind.btlm_trail_marod',
  category: { group: 'builtin', nameKey: 'cat.oscillator' },
  tab: 'indicator',
  placement: 'pane',
  params: [
    // ソース: btlm_trail と同一 8 択（applied_price 参照・既定 close）。
    param('source', ParamType.ENUM, 'close', [], ['close', 'open', 'high', 'low', 'hl2', 'hlc3', 'ohlc4', 'hlcc4'], { group: 'group.calc', order: 1, label: 'ソース', enumLabels: BTLM_TRAIL_SOURCE_LABELS }),
    // maxbars: 回帰窓（既定 100・min 3・btlm_trail core DEFAULT_MAXBARS）。
    param('maxbars', ParamType.INT, 100, [{ kind: ConstraintKind.MIN_VALUE, operands: ['maxbars', 3], messageKey: 'err.maxbars' }], null, { group: 'group.calc', order: 2, step: 1, min: 3, unit: 'unit.bars' }),
    // 分位ペア（0<q_low<q_high<1・btlm_trail と対称の q-chain 制約）。σ・分位バンドの下側/上側分位。
    param('q_low', ParamType.FLOAT, 0.05, [
      { kind: ConstraintKind.RANGE_OPEN, operands: [0, 'q_low', 1], messageKey: 'err.q_low.range' },
      { kind: ConstraintKind.LT, operands: ['q_low', 'q_high'], messageKey: 'err.q_order' },
    ], null, { group: 'group.calc', order: 3, step: 0.01, min: 0, max: 1, label: '下側分位' }),
    param('q_high', ParamType.FLOAT, 0.95, [
      { kind: ConstraintKind.RANGE_OPEN, operands: [0, 'q_high', 1], messageKey: 'err.q_high.range' },
    ], null, { group: 'group.calc', order: 4, step: 0.01, min: 0, max: 1, label: '上側分位' }),
    // 外れ値イベント分位の 3 パラメータ（共有ビルダー・ma_marod と対称＝ユーザー裁定 2026-07-21）。
    ...EVQ_PARAMS(5),
    // window_n: 正常バンド（分位バンド）の因果ローリング窓（既定 500・min 2）。実測で MAROD は
    //   分散非定常のため固定でなくこの窓で局所再計算する（当該バー除外＝非リペイント）。
    param('window_n', ParamType.INT, 500, [{ kind: ConstraintKind.MIN_VALUE, operands: ['window_n', 2], messageKey: 'err.window_n' }], null, {
      group: 'group.calc', order: 8, step: 1, min: 2, unit: 'unit.bars', label: '分位の窓',
      tooltip: '正常バンド（経験分位バンド・下側/上側分位）を算出する因果ローリング窓の本数。MAROD は分散非定常のため固定でなくこの窓で局所再計算する。当該バーは除外（非リペイント）。',
    }),
    // color は MAROD 線の色（スタイルタブへ移譲）。既定は add_btlm_trail_marod の _COLOR_MAROD。
    param('color', ParamType.COLOR, 'rgba(123, 104, 238, 1)', [], null, { group: 'group.style', order: 1 }),
  ],
  // 系列: MAROD line（別 pane オシレータ）＋ 0% 水平基準線＋ 正常バンド＋イベント分位水準線。
  //   σ バンドは描画廃止（認知負荷削減・ユーザー裁定 2026-07-21。core 計算は温存）。
  series: [
    // barStyleEditable（案A）: MAROD line のみスタイルタブで「棒グラフ（histogram）」表示を選択可
    //   （選択時 renderer が LineSeries→HistogramSeries に再生成し 0% 中心の棒表示にする）。
    new SeriesDef({ kind: SeriesKind.LINE, sourceColumn: 'btlm_trail_marod', seriesName: 'btlm_trail_marod', dynamic: false, barStyleEditable: true }),
    new SeriesDef({ kind: SeriesKind.HORIZONTAL_LINE, sourceColumn: null, seriesName: 'btlm_trail_marod', dynamic: false }),
    // 分位バンド（動的・q_low/q_high に依存＝btlm_trail_marod_q{pct}）。btlm_trail_q{pct} と対称の命名。
    new SeriesDef({
      kind: SeriesKind.LINE, sourceColumn: null, seriesName: null, dynamic: true,
      seriesNamePattern: {
        template: 'btlm_trail_marod_q{pct}', buckets: [''],
        pcts: Array.from({ length: 99 }, (_, i) => String(i + 1)),
      },
    }),
    // 外れ値イベント分位の水準線（共有ビルダー・4 本）。
    ...EVQ_SERIES_DEFS('btlm_trail_marod'),
  ],
  compute: { computeId: 'btlm_trail_marod', requiredColumns: OHLC, timeRequired: true, backendParam: null, variants: ['default'] },
});

// （ma_marod は MA_TYPE_LABELS / MA_SOURCE_LABELS 定義後＝MOVING_AVERAGES 直後に定義する。
//   const の TDZ 制約による配置であり、REGISTRY 上は BTLM_TRAIL_MAROD の直後に並ぶ。）

// --- profit_band（global / robust・OVERLAY）------------------------------
// バンド値は始値±分位点を価格水準へ復元した price-level（bands.py / robust_bands.py）。
// よって価格 pane(0) のローソクへ重畳する（§下部コメント「価格バンドは 'overlay'」準拠）。
const PROFIT_BAND = new IndicatorDef({
  id: 'profit_band',
  displayNameKey: 'ind.profit_band',
  category: { group: 'builtin', nameKey: 'cat.statistics' },
  tab: 'indicator',
  placement: 'overlay',
  params: [
    // probabilities 既定 [0.95,0.99]→実 7 水準 PROBABILITIES 是正（M-4・core.py:19）。
    param('probabilities', ParamType.FLOAT_LIST, [0.51, 0.8, 0.85, 0.9, 0.95, 0.98, 0.99], [
      { kind: ConstraintKind.RANGE_OPEN, operands: [0, 'probabilities', 1], messageKey: 'err.prob' },
    ], null, { group: 'group.calc', order: 1, controlType: 'list' }),
    // buckets（系統）: DEFAULT_BUCKETS=("nOH","pOL","pOH","nOL")（lwc_chart.py:51）。
    param('buckets', ParamType.ENUM_LIST, ['nOH', 'pOL', 'pOH', 'nOL'], [], ['nOH', 'pOL', 'pOH', 'nOL'], { group: 'group.calc', order: 2, controlType: 'multiselect' }),
    // require_full=True（lwc_chart.py:96）。
    param('require_full', ParamType.BOOL, true, [], null, { group: 'group.calc', order: 3 }),
    // legend=False（lwc_chart.py:97）。表示グループ。
    param('legend', ParamType.BOOL, false, [], null, { group: 'group.display', order: 1 }),
    // --- robust variant 固有（add_robust_profit_band・§4.3）---------------
    // normalize="return"（enum return/atr・lwc_chart.py:164・robust_bands.py:135-140）。
    param('normalize', ParamType.ENUM, 'return', [], ['return', 'atr'], { group: 'group.robust', order: 1 }),
    // window="expanding"（Union[str,int]・複合型 UI・lwc_chart.py:165・§4.3.1）。
    param('window', ParamType.ENUM, 'expanding', [], ['expanding'], { group: 'group.robust', order: 2, controlType: 'window_compound' }),
    // atr_period=14（INT・normalize=="atr" のときのみ ATR 計算で使用・robust_bands.py:135-138）。
    // 条件付き有効化（§3.5）: normalize==atr で有効、return で無効。
    param('atr_period', ParamType.INT, 14, [], null, {
      group: 'group.robust', order: 3, step: 1, min: 1, unit: 'unit.bars',
      conditionalEnable: { when: { param: 'normalize', equals: 'atr' } },
    }),
    // min_obs=30（INT・lwc_chart.py:167）。
    param('min_obs', ParamType.INT, 30, [{ kind: ConstraintKind.MIN_VALUE, operands: ['min_obs', 1], messageKey: 'err.min_obs' }], null, { group: 'group.robust', order: 4, step: 1, min: 1 }),
  ],
  // 動的系列（dynamic=true）: 系列名は実バインディングの命名規則
  // `{bucket} {pct}%`（lwc_chart.py:137 `name = f"{bucket} {tag}%"`）に従い、
  // bucket ∈ {nOH,pOL,pOH,nOL} × pct ∈ {51,80,85,90,95,98,99} = 28 系列。
  // F3 照合は seriesNamePattern の展開集合を基準に行う（§3.3.6 dynamic 展開）。
  series: [
    new SeriesDef({
      kind: SeriesKind.LINE,
      sourceColumn: null,
      seriesName: null,
      dynamic: true,
      seriesNamePattern: { template: '{bucket} {pct}%', buckets: ['nOH', 'pOL', 'pOH', 'nOL'], pcts: ['51', '80', '85', '90', '95', '98', '99'] },
    }),
  ],
  // variants[0] が既定 variant になる（参照は複数サイト: indicator_controller._defaultVariant、
  // および properties_dialog の新規インスタンス既定 _variants[0]）。順序入替はこれら全てに波及する。
  // 先頭の robust を既定にする理由: global は全長分位点＋生値幅のため repaint＋価格水準依存の欠陥を持つ。
  // robust は因果窓＋比率/ATR 正規化による是正版。global は後方互換のため末尾に温存する。
  compute: { computeId: 'profit_band', requiredColumns: OHLC, timeRequired: true, backendParam: null, variants: ['robust', 'global'] },
});

// --- price_range_power（OVERLAY・horizontal_line）-------------------------
const PRICE_RANGE_POWER = new IndicatorDef({
  id: 'price_range_power',
  displayNameKey: 'ind.price_range_power',
  category: { group: 'builtin', nameKey: 'cat.volume' },
  tab: 'indicator',
  placement: 'overlay',
  params: [
    // interval は ENUM 維持（INTERVAL_CHOICES=(0.1,0.01,0.001)・core.py:41）。
    param('interval', ParamType.ENUM, 0.1, [], [0.1, 0.01, 0.001], { group: 'group.calc', order: 1 }),
    // range_from/range_to=None（自動・lwc_chart.py:41-42）。両者非 null 時のみ range_from<range_to
    // を form_model 側で前提付き検証（§11.2 Q-3・素の LT は両者 null 時に誤検出するため）。
    param('range_from', ParamType.FLOAT, null, [], null, { group: 'group.calc', order: 2 }),
    param('range_to', ParamType.FLOAT, null, [], null, { group: 'group.calc', order: 3 }),
    // top_n 既定 2→5 是正（M-3・lwc_chart.py:43）。
    param('top_n', ParamType.INT, 5, [{ kind: ConstraintKind.MIN_VALUE, operands: ['top_n', 0], messageKey: 'err.top_n' }], null, { group: 'group.calc', order: 4, step: 1, min: 0, unit: 'unit.bands' }),
    // width=2（線幅・スタイルタブへ移譲・lwc_chart.py:46）。
    param('width', ParamType.INT, 2, [], null, { group: 'group.style', order: 1, step: 1, min: 1 }),
    // 色は bull/bear の 2 色（実コード add_price_range_power の bull_color/bear_color・
    // lwc_chart.py:44-45）。既定は _BULL_COLOR / _BEAR_COLOR（lwc_chart.py:27-28）。
    // スタイルタブへ移譲。
    param('bull_color', ParamType.COLOR, 'rgba(46, 158, 91, 0.9)', [], null, { group: 'group.style', order: 2 }),
    param('bear_color', ParamType.COLOR, 'rgba(210, 67, 58, 0.9)', [], null, { group: 'group.style', order: 3 }),
  ],
  // 実バインディングの horizontal_line 系列は単一（name='price_range_power'）。
  // BULL/BEAR は各価格線の text に載る（precomputed["price_range_power:default"][0].lines[].text）。
  // F3 照合基準は系列名 'price_range_power'。
  series: [
    new SeriesDef({ kind: SeriesKind.HORIZONTAL_LINE, sourceColumn: null, seriesName: 'price_range_power', dynamic: false }),
  ],
  compute: { computeId: 'price_range_power', requiredColumns: OHLC, timeRequired: false, backendParam: null, variants: ['default'] },
});

// --- moving_averages（OVERLAY・単一 MA＋平滑化＋BB・TradingView「移動平均」準拠）--------
// 実バインディング add_moving_averages（moving_averages/src/lwc_chart.py）。系列名は固定
//   "MA"/"Smoothing"/"Upper"/"Lower"（4 静的 SeriesDef）。backend は平滑化タイプに応じて部分集合を
//   出力し F3 を通過する。ダイアログは 3 セクション（基本 / 平滑化 / 計算）で画像レイアウトに準拠。
const MA_TYPE_LABELS = { sma: 'SMA', ema: 'EMA', smma: 'SMMA', lwma: 'LWMA' };
const MA_SOURCE_LABELS = {
  close: '終値', open: '始値', high: '高値', low: '安値',
  hl2: '(高値 + 安値)/2', hlc3: '(高値 + 安値 + 終値)/3',
  ohlc4: '(始値 + 高値 + 安値 + 終値)/4', hlcc4: '(高値 + 安値 + 終値 + 終値)/4',
};
const MA_SMOOTHING_LABELS = {
  none: 'なし', sma: 'SMA', ema: 'EMA', smma: 'SMMA', wma: 'WMA', sma_bb: 'SMA + ボリンジャーバンド',
};
const MA_TIMEFRAME_LABELS = {
  chart: 'チャート', '1m': '1分', '5m': '5分', '15m': '15分', '30m': '30分', '1h': '1時間',
  '4h': '4時間', '1D': '日', '1W': '週', '1M': '月',
};
const MA_LINE = (seriesName) => new SeriesDef({ kind: SeriesKind.LINE, sourceColumn: null, seriesName, dynamic: false });
const MOVING_AVERAGES = new IndicatorDef({
  id: 'moving_averages',
  displayNameKey: 'ind.moving_averages',
  category: { group: 'builtin', nameKey: 'cat.technical' },
  tab: 'indicator',
  placement: 'overlay',
  params: [
    // --- 基本（無見出しの先頭セクション）---
    param('ma_type', ParamType.ENUM, 'ema', [], ['sma', 'ema', 'smma', 'lwma'], { order: 1, label: '種別', enumLabels: MA_TYPE_LABELS }),
    param('length', ParamType.INT, 9, [{ kind: ConstraintKind.MIN_VALUE, operands: ['length', 2], messageKey: 'err.length' }], null, { order: 2, label: '期間', step: 1, min: 2 }),
    param('source', ParamType.ENUM, 'close', [], ['close', 'open', 'high', 'low', 'hl2', 'hlc3', 'ohlc4', 'hlcc4'], { order: 3, label: 'ソース', enumLabels: MA_SOURCE_LABELS }),
    param('offset', ParamType.INT, 0, [], null, { order: 4, label: 'オフセット', step: 1 }),
    // --- 平滑化 ---
    param('smoothing_type', ParamType.ENUM, 'none', [], ['none', 'sma', 'ema', 'smma', 'wma', 'sma_bb'], { group: '平滑化', order: 1, label: 'タイプ', enumLabels: MA_SMOOTHING_LABELS }),
    param('smoothing_length', ParamType.INT, 9, [{ kind: ConstraintKind.MIN_VALUE, operands: ['smoothing_length', 2], messageKey: 'err.length' }], null, { group: '平滑化', order: 2, label: '期間', step: 1, min: 2 }),
    // BB標準偏差: smoothing_type==sma_bb のときのみ有効（conditionalEnable で他はグレーアウト＝画像準拠）。
    param('bb_stddev', ParamType.FLOAT, 2.0, [], null, {
      group: '平滑化', order: 3, label: 'BB標準偏差', step: 0.001, min: 0.001,
      tooltip: 'ボリンジャーバンドの標準偏差倍率（平滑化タイプが「SMA + ボリンジャーバンド」のとき有効）',
      conditionalEnable: { when: { param: 'smoothing_type', equals: 'sma_bb' } },
    }),
    // --- 計算 ---
    param('timeframe', ParamType.ENUM, 'chart', [], ['chart', '1m', '5m', '15m', '30m', '1h', '4h', '1D', '1W', '1M'], { group: '計算', order: 1, label: '時間足', enumLabels: MA_TIMEFRAME_LABELS, tooltip: 'この指標を計算する時間足（「チャート」はチャートの時間足に追従）' }),
    // 既定 false: 未確定の最新足も計算し MA を最新足まで描画する（true だと最終足を除外し
    //   常に1本手前で止まる）。確定足のみで計算したい場合はダイアログで ON にする。
    param('wait_for_close', ParamType.BOOL, false, [], null, { group: '計算', order: 2, label: '時間足の確定を待つ' }),
  ],
  // 固定系列（dynamic=false）: backend が平滑化タイプに応じて部分集合を出力する。
  series: [MA_LINE('MA'), MA_LINE('Smoothing'), MA_LINE('Upper'), MA_LINE('Lower')],
  compute: { computeId: 'moving_averages', requiredColumns: OHLC, timeRequired: true, backendParam: null, variants: ['default'] },
});

// --- ma_marod（移動平均乖離率・MA 種別選択式・別 pane オシレータ）----------
// 新指標（.doc/MA_MAROD_BASIC_DESIGN.md）。moving_averages core の 4 種 MA（sma/ema/smma/lwma）
//   を基準線の参照実装とし、(price - ma)/ma*100 を別 pane の line オシレータとして描く
//   （0% 基準線・σ／分位バンド付き＝btlm_trail_marod と同一仕様）。計算の原子（価格ソース）は
//   moving_averages と同期（同一写像・単一経路）。確定バー不変（非リペイント・前進逐次計算）。
//   実バインディング add_ma_marod（indigators/ma_marod/src/lwc_chart.py）。
//   系列名: ma_marod（line）＋ ma_marod（0% 水平基準線群 payload・compute_id 一致）。
const MA_MAROD = new IndicatorDef({
  id: 'ma_marod',
  displayNameKey: 'ind.ma_marod',
  category: { group: 'builtin', nameKey: 'cat.oscillator' },
  tab: 'indicator',
  placement: 'pane',
  params: [
    // ソース: moving_averages と同一 8 択・同一ラベル（計算の原子の同期を UI 側でも維持）。
    param('source', ParamType.ENUM, 'close', [], ['close', 'open', 'high', 'low', 'hl2', 'hlc3', 'ohlc4', 'hlcc4'], { group: 'group.calc', order: 1, label: 'ソース', enumLabels: MA_SOURCE_LABELS }),
    // 基準線 MA 種別（moving_averages と同一 4 択・同一ラベル・既定 ema）。
    param('ma_type', ParamType.ENUM, 'ema', [], ['sma', 'ema', 'smma', 'lwma'], { group: 'group.calc', order: 2, label: '種別', enumLabels: MA_TYPE_LABELS }),
    // length: MA 本数（既定 50・min 2＝参照実装 *_on_buffer の契約）。
    param('length', ParamType.INT, 50, [{ kind: ConstraintKind.MIN_VALUE, operands: ['length', 2], messageKey: 'err.length' }], null, { group: 'group.calc', order: 3, label: '期間', step: 1, min: 2, unit: 'unit.bars' }),
    // 分位ペア（0<q_low<q_high<1・btlm_trail_marod と対称の q-chain 制約）。σ・分位バンドの下側/上側分位。
    param('q_low', ParamType.FLOAT, 0.05, [
      { kind: ConstraintKind.RANGE_OPEN, operands: [0, 'q_low', 1], messageKey: 'err.q_low.range' },
      { kind: ConstraintKind.LT, operands: ['q_low', 'q_high'], messageKey: 'err.q_order' },
    ], null, { group: 'group.calc', order: 4, step: 0.01, min: 0, max: 1, label: '下側分位' }),
    param('q_high', ParamType.FLOAT, 0.95, [
      { kind: ConstraintKind.RANGE_OPEN, operands: [0, 'q_high', 1], messageKey: 'err.q_high.range' },
    ], null, { group: 'group.calc', order: 5, step: 0.01, min: 0, max: 1, label: '上側分位' }),
    // 外れ値イベント分位の 3 パラメータ（共有ビルダー・btlm_trail_marod と対称＝ユーザー裁定
    //   2026-07-21）。q_out＝極端分位／k_events＝直近観測件数／event_agg＝集計単位。
    ...EVQ_PARAMS(6),
    // window_n: 正常バンド（分位バンド）の因果ローリング窓（既定 500・min 2）。乖離率は
    //   分散非定常（実測）のため固定でなくこの窓で局所再計算する（当該バー除外＝非リペイント）。
    param('window_n', ParamType.INT, 500, [{ kind: ConstraintKind.MIN_VALUE, operands: ['window_n', 2], messageKey: 'err.window_n' }], null, {
      group: 'group.calc', order: 9, step: 1, min: 2, unit: 'unit.bars', label: '分位の窓',
      tooltip: '正常バンド（経験分位バンド・下側/上側分位）を算出する因果ローリング窓の本数。乖離率は分散非定常のため固定でなくこの窓で局所再計算する。当該バーは除外（非リペイント）。',
    }),
    // color は MA_MAROD 線の色（スタイルタブへ移譲）。既定は add_ma_marod の _COLOR_MA_MAROD。
    param('color', ParamType.COLOR, 'rgba(255, 152, 0, 1)', [], null, { group: 'group.style', order: 1 }),
  ],
  // 系列: MA_MAROD line（別 pane オシレータ）＋ 0% 水平基準線＋ 正常バンド＋イベント分位水準線。
  series: [
    // barStyleEditable: MA_MAROD line のみスタイルタブで「棒グラフ（histogram）」表示を選択可
    //   （btlm_trail_marod 案A と同一の非波及ゲート）。
    new SeriesDef({ kind: SeriesKind.LINE, sourceColumn: 'ma_marod', seriesName: 'ma_marod', dynamic: false, barStyleEditable: true }),
    new SeriesDef({ kind: SeriesKind.HORIZONTAL_LINE, sourceColumn: null, seriesName: 'ma_marod', dynamic: false }),
    // 分位バンド（動的・q_low/q_high に依存＝ma_marod_q{pct}）。btlm_trail_marod_q{pct} と対称の命名。
    new SeriesDef({
      kind: SeriesKind.LINE, sourceColumn: null, seriesName: null, dynamic: true,
      seriesNamePattern: {
        template: 'ma_marod_q{pct}', buckets: [''],
        pcts: Array.from({ length: 99 }, (_, i) => String(i + 1)),
      },
    }),
    // 外れ値イベント分位の水準線（共有ビルダー・4 本）。σ バンドと全履歴（_all）系列は
    //   認知負荷削減のため描画廃止（ユーザー裁定 2026-07-21）。
    ...EVQ_SERIES_DEFS('ma_marod'),
  ],
  compute: { computeId: 'ma_marod', requiredColumns: OHLC, timeRequired: true, backendParam: null, variants: ['default'] },
});

// ===========================================================================
// profit_* 系（MQL 移植・lwc 仕様）。実バインディング add_*（indigators/profit_*/src/
// lwc_chart.py）の系列名と完全一致させる（F3 照合・§3.3.6）。系列 kind:
//   histogram = create_histogram、line = create_line、horizontal_line = σ 水準線群
//   （統合 FakeChart が compute_id 名 1 件にまとめる＝seriesName は指標 id と一致）。
// placement: オシレータは 'pane'（instance 専用 overlay スケールへ autoscale 分離）、
//   価格バンドは 'overlay'（価格スケール上）。
// ---------------------------------------------------------------------------
const PF_HLINE = (id) => new SeriesDef({ kind: SeriesKind.HORIZONTAL_LINE, sourceColumn: null, seriesName: id, dynamic: false });
const PF_LINE = (seriesName) => new SeriesDef({ kind: SeriesKind.LINE, sourceColumn: null, seriesName, dynamic: false });
const PF_HIST = (seriesName) => new SeriesDef({ kind: SeriesKind.HISTOGRAM, sourceColumn: null, seriesName, dynamic: false });
// 正整数パラメータ（period 系）。MIN_VALUE>=1 の制約付き・group.calc・step1。
const PF_INT = (name, def, extraUi = {}) => param(
  name, ParamType.INT, def,
  [{ kind: ConstraintKind.MIN_VALUE, operands: [name, 1], messageKey: `err.${name}` }],
  null, { group: 'group.calc', step: 1, min: 1, ...extraUi },
);
// 標準化窓 W（直近 W 本の過去のみで標準化＝look-ahead 除去・repaint しない）。
// profit_* の因果化済み 6 指標で共通の window パラメータ（def=120・min:2・専用ラベル）。
// PF_INT('window', ...) リテラルの DRY 集約（生成 param は従来と完全同一）。
const PF_WINDOW = () => PF_INT('window', 120, { min: 2, label: '標準化窓 W（直近本数）' });
const MA_METHOD_ENUM_LABELS = { 0: 'SMA', 1: 'EMA', 2: 'SMMA', 3: 'LWMA' };
// compute 共通（OHLCV サンプルを前提に requiredColumns は OHLC、時刻必須）。
const PF_COMPUTE = (id, variants = ['default']) => ({
  computeId: id, requiredColumns: OHLC, timeRequired: true, backendParam: null, variants,
});
const pfDef = ({ id, name, cat, placement, params, series, variants }) => new IndicatorDef({
  id,
  displayNameKey: `ind.${name}`,
  category: { group: 'builtin', nameKey: `cat.${cat}` },
  tab: 'indicator',
  placement,
  params,
  series,
  compute: PF_COMPUTE(id, variants),
});

const PROFIT_ADX_NEEDLE = pfDef({
  id: 'profit_adx_needle', name: 'ADXNeedle', cat: 'oscillator', placement: 'pane',
  params: [PF_INT('period', 6), PF_WINDOW()],
  series: [PF_HIST('adx_needle'), PF_HLINE('profit_adx_needle')],
});
const PROFIT_ARCTAN = pfDef({
  id: 'profit_arctan', name: 'ArcTan', cat: 'oscillator', placement: 'pane',
  params: [
    PF_INT('period', 6),
    param('ma_method', ParamType.ENUM, 1, [], [0, 1, 2, 3], { group: 'group.calc', enumLabels: MA_METHOD_ENUM_LABELS }),
    param('bar_width', ParamType.FLOAT, 0.1, [], null, { group: 'group.calc', step: 0.05, min: 0.05 }),
    PF_WINDOW(),
  ],
  series: [PF_HIST('arctan_lc'), PF_HLINE('profit_arctan')],
});
const PROFIT_MFI = pfDef({
  id: 'profit_mfi', name: 'MFI', cat: 'volume', placement: 'pane',
  params: [PF_INT('mfi_period', 14), PF_INT('ma_period', 5)],
  series: [PF_LINE('mfi'), PF_LINE('mfi_ma'), PF_HLINE('profit_mfi')],
});
const PROFIT_RSI = pfDef({
  id: 'profit_rsi', name: 'RSI', cat: 'oscillator', placement: 'pane',
  params: [
    PF_INT('rsi_period', 6),
    param('apply', ParamType.ENUM, 5, [], [1, 2, 3, 4, 5, 6], { group: 'group.calc' }),
    PF_INT('ma_period', 5),
  ],
  series: [PF_LINE('rsi'), PF_LINE('rsi_ma'), PF_HLINE('profit_rsi')],
});
const PROFIT_STC = pfDef({
  id: 'profit_stc', name: 'STC', cat: 'oscillator', placement: 'pane',
  params: [PF_INT('period', 70)],
  series: [PF_LINE('stc_osc'), PF_HLINE('profit_stc')],
});
const PROFIT_OSCILLATOR = pfDef({
  id: 'profit_oscillator', name: 'Oscillator', cat: 'volume', placement: 'pane',
  params: [PF_INT('period_a', 6), PF_INT('period_b', 60), PF_WINDOW()],
  series: [PF_HIST('oscillator_lc'), PF_HLINE('profit_oscillator')],
});
const PROFIT_OSCILLATOR2 = pfDef({
  id: 'profit_oscillator2', name: 'Oscillator2', cat: 'volume', placement: 'pane',
  params: [
    PF_INT('osc_period', 6), PF_INT('stc_slow', 6), PF_INT('ma_period', 60), PF_INT('rci_period', 12),
    param('direction', ParamType.BOOL, false, [], null, { group: 'group.calc' }),
  ],
  series: [PF_HIST('oscillator2_lc'), PF_LINE('oscillator2_rci'), PF_HLINE('profit_oscillator2')],
});
const PROFIT_OSI_MA = pfDef({
  id: 'profit_osi_ma', name: 'OsiMA', cat: 'oscillator', placement: 'pane',
  params: [
    param('ma_mode', ParamType.ENUM, 1, [], [0, 1, 2, 3], { group: 'group.calc', enumLabels: MA_METHOD_ENUM_LABELS }),
    PF_INT('ma_period', 21),
  ],
  series: [PF_HIST('osi_ma_kairi'), PF_HLINE('profit_osi_ma')],
});
const PROFIT_RMM = pfDef({
  id: 'profit_rmm', name: 'RMM', cat: 'volume', placement: 'pane',
  params: [PF_INT('osc_period', 6), PF_INT('ma_period', 6), PF_WINDOW()],
  series: [PF_HIST('rmm_lc'), PF_HLINE('profit_rmm')],
});
const PROFIT_VOLATILITY = pfDef({
  id: 'profit_volatility', name: 'Volatility', cat: 'oscillator', placement: 'pane',
  // period=測定幅（OHLC4 の何本変化か）/ window=標準化窓 W（直近 W 本の過去のみで標準化＝
  // look-ahead 除去・repaint しない。min:2。i18n キー不在のため label を直指定）。
  params: [
    PF_INT('period', 6),
    PF_WINDOW(),
  ],
  series: [PF_HIST('volatility_lc'), PF_HLINE('profit_volatility')],
});
const PROFIT_HL_BAND = pfDef({
  id: 'profit_hl_band', name: 'HLBand', cat: 'band', placement: 'overlay',
  params: [PF_WINDOW()],
  series: [PF_HLINE('profit_hl_band')],
});
// hlband は variant で出力が一変する（separate=histogram pane / overlay=価格バンド）。
// series は両者の和集合（F3 は欠落を許容し未知のみ除外）。placement='pane' は separate 基準だが、
// overlay variant は line/histogram を出さないため renderer が水準線を mainSeries（価格軸）へ自動配線する。
const PROFIT_HLBAND = pfDef({
  id: 'profit_hlband', name: 'HLBandSep', cat: 'band', placement: 'pane',
  params: [param('draw_levels', ParamType.BOOL, true, [], null, { group: 'group.display' })],
  series: [PF_HIST('hl_range'), PF_HLINE('profit_hlband')],
  variants: ['separate', 'overlay'],
});
const PROFIT_MFI_MACD = pfDef({
  id: 'profit_mfi_macd', name: 'MFIMACD', cat: 'volume', placement: 'pane',
  params: [PF_INT('mfi_period', 13), PF_INT('fast', 4), PF_INT('slow', 8), PF_INT('signal', 4)],
  series: [PF_HIST('mfimacd_hist'), PF_LINE('MFIMACD'), PF_LINE('Signal'), PF_HLINE('profit_mfi_macd')],
});
const PROFIT_RMM_MACD = pfDef({
  id: 'profit_rmm_macd', name: 'RMMMACD', cat: 'volume', placement: 'pane',
  params: [PF_INT('osc_period', 6), PF_INT('ma_period', 6), PF_INT('fast', 4), PF_INT('slow', 8), PF_INT('signal', 4), PF_WINDOW()],
  series: [PF_HIST('rmmmacd_hist'), PF_LINE('RMMWMACD'), PF_LINE('Signal')],
});
const PROFIT_RSI_MACD = pfDef({
  id: 'profit_rsi_macd', name: 'RSIMACD', cat: 'oscillator', placement: 'pane',
  params: [PF_INT('rsi_period', 13), PF_INT('fast', 4), PF_INT('slow', 8), PF_INT('signal', 4)],
  series: [PF_HIST('rsimacd_hist'), PF_LINE('RSIMACD'), PF_LINE('Signal'), PF_HLINE('profit_rsi_macd')],
});

// --- market_profile（プロファイルタブ・アクター委譲型）--------------------
// IndicatorDef 定義は MP モジュール（market_profile/web/js/usecase/catalog_entry.js）へ移設。
//   present はローカル helper（param / OHLC）とドメイン型を注入して factory を呼び登録する
//   （メニュー導線＝プロファイルタブからの MP 追加はユーザー明示指示で維持）。挙動は移設前と byte 等価。
const MARKET_PROFILE = makeMarketProfileDef({
  IndicatorDef, SeriesDef, SeriesKind, ParamType, ConstraintKind, param, OHLC,
});

const REGISTRY = Object.freeze([
  TGP_BTLM, BTLM_TRAIL, BTLM_TRAIL_MAROD, MA_MAROD, PROFIT_BAND, PRICE_RANGE_POWER, MOVING_AVERAGES, MARKET_PROFILE,
  PROFIT_ADX_NEEDLE, PROFIT_ARCTAN, PROFIT_MFI, PROFIT_RSI, PROFIT_STC,
  PROFIT_OSCILLATOR, PROFIT_OSCILLATOR2, PROFIT_OSI_MA, PROFIT_RMM, PROFIT_VOLATILITY,
  PROFIT_HL_BAND, PROFIT_HLBAND, PROFIT_MFI_MACD, PROFIT_RMM_MACD, PROFIT_RSI_MACD,
]);
const BY_ID = new Map(REGISTRY.map((d) => [d.id, d]));

// 全 IndicatorDef を返す（読み取り専用配列の複製）。
export function list() {
  return [...REGISTRY];
}

// id で IndicatorDef を返す。未知 id は null。
export function get(id) {
  return BY_ID.get(id) ?? null;
}

// サーバ由来スキーマ（GET /catalog）で param 既定値を解決する（単一情報源・ISSUE-092 ③）。
//
// schema = { compute_id: { param_name: default } }（back catalog_schema.PARAM_DEFAULTS の配信形）。
// レジストリの各 ParamDef.default を schema の値へ overlay する。既定値の正は back（Python）側で、
// front のリテラルはフェッチ失敗時の静的フォールバック（オフライン耐性）。未知 id / 未知 param は
// 黙って無視する（前方互換・back が先行して指標を増やしても front を壊さない）。schema に無い指標
// （market_profile 等・独立アクター所有）は不変。表示ラベル等の純 UI メタは触らない（既定値のみ）。
//
// IndicatorDef は Object.freeze 済みだが params 配列要素（ParamDef plain object）は凍結されていない
// ため .default の上書きは可能（domain_models.js は shallow freeze）。反映した param 数を返す。
export function applyServerDefaults(schema) {
  if (!schema || typeof schema !== 'object') {
    return 0;
  }
  let applied = 0;
  for (const [id, defaults] of Object.entries(schema)) {
    const def = BY_ID.get(id);
    if (!def || !defaults || typeof defaults !== 'object') {
      continue;
    }
    for (const p of def.params) {
      if (Object.prototype.hasOwnProperty.call(defaults, p.name)) {
        p.default = defaults[p.name];
        applied += 1;
      }
    }
  }
  return applied;
}
