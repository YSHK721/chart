// 指標レジストリ（usecase/catalog.js）。
//
// 実在 4 バインディング（tgp_btlm / profit_band global,robust / price_range_power）の
// IndicatorDef を最低限定義し、list() / get(id) を提供する（§3.1.3）。
// 検索・フィルタ（UC-01）は id/display_name を対象とし series/params は読まない（§4.6）。
// 架空指標は足さない（実在バインディング中心）。

import { ConstraintKind, ParamType } from '../domain/constraint_eval.js';
import { IndicatorDef, SeriesDef, SeriesKind } from '../domain/domain_models.js';

const OHLC = ['open', 'high', 'low', 'close'];

// ParamDef（JS plain object）を生成する。
//
// 必須引数 name/type/def は従来どおり。constraints/enumValues も従来位置を維持。
// 第 6 引数 ui は UI 向けメタデータ（純 UI 情報・後方互換）。省略時は従来同値で、
// ConstraintEvaluator.evaluate は ui フィールドを参照しない（constraint_eval.js:53-67）
// ため evaluate 挙動は不変（§3.3.3 移行方針・C-3）。
//
// ui のオプション: group / controlType / tooltip / unit / step / min / max /
//                  conditionalEnable / order / uiVisible。すべて末尾 default 付き。
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
  order: null,
  uiVisible: true,
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
    param('price', ParamType.ENUM, 'open', [], ['open', 'high', 'low', 'close'], { group: 'group.calc', order: 2 }),
    // maxbars 既定 40→100 是正（M-1・core.py:33 DEFAULT_MAXBARS=100）。
    param('maxbars', ParamType.INT, 100, [{ kind: ConstraintKind.MIN_VALUE, operands: ['maxbars', 1], messageKey: 'err.maxbars' }], null, { group: 'group.calc', order: 3, step: 1, min: 1, unit: 'unit.bars' }),
    param('q_low', ParamType.FLOAT, 0.05, [
      { kind: ConstraintKind.RANGE_OPEN, operands: [0, 'q_low', 1], messageKey: 'err.q_low.range' },
      { kind: ConstraintKind.LT, operands: ['q_low', 'q_high'], messageKey: 'err.q_order' },
    ], null, { group: 'group.calc', order: 4, step: 0.01, min: 0, max: 1 }),
    param('q_high', ParamType.FLOAT, 0.95, [
      { kind: ConstraintKind.RANGE_OPEN, operands: [0, 'q_high', 1], messageKey: 'err.q_high.range' },
    ], null, { group: 'group.calc', order: 5, step: 0.01, min: 0, max: 1 }),
    // color は COLOR＝スタイルタブへ移譲（§4.1）。既定は実コード add_btlm の
    // color=_COLOR（MediumSlateBlue・lwc_chart.py:33,73）。
    param('color', ParamType.COLOR, 'rgba(123, 104, 238, 1)', [], null, { group: 'group.style', order: 1 }),
  ],
  // 系列名は実バインディング（precomputed["tgp_btlm:default"]）の name に一致させる
  // （F3 _validateSeriesNames は series_name 基準・§3.3.6）。回帰チャネルは
  // 平均線 btlm_mean ＋ 分位線 btlm_q5 / btlm_q95（lwc_chart.py の create_line(name=...)）。
  series: [
    new SeriesDef({ kind: SeriesKind.LINE, sourceColumn: 'btlm_mean', seriesName: 'btlm_mean', dynamic: false }),
    new SeriesDef({ kind: SeriesKind.LINE, sourceColumn: 'btlm_q5', seriesName: 'btlm_q5', dynamic: false }),
    new SeriesDef({ kind: SeriesKind.LINE, sourceColumn: 'btlm_q95', seriesName: 'btlm_q95', dynamic: false }),
  ],
  compute: { computeId: 'tgp_btlm', requiredColumns: OHLC, timeRequired: true, backendParam: 'fitter', variants: ['default'] },
});

// --- profit_band（global / robust・PANE）---------------------------------
const PROFIT_BAND = new IndicatorDef({
  id: 'profit_band',
  displayNameKey: 'ind.profit_band',
  category: { group: 'builtin', nameKey: 'cat.statistics' },
  tab: 'indicator',
  placement: 'pane',
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
  compute: { computeId: 'profit_band', requiredColumns: OHLC, timeRequired: true, backendParam: null, variants: ['global', 'robust'] },
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

const REGISTRY = Object.freeze([TGP_BTLM, PROFIT_BAND, PRICE_RANGE_POWER]);
const BY_ID = new Map(REGISTRY.map((d) => [d.id, d]));

// 全 IndicatorDef を返す（読み取り専用配列の複製）。
export function list() {
  return [...REGISTRY];
}

// id で IndicatorDef を返す。未知 id は null。
export function get(id) {
  return BY_ID.get(id) ?? null;
}
