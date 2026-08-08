// 指標レジストリ（usecase/catalog.js）。
//
// 実在 4 バインディング（tgp_btlm / profit_band global,robust / price_range_power）の
// IndicatorDef を最低限定義し、list() / get(id) を提供する（§3.1.3）。
// 検索・フィルタ（UC-01）は id/display_name を対象とし series/params は読まない（§4.6）。
// 架空指標は足さない（実在バインディング中心）。

import { ConstraintKind, ParamType } from '../domain/constraint_eval.js';
import { IndicatorDef, SeriesDef, SeriesKind } from '../domain/domain_models.js';
import { TF_CODES } from '../domain/tf_meta.js';
import { makeMarketProfileDef } from './catalog_entry.js';
import { makeTickvolBandsDef } from './tickvol_bands_catalog_entry.js';
import { isActorDriven } from './actor_driven_ids.js';

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
  // variants: この param を受理する variant 名の配列（ISSUE-278 #8）。null＝全 variant 共通。
  //   宣言の正は back（call_binding._TABLE の params_defaults）で、GET /catalog の paramScopes を
  //   applyServerParamScopes が overlay する。front はリテラルを持たない（二重定義を作らない）。
  //   受理しない variant では行を非表示にし（form_model.computeVisible）、/compute へも送らない
  //   （indicator_controller）。従来は全 variant の和集合を送り back が無言で捨てていたため、
  //   効かないコントロールが UI に出続けていた。
  variants: null,
  order: null,
  uiVisible: true,
  // label: フィールド表示名の直接指定（日本語ラベル。省略時は labelKey 末尾を表示）。
  label: null,
  // enumLabels: enum 値 → 表示名のマップ（select の選択肢を日本語表示する。省略時は値をそのまま表示）。
  enumLabels: null,
  // isPeriod: 期間フラグ（基本設計_期間プリセット.md §5.1）。true のとき「その値は直近 N 本の
  //   バーを意味する」＝期間プリセット UI の対象になる（controlType 既定が 'period' になる）。
  //   判定源はこのフラグのみ。unit:'unit.bars' は単位表示のためのメタデータであり流用しない
  //   （承認事項 A-7。現に moving_averages.length 等の明白な期間パラメータへ付いていない）。
  isPeriod: false,
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
    param('maxbars', ParamType.INT, 100, [{ kind: ConstraintKind.MIN_VALUE, operands: ['maxbars', 1], messageKey: 'err.maxbars' }], null, { group: 'group.calc', order: 3, step: 1, min: 1, unit: 'unit.bars', label: '移動期間', isPeriod: true }),
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
// 適用価格（ソース）の表示名。**単一情報源**（同一概念に複数の呼び名を作らない）。
//   共有 common/applied_price.py の AppliedPrice と 1:1 で対応する。
const APPLIED_PRICE_LABELS = {
  close: '終値', open: '始値', high: '高値', low: '安値',
  hl2: '(高値 + 安値)/2', hlc3: '(高値 + 安値 + 終値)/3',
  ohlc4: '(始値 + 高値 + 安値 + 終値)/4', hlcc4: '(高値 + 安値 + 終値 + 終値)/4',
};
// profit_rsi の `apply` は MQL 由来の**数値**（1..6）で選ぶ。数値のままでは何に適用されるか
//   読めないため（ユーザー報告 2026-07-31）、上の単一情報源から表示名を導く。
//   写像は参照実装 `indigators/profit_rsi/src/core.py` の `_APPLY_MAP` と一致させる:
//     1=OPEN / 2=HIGH / 3=LOW / 4=MEDIAN / 5=TYPICAL（既定）/ 6=WEIGHTED
//   ※ 終値は選択肢に無い（元 MQL の Apply が 1..6 のため）。範囲外を渡すと計算側は
//     CLOSE へ縮退するが、UI からその値は選べない。
const RSI_APPLY_TO_SOURCE = { 1: 'open', 2: 'high', 3: 'low', 4: 'hl2', 5: 'hlc3', 6: 'hlcc4' };
const RSI_APPLY_LABELS = Object.fromEntries(
  Object.entries(RSI_APPLY_TO_SOURCE).map(([value, src]) => [value, APPLIED_PRICE_LABELS[src]]),
);
const BTLM_TRAIL_SOURCE_LABELS = APPLIED_PRICE_LABELS;
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
    param('maxbars', ParamType.INT, 100, [{ kind: ConstraintKind.MIN_VALUE, operands: ['maxbars', 3], messageKey: 'err.maxbars' }], null, { group: 'group.calc', order: 2, step: 1, min: 3, unit: 'unit.bars', label: '移動期間（回帰）', isPeriod: true }),
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
      group: 'group.calc', order: 6, step: 1, min: 2, unit: 'unit.bars', label: '移動期間（分位）', isPeriod: true,
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
      group: 'group.display', order: 4, label: '移動期間（実績率）', step: 1, min: 2, unit: 'unit.bars', isPeriod: true,
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
    param('maxbars', ParamType.INT, 100, [{ kind: ConstraintKind.MIN_VALUE, operands: ['maxbars', 3], messageKey: 'err.maxbars' }], null, { group: 'group.calc', order: 2, step: 1, min: 3, unit: 'unit.bars', label: '移動期間（回帰）', isPeriod: true }),
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
      group: 'group.calc', order: 8, step: 1, min: 2, unit: 'unit.bars', label: '移動期間（分位）', isPeriod: true,
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
      group: 'group.robust', order: 3, step: 1, min: 1, unit: 'unit.bars', label: '移動期間', isPeriod: true,
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
const MA_SOURCE_LABELS = APPLIED_PRICE_LABELS;   // 同一概念＝同一情報源
const MA_SMOOTHING_LABELS = {
  none: 'なし', sma: 'SMA', ema: 'EMA', smma: 'SMMA', wma: 'WMA', sma_bb: 'SMA + ボリンジャーバンド',
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
    param('length', ParamType.INT, 9, [{ kind: ConstraintKind.MIN_VALUE, operands: ['length', 2], messageKey: 'err.length' }], null, { order: 2, label: '移動期間（平均）', step: 1, min: 2, isPeriod: true }),
    param('source', ParamType.ENUM, 'close', [], ['close', 'open', 'high', 'low', 'hl2', 'hlc3', 'ohlc4', 'hlcc4'], { order: 3, label: 'ソース', enumLabels: MA_SOURCE_LABELS }),
    param('offset', ParamType.INT, 0, [], null, { order: 4, label: 'オフセット', step: 1 }),
    // --- 平滑化 ---
    param('smoothing_type', ParamType.ENUM, 'none', [], ['none', 'sma', 'ema', 'smma', 'wma', 'sma_bb'], { group: '平滑化', order: 1, label: 'タイプ', enumLabels: MA_SMOOTHING_LABELS }),
    param('smoothing_length', ParamType.INT, 9, [{ kind: ConstraintKind.MIN_VALUE, operands: ['smoothing_length', 2], messageKey: 'err.length' }], null, { group: '平滑化', order: 2, label: '移動期間（平滑）', step: 1, min: 2, isPeriod: true }),
    // BB標準偏差: smoothing_type==sma_bb のときのみ有効（conditionalEnable で他はグレーアウト＝画像準拠）。
    param('bb_stddev', ParamType.FLOAT, 2.0, [], null, {
      group: '平滑化', order: 3, label: 'BB標準偏差', step: 0.001, min: 0.001,
      tooltip: 'ボリンジャーバンドの標準偏差倍率（平滑化タイプが「SMA + ボリンジャーバンド」のとき有効）',
      conditionalEnable: { when: { param: 'smoothing_type', equals: 'sma_bb' } },
    }),
    // --- 計算 ---
    // 「時間足」（計算.時間足）は全指標共通のため CALC_TIMEFRAME_PARAM として REGISTRY 構築時に
    //   注入する（ISSUE-274。ここに直書きすると第 2 定義になる）。
    // 既定 false: 未確定の最新足も計算し MA を最新足まで描画する（true だと最終足を除外し
    //   常に1本手前で止まる）。確定足のみで計算したい場合はダイアログで ON にする。
    //   上位足計算時は「形成中の上位足を使うか」の意味も兼ねる（投影の前方保持規約）。
    //   group は他指標の計算グループ（'group.calc'）へ揃える。注入される「時間足」と同じ見出しに
    //   入らないと、同一ダイアログ内に計算グループが 2 つ並ぶ（ISSUE-274）。
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
    param('length', ParamType.INT, 50, [{ kind: ConstraintKind.MIN_VALUE, operands: ['length', 2], messageKey: 'err.length' }], null, { group: 'group.calc', order: 3, label: '移動期間（平均）', step: 1, min: 2, unit: 'unit.bars', isPeriod: true }),
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
      group: 'group.calc', order: 9, step: 1, min: 2, unit: 'unit.bars', label: '移動期間（分位）', isPeriod: true,
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
  null, { group: 'group.calc', step: 1, min: 1, isPeriod: true, ...extraUi },
);
// 標準化窓 W（直近 W 本の過去のみで標準化＝look-ahead 除去・repaint しない）。
// profit_* の因果化済み 6 指標で共通の window パラメータ（def=120・min:2・専用ラベル）。
// PF_INT('window', ...) リテラルの DRY 集約（生成 param は従来と完全同一）。
const PF_WINDOW = () => PF_INT('window', 120, { min: 2, label: '移動期間（標準化）' });
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
    // 適用価格（何に対して RSI を計算するか）。数値のままでは意味が読めないため表示名を与える。
    param('apply', ParamType.ENUM, 5, [], [1, 2, 3, 4, 5, 6],
      { group: 'group.calc', label: 'ソース', enumLabels: RSI_APPLY_LABELS,
        tooltip: 'RSI を計算する価格。既定は (高値 + 安値 + 終値)/3' }),
    // 水準パラメータ（tickvol と同名・同既定＝同じ意味の設定は指標間で同じ名前にする）。
    //   元 MQL の σ 7 水準は全系列＝未来を含む非因果な水準だったため、因果ローリング分位＋
    //   POT/GPD へ全面置換した（承認 2026-08-02）。
    param('window_n', ParamType.INT, 500, [{ kind: ConstraintKind.MIN_VALUE, operands: ['window_n', 2], messageKey: 'err.window_n' }], null, {
      group: 'group.calc', order: 3, step: 1, min: 2, unit: 'unit.bars', label: '移動期間（閾値）', isPeriod: true,
      tooltip: '「普段どのあたりの RSI か」を測る因果ローリング窓の本数（既定 500）。当該バーは除外する（非リペイント）。元の σ 水準は全期間（未来を含む）で 1 本に固定されていたが、この窓で局所的に測り直す。',
    }),
    param('q_low', ParamType.FLOAT, 0.10, [
      { kind: ConstraintKind.RANGE_OPEN, operands: [0, 'q_low', 1], messageKey: 'err.q_low.range' },
      { kind: ConstraintKind.LT, operands: ['q_low', 'q_high'], messageKey: 'err.q_order' },
    ], null, {
      group: 'group.calc', order: 4, step: 0.01, min: 0, max: 1, label: '下側分位',
      tooltip: '正常帯の下端（既定 0.10＝下位 10%）。これを下回る RSI を「売られ過ぎイベント」として数える。下側 POT の閾値でもある。',
    }),
    param('q_high', ParamType.FLOAT, 0.90, [
      { kind: ConstraintKind.RANGE_OPEN, operands: [0, 'q_high', 1], messageKey: 'err.q_high.range' },
    ], null, {
      group: 'group.calc', order: 5, step: 0.01, min: 0, max: 1, label: '上側分位（外れ値の境目）',
      tooltip: '正常帯の上端（既定 0.90＝上位 10%）。これを超えた RSI を「買われ過ぎイベント」として数える。上側 POT の閾値でもある。閾値の自動選択（GPD 適合度＋ForwardStop）は実測で時間足ごとに 0.80〜0.95 へ散り、直近 50 件の当てはめでは 0.80〜0.95 のどこでも適合するため、観測数を確保できる 0.90 を既定にしている。',
    }),
    param('q_out', ParamType.FLOAT, 0.99, [], null, {
      group: 'group.calc', order: 6, label: '外れ値の極端分位', step: 0.01, min: 0, max: 1,
      tooltip: '過熱イベントの「極端にはどこまで行くか」の分位（既定 0.99）。経験的分位線（赤破線）と GPD 線（橙破線）は同じこの分位を推定しており、差は外挿量そのもの。空欄・上側分位以下・範囲外は極端線と GPD 線のみオフ。',
    }),
    param('k_events', ParamType.INT, 50, [{ kind: ConstraintKind.MIN_VALUE, operands: ['k_events', 1], messageKey: 'err.k_events' }], null, {
      group: 'group.calc', order: 7, step: 1, min: 1, label: '外れ値イベント数 K',
      tooltip: '水準を直近何件の過熱イベントから計算するか（既定 50・経験的分位と GPD で共通）。全履歴で当てはめると分布が非定常なため適合度検定に落ちるが、直近 50 件なら落ちない＝ローリングでこそ成立する。GPD 線は観測が 30 件に満たない区間では描かない（推定値が自身と同じ大きさで揺れるため）。',
    }),
  ],
  // 系列: RSI 本線＋正常帯 2 本（動的名 rsi_q{pct}）＋外れ値水準 4 本（経験的 evq_ext・GPD の
  //   上下）。水準は当該バー除外の因果ローリング分位に基づき時間で動くため、水平線ではなく
  //   時系列（line）で出す。命名は共有規約（common.event_quantiles / btlm_trail_q{pct}）に従う。
  series: [
    PF_LINE('rsi'),
    new SeriesDef({
      kind: SeriesKind.LINE, sourceColumn: null, seriesName: null, dynamic: true,
      seriesNamePattern: {
        template: 'rsi_q{pct}', buckets: [''],
        pcts: Array.from({ length: 99 }, (_, i) => String(i + 1)),
      },
    }),
    ...['rsi_evq_ext_hi', 'rsi_evq_ext_lo', 'rsi_gpd_hi', 'rsi_gpd_lo'].map(
      (n) => new SeriesDef({ kind: SeriesKind.LINE, sourceColumn: n, seriesName: n, dynamic: false }),
    ),
  ],
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

