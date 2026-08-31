// 版面の**可読性**を宣言ではなく計算で固定する。
//
// なぜ必要か（ISSUE-461 の実測）: 「読みにくい」は状態検証（DOM の中身が正しいか）では
//   原理的に落ちない。既存 119 件はすべて緑のまま、下部ペインの地の上で本文と背景の
//   コントラスト比が 4.312 しか無い状態を保護していた。よって「読めること」を**性質**として
//   機械的に検査する。
//
// ISSUE-463（版面モックへの同期）で変わった点と、変えていない点:
//   変えた: パレットが `--ct-*`（宿主のテーマ）から本 CSS が自分で宣言するトークンへ移り、
//     **明・暗の 2 テーマ**を持つようになった。したがって両テーマの解決値で同じ検査を回す。
//   変えた: 地と文字の総当たりを「実際に同じ場所へ載る組」（下の SURFACES）に限定した。
//     モックは現在値行を**反転**（地 --ink・文字 --bg）させる。全地 × 全文字色の総当たりは
//     この版面では**充足不能**である——例えば --muted は --bg（明・L=0.919）と --ink（L=0.0086）
//     の両方に対して 4.5 を満たす必要が生じるが、前者は L ≤ 0.161、後者は L ≥ 0.214 を要求し、
//     両立する値が存在しない。総当たりは実在しない組を含む過大近似だったので、実在する組へ
//     詰めた（弱めたのではなく、**取りこぼしを塞ぐ被覆検定**を下に足してある）。
//   変えていない: 4.5:1（WCAG 2.1 SC 1.4.3）・隣接 ΔE ≥ 3・色は必ずトークン経由、の 3 性質。
//   変えていない: 具体 hex を 1 つも書かないこと。色は heat_scale.js と dashboard.css から
//     **読み取って**検査する。どちらかが色を変えれば、この検定が自分で読み直して合否を出す。
//
// 参照した規格: WCAG 2.1 SC 1.4.3（本文の最低コントラスト比 4.5:1）の相対輝度・比の定義。
// 隣接識別は CIE76 の ΔE（Lab 空間のユークリッド距離）。JND は概ね 2.3 とされるため、
//   段差の下限はその上に取る（3.0）。

import { test, describe } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

import { colorForP, tailUnscaledColor } from '../js/adapter/front/heat_scale.js';
import { styleRules, declarations, tokensIn } from './_css.js';

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
// `dash-osc-no-level` は含めない: 「水準なし」の小片は**不透明な地（--amber-bg）を自分で
//   持つ**ので heat の上には載らない（別の地として SURFACES に在る）。
const HEAT_BACKED_CLASSES = Object.freeze([
  'dash-ladder-price',        // 3 分割の帯を敷く価格セル
  'dash-ladder-price-text',   // その上に載る価格の文字
  'dash-ladder-gap',          // その下に添える直前行との差
  'dash-osc-cell',            // p で塗るセル
  'dash-osc-tail-unscaled',   // 帯外単一色のセル
  'dash-osc-value',           // セル内の現在値
  'dash-osc-reach',           // セル内の到達時刻
]);

/**
 * 実際に同じ場所へ載る「地 × その上の文字」。
 *
 * 各項の根拠は dashboard.css の規則 1 つと View 1 か所で、どちらかが増えたら下の
 * `every_colour_the_stylesheet_paints_is_covered_by_a_surface` が落ちる（表が腐らない）。
 *
 * `heatTexts` は「その地に敷かれた heat の**上に**載る文字」。heat は価格セル
 * （.dash-ladder-price）とオシレータのセル（.dash-osc-cell）にしか敷かれないので、
 * 同じ地に載る文字でも欄が違えば heat の上には来ない（距離欄・水準名欄・表頭など）。
 */
