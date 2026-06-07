// 指標レジストリ（usecase/catalog.js）。
//
// 実在 4 バインディング（tgp_btlm / profit_band global,robust / price_range_power）の
// IndicatorDef を最低限定義し、list() / get(id) を提供する（§3.1.3）。
// 検索・フィルタ（UC-01）は id/display_name を対象とし series/params は読まない（§4.6）。
// 架空指標は足さない（実在バインディング中心）。

import { ConstraintKind, ParamType } from '../domain/constraint_eval.js';
import { IndicatorDef, SeriesDef, SeriesKind } from '../domain/domain_models.js';

const OHLC = ['open', 'high', 'low', 'close'];

function param(name, type, def, constraints = [], enumValues = null) {
  return { name, labelKey: `label.${name}`, type, default: def, enumValues, constraints };
}

// --- tgp_btlm（回帰チャネル・OVERLAY）-------------------------------------
const TGP_BTLM = new IndicatorDef({
  id: 'tgp_btlm',
  displayNameKey: 'ind.tgp_btlm',
  category: { group: 'builtin', nameKey: 'cat.technical' },
  tab: 'indicator',
  placement: 'overlay',
  params: [
    param('fitter', ParamType.ENUM, 'ols', [], ['ols', 'tgp']),
    param('maxbars', ParamType.INT, 40, [{ kind: ConstraintKind.MIN_VALUE, operands: ['maxbars', 1], messageKey: 'err.maxbars' }]),
    param('q_low', ParamType.FLOAT, 0.05, [
      { kind: ConstraintKind.RANGE_OPEN, operands: [0, 'q_low', 1], messageKey: 'err.q_low.range' },
      { kind: ConstraintKind.LT, operands: ['q_low', 'q_high'], messageKey: 'err.q_order' },
    ]),
    param('q_high', ParamType.FLOAT, 0.95, [
      { kind: ConstraintKind.RANGE_OPEN, operands: [0, 'q_high', 1], messageKey: 'err.q_high.range' },
    ]),
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
    param('probabilities', ParamType.FLOAT_LIST, [0.95, 0.99], [
      { kind: ConstraintKind.RANGE_OPEN, operands: [0, 'probabilities', 1], messageKey: 'err.prob' },
    ]),
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
    param('interval', ParamType.ENUM, 0.1, [], [0.1, 0.01, 0.001]),
    param('top_n', ParamType.INT, 2, [{ kind: ConstraintKind.MIN_VALUE, operands: ['top_n', 0], messageKey: 'err.top_n' }]),
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