// --- tickvol_bands（プロファイルタブ・アクター委譲型）----------------------
// 一日の取引時間のうちティックが集中する時刻帯のチャートパネル背景色を変える（1 時間足以下）。
//   定義は tickvol_bands_catalog_entry.js（MP と同じ factory 注入方式）。
const TICKVOL_BANDS = makeTickvolBandsDef({
  IndicatorDef, SeriesDef, SeriesKind, ParamType, ConstraintKind, param, OHLC,
});

// --- cvfe（条件付ボラティリティ予測・OVERLAY）------------------------------
// 正本仕様 indigators/cvfe/CVFE_spec_v1.0.md。次バーの条件付ボラティリティ σ̂_{t+1} を
//   HAR-CJ-L で 1 期先予測し、各バーの水準を「ローソク足幅の水平ダッシュ」で並べる
//   （バー間を繋がない＝傾きという誤った情報を与えない）。確定バー不変（非リペイント）。
//   実バインディング add_cvfe（indigators/cvfe/src/lwc_chart.py）。
//
// 認知負荷の最小化（ユーザー厳命 2026-07-30）: 公開パラメータは 6 個に絞る。
//   内部固定にしたもの（いずれも「動かす根拠が無い」ことを実測または仕様で確認済み）:
//     refit_every=0   実測: 1/20/100 いずれも凍結に対し DM 検定で有意差なし（p=0.94〜0.97）。
//                     毎バー再学習は約 200 倍遅い（0.09s → 17.79s・2,600 本）。
//     lam_gap=0.97    窓開けが無い時間足では効果ゼロ。既定から動かす根拠が仕様 §10 に無い。
//     q_low/q_high/window_n/q_out/k_events/event_agg
//                     外れ値判定の内部しきい値。対応する線を持たず、名前と系列名が
//                     対応しないことが混乱の原因になっていた。共有既定のまま固定する。
//     show_outer/show_mid  σ線②は主要 2 本の一方なので常時表示。中心線は既定どおり非表示。
//
//   系列名: cvfe_u1 / cvfe_l1（σ線①）・cvfe_u2 / cvfe_l2（σ線②）・
//           cvfe_evq_{med|ext}_{hi|lo}（外れ値線・極端線）・cvfe_mid（中心・非表示）
//   ⚠ 仕様 §1 はバンド構築を CEB の責務としてスコープ外にしている（ISSUE-223）。
//   ⚠ UI 経路はティックを渡せず §4.1-6 の FAIL 縮退（PARK）で算出する（ISSUE-218）。
const CVFE_DISPLAY_LABELS = { dashes: '水平ダッシュ（バー毎）', bands: '線で繋いだ帯' };
const CVFE = new IndicatorDef({
  id: 'cvfe',
  displayNameKey: 'ind.cvfe',
  category: { group: 'builtin', nameKey: 'cat.technical' },
  tab: 'indicator',
  placement: 'overlay',
  params: [
    // 窓系パラメータの呼称は「移動期間」に統一する（ユーザー裁定 2026-07-30）。従来は同一概念に
    //   期間／学習本数／分位の窓／バンド内実績率の本数／… と 6 通りの呼び名が混在していた。
    //   cvfe は公開する窓が 1 つだけなので用途の注釈を付けない（専門用語を持ち込まない）。
    param('n_har', ParamType.INT, 500, [{ kind: ConstraintKind.MIN_VALUE, operands: ['n_har', 500], messageKey: 'err.n_har' }], null, {
      group: 'group.calc', order: 1, step: 1, min: 500, unit: 'unit.bars', label: '移動期間', isPeriod: true,
      tooltip: '【全ての線に影響】「過去の変動幅から次の変動幅を出す式」を作るのに、何本さかのぼるか（下限 500）。長いほど安定し、短いほど直近の地合いに追随する。先頭 本数+22 本は準備期間として何も描かない。',
    }),
    // σ線①/②: 描かれる線と 1:1 で対応する名前にする。
    param('sigma_inner', ParamType.FLOAT, 1.0, [
      { kind: ConstraintKind.RANGE_OPEN, operands: [0, 'sigma_inner', 10], messageKey: 'err.sigma_inner.range' },
      { kind: ConstraintKind.LT, operands: ['sigma_inner', 'sigma_outer'], messageKey: 'err.sigma_order' },
    ], null, {
      group: 'group.display', order: 1, step: 0.1, min: 0.1, max: 10, label: 'σ線①の倍率',
      tooltip: '系列 cvfe_u1 / cvfe_l1 の位置。予測変動幅 σ̂ の何倍に置くか。実測（jp225 5 分足）では 1.0 で高安の 62% が到達する。',
    }),
    param('sigma_outer', ParamType.FLOAT, 2.0, [
      { kind: ConstraintKind.RANGE_OPEN, operands: [0, 'sigma_outer', 10], messageKey: 'err.sigma_outer.range' },
    ], null, {
      group: 'group.display', order: 2, step: 0.1, min: 0.1, max: 10, label: 'σ線②の倍率',
      tooltip: '系列 cvfe_u2 / cvfe_l2 の位置。予測変動幅 σ̂ の何倍に置くか。実測（jp225 5 分足）では 2.0 で高安の 15%・終値の 7.8% が超える。',
    }),
    param('show_outliers', ParamType.BOOL, true, [], null, {
      group: 'group.display', order: 3, label: '外れ値線・極端線を表示',
      tooltip: '系列 cvfe_evq_med_hi / med_lo（外れ値線）と cvfe_evq_ext_hi / ext_lo（極端線）。正規分布の仮定ではなく、過去に実際に外れた履歴から測った水準。実測の到達率は外れ値線 4.8%・極端線 0.26%。',
    }),
    param('display_mode', ParamType.ENUM, 'dashes', [], ['dashes', 'bands'], {
      group: 'group.display', order: 4, label: '表示形式', enumLabels: CVFE_DISPLAY_LABELS,
      tooltip: '水平ダッシュ＝各バーの水準を、そのバーの幅だけの短い水平線で並べる（推奨。バー間を繋がないので傾きに誤った意味が乗らない）。線で繋いだ帯＝上下端を折れ線で結ぶ（検証用。傾きは価格そのものの動きで σ̂ の情報を持たない）。',
    }),
    // color はここに置かない。系列色は「スタイル」タブが系列ごとに持っており重複するため
    //   （ユーザー裁定 2026-07-30・認知負荷の最小化）。初期色は add_cvfe の既定値。
    param('dash_opacity', ParamType.FLOAT, 0.5, [
      { kind: ConstraintKind.RANGE_OPEN, operands: [0, 'dash_opacity', 1.01], messageKey: 'err.dash_opacity.range' },
    ], null, {
      group: 'group.display', order: 5, step: 0.05, min: 0.05, max: 1, label: 'ダッシュの濃さ',
      conditionalEnable: { when: { param: 'display_mode', equals: 'dashes' } },
      tooltip: '【全ての線に影響】水平ダッシュの不透明度（既定 0.5）。幅はローソク足の幅に自動で合うため、主張の強さはここで調整する。',
    }),
  ],
  // 系列: σ線①②（各上下）＋ 外れ値線・極端線（各上下）＋ 中心線。すべて価格スケール上。
  //   display_mode='dashes'（既定）は level_dash、'bands' は line で届くため同名で両 kind を宣言する。
  series: [
    ...['cvfe_mid', 'cvfe_u1', 'cvfe_l1', 'cvfe_u2', 'cvfe_l2'].flatMap((n) => [
      new SeriesDef({ kind: SeriesKind.LEVEL_DASH, sourceColumn: n, seriesName: n, dynamic: false }),
      new SeriesDef({ kind: SeriesKind.LINE, sourceColumn: n, seriesName: n, dynamic: false }),
    ]),
    ...EVQ_SERIES_DEFS('cvfe'),
    ...['med_hi', 'med_lo', 'ext_hi', 'ext_lo'].map((k) => new SeriesDef({
      kind: SeriesKind.LEVEL_DASH, sourceColumn: `cvfe_evq_${k}`, seriesName: `cvfe_evq_${k}`, dynamic: false,
    })),
  ],
  compute: { computeId: 'cvfe', requiredColumns: OHLC, timeRequired: true, backendParam: null, variants: ['default'] },
});

