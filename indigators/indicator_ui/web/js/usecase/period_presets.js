// 期間プリセット（usecase/period_presets.js）。
//
// 設計入力: `.doc/indicator-management-ui/基本設計_期間プリセット.md` v0.1.0
//   §4 換算モデル / §5 対象パラメータ / §6.1 提示 / §6.3 期間表記入力 / §6.5 実効計算時間足。
//
// 責務: 「暦期間 ⇄ バー本数」の換算に関する純ロジックのみ。DOM・fetch・localStorage 非依存
//   （母体「純ロジック分離」方針・基本設計書 §10.4）。DOM 生成は adapter
//   （property_control_builders.js の buildPeriod）が担う。
//
// 換算プリミティブ（設計 §4.1・唯一の定義）:
//   bars(tf, P) = 半開ローリング窓 [t - P, t) に含まれる実バー本数の中央値
//   （右端 t は実バー時刻のみを走査。窓の左端が実測期間の外に出る t は除外）
// プリセット値（§6.1）と期間表記入力の換算値（§6.3）は、いずれもこの 1 つの表から導く。

// ---------------------------------------------------------------------------
// 換算表 v1（設計 §4.3・実測値）
// ---------------------------------------------------------------------------
// 版         : v1
// 計測日     : 2026-07-28
// データ     : data/marketdata/rollups/jp225_tick/jp225_tick_{tf}.csv
//              （1m のみ data/marketdata/jp225_tick_m1.csv）
// 実測期間   : 日中足（1m〜4h）= 2021-01-01 以降（セッション構造が現行値で定常な区間・設計 §4.5 検証 1）
//              1D/1W/1M     = 全期間（2012 年〜。日足以上の本数は取引日カレンダーのみに依存する）
// 日切り規約 : marketdata.session_day（ブローカー時間＝NY +7h・境界は NY 前日 17:00）
// 再現手順   : 設計書 §11
//
// ★ 表は静的定数として凍結する（設計 §4.4）。実行時の再計測・サーバ問い合わせは行わない。
//   更新は明示的な版上げ（v2 の追加）でのみ行い、v1 は削除しない。保存済みパラメータは
//   本数として保存されているため、版上げによって遡って変化しない。
const TABLE_V1 = Object.freeze({
  jp225_tick: Object.freeze({
    '1m': Object.freeze({ '1h': 60, '4h': 237, '1d': 1281, '1w': 6425, '1mo': 27505, '3mo': 82560, '6mo': 164788, '1y': 328956, '2y': 645585, '3y': 976826, '5y': 1634898 }),
    '5m': Object.freeze({ '1h': 12, '4h': 48, '1d': 266, '1w': 1329, '1mo': 5731, '3mo': 17099, '6mo': 34011, '1y': 68151, '2y': 136153, '3y': 204185, '5y': 340504 }),
    '15m': Object.freeze({ '1h': 4, '4h': 16, '1d': 89, '1w': 445, '1mo': 1921, '3mo': 5730, '6mo': 11400, '1y': 22814, '2y': 45585, '3y': 68373, '5y': 113987 }),
    '30m': Object.freeze({ '1h': 2, '4h': 8, '1d': 45, '1w': 225, '1mo': 971, '3mo': 2897, '6mo': 5765, '1y': 11538, '2y': 23052, '3y': 34578, '5y': 57646 }),
    '1h': Object.freeze({ '1h': 1, '4h': 4, '1d': 23, '1w': 115, '1mo': 496, '3mo': 1480, '6mo': 2946, '1y': 5895, '2y': 11781, '3y': 17670, '5y': 29456 }),
    '4h': Object.freeze({ '4h': 1, '1d': 6, '1w': 31, '1mo': 134, '3mo': 401, '6mo': 798, '1y': 1597, '2y': 3195, '3y': 4789, '5y': 7985 }),
    '1D': Object.freeze({ '1d': 1, '1w': 5, '1mo': 21, '3mo': 65, '6mo': 129, '1y': 258, '2y': 516, '3y': 774, '5y': 1291 }),
    '1W': Object.freeze({ '1w': 1, '1mo': 4, '3mo': 13, '6mo': 26, '1y': 52, '2y': 104, '3y': 156, '5y': 260 }),
    '1M': Object.freeze({ '1mo': 1, '3mo': 3, '6mo': 6, '1y': 12, '2y': 24, '3y': 36, '5y': 60 }),
  }),
});

export const PRESET_TABLE_VERSION = 'v1';

// 単位の並び（短い順・§6.1-4 の「期間の短い順」の唯一源）。
const UNIT_ORDER = Object.freeze(['1h', '4h', '1d', '1w', '1mo', '3mo', '6mo', '1y', '2y', '3y', '5y']);

// 単位 → 表示名（設計 §4.3 の表見出しと一致させる）。
const UNIT_LABELS = Object.freeze({
  '1h': '1時間', '4h': '4時間', '1d': '1日', '1w': '1週間',
  '1mo': '1ヶ月', '3mo': '3ヶ月', '6mo': '6ヶ月',
  '1y': '1年', '2y': '2年', '3y': '3年', '5y': '5年',
});

