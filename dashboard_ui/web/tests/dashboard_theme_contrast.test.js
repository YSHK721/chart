// 版面の**可読性**を宣言ではなく計算で固定する。
//
// なぜ必要か（ISSUE-461 の実測）: 「読みにくい」は状態検証（DOM の中身が正しいか）では
//   原理的に落ちない。既存 119 件はすべて緑のまま、下部ペインの地（--ct-uiPanel 相当）の上で
//   本文と背景のコントラスト比が 4.312 しか無い状態を保護していた（下の #実測 参照）。
//   よって「読めること」を**性質**として機械的に検査する。
//
// 実測（本ファイル導入前・rgba 合成後の対 uiText コントラスト比）:
//   地 uiSurface #131722 : 過熱端 4.574 / 沈静端 4.851
//   地 uiPanel   #1e222d : 過熱端 4.312 / 沈静端 4.564   ← 4.5 未満
//   地 uiBorder  #2a2e39 : 過熱端 4.045 / 沈静端 4.272   ← 4.5 未満（行 hover の地）
//   さらに旧 CSS は副文を opacity .75 で薄めており、過熱セル上の実効比は 3.11（同 uiPanel）。
//
// 固定するのは**性質**であって色そのものではない（具体 hex は 1 つも書かない）。
//   - 期待値は「4.5 以上」「識別できる隔たりがある」という条件のみ。
//   - 色の値は heat_scale.js と dashboard.css から**読み取って**検査する。
//     どちらかが色を変えれば、この検定が自分で読み直して合否を出す。
//
// 参照した規格: WCAG 2.1 SC 1.4.3（本文の最低コントラスト比 4.5:1）の相対輝度・比の定義。
// 隣接識別は CIE76 の ΔE（Lab 空間のユークリッド距離）。JND は概ね 2.3 とされるため、
//   段差の下限はその上に取る（3.0）。

import { test, describe } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

import { colorForP, tailUnscaledColor } from '../js/adapter/front/heat_scale.js';

const CSS = readFileSync(fileURLToPath(new URL('../css/dashboard.css', import.meta.url)), 'utf8');
const LADDER_VIEW = readFileSync(fileURLToPath(new URL('../js/adapter/front/reach_sheet_view.js', import.meta.url)), 'utf8');
const OSC_VIEW = readFileSync(fileURLToPath(new URL('../js/adapter/front/oscillator_sheet_view.js', import.meta.url)), 'utf8');

/** WCAG 2.1 SC 1.4.3 の本文コントラスト比。 */
const MIN_CONTRAST = 4.5;

/** 隣接の識別下限（CIE76 ΔE。JND ≒ 2.3 の上に取る）。 */
const MIN_DELTA_E = 3.0;

/** `p` の刻み。この幅だけ離れた 2 点は色で見分けられねばならない。 */
const P_STEP = 0.125;

/** heat の地の上に載る文字を持つ選択子（View が背景を塗る要素とその子）。 */
const HEAT_BACKED_CLASSES = Object.freeze([
  'dash-ladder-price',        // 3 分割の帯を敷く価格セル
  'dash-ladder-price-text',   // その上に載る価格の文字
  'dash-osc-cell',            // p で塗るセル
  'dash-osc-tail-unscaled',   // 帯外単一色のセル
  'dash-osc-value',           // セル内の現在値
  'dash-osc-reach',           // セル内の到達時刻
  'dash-osc-no-level',        // セル内の「水準なし」
]);

// ---------------------------------------------------------------- 色の計算

/** `#rgb` / `#rrggbb` / `#rrggbbaa` / `rgb()` / `rgba()` を [r,g,b,a] へ。色でなければ null。 */
function parseColor(value) {
  const v = String(value).trim();
  const hex = /^#([0-9a-fA-F]{3,8})$/.exec(v);
  if (hex) {
    const h = hex[1];
    const expand = (s) => parseInt(s.length === 1 ? s + s : s, 16);
    if (h.length === 3 || h.length === 4) {
      return [expand(h[0]), expand(h[1]), expand(h[2]), h.length === 4 ? expand(h[3]) / 255 : 1];
    }
    if (h.length === 6 || h.length === 8) {
      const at = (i) => parseInt(h.slice(i * 2, i * 2 + 2), 16);
      return [at(0), at(1), at(2), h.length === 8 ? at(3) / 255 : 1];
    }
    return null;
  }
  const fn = /^rgba?\(([^()]*)\)$/.exec(v);
  if (fn) {
    const parts = fn[1].split(',').map((s) => Number(s.trim()));
    if (parts.length < 3 || parts.slice(0, 3).some((n) => !Number.isFinite(n))) return null;
    return [parts[0], parts[1], parts[2], parts.length > 3 && Number.isFinite(parts[3]) ? parts[3] : 1];
  }
  return null;
}