// --- tickvol（ティックボリューム・専用ペインのヒストグラム＋外れ値水準）------
// その足の間に到来した tick 数を、専用ペインのヒストグラム 1 本で描く（依頼者確定 2026-08-01）。
//   実バインディング add_tickvol（indigators/tickvol/src/lwc_chart.py）は供給側 volume 列
//   （＝tick 数。確定足はロールアップ、形成中足は forming_bar の len(mids)）を加工せず渡す。
// placement='pane': ローソクと価格スケールを共有しない（tick 数は価格と単位が異なる）。
//
// 外れ値水準（依頼者指示 2026-08-01「経験的分位＋GPD を並列表示」）:
//   正常帯上端（当該バー除外の因果ローリング分位＝POT の閾値）を超えた**エピソード極値**を
//   1 観測とし（宣言クラスタリング）、その超過分の同じ分位を経験的分位と GPD の 2 通りで
//   推定して並べる。2 本の差が「標本内で数えた値」と「裾の分布形から外挿した値」の差になる。
//   計算は既存の共有プリミティブ（common.marod_bands / common.event_quantiles / common.gpd）を
//   無改変参照する（indigators/tickvol/src/levels.py に実測根拠を記載）。
//
// 集計単位（event_agg）を公開しない理由: GPD は超過の独立を前提にする。実測（2026-08-01）で
//   生の閾値超過は θ=0.16〜0.27 と強くクラスタ化し、エピソード極値へ畳んで初めて θ=0.49〜0.89
//   （ゲート θ>=0.2）を満たす。「バー値」集計を選べるようにすると前提が壊れるため固定する。
const TICKVOL = new IndicatorDef({
  id: 'tickvol',
  displayNameKey: 'ind.ティックボリューム',
  category: { group: 'builtin', nameKey: 'cat.volume' },
  tab: 'indicator',
  placement: 'pane',
  params: [
    // window_n: 正常帯（＝POT 閾値）の因果ローリング窓。tickvol は水準そのものが非定常
    //   （実測: 履歴 4 分割の中央値が 5m 170→489・1h 666→2049）ため固定閾値は使えない。
    param('window_n', ParamType.INT, 500, [{ kind: ConstraintKind.MIN_VALUE, operands: ['window_n', 2], messageKey: 'err.window_n' }], null, {
      group: 'group.calc', order: 1, step: 1, min: 2, unit: 'unit.bars', label: '移動期間（閾値）', isPeriod: true,
      tooltip: '「普段どれくらいの tick 数か」を測る因果ローリング窓の本数（既定 500）。当該バーは除外する（非リペイント）。tick 数の水準は数か月スケールで数倍動くため、固定値ではなくこの窓で局所的に測り直す。',
    }),
    // 分位ペア（0<q_low<q_high<1・MAROD 系と対称の q-chain 制約）。正常帯の下側/上側分位で、
    //   上側は POT の閾値そのもの。下側は「普段より極端に静かな足」を示す表示専用
    //   （tick 数は最小 1 の計数量で下側は裾でないため GPD の対象にしない）。
    param('q_low', ParamType.FLOAT, 0.10, [
      { kind: ConstraintKind.RANGE_OPEN, operands: [0, 'q_low', 1], messageKey: 'err.q_low.range' },
      { kind: ConstraintKind.LT, operands: ['q_low', 'q_high'], messageKey: 'err.q_order' },
    ], null, {
      group: 'group.calc', order: 2, step: 0.01, min: 0, max: 1, label: '下側分位',
      tooltip: '正常帯の下端（既定 0.10＝下位 10%）。これを下回る足は「普段より極端に静か」。表示専用で、外れ値イベント・GPD の算出には使わない。',
    }),
    // q_high: 正常帯の上側分位＝POT の閾値分位。ForwardStop（common.gpd.select_threshold）の
    //   自動選択は実測で 5m 0.95 / 15m 0.90 / 1h 0.85 と時間足で動くため、採択域の内側で
    //   観測件数が最も確保できる 0.90 を既定にする。
    param('q_high', ParamType.FLOAT, 0.90, [
      { kind: ConstraintKind.RANGE_OPEN, operands: [0, 'q_high', 1], messageKey: 'err.q_high.range' },
    ], null, {
      group: 'group.calc', order: 3, step: 0.01, min: 0, max: 1, label: '上側分位（外れ値の境目）',
      tooltip: '正常帯の上端。この分位を超えた足を「外れ値イベント」として数える（既定 0.90＝上位 10%）。この閾値が GPD の当てはめ開始点（POT の閾値）でもある。閾値の自動選択（GPD 適合度＋ForwardStop）は実測で 5 分足 0.95・1 時間足 0.85 と時間足で動くため、その内側の 0.90 を既定にしている。',
    }),
    // q_out: イベント超過分の極端分位。経験的線と GPD 線は**同じ q_out** を推定する
    //   （だから 2 本を並べて読める）。無効値は共有規約 q_out_valid で黙ってオフ。
    param('q_out', ParamType.FLOAT, 0.99, [], null, {
      group: 'group.calc', order: 4, label: '外れ値の極端分位', step: 0.01, min: 0, max: 1,
      tooltip: '外れ値イベントの「極端にはどこまで行くか」の分位（既定 0.99）。経験的分位線（赤破線）と GPD 線（橙破線）は同じこの分位を推定しており、差は外挿量そのもの。空欄・上側分位以下・範囲外は極端線と GPD 線のみオフ。',
    }),
    // k_events: 水準に使う直近観測件数（経験的・GPD で共通）。実測で全履歴の当てはめは
    //   AD 適合度検定で棄却され（p=0.005〜0.255）、直近 50 件では棄却されない（p=0.455〜0.720）。
    param('k_events', ParamType.INT, 50, [{ kind: ConstraintKind.MIN_VALUE, operands: ['k_events', 1], messageKey: 'err.k_events' }], null, {
      group: 'group.calc', order: 5, step: 1, min: 1, label: '外れ値イベント数 K',
      tooltip: '水準を直近何件の外れ値イベントから計算するか（既定 50・経験的分位と GPD で共通）。全履歴で当てはめると分布が非定常なため適合度検定に落ちるが、直近 50 件なら落ちない＝ローリングでこそ成立する。GPD 線は観測が 30 件に満たない区間では描かない（推定値が自身と同じ大きさで揺れるため）。',
    }),
  ],
  // 系列: 本体ヒストグラム＋正常帯 2 本（動的名）＋水準線 3 本（典型深度＝実線／経験的極端
  //   分位・GPD＝破線）。命名 `_evq_{med|ext}_{hi}` と `_q{pct}` はいずれも共有規約に従う
  //   （前者 common.event_quantiles・後者 btlm_trail_q{pct} と対称）。イベント水準の下側
  //   （_evq_*_lo）は持たない（tick 数は 1 以上の計数量で下側は裾でない・実測 min=1）。
  series: [
    PF_HIST('tickvol'),
    // 正常帯（動的・q_low/q_high に依存＝tickvol_q{pct}）。
    new SeriesDef({
      kind: SeriesKind.LINE, sourceColumn: null, seriesName: null, dynamic: true,
      seriesNamePattern: {
        template: 'tickvol_q{pct}', buckets: [''],
        pcts: Array.from({ length: 99 }, (_, i) => String(i + 1)),
      },
    }),
    ...['tickvol_evq_med_hi', 'tickvol_evq_ext_hi', 'tickvol_gpd_hi'].map(
      (n) => new SeriesDef({ kind: SeriesKind.LINE, sourceColumn: n, seriesName: n, dynamic: false }),
    ),
  ],
  compute: { computeId: 'tickvol', requiredColumns: OHLC, timeRequired: false, backendParam: null, variants: ['default'] },
});