// 時間足 → バー秒長（名目）。分単位の換算（§6.3-3）にのみ使う。周期集合は
//   marketdata.resample.TIMEFRAME_RULES と一致させる（front 側の名目値）。
const TF_SEC = Object.freeze({
  '1m': 60, '5m': 300, '15m': 900, '30m': 1800,
  '1h': 3600, '4h': 14400, '1D': 86400, '1W': 604800, '1M': 2592000,
});

// 提示の下限（§6.1-2）。本数 1 は「期間の指定」として意味を持たない。
export const MIN_PRESET_BARS = 2;

// 提示の上限（§6.1-2・承認事項 A-6）。チャートが保持するバーの上限であり、
//   adapter/front/composition_root_front.js の RECENT_BARS と一致していなければならない。
//   一致は tests/period_presets.test.js が固定する（usecase → adapter の依存を作らないため、
//   ここは定数で持ち、テストで突き合わせる）。
export const MAX_PRESET_BARS = 1500;

// 提示件数の上限（§6.1-4・承認事項 A-5）。
export const MAX_PRESETS = 5;

// ---------------------------------------------------------------------------
// 実効計算時間足（§6.5）
// ---------------------------------------------------------------------------

// 指標ごとの計算時間足 override（params.timeframe）を解決する。
//   'chart'／未指定はチャートの現在足に追従し、特定足（1h 等）はその足で計算する（MTF）。
//   規則は adapter/front/timeframe_controller.js の effectiveTimeframe と同一（二重定義しない
//   ため同値の純関数として置き、adapter 側は既存メソッドを保持する）。
export function effectiveTimeframe(values, chartTimeframe) {
  const tfParam = values && values.timeframe;
  return tfParam && tfParam !== 'chart' ? tfParam : chartTimeframe;
}

// ---------------------------------------------------------------------------
// 表の引き当て
// ---------------------------------------------------------------------------

// datasetRef × timeframe の行を返す（未登録は null＝F-P2/F-P3）。
export function tableRow(datasetRef, timeframe) {
  const byRef = TABLE_V1[datasetRef];
  if (!byRef) {
    return null;
  }
  return byRef[timeframe] ?? null;
}

// 単位の表示名（未知は単位キーをそのまま返す）。
export function unitLabel(unit) {
  return UNIT_LABELS[unit] ?? String(unit);
}

// ---------------------------------------------------------------------------
// UC-P01 プリセットの提示（§6.1）
// ---------------------------------------------------------------------------

// 提示するプリセットを決定する。戻り値: [{ unit, label, bars }]（期間の短い順・最大 limit 件）。
//   min/max は当該パラメータの制約（ParamDef.min / ParamDef.max）。null は制約なし。
//   候補 0 件（未登録 datasetRef/tf・制約が厳しい）は空配列を返す＝呼び出し側は UI を出さない。
export function presetsFor({
  datasetRef, timeframe, min = null, max = null,
  maxBars = MAX_PRESET_BARS, limit = MAX_PRESETS,
} = {}) {
  const row = tableRow(datasetRef, timeframe);
  if (!row) {
    return [];
  }
  const out = [];
  for (const unit of UNIT_ORDER) {
    const bars = row[unit];
    if (bars === undefined) {
      continue; // 当該時間足で 1 本未満になる組み合わせ（表に無い）。
    }
    if (bars < MIN_PRESET_BARS || bars > maxBars) {
      continue;
    }
    if (min !== null && min !== undefined && bars < min) {
      continue;
    }
    if (max !== null && max !== undefined && bars > max) {
      continue;
    }
    out.push({ unit, label: unitLabel(unit), bars });
    if (out.length >= limit) {
      break;
    }
  }
  return out;
}

// ---------------------------------------------------------------------------
// UC-P03 期間表記の直接入力（§6.3）
// ---------------------------------------------------------------------------

// 入力文字列を正規化する（全角→半角・前後空白除去・内部空白除去）。
//   NFKC は 'Ｍ'→'M' / 'ｍ'→'m' のように**大文字小文字を保存**するため、
//   M（月）と m（分）の区別は正規化後も失われない。
function normalizeInput(text) {
  const s = String(text ?? '');
  const nfkc = typeof s.normalize === 'function' ? s.normalize('NFKC') : s;
  return nfkc.replace(/\s+/g, '');
}

// 単位トークン → 種別。'M'（月）と 'm'（分）の衝突を避けるため、
//   分は 'min'、月は 'M'／'mo' のみを受理する（裸の 'm' は曖昧として拒否・§6.3 文法）。
const UNIT_PATTERNS = [
  { kind: 'min', re: /^(min|分)$/i },
  { kind: 'h', re: /^(h|時間)$/i },
  { kind: 'd', re: /^(d|日|営業日)$/i },
  { kind: 'w', re: /^(w|週間|週)$/i },
  // 月: 'M'（大文字のみ）・'mo'（大小問わず）・日本語各表記。
  { kind: 'mo', re: /^(M|mo|MO|Mo|ヶ月|ケ月|ヵ月|カ月|か月|month|months)$/ },
  { kind: 'y', re: /^(y|年)$/i },
];

