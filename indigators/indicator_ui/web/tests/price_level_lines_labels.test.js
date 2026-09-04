// 水準線の「項目名＋価格」**タグ**（ISSUE-435 実装 2・裁定 2026-08-21）の仕様検証。
//
// 経緯（実 UI 実測 2026-08-21・ライブ 8000/live・1600×1000・dpr=1）:
//   参照実装 `marker()` の書式（9px・背景なし・2 段）をそのまま写した版は、
//   **ローソク・移動平均・btlm_trail の帯に埋もれて読めなかった**。参照実装の数直線は
//   幅 300px 程度の無地キャンバスであり、指標が密集する 1600px のチャート上での可読性を
//   一度も定義していない（＝参照実装の射程外）。
// 依頼者裁定（2026-08-21・確定）: **背景付きのタグにする**。線と同色で塗った小さなタグの中に
//   「項目名 価格」を抜き文字で入れる。価格軸のタグ・現在値タグと見た目を揃える。
//
// 参照実装から離れる点（すべて裁定・実測が根拠。コード側にも明記）:
//   - 2 段（項目名 y-10 / 価格 y-20）→ **1 行**（タグの中に「項目名 価格」）
//   - 文字色（灰 `#8C96A8`／既存スロット uiTextAux）→ **抜き文字**（塗りの上で読める色）
//   - 9px → 価格軸タグと同じ字送り（lwc の layout 既定 `fontSize:12`・vendor 実測）
//   維持する点: 項目名の単一ソース（priceTargetLabel）・価格書式の単一ソース（priceOnLine）・
//   右端配置・HiDPI の座標系（ratio を掛ける）。
//
// 観点: (a) 塗りと文字が対で出る (b) 色が線と紐づく (c) 中身が単一ソース由来
//   (d) 右端に収まり価格軸へ食い込まない (e) 線を覆わない (f) **タグ同士が重ならない**
// 構造: Arrange-Act-Assert。fake target で描画命令列を観測する。
//   **検定できない範囲**: canvas の実描画・実際の見た目・実フォントの字幅は fake DOM では
//   観測できない（実 UI 検証へ委譲）。ここで固定できるのは「どの命令をどの座標・色で出したか」だけ。

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { PriceLevelLinesPrimitive } from '../js/adapter/front/price_level_lines_primitive.js';
import { priceOnLine } from '../js/adapter/front/price_format.js';
import { CHROME_CURRENT } from '../js/usecase/chrome_tokens.js';

// 価格 → y（媒体座標）。可視範囲は y ∈ [-400, 800]。実 lwc と同じく範囲外は null。
const Y_OF = (price) => {
  const y = 100 + (58700 - price) / 10;
  return y >= -400 && y <= 800 ? y : null;
};

const LEVELS = Object.freeze({
  direction: 'long',
  entryPrices: [58700, 59700],
  stopPrice: 58340,
  takePrice: 61500,
  losscutPrice: 57000,
});

const ONE_LINE = Object.freeze({
  direction: 'long', entryPrices: [58700], stopPrice: null, takePrice: null, losscutPrice: null,
});