// ---------------------------------------------------------------------------
// 計算.時間足（上位足計算・ISSUE-274）
// ---------------------------------------------------------------------------
// 「この指標を何の足で計算するか」は指標固有の性質ではなく全指標に共通の設定であるため、
//   各定義へ直書きせず REGISTRY 構築時に 1 箇所から注入する（第 2 定義を作らない）。
//   選択肢は時間足台帳（TF_LEDGER 由来の TF_CODES）から導出する。手書きの配列にすると
//   台帳へ足しても追随せず静かにずれる（ISSUE-254 / ISSUE-261 と同型の事故源）。
const CALC_TIMEFRAME_LABELS = Object.freeze({
  chart: 'チャート', '1m': '1分', '5m': '5分', '15m': '15分', '30m': '30分', '1h': '1時間',
  '4h': '4時間', '1D': '日', '1W': '週', '1M': '月',
});

const calcTimeframeParam = () => param(
  'timeframe', ParamType.ENUM, 'chart', [], ['chart', ...TF_CODES],
  {
    group: 'group.calc', order: 1, label: '時間足', enumLabels: CALC_TIMEFRAME_LABELS,
    tooltip: 'この指標を計算する時間足（「チャート」はチャートの時間足に追従）',
  },
);

// def へ計算.時間足を付与する。**先頭へ**置く（依頼者指示 2026-08-08: 設定ダイアログの一番上）。
//   ダイアログのグループ順は form_model が「param の初出順」で決めるため、先頭に置くことで
//   group.calc（時間足）が最初の見出しになる。順序の決定点は本関数 1 箇所（指標定義は無改変）。
// 対象外:
//   - アクター駆動型（market_profile / tickvol_bands）: /compute を持たず投影経路に乗らない。
//     効かない設定を出さない（表示できるものと効くものを一致させる）。
//   - すでに timeframe を持つ定義: 二重付与しない（将来 def 側で特別扱いする余地を残す）。
function withCalcTimeframe(def) {
  if (isActorDriven(def) || def.params.some((p) => p.name === 'timeframe')) {
    return def;
  }
  return new IndicatorDef({ ...def, params: [calcTimeframeParam(), ...def.params] });
}

