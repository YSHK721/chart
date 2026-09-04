// catalog_color_roles.test.js — 全 SeriesDef への colorRole 付与の台帳テスト
//   （基本設計_指標カラーテーマ.md §4.1.3 付与規則・§4.1.5 全数内訳・§7.4 段階 1 通過条件 1〜4）。
//
// 本テストは「実装が設計書の表と一致すること」を全数で固定する。設計書 §4.1.5 の表を逐語で
//   写し、行和（指標ごとの SeriesDef 数）・列和（トークンごとの件数）・総計 97 の三方向で突き
//   合わせる。1 件の取り残しも 1 件の余剰も落ちる（クロム 19→20 の取り残しと同型の事故を防ぐ）。

import test from 'node:test';
import assert from 'node:assert/strict';

import { list, get } from '../js/usecase/catalog.js';
import { SeriesKind } from '../js/domain/domain_models.js';
import { COLOR_ROLES, ColorRole, isColorRole } from '../js/domain/color_roles.js';
import { CHROME_TOKENS } from '../js/usecase/chrome_tokens.js';
import { buildSeriesStyleRows } from '../js/usecase/form_model.js';
import { expandSeriesNamePattern, expectedSeriesNames } from '../js/adapter/front/series_name_matcher.js';

// --- §4.1.5 の表（逐語）--------------------------------------------------
// [指標 id, SeriesDef 数, bullish, bearish, neutral, alert, primary, secondary, range, level, muted]
const LEDGER = [
  ['tgp_btlm', 2, 0, 0, 1, 0, 0, 0, 1, 0, 0],
  ['btlm_trail', 7, 0, 0, 1, 2, 0, 0, 1, 0, 3],
  ['btlm_trail_marod', 7, 0, 0, 0, 4, 1, 0, 1, 1, 0],
  ['ma_marod', 7, 0, 0, 0, 4, 1, 0, 1, 1, 0],
  ['cvfe', 18, 0, 0, 2, 8, 0, 0, 8, 0, 0],
  ['profit_band', 4, 1, 1, 0, 0, 0, 0, 2, 0, 0],
  ['price_range_power', 1, 0, 0, 0, 0, 0, 0, 0, 1, 0],
  ['moving_averages', 4, 0, 0, 0, 0, 1, 1, 2, 0, 0],
  ['market_profile', 1, 0, 0, 0, 0, 0, 0, 0, 1, 0],
  ['tickvol_bands', 1, 0, 0, 0, 0, 0, 0, 0, 1, 0],
  ['tickvol', 5, 0, 0, 0, 3, 1, 0, 1, 0, 0],
  ['profit_adx_needle', 2, 0, 0, 0, 0, 1, 0, 0, 1, 0],
  ['profit_arctan', 2, 0, 0, 0, 0, 1, 0, 0, 1, 0],
  ['profit_mfi', 3, 0, 0, 0, 0, 1, 1, 0, 1, 0],
  ['profit_rsi', 6, 0, 0, 0, 4, 1, 0, 1, 0, 0],
  ['profit_stc', 2, 0, 0, 0, 0, 1, 0, 0, 1, 0],
  ['profit_oscillator', 2, 0, 0, 0, 0, 1, 0, 0, 1, 0],
  ['profit_oscillator2', 3, 0, 0, 0, 0, 1, 1, 0, 1, 0],
  ['profit_osi_ma', 2, 0, 0, 0, 0, 1, 0, 0, 1, 0],
  ['profit_rmm', 2, 0, 0, 0, 0, 1, 0, 0, 1, 0],
  ['profit_volatility', 2, 0, 0, 0, 0, 1, 0, 0, 1, 0],
  ['profit_hl_band', 1, 0, 0, 0, 0, 0, 0, 0, 1, 0],
  ['profit_hlband', 2, 0, 0, 0, 0, 1, 0, 0, 1, 0],
  ['profit_mfi_macd', 4, 0, 0, 0, 0, 2, 1, 0, 1, 0],
  ['profit_rmm_macd', 3, 0, 0, 0, 0, 2, 1, 0, 0, 0],
  ['profit_rsi_macd', 4, 0, 0, 0, 0, 2, 1, 0, 1, 0],
];
// 表の列順（SeriesDef 数の次から）。§4.1.5 の見出しと同順。
const LEDGER_TOKENS = ['bullish', 'bearish', 'neutral', 'alert', 'primary', 'secondary', 'range', 'level', 'muted'];
// §4.1.5 の合計行。
const LEDGER_TOTAL = { total: 97, bullish: 1, bearish: 1, neutral: 4, alert: 25, primary: 21, secondary: 6, range: 18, level: 18, muted: 3 };