// 描画命令を記録する fake。実 canvas と同じく文字幅は font に比例する（字幅は 0.6em の等幅想定）。
function fakeTarget({ hRatio = 1, vRatio = 1, width = 1540, rich = true } = {}) {
  const ops = [];
  let fill = null;
  let stroke = null;
  let fontPx = 0;
  const base = {
    save() {}, restore() {}, beginPath() {},
    moveTo(x, y) { ops.push({ op: 'moveTo', x, y, color: stroke }); },
    lineTo() {}, stroke() {}, setLineDash() {},
    set fillStyle(v) { fill = v; }, get fillStyle() { return fill; },
    set strokeStyle(v) { stroke = v; }, get strokeStyle() { return stroke; },
    set lineWidth(_v) {}, get lineWidth() { return 1; },
  };
  const richOps = {
    measureText: (t) => ({ width: t.length * fontPx * 0.6 }),
    fillRect(x, y, w, h) { ops.push({ op: 'fillRect', x, y, w, h, color: fill }); },
    fillText(text, x, y) { ops.push({ op: 'fillText', text, x, y, color: fill }); },
    set font(v) { fontPx = parseFloat(v); ops.push({ op: 'font', text: v }); },
    get font() { return `${fontPx}px`; },
    set textAlign(v) { ops.push({ op: 'textAlign', text: v }); }, get textAlign() { return null; },
    set textBaseline(v) { ops.push({ op: 'textBaseline', text: v }); }, get textBaseline() { return null; },
  };
  const ctx = rich ? Object.defineProperties(base, Object.getOwnPropertyDescriptors(richOps)) : base;
  return {
    ops,
    width,
    useBitmapCoordinateSpace(fn) {
      fn({
        context: ctx,
        bitmapSize: { width: width * hRatio, height: 800 * vRatio },
        mediaSize: { width, height: 800 },
        horizontalPixelRatio: hRatio,
        verticalPixelRatio: vRatio,
      });
    },
  };
}

function draw(primitive, opts = {}) {
  const target = fakeTarget(opts);
  primitive.draw(target);
  const pick = (op) => target.ops.filter((o) => o.op === op);
  return {
    lines: pick('moveTo'),
    rects: pick('fillRect'),
    texts: pick('fillText'),
    fonts: pick('font').map((o) => o.text),
    aligns: pick('textAlign').map((o) => o.text),
    ops: target.ops,
  };
}

function build({ levels = LEVELS, spec = undefined } = {}) {
  const p = new PriceLevelLinesPrimitive();
  p.attached({
    chart: { timeScale: () => ({ width: () => 1540 }) },
    series: { priceToCoordinate: Y_OF },
    requestUpdate: () => {},
  });
  if (spec !== undefined) {
    p.setSymbolSpec(spec);
  }
  p.setLevels(levels);
  return p;
}

// 2 つの矩形が重なるか（境界の接触は重なりとしない）。
const overlaps = (a, b) => a.x < b.x + b.w && b.x < a.x + a.w && a.y < b.y + b.h && b.y < a.y + a.h;

test('TC-PL01 各線に「塗り＋抜き文字」のタグが 1 つずつ出る（塗りは線と同色）', () => {
  // Arrange
  const p = build();
  // Act
  const { lines, rects, texts } = draw(p);
  // Assert: 線 5 本（建値 2・損切り・利確・ロスカット）に対しタグは 5 つ（1 行）。
  assert.equal(lines.length, 5);
  assert.equal(rects.length, 5, 'タグの背景（塗り）が線の数だけ出ていない');
  assert.equal(texts.length, 5, 'タグの文字が 1 行になっていない');
  // 塗りは線と同色（裁定: 線と同色で塗る）。
  assert.deepEqual(rects.map((r) => r.color).sort(), lines.map((l) => l.color).sort());
});

test('TC-PL02 文字は抜き文字＝地の色（塗りの上で読める色を既存スロットから選ぶ）', () => {
  // 選定根拠（実測値・WCAG コントラスト比）: 塗りは線色 3 種
  //   priceLine #ff9800 / pairLineWin #26a69a / pairLineLoss #ef5350。
  //   layoutBackground #131722 → 8.30 / 5.97 / 5.13（最小 5.13・AA 4.5 超）
  //   uiTextStrong #ffffff  → 2.16 / 3.00 / 3.49（最小 2.16・不可）
  //   uiText #d1d4dc        → 1.45 / 2.02 / 2.35（最小 1.45・不可）
  // Arrange
  const p = build();
  // Act
  const { texts } = draw(p);
  // Assert
  for (const t of texts) {
    assert.equal(t.color, CHROME_CURRENT.layoutBackground, `抜き文字が地の色でない: ${t.text}`);
  }
});