const REGISTRY = Object.freeze([
  TGP_BTLM, BTLM_TRAIL, BTLM_TRAIL_MAROD, MA_MAROD, CVFE, PROFIT_BAND, PRICE_RANGE_POWER, MOVING_AVERAGES, MARKET_PROFILE, TICKVOL_BANDS, TICKVOL,
  PROFIT_ADX_NEEDLE, PROFIT_ARCTAN, PROFIT_MFI, PROFIT_RSI, PROFIT_STC,
  PROFIT_OSCILLATOR, PROFIT_OSCILLATOR2, PROFIT_OSI_MA, PROFIT_RMM, PROFIT_VOLATILITY,
  PROFIT_HL_BAND, PROFIT_HLBAND, PROFIT_MFI_MACD, PROFIT_RMM_MACD, PROFIT_RSI_MACD,
].map(withCalcTimeframe));
const BY_ID = new Map(REGISTRY.map((d) => [d.id, d]));

// カテゴリ key → 表示名。従来は index.html に 3 件だけ直書きされており、oscillator(10) と
//   band(2) のボタンが無いまま 24 指標中 12 件が絞り込みから到達不能だった（ISSUE-221）。
//   ここを単一情報源とし、サイドバーは categories() から動的生成する（新カテゴリの指標を
//   足しても HTML の同時改変が不要＝OCP）。
export const CATEGORY_LABELS = Object.freeze({
  'cat.technical': 'テクニカル',
  'cat.oscillator': 'オシレーター',
  'cat.statistics': '統計',
  'cat.volume': '出来高',
  'cat.band': 'バンド',
});