/** 半透明色を地の上へ合成して不透明色にする（実ブラウザの重ね順と同じ計算）。 */
function composite(color, base) {
  const a = color[3];
  return [0, 1, 2].map((i) => a * color[i] + (1 - a) * base[i]);
}

/** sRGB 1 チャネルの線形化（WCAG 2.1 の定義）。 */
function channel(v) {
  const s = v / 255;
  return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
}

/** 相対輝度（WCAG 2.1）。 */
function luminance(rgb) {
  return 0.2126 * channel(rgb[0]) + 0.7152 * channel(rgb[1]) + 0.0722 * channel(rgb[2]);
}

/** コントラスト比（WCAG 2.1）。 */
function contrast(fg, bg) {
  const a = luminance(fg);
  const b = luminance(bg);
  const [hi, lo] = a > b ? [a, b] : [b, a];
  return (hi + 0.05) / (lo + 0.05);
}

/** sRGB → CIE Lab（D65）。 */
function lab(rgb) {
  const [r, g, b] = [0, 1, 2].map((i) => channel(rgb[i]));
  const x = (0.4124 * r + 0.3576 * g + 0.1805 * b) / 0.95047;
  const y = 0.2126 * r + 0.7152 * g + 0.0722 * b;
  const z = (0.0193 * r + 0.1192 * g + 0.9505 * b) / 1.08883;
  const f = (t) => (t > 216 / 24389 ? Math.cbrt(t) : (841 / 108) * t + 4 / 29);
  return [116 * f(y) - 16, 500 * (f(x) - f(y)), 200 * (f(y) - f(z))];
}

/** CIE76 の色差。 */
function deltaE(p, q) {
  const a = lab(p);
  const b = lab(q);
  return Math.hypot(a[0] - b[0], a[1] - b[1], a[2] - b[2]);
}

// ---------------------------------------------------------------- CSS の読み取り

/** `var(--ct-*, fallback)` の fallback（＝テーマ未供給時に実際に描かれる値）。 */
const VAR_WITH_FALLBACK = /var\(\s*--ct-[\w-]+\s*,\s*((?:[^(),]|\([^()]*\))+?)\s*\)/g;