test('TC-PL03 タグの中身は「項目名 価格」（表示名・価格書式とも単一ソース）', () => {
  // Arrange
  const p = build();
  // Act
  const { texts } = draw(p);
  // Assert
  assert.deepEqual(texts.map((t) => t.text).sort(), [
    `ロスカット ${priceOnLine(57000)}`,
    `利確 ${priceOnLine(61500)}`,
    `建値 1 ${priceOnLine(58700)}`,
    `建値 2 ${priceOnLine(59700)}`,
    `損切り ${priceOnLine(58340)}`,
  ].sort());
});

test('TC-PL04 価格は銘柄仕様の桁に従う（書式の第 2 実装を作らない）', () => {
  // Arrange
  const levels = {
    direction: 'long', entryPrices: [58700.256], stopPrice: null, takePrice: null, losscutPrice: null,
  };
  // Act
  const withDigits = draw(build({ levels, spec: { tick: 0.01, digits: 2 } })).texts[0].text;
  const noSpec = draw(build({ levels })).texts[0].text;
  // Assert
  assert.equal(withDigits, `建値 1 ${priceOnLine(58700.256, 2)}`);
  assert.equal(noSpec, `建値 1 ${priceOnLine(58700.256)}`, '仕様未解決なら参照実装どおり整数');
});

test('TC-PL05 タグは右端に収まり、価格軸へ食い込まない（版面幅の内側）', () => {
  // 実 UI 実測（2026-08-21）: 版面は 1540px で終わり価格軸は 1540px から始まる。
  //   primitive が描く canvas は版面のものなので、幅の内側に収まれば軸へは掛からない。
  // Arrange
  const p = build();
  // Act
  const { rects, aligns, fonts } = draw(p, { width: 1540 });
  // Assert
  for (const r of rects) {
    assert.ok(r.x + r.w <= 1540, `右端からはみ出している: ${r.x + r.w}`);
    assert.ok(r.x >= 0, `左端からはみ出している: ${r.x}`);
    assert.ok(r.x + r.w >= 1540 - 24, `右端側に寄っていない: ${r.x + r.w}`);
  }
  // 価格軸タグと字送りを揃える（lwc の layout 既定 fontSize:12・vendor 実測）。
  assert.equal(fonts.includes('12px ui-monospace,monospace'), true, `font が価格軸タグと揃っていない: ${fonts.join(',')}`);
  assert.equal(aligns.includes('right'), true, '右寄せにしていない');
});

test('TC-PL06 タグは線を覆わない（線の視認を妨げない）', () => {
  // Arrange: 1 本だけ（重なり回避の押し下げが働かない条件）。
  const p = build({ levels: ONE_LINE });
  // Act
  const { lines, rects } = draw(p);
  // Assert: タグの下端が線より上（線の上に置く）。
  assert.equal(rects.length, 1);
  assert.ok(rects[0].y + rects[0].h <= lines[0].y, 'タグが線に重なっている');
  assert.ok(lines[0].y - (rects[0].y + rects[0].h) <= 4, 'タグが線から離れすぎている（紐づきが読めない）');
});

test('TC-PL07 近接した 2 本でもタグ同士が重ならない', () => {
  // Arrange: 5px しか離れていない 2 本（同じ規則で置くと必ず重なる距離）。
  const p = build({
    levels: {
      direction: 'long', entryPrices: [58700, 58650], stopPrice: null, takePrice: null, losscutPrice: null,
    },
  });
  // Act
  const { rects } = draw(p);
  // Assert
  assert.equal(rects.length, 2);
  assert.equal(overlaps(rects[0], rects[1]), false, `タグが重なっている: ${JSON.stringify(rects)}`);
});