// 登録済み指標が実際に持つカテゴリを、REGISTRY の出現順で返す（key と件数）。
//   未知 key は表示名を key そのものにフォールバックする（登録漏れで消えないこと）。
export function categories() {
  const counts = new Map();
  for (const d of REGISTRY) {
    const key = d.category?.nameKey;
    if (!key) continue;
    counts.set(key, (counts.get(key) ?? 0) + 1);
  }
  return [...counts].map(([key, count]) => ({ key, count, label: CATEGORY_LABELS[key] ?? key }));
}

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
// サーバ由来の variant 別受理 param（GET /catalog の paramScopes）をレジストリへ overlay する
//   （ISSUE-278 #8・applyServerDefaults と対称）。
//
// scopes = { compute_id: { variant: [param_name, ...] } }（back catalog_schema.PARAM_SCOPES の配信形）。
// 各 ParamDef へ「その param を受理する variant の配列」を書き込む。全 variant が受理する param は
// null（＝variant 非依存）にして、単一 variant 指標のフィールドが余計な条件を持たないようにする。
//
// これが無いと front は variant 横断の全 params を送り、back が受理しない引数を捨てる（旧挙動）。
// 結果として「動かしても計算結果が変わらないコントロール」が UI に出続けていた（実測: profit_band
// variant=global の normalize/window/atr_period/min_obs は応答 byte 同一）。反映した param 数を返す。
export function applyServerParamScopes(scopes) {
  if (!scopes || typeof scopes !== 'object') {
    return 0;
  }
  let applied = 0;
  for (const [id, byVariant] of Object.entries(scopes)) {
    const def = BY_ID.get(id);
    if (!def || !byVariant || typeof byVariant !== 'object') {
      continue;
    }
    const variants = Object.keys(byVariant);
    if (variants.length === 0) {
      continue;
    }
    for (const p of def.params) {
      const accepting = variants.filter((v) => (byVariant[v] || []).includes(p.name));
      // 全 variant が受理 → null（条件なし）。1 つも受理しない param は back 契約に無い＝
      //   catalog_schema_sync.test.js が検出する対象なので、ここでは条件を付けない。
      p.variants = (accepting.length === variants.length || accepting.length === 0)
        ? null
        : accepting;
      applied += 1;
    }
  }
  return applied;
}