const SURFACES = Object.freeze([
  // .dash-sheet-host（版面の地）: 見出し・凡例・段落が直接載る。heat はここには敷かれない
  //   （表は必ず .dash-panel / .dash-scroll が --surface を敷いた内側にある）。
  { name: 'ホストの地', ground: '--bg', texts: ['--ink', '--ink2', '--muted'] },
  // .dash-panel / .dash-scroll / .dash-ladder-row / .dash-osc-name（表の地）。
  {
    name: '表の地',
    ground: '--surface',
    texts: ['--ink', '--ink2', '--muted', '--up', '--down'],
    heatTexts: ['--ink', '--ink2'],
  },
  // .dash-panel-head / thead th / .dash-tf-pill（一段沈めた地）。
  { name: '表頭と枠の頭', ground: '--surface2', texts: ['--ink', '--ink2', '--muted', '--cyan'] },
  // .dash-ladder-hit（到達側の帯）。--up-bg を 55% 薄めた合成済みの実色。
  {
    name: '到達行の帯',
    ground: '--up-band',
    texts: ['--ink', '--ink2', '--muted', '--up'],
    heatTexts: ['--ink', '--ink2'],
  },
  // .dash-ladder-current（現在値行）。地は他の行と同じ --surface（依頼者指示 2026-08-31・
  //   「表の地」の組で被覆済み）。--ink の地は次のターゲットの長期バッジが使い続ける。
  { name: '長期バッジ', ground: '--ink', texts: ['--bg'] },
  // .dash-ladder-current-up / -down（直近ティックの上下・依頼者指示 2026-08-30）。
  { name: '現在値行（上昇）', ground: '--tick-up-bg', texts: ['--ink', '--ink2'] },
  { name: '現在値行（下降）', ground: '--tick-down-bg', texts: ['--ink', '--ink2'] },
  // 縮退の掲示（--warn 系）は依頼者指示 2026-08-30 で廃止（規則・トークンごと撤去）。
  // .dash-sheet-message（異常の掲示）。
  { name: '異常の掲示', ground: '--down-bg', texts: ['--down'] },
  // .dash-osc-no-level（水準が無いことの小片）。
  { name: '水準なしの小片', ground: '--amber-bg', texts: ['--amber'] },
  // 行発光の被膜（次のターゲット移動・依頼者指示 2026-08-31）。セルの上へ 70% 透過で乗り
  //   8 秒で透明へ戻る**一時表示**で、この地の上に文字は置かれない（文字は被膜の下）。
  { name: '行発光の被膜', ground: '--amber', texts: [] },
  // .dash-ladder-next-h1 / -h2 / -h3（地平バッジ）。
  { name: '地平バッジ 短期', ground: '--muted', texts: ['--bg'] },
  { name: '地平バッジ 中期', ground: '--cyan', texts: ['--bg'] },
  { name: '地平バッジ 長期', ground: '--ink', texts: ['--bg'] },
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

// ---------------------------------------------------------------- テーマの読み取り

/** その規則が暗色テーマの宣言か（`@media prefers-color-scheme: dark` か `[data-theme="dark"]`）。 */
function isDarkRule(rule) {
  return rule.at.some((at) => /prefers-color-scheme\s*:\s*dark/.test(at))
    || /\[data-theme\s*=\s*"dark"\]/.test(rule.selector);
}

/**
 * CSS が宣言したパレットを明・暗の 2 つの表として読む。
 *
 * 明は既定（暗色の条件が付かない `.dash-sheet-host` の宣言）、暗はその上書き。
 */
function paletteByTheme() {
  const light = new Map();
  const dark = new Map();
  for (const rule of styleRules(CSS)) {
    if (!rule.selector.includes('.dash-sheet-host')) continue;
    for (const d of declarations(rule.body)) {
      if (!d.prop.startsWith('--')) continue;
      (isDarkRule(rule) ? dark : light).set(d.prop, d.value);
    }
  }
  assert.ok(light.size > 0, 'CSS がパレットを宣言していません');
  assert.ok(dark.size > 0, 'CSS が暗色テーマのパレットを宣言していません');
  return { light, dark: new Map([...light, ...dark]) };
}

const PALETTE = paletteByTheme();
const THEMES = Object.freeze([['明色', PALETTE.light], ['暗色', PALETTE.dark]]);

/** トークン 1 つを不透明色として解く（`var()` は 1 段で足りる＝パレットは自己完結）。 */
function colorOf(palette, token) {
  const raw = palette.get(token);
  assert.ok(raw, `パレットに ${token} がありません`);
  const parsed = parseColor(raw);
  assert.ok(parsed, `${token} が色として読めません: ${raw}`);
  return [parsed[0], parsed[1], parsed[2]];
}

/** SURFACES の 1 項から地の実色を作る。 */
function groundOf(palette, surface) {
  return colorOf(palette, surface.ground);
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

/** heat が実際に載る地（SURFACES のうち heat の上の文字を持つもの）。 */
const HEAT_SURFACES = SURFACES.filter((s) => Array.isArray(s.heatTexts) && s.heatTexts.length > 0);

// ---------------------------------------------------------------- 検定

describe('dashboard の版面 — 読めることを計算で固定する', () => {
  test('every_text_stays_above_the_minimum_contrast_ratio_on_the_ground_it_sits_on', () => {
    // 反転する現在値行・warn の掲示・地平バッジまで含め、実在する組をすべて見る。
    for (const [themeName, palette] of THEMES) {
      for (const surface of SURFACES) {
        const ground = groundOf(palette, surface);
        for (const token of surface.texts) {
          const ratio = contrast(colorOf(palette, token), ground);
          assert.ok(
            ratio >= MIN_CONTRAST,
            `${themeName}: ${surface.name} の上で ${token} が読めません: ${ratio.toFixed(3)} < ${MIN_CONTRAST}`,
          );
        }
      }
    }
  });

  test('every_heat_background_keeps_its_text_above_the_minimum_contrast_ratio', () => {
    // Arrange: heat が載りうる地 × 全 p × その地に載る文字色。両テーマで回す。
    for (const [themeName, palette] of THEMES) {
      assert.ok(HEAT_SURFACES.length > 0, 'heat の載る地が 1 つも定義されていません');
      for (const surface of HEAT_SURFACES) {
        const base = groundOf(palette, surface);
        for (const bg of heatBackgrounds(base)) {
          for (const token of surface.heatTexts) {
            const ratio = contrast(colorOf(palette, token), bg.rgb);
            assert.ok(
              ratio >= MIN_CONTRAST,
              `${themeName}: ${surface.name} の ${bg.what} の上で ${token} が読めません: ${ratio.toFixed(3)} < ${MIN_CONTRAST}`,
            );
          }
        }
      }
    }
  });

  test('two_p_values_one_step_apart_stay_distinguishable_on_every_background', () => {
    // 濃さが量を担う以上、刻みが潰れると「隔たり」が読めなくなる。
    for (const [themeName, palette] of THEMES) {
      for (const surface of HEAT_SURFACES) {
        const base = groundOf(palette, surface);
        for (let p = P_STEP; p <= 1 + 1e-9; p += P_STEP) {
          const lower = composite(parseColor(colorForP(p - P_STEP)), base);
          const upper = composite(parseColor(colorForP(Math.min(p, 1))), base);
          const d = deltaE(lower, upper);
          assert.ok(
            d >= MIN_DELTA_E,
            `${themeName}/${surface.name}: p=${(p - P_STEP).toFixed(3)} と p=${Math.min(p, 1).toFixed(3)} が見分けられません: ΔE=${d.toFixed(2)}`,
          );
        }
      }
    }
  });

  test('the_off_scale_colour_stays_distinguishable_from_every_colour_on_the_scale', () => {
    // §5.3.2: 帯外単一色は「目盛りが無い」ことを示す。目盛り上の色と紛れたら意味が消える。
    for (const [themeName, palette] of THEMES) {
      for (const surface of HEAT_SURFACES) {
        const base = groundOf(palette, surface);
        const tail = composite(parseColor(tailUnscaledColor()), base);
        for (let i = 0; i <= 40; i += 1) {
          const onScale = composite(parseColor(colorForP(i / 40)), base);
          const d = deltaE(tail, onScale);
          assert.ok(d >= MIN_DELTA_E,
            `${themeName}/${surface.name}: 帯外単一色が p=${i / 40} と紛れます: ΔE=${d.toFixed(2)}`);
        }
      }
    }
  });

  test('the_stylesheet_expresses_every_colour_through_a_theme_token', () => {
    // 生の色を直書きすると、テーマを足したときにそこだけ取り残される
    //   （MEMORY: enforce-constraints-mechanically）。生の色を書いてよいのは
    //   **カスタムプロパティの宣言**だけで、そこがパレットの唯一の定義点になる。
    const literal = /#[0-9a-fA-F]{3,8}\b|\brgba?\(|\bhsla?\(|\b(?:white|black|red|blue|green|gray|grey|silver|yellow|orange|purple|navy|teal|maroon|olive|lime|aqua|fuchsia)\b/;
    for (const rule of styleRules(CSS)) {
      for (const d of declarations(rule.body)) {
        if (d.prop.startsWith('--')) continue;
        assert.equal(
          literal.test(d.value), false,
          `トークンを経由しない色があります: ${rule.selector} { ${d.prop}: ${d.value} }`,
        );
      }
    }
  });

  test('every_colour_the_stylesheet_paints_is_covered_by_a_surface', () => {
    // 被覆検定: SURFACES が腐って検査が no-op に化けるのを防ぐ。CSS が新しい文字色や
    //   新しい地を使い始めたのに表へ足されていなければ、ここで落ちる。
    // 色ではないカスタムプロパティ（数値パラメータ）。color-mix の混合率などに現れるが、
    //   地でも文字色でもないため被覆の対象から除く。**ここへ足してよいのは色でない値だけ**
    //   （色トークンを足すと検査が黙って抜ける）。
    const NON_COLOR_TOKENS = new Set(['--tick-strength']);
    const declaredTexts = new Set();
    const declaredGrounds = new Set();
    for (const rule of styleRules(CSS)) {
      for (const d of declarations(rule.body)) {
        if (d.prop.startsWith('--')) continue;
        if (d.prop === 'color') {
          tokensIn(d.value).filter((t) => !NON_COLOR_TOKENS.has(t))
            .forEach((t) => declaredTexts.add(t));
        }
        if (d.prop === 'background' || d.prop === 'background-color') {
          tokensIn(d.value).filter((t) => !NON_COLOR_TOKENS.has(t))
            .forEach((t) => declaredGrounds.add(t));
        }
      }
    }
    const coveredTexts = new Set(SURFACES.flatMap((s) => s.texts));
    const coveredGrounds = new Set(SURFACES.map((s) => s.ground));
    for (const token of declaredTexts) {
      assert.ok(coveredTexts.has(token), `文字色 ${token} を載せる地が SURFACES にありません`);
    }
    for (const token of declaredGrounds) {
      assert.ok(coveredGrounds.has(token), `地 ${token} が SURFACES にありません`);
    }
    // 逆向き: 表に在るのに CSS が使わない項（＝実在しない組の検査）も許さない。
    for (const surface of SURFACES) {
      assert.ok(declaredGrounds.has(surface.ground),
        `SURFACES の「${surface.name}」の地 ${surface.ground} を CSS が使っていません`);
    }
  });

  test('the_reached_band_stays_the_declared_dilution_of_the_reached_ground', () => {
    // モックは `color-mix(in srgb, var(--up-bg) 55%, transparent)` で帯を作るが、本 CSS は
    //   合成済みの実色 --up-band を持つ（color-mix を解さない実装で帯が無言で消えないため）。
    //   合成済みにすると「元の色との関係」が CSS から読めなくなるので、その関係をここで固定する。
    const DILUTION = 0.55;
    for (const [themeName, palette] of THEMES) {
      const expected = composite([...colorOf(palette, '--up-bg'), DILUTION], colorOf(palette, '--surface'));
      const actual = colorOf(palette, '--up-band');
      for (const i of [0, 1, 2]) {
        assert.ok(
          Math.abs(actual[i] - expected[i]) <= 0.5,
          `${themeName}: --up-band が --up-bg の ${DILUTION * 100}% ではありません`
          + `（期待 ${expected.map((v) => Math.round(v)).join(',')} / 実際 ${actual.join(',')}）`,
        );
      }
    }
  });

  test('the_two_ways_of_asking_for_dark_declare_the_same_palette', () => {
    // 暗色は「端末が暗色を望む」と「data-theme で明示」の 2 つの規則が同じ値を宣言する。
    //   片方だけ直すと、どちらの経路で暗色になったかで配色が変わる（無言の食い違い）。
    const blocks = styleRules(CSS)
      .filter((rule) => rule.selector.includes('.dash-sheet-host') && isDarkRule(rule))
      .map((rule) => new Map(declarations(rule.body)
        .filter((d) => d.prop.startsWith('--'))
        .map((d) => [d.prop, d.value])));
    assert.equal(blocks.length, 2, '暗色の宣言が 2 つ（media と data-theme）ではありません');
    assert.deepEqual([...blocks[0]].sort(), [...blocks[1]].sort());
  });

  test('both_themes_declare_the_same_set_of_tokens', () => {
    // 片方のテーマだけトークンを足すと、もう片方は前のテーマの色を引きずって無言で壊れる。
    assert.deepEqual([...PALETTE.dark.keys()].sort(), [...PALETTE.light.keys()].sort());
  });

  test('the_two_themes_actually_differ', () => {
    // 恒真化の防止: 暗色の上書きが効いていなければ、2 テーマ検査は 1 テーマ検査に化ける。
    const changed = [...PALETTE.light.keys()].filter((k) => PALETTE.light.get(k) !== PALETTE.dark.get(k));
    assert.ok(changed.length > 0, '暗色テーマが 1 つも色を変えていません');
  });

  test('the_selectors_this_check_relies_on_are_the_ones_the_views_actually_use', () => {
    // 検定の検定: View 側の改名でこの検査が無言の no-op に化けるのを防ぐ。
    const sources = `${LADDER_VIEW}\n${OSC_VIEW}`;
    for (const name of HEAT_BACKED_CLASSES) {
      assert.ok(sources.includes(name), `View が使わないクラスを検査しています: ${name}`);
    }
    // heat の地の上に載る文字は、その地を持つ SURFACES の heatTexts で覆われていなければならない。
    const heatTexts = new Set(HEAT_SURFACES.flatMap((s) => s.heatTexts));
    for (const rule of styleRules(CSS)) {
      if (!HEAT_BACKED_CLASSES.some((c) => rule.selector.includes(`.${c}`))) continue;
      for (const d of declarations(rule.body)) {
        if (d.prop !== 'color') continue;
        for (const token of tokensIn(d.value)) {
          assert.ok(heatTexts.has(token),
            `heat の上に載る文字色 ${token} が heat の地の検査から漏れています: ${rule.selector}`);
        }
      }
    }
  });
});