test('TC-PL08 5 本が密集してもすべてのタグが重ならず、線の順序と同じ順に並ぶ', () => {
  // Arrange: 4px 刻みで 5 本（タグ高さより明らかに近い）。
  const p = build({
    levels: {
      direction: 'long',
      entryPrices: [58700, 58660, 58620, 58580],
      stopPrice: 58540,
      takePrice: null,
      losscutPrice: null,
    },
  });
  // Act
  const { lines, rects } = draw(p);
  // Assert: 総当たりで重なり 0。
  assert.equal(rects.length, 5);
  for (let i = 0; i < rects.length; i += 1) {
    for (let j = i + 1; j < rects.length; j += 1) {
      assert.equal(overlaps(rects[i], rects[j]), false, `タグ ${i} と ${j} が重なっている`);
    }
  }
  // 順序保存: 線の y 昇順とタグの y 昇順が一致する（タグ同士が入れ替わらない＝対応が読める）。
  const byLine = [...lines].sort((a, b) => a.y - b.y).map((l) => l.y);
  const byTag = [...rects].sort((a, b) => a.y - b.y).map((r) => r.y);
  assert.equal(byTag.length, byLine.length);
  assert.deepEqual([...byTag].sort((a, b) => a - b), byTag, 'タグの並びが単調でない');
});

test('TC-PL09 HiDPI: 塗り・文字・字送りが線と同じ倍率で拡大される', () => {
  // Arrange
  const p = build();
  // Act
  const at1 = draw(p, { hRatio: 1, vRatio: 1 });
  const at2 = draw(p, { hRatio: 2, vRatio: 2 });
  // Assert: vendor 実測（useBitmapCoordinateSpace は setTransform(1,0,0,1,0,0)＝装置ピクセル）
  //   に従い、線もタグも媒体座標へ ratio を掛ける。
  assert.equal(at2.lines[0].y, at1.lines[0].y * 2, '線が dpr で拡大されていない');
  assert.equal(at2.rects[0].y, at1.rects[0].y * 2, 'タグの塗りが線と同じ座標系でない');
  assert.equal(at2.rects[0].h, at1.rects[0].h * 2, 'タグの高さが dpr で拡大されていない');
  assert.equal(at2.fonts.includes('24px ui-monospace,monospace'), true, '字送りが dpr で拡大されていない');
});

test('TC-PL10 dpr=1 の線は従来と 1px も変わらない（線描画に副作用を持ち込まない）', () => {
  // Arrange
  const p = build();
  // Act
  const { lines } = draw(p, { hRatio: 1, vRatio: 1 });
  // Assert
  assert.deepEqual(lines.map((l) => l.y), [Y_OF(58700), Y_OF(59700), Y_OF(58340), Y_OF(61500), Y_OF(57000)]);
});

test('TC-PL11 抜き文字の色は既存スロットの配信で差し替わる（全域的・部分指定でも例外なし）', () => {
  // Arrange
  const p = build();
  // Act
  p.setChromeColors({ layoutBackground: '#123456' });
  p.setChromeColors(null);
  p.setChromeColors({ layoutBackground: 42 });
  const { texts } = draw(p);
  // Assert
  for (const t of texts) {
    assert.equal(t.color, '#123456');
  }
});

test('TC-PL12 可視範囲外の線はタグも出ない（線が無いのに名前だけ出さない）', () => {
  // Arrange: 損切りだけ範囲外（y=null）。
  const p = build({
    levels: {
      direction: 'long', entryPrices: [58700], stopPrice: 99999, takePrice: null, losscutPrice: null,
    },
  });
  // Act
  const { lines, rects, texts } = draw(p);
  // Assert
  assert.equal(lines.length, 1);
  assert.equal(rects.length, 1);
  assert.equal(texts.some((t) => t.text.startsWith('損切り')), false, '描いていない線のタグが出ている');
});

test('TC-PL13 文字を描けない描画文脈でも線は引ける（最小 fake・後方互換で例外を投げない）', () => {
  // Arrange: fillRect/fillText/measureText を持たない ctx（既存検定の fake と同型）。
  const p = build();
  // Act
  const { lines, rects, texts } = draw(p, { rich: false });
  // Assert
  assert.equal(lines.length, 5, '線が引けていない');
  assert.deepEqual([rects.length, texts.length], [0, 0], 'タグを描けない文脈で描こうとしている');
});