// params から「その variant が受理しないキー」を落とす（ISSUE-278 #8）。
//   def が無い / scope 未 overlay（旧サーバ・オフライン）の param は素通し＝従来挙動。
//   back は未受理キーを無言で捨てず validation エラーにするため、送信前にここで絞る。
export function scopedParams(def, variant, params) {
  if (!def || !params || typeof params !== 'object') {
    return params;
  }
  const scoped = {};
  for (const [name, value] of Object.entries(params)) {
    const p = def.params.find((q) => q.name === name);
    // ISSUE-281: **許可リスト**。カタログが知らない param は送らない。
    //   旧実装は拒否リスト（「除外指定がある param だけ落とす」）で、定義に無い名前を素通ししていた。
    //   サーバは ISSUE-278 #8 で無言破棄をやめフェイルクローズ化したため、古い永続状態
    //   （applied.v1 / テンプレート）に残った廃止 param が 400 を引き起こし、その指標は**永久に
    //   計算できなくなる**（実測: profit_rsi に `ma_period` が残ると常に validation エラー）。
    //   受理集合は /catalog の paramScopes が ParamDef へ overlay 済み＝front は正解を知っている。
    if (!p) {
      continue;
    }
    if (Array.isArray(p.variants) && variant && !p.variants.includes(variant)) {
      continue;
    }
    scoped[name] = value;
  }
  return scoped;
}

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