function countByRole(def) {
  const counts = Object.fromEntries(LEDGER_TOKENS.map((t) => [t, 0]));
  for (const s of def.series) {
    counts[s.colorRole] = (counts[s.colorRole] ?? 0) + 1;
  }
  return counts;
}

// --- §7.4 段階 1 通過条件 1 --------------------------------------------
test('通過条件 1: 全 SeriesDef に colorRole が在席し、値は語彙 14 種のいずれか', () => {
  for (const def of list()) {
    for (const s of def.series) {
      assert.ok(isColorRole(s.colorRole),
        `${def.id}: seriesName=${s.seriesName} colorRole=${String(s.colorRole)} が語彙外`);
    }
  }
});

test('通過条件 1: SeriesDef の総数は 97 件（§4.1.5 合計）', () => {
  const total = list().reduce((n, d) => n + d.series.length, 0);
  assert.equal(total, LEDGER_TOTAL.total);
});

test('REGISTRY は 26 指標（§4.1.5 の行数と一致）', () => {
  assert.equal(list().length, LEDGER.length);
  assert.deepEqual(list().map((d) => d.id).sort(), LEDGER.map((r) => r[0]).sort());
});

// --- §4.1.5 全数内訳（行和・列和） --------------------------------------
test('§4.1.5: 指標ごとの SeriesDef 数とトークン内訳が表と全数一致する', () => {
  for (const row of LEDGER) {
    const [id, seriesCount, ...tokenCounts] = row;
    const def = get(id);
    assert.ok(def, `未登録の指標: ${id}`);
    assert.equal(def.series.length, seriesCount, `${id}: SeriesDef 数`);
    const actual = countByRole(def);
    const expected = Object.fromEntries(LEDGER_TOKENS.map((t, i) => [t, tokenCounts[i]]));
    assert.deepEqual(actual, expected, `${id}: トークン内訳`);
  }
});

test('§4.1.5: 列和（トークンごとの総件数）が合計行と一致する', () => {
  const totals = Object.fromEntries(LEDGER_TOKENS.map((t) => [t, 0]));
  for (const def of list()) {
    for (const s of def.series) {
      totals[s.colorRole] += 1;
    }
  }
  for (const t of LEDGER_TOKENS) {
    assert.equal(totals[t], LEDGER_TOTAL[t], `列和 ${t}`);
  }
  assert.equal(Object.values(totals).reduce((a, b) => a + b, 0), LEDGER_TOTAL.total);
});

test('§4.1.1: クロム専用 5 トークンは指標側 SeriesDef に 1 件も付与されない', () => {
  const chromeOnly = new Set(['surface', 'grid', 'border', 'text', 'highlight']);
  for (const def of list()) {
    for (const s of def.series) {
      assert.equal(chromeOnly.has(s.colorRole), false,
        `${def.id}/${s.seriesName}: クロム専用トークン ${s.colorRole} が指標へ付与されている`);
    }
  }
});

// --- §7.4 段階 1 通過条件 2 --------------------------------------------
test('通過条件 2: kind==="horizontal_line" の 18 件がすべて level', () => {
  let n = 0;
  for (const def of list()) {
    for (const s of def.series) {
      if (s.kind === SeriesKind.HORIZONTAL_LINE) {
        n += 1;
        assert.equal(s.colorRole, ColorRole.LEVEL, `${def.id}/${s.seriesName}`);
      }
    }
  }
  assert.equal(n, 18, 'horizontal_line の件数');
  // level を持つ SeriesDef は horizontal_line だけ（§4.1.3 規則 1・5 の裏側）。
  const levelCount = list().reduce(
    (acc, d) => acc + d.series.filter((s) => s.colorRole === ColorRole.LEVEL).length, 0,
  );
  assert.equal(levelCount, 18);
});