/** 規則（選択子と宣言の対）へ分解する。 */
function cssRules() {
  const body = CSS.replace(/\/\*[\s\S]*?\*\//g, '');
  const out = [];
  const re = /([^{}]+)\{([^{}]*)\}/g;
  let m = re.exec(body);
  while (m) {
    out.push({ selector: m[1].trim(), body: m[2] });
    m = re.exec(body);
  }
  return out;
}

/** 宣言（プロパティと値の対）へ分解する。 */
function declarations(ruleBody) {
  return ruleBody
    .split(';')
    .map((chunk) => chunk.trim())
    .filter(Boolean)
    .map((chunk) => {
      const at = chunk.indexOf(':');
      return { prop: chunk.slice(0, at).trim().toLowerCase(), value: chunk.slice(at + 1).trim() };
    })
    .filter((d) => d.prop && d.value);
}

/** 1 つの値が持つトークン fallback 色の一覧。 */
function fallbackColors(value) {
  const out = [];
  for (const m of String(value).matchAll(VAR_WITH_FALLBACK)) {
    const color = parseColor(m[1]);
    if (color) out.push(color);
  }
  return out;
}

/** `.dash-sheet-host` が敷く地（＝ホストの既定の背景）。 */
function hostBackground() {
  for (const rule of cssRules()) {
    if (!/(^|,|\s)\.dash-sheet-host\s*(,|$)/.test(rule.selector)) continue;
    for (const d of declarations(rule.body)) {
      if (d.prop !== 'background' && d.prop !== 'background-color') continue;
      const colors = fallbackColors(d.value);
      if (colors.length > 0) return colors[0];
    }
  }
  return null;
}

/** 版面が敷きうる地の一覧（heat はこのいずれかの上に載る）。半透明はホスト地へ合成する。 */
function paintedBackgrounds(host) {
  const bases = [[host[0], host[1], host[2]]];
  for (const rule of cssRules()) {
    for (const d of declarations(rule.body)) {
      if (d.prop !== 'background' && d.prop !== 'background-color') continue;
      for (const color of fallbackColors(d.value)) {
        bases.push(composite(color, host));
      }
    }
  }
  return bases;
}

/** heat の地の上に載りうる文字色（当該選択子の宣言＋ホストからの継承）。 */
function textColorsOverHeat(host) {
  const colors = [];
  for (const rule of cssRules()) {
    const touches = HEAT_BACKED_CLASSES.some((c) => rule.selector.includes(`.${c}`))
      || /\.dash-sheet-host\s*$/.test(rule.selector.split(',')[0].trim());
    if (!touches) continue;
    for (const d of declarations(rule.body)) {
      if (d.prop !== 'color') continue;
      for (const color of fallbackColors(d.value)) colors.push(composite(color, host));
    }
  }
  return colors;
}

/** 目盛りの上の色（p の全域）と帯外単一色を、指定の地へ合成した一覧。 */
function heatBackgrounds(base) {
  const out = [];
  for (let i = 0; i <= 40; i += 1) {
    const p = i / 40;
    const color = parseColor(colorForP(p));
    assert.ok(color, `heat_scale が解釈できない色を返しました: p=${p}`);
    out.push({ what: `p=${p}`, rgb: composite(color, base) });
  }
  const tail = parseColor(tailUnscaledColor());
  assert.ok(tail, '帯外単一色を解釈できません');
  out.push({ what: '帯外単一色', rgb: composite(tail, base) });
  return out;
}

// ---------------------------------------------------------------- 検定

describe('dashboard の版面 — 読めることを計算で固定する', () => {
  test('every_heat_background_keeps_its_text_above_the_minimum_contrast_ratio', () => {
    // Arrange: 地は CSS が宣言したものすべて（ホスト地・見出し地・行 hover 地…）。
    const host = hostBackground();
    assert.ok(host, '.dash-sheet-host が地を宣言していません（合成の基準が決まらない）');
    const bases = paintedBackgrounds(host);
    const texts = textColorsOverHeat(host);
    assert.ok(texts.length > 0, 'heat の上に載る文字色が 1 つも読み取れません');

    // Act & Assert: 全 p × 全地 × 全文字色。
    for (const base of bases) {
      for (const bg of heatBackgrounds(base)) {
        for (const fg of texts) {
          const ratio = contrast(fg, bg.rgb);
          assert.ok(
            ratio >= MIN_CONTRAST,
            `${bg.what} の上で文字が読めません: コントラスト比 ${ratio.toFixed(3)} < ${MIN_CONTRAST}`,
          );
        }
      }
    }
  });

  test('two_p_values_one_step_apart_stay_distinguishable_on_every_background', () => {
    // 濃さが量を担う以上、刻みが潰れると「隔たり」が読めなくなる。
    const host = hostBackground();
    assert.ok(host, '.dash-sheet-host が地を宣言していません');
    for (const base of paintedBackgrounds(host)) {
      for (let p = P_STEP; p <= 1 + 1e-9; p += P_STEP) {
        const lower = composite(parseColor(colorForP(p - P_STEP)), base);
        const upper = composite(parseColor(colorForP(Math.min(p, 1))), base);
        const d = deltaE(lower, upper);
        assert.ok(
          d >= MIN_DELTA_E,
          `p=${(p - P_STEP).toFixed(3)} と p=${Math.min(p, 1).toFixed(3)} が見分けられません: ΔE=${d.toFixed(2)}`,
        );
      }
    }
  });

  test('the_off_scale_colour_stays_distinguishable_from_every_colour_on_the_scale', () => {
    // §5.3.2: 帯外単一色は「目盛りが無い」ことを示す。目盛り上の色と紛れたら意味が消える。
    const host = hostBackground();
    for (const base of paintedBackgrounds(host)) {
      const tail = composite(parseColor(tailUnscaledColor()), base);
      for (let i = 0; i <= 40; i += 1) {
        const onScale = composite(parseColor(colorForP(i / 40)), base);
        const d = deltaE(tail, onScale);
        assert.ok(d >= MIN_DELTA_E, `帯外単一色が p=${i / 40} と紛れます: ΔE=${d.toFixed(2)}`);
      }
    }
  });

  test('the_stylesheet_expresses_every_colour_through_a_theme_token', () => {
    // 生の色を直書きすると、宿主のテーマを変えたときにそこだけ取り残される
    //   （MEMORY: enforce-constraints-mechanically）。許すのは var(--ct-*, …) の fallback のみ。
    const literal = /#[0-9a-fA-F]{3,8}\b|\brgba?\(|\bhsla?\(|\b(?:white|black|red|blue|green|gray|grey|silver|yellow|orange|purple|navy|teal|maroon|olive|lime|aqua|fuchsia)\b/;
    for (const rule of cssRules()) {
      for (const d of declarations(rule.body)) {
        const stripped = d.value.replace(VAR_WITH_FALLBACK, '');
        assert.equal(
          literal.test(stripped), false,
          `トークンを経由しない色があります: ${rule.selector} { ${d.prop}: ${d.value} }`,
        );
      }
    }
  });

  test('the_selectors_this_check_relies_on_are_the_ones_the_views_actually_use', () => {
    // 検定の検定: View 側の改名でこの検査が無言の no-op に化けるのを防ぐ。
    const sources = `${LADDER_VIEW}\n${OSC_VIEW}`;
    for (const name of HEAT_BACKED_CLASSES) {
      assert.ok(sources.includes(name), `View が使わないクラスを検査しています: ${name}`);
    }
  });
});