// 種別 → 表の基本単位キー（§6.3-2 の「基本単位の N 倍」の基準）。
const BASE_UNIT_BY_KIND = Object.freeze({ h: '1h', d: '1d', w: '1w', mo: '1mo', y: '1y' });

// 種別 × 係数 → 表の直接エントリ（§6.3-1 の「直接エントリがあればその実測値」）。
function directUnitKey(kind, n) {
  if (!Number.isInteger(n)) {
    return null;
  }
  const key = kind === 'mo' ? `${n}mo` : kind === 'y' ? `${n}y` : `${n}${kind}`;
  return UNIT_ORDER.includes(key) ? key : null;
}

// 期間表記／本数を解釈して本数へ換算する。
//
// 戻り値（成功）: { ok: true, bars, label }（label は入力の期間表記。単位なし入力は null）
// 戻り値（失敗）: { ok: false, code, message }
//   code: 'empty' | 'syntax' | 'ambiguous_unit' | 'no_table' | 'too_small'
//         | 'below_min' | 'above_max' | 'exceeds_capacity'
export function parsePeriodInput(text, {
  datasetRef, timeframe, min = null, max = null, maxBars = MAX_PRESET_BARS,
} = {}) {
  const s = normalizeInput(text);
  if (s === '') {
    return { ok: false, code: 'empty', message: '値を入力してください。' };
  }

  // 単位なし＝本数そのもの（従来どおり）。
  if (/^\d+(\.\d+)?$/.test(s)) {
    return finalize(Math.round(Number(s)), null, { min, max, maxBars });
  }

  const m = s.match(/^(\d+(?:\.\d+)?)(.+)$/);
  if (!m) {
    return { ok: false, code: 'syntax', message: '「50」「5d」「3M」のように入力してください。' };
  }
  const n = Number(m[1]);
  const token = m[2];

  if (token === 'm') {
    return {
      ok: false,
      code: 'ambiguous_unit',
      message: '「m」は分と月のどちらか判別できません。分は「min」、月は「M」を使ってください。',
    };
  }
  const hit = UNIT_PATTERNS.find((p) => p.re.test(token));
  if (!hit) {
    return { ok: false, code: 'syntax', message: `単位「${token}」は使えません（min / h / d / w / M / y）。` };
  }
  if (!(n > 0)) {
    return { ok: false, code: 'too_small', message: '期間は 0 より大きい値で指定してください。' };
  }

  const label = `${m[1]}${normalizeUnitLabel(hit.kind)}`;

  // 分単位は名目換算（§6.3-3。セッション内で分足・時間足に欠落がないことを実測で確認済み）。
  if (hit.kind === 'min') {
    const sec = TF_SEC[timeframe];
    if (!sec) {
      return { ok: false, code: 'no_table', message: 'この時間足では期間表記を使えません。' };
    }
    return finalize(Math.round((n * 60) / sec), label, { min, max, maxBars });
  }

  const row = tableRow(datasetRef, timeframe);
  if (!row) {
    return { ok: false, code: 'no_table', message: 'この時間足では期間表記を使えません。' };
  }

  // 1) 表に直接エントリがあればその実測値を用いる。
  const direct = directUnitKey(hit.kind, n);
  if (direct && row[direct] !== undefined) {
    return finalize(row[direct], label, { min, max, maxBars });
  }
  // 2) 無ければ基本単位の N 倍。
  const base = row[BASE_UNIT_BY_KIND[hit.kind]];
  if (base === undefined) {
    return { ok: false, code: 'too_small', message: `${timeframe} では「${label}」は 1 本未満になります。` };
  }
  return finalize(Math.round(n * base), label, { min, max, maxBars });
}

// 種別 → 表示用単位（label 組み立て用）。
function normalizeUnitLabel(kind) {
  return { min: '分', h: '時間', d: '日', w: '週間', mo: 'ヶ月', y: '年' }[kind] ?? '';
}

// 換算結果を制約と突き合わせて確定する（§6.3-4/5・F-P1・F-P4）。
function finalize(bars, label, { min, max, maxBars }) {
  if (!Number.isFinite(bars) || bars < 1) {
    return { ok: false, code: 'too_small', message: 'この時間足では 1 本未満になります。' };
  }
  if (min !== null && min !== undefined && bars < min) {
    return { ok: false, code: 'below_min', message: `${bars} 本は下限 ${min} 本を下回ります。` };
  }
  if (max !== null && max !== undefined && bars > max) {
    return { ok: false, code: 'above_max', message: `${bars} 本は上限 ${max} 本を超えます。` };
  }
  if (bars > maxBars) {
    return {
      ok: false,
      code: 'exceeds_capacity',
      message: `${bars} 本はチャートが保持する ${maxBars} 本を超えます。`,
    };
  }
  return { ok: true, bars, label };
}