// --- §7.4 段階 1 通過条件 3 --------------------------------------------
// 規則 3（同名系列は同一トークン）の適用範囲は「実描画系列の名前空間」に限る。
//   実測: series_drawer._createPriceLines は styleMeta を書かないため、horizontal_line の
//   seriesName は getSeriesStyles（＝§5.8 の解決入力）に一度も現れない（E-10）。水準線名は
//   payload グループ id であって実描画系列名ではなく、両者は**別の名前空間**である。
//   この区別を入れないと規則 1（horizontal_line は必ず level）と規則 3 が btlm_trail_marod /
//   ma_marod（同名の line と水平基準線）で同時に成立しない。
const RENDERED_KINDS = new Set([SeriesKind.LINE, SeriesKind.HISTOGRAM, SeriesKind.LEVEL_DASH]);

test('通過条件 3: 実描画系列の名前空間で、同一 seriesName の colorRole は一致する', () => {
  for (const def of list()) {
    const byName = new Map();
    for (const s of def.series) {
      if (!s.seriesName || !RENDERED_KINDS.has(s.kind)) continue;
      if (!byName.has(s.seriesName)) {
        byName.set(s.seriesName, new Set());
      }
      byName.get(s.seriesName).add(s.colorRole);
    }
    for (const [name, roles] of byName) {
      assert.equal(roles.size, 1,
        `${def.id}/${name}: 同名系列に複数トークン ${[...roles].join(',')}（解決が非決定になる）`);
    }
  }
});

test('水準線の seriesName は実描画系列名と衝突し得る（別名前空間であることの明示）', () => {
  // btlm_trail_marod / ma_marod は line と 0% 水平基準線が同名。前者 primary・後者 level で
  //   食い違うが、水準線は priceLine 経路で applySeriesStyle に到達しないため衝突しない。
  for (const id of ['btlm_trail_marod', 'ma_marod']) {
    const def = get(id);
    const rendered = def.series.filter((s) => RENDERED_KINDS.has(s.kind) && s.seriesName === id);
    const hlines = def.series.filter((s) => s.kind === SeriesKind.HORIZONTAL_LINE && s.seriesName === id);
    assert.equal(rendered.length, 1, `${id}: 同名の実描画系列`);
    assert.equal(hlines.length, 1, `${id}: 同名の水準線`);
    assert.equal(rendered[0].colorRole, ColorRole.PRIMARY);
    assert.equal(hlines[0].colorRole, ColorRole.LEVEL);
  }
});

test('§4.1.3 規則 4: 動的 SeriesDef はパターン単位で 1 トークン（展開後も同一）', () => {
  for (const def of list()) {
    for (const s of def.series) {
      if (s.dynamic) {
        assert.ok(isColorRole(s.colorRole), `${def.id}: 動的 SeriesDef の colorRole`);
      }
    }
  }
});

// --- §7.4 段階 1 通過条件 4（profit_band 分割の挙動不変） -----------------
// 分割前は buckets 4 種 × pcts 7 種の動的 SeriesDef 1 件。分割後は 1 bucket × 7 pcts の 4 件。
//   和集合が同一（28 名）かつ buildSeriesStyleRows の行構成が同一（4 行）であることを固定する。
const PB_BUCKETS = ['nOH', 'pOL', 'pOH', 'nOL'];
const PB_PCTS = ['51', '80', '85', '90', '95', '98', '99'];
const PB_EXPECTED_NAMES = PB_BUCKETS.flatMap((b) => PB_PCTS.map((p) => `${b} ${p}%`));

test('通過条件 4: profit_band は bucket 別 4 件の SeriesDef へ分割されている', () => {
  const def = get('profit_band');
  assert.equal(def.series.length, 4);
  const buckets = def.series.map((s) => {
    assert.equal(s.dynamic, true);
    assert.deepEqual(s.seriesNamePattern.pcts, PB_PCTS);
    assert.equal(s.seriesNamePattern.template, '{bucket} {pct}%');
    assert.equal(s.seriesNamePattern.buckets.length, 1);
    return s.seriesNamePattern.buckets[0];
  });
  assert.deepEqual(buckets, PB_BUCKETS, '分割の順序は分割前の buckets 宣言順を保つ');
});

test('通過条件 4: 展開集合は分割前と同一（28 名の和集合）', () => {
  const def = get('profit_band');
  const union = new Set();
  for (const s of def.series) {
    for (const n of expandSeriesNamePattern(s.seriesNamePattern)) {
      union.add(n);
    }
  }
  assert.equal(union.size, 28);
  assert.deepEqual([...union].sort(), [...PB_EXPECTED_NAMES].sort());
  // F3 照合の入口（expectedSeriesNames）でも同一集合になること。
  assert.deepEqual([...expectedSeriesNames(def)].sort(), [...PB_EXPECTED_NAMES].sort());
});

test('通過条件 4: buildSeriesStyleRows の行構成は 4 行（bucket 粒度）で分割前と同一', () => {
  const def = get('profit_band');
  const seriesStyles = PB_EXPECTED_NAMES.map((name) => ({
    name, kind: 'line', color: '#1565c0', width: 1, style: 'solid', visible: true,
  }));
  const rows = buildSeriesStyleRows(def, seriesStyles);
  assert.equal(rows.length, 4);
  assert.deepEqual(rows.map((r) => r.label), PB_BUCKETS);
  // 各行が当該 bucket の 7 系列を丸ごと束ねる（畳み込みの粒度が変わっていない）。
  for (const r of rows) {
    assert.equal(r.names.length, 7, `${r.label} の系列数`);
    assert.ok(r.names.every((n) => n.startsWith(`${r.label} `)), `${r.label} の系列名`);
  }
});

test('§4.1.4: 方向の意味が bucket に対応して付与されている（E-24 の統合）', () => {
  const def = get('profit_band');
  const byBucket = Object.fromEntries(
    def.series.map((s) => [s.seriesNamePattern.buckets[0], s.colorRole]),
  );
  // pOH＝陽線の Open→High 幅（上伸）／nOL＝陰線の Open→Low 幅（下落）が方向を表す。
  assert.equal(byBucket.pOH, ColorRole.BULLISH);
  assert.equal(byBucket.nOL, ColorRole.BEARISH);
  // pOL / nOH は塗りバンド端であり方向を表さない（U-12 の是正）。
  assert.equal(byBucket.pOL, ColorRole.RANGE);
  assert.equal(byBucket.nOH, ColorRole.RANGE);
});

// --- §4.1.3 規則 2 / 5 ---------------------------------------------------
test('§4.1.3 規則 2: 読取欄専用系列（btlm_trail の β/σ/実績率）は muted', () => {
  const def = get('btlm_trail');
  const muted = def.series.filter((s) => s.colorRole === ColorRole.MUTED).map((s) => s.seriesName);
  assert.deepEqual(muted.sort(),
    ['btlm_trail_band_hit_rate', 'btlm_trail_beta', 'btlm_trail_sigma']);
});

test('§4.1.3 規則 5: アクター駆動指標のダミー SeriesDef も level（描画されず無害）', () => {
  for (const id of ['market_profile', 'tickvol_bands']) {
    const def = get(id);
    assert.equal(def.series.length, 1);
    assert.equal(def.series[0].kind, SeriesKind.HORIZONTAL_LINE);
    assert.equal(def.series[0].colorRole, ColorRole.LEVEL);
  }
});

// --- §4.1.3 規則 6 ------------------------------------------------------
test('§4.1.3 規則 6: bullish/bearish は方向を伝える系列にのみ付与（v1 は profit_band のみ）', () => {
  const holders = [];
  for (const def of list()) {
    for (const s of def.series) {
      if (s.colorRole === ColorRole.BULLISH || s.colorRole === ColorRole.BEARISH) {
        holders.push(def.id);
      }
    }
  }
  assert.deepEqual([...new Set(holders)], ['profit_band']);
});

test('語彙台帳のトークンはすべて実在の宣言先を持つか、クロム専用である（死語を作らない）', () => {
  const used = new Set();
  for (const def of list()) {
    for (const s of def.series) {
      used.add(s.colorRole);
    }
  }
  // クロム専用語の一覧は**手書きしない**。配線点台帳（chrome_tokens.js）から導くことで、
  //   「語彙に足したがどこにも配線しなかった」＝死語（通過条件 4）が構成上ここで落ちる。
  //   手書きの集合にすると、語を足すときに集合へも足せてしまい、死語の検出が効かなくなる。
  const chromeOnly = new Set(CHROME_TOKENS);
  for (const token of COLOR_ROLES) {
    assert.ok(used.has(token) || chromeOnly.has(token), `${token} はどこからも宣言されていない`);
  }
});
