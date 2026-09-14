// 水準線タグの**前面化**（ISSUE-435 残件 1）と**半透明化**（残件 2・依頼者裁定 2026-08-21）。
//
// 残件 1（実 UI 実測 2026-08-21）: pane view が zOrder を宣言しておらず、タグが移動平均の帯の
//   下に描かれて読めなかった（建値 1 のタグが隠れるのを目視）。vendor は paneView の
//   `zOrder?.()`（'bottom'|'normal'|'top'・既定 'normal'）に対応済み（実測）。
//   裁定: **線は従来のまま・タグだけ前面**。→ タグ専用の paneView を足し 'top' を宣言する。
//   線の paneView は zOrder を宣言しない（従来の描画順を 1 つも変えない）。
//
// 残件 2（依頼者裁定 2026-08-21「右端のままで半透明にする」）: 不透明なタグが直近の足を覆う。
//   **不透明度は決め打ちにしない**。下に来る現実の色（地・陽線/陰線・取引密度帯）に対する
//   合成後の実効コントラスト（WCAG 2.x）を算出し、**AA 4.5 を割らない範囲で最も透ける値**を選ぶ。
//   コントラストの数学は domain/color_value.js（単一ソース）を使う（第 2 実装を作らない）。
//
// 観点:
//   (a) paneView の分離（線=従来・タグ=top）と座標源の単一性（タグ面は線工程の y を使う）
//   (b) 塗りは算出した不透明度・抜き文字は不透明（文字まで透かすと読めなくなる）
//   (c) 不透明度の選定規則（AA を満たす最小・満たせなければ不透明へ縮退＝従来挙動）
//   (d) 計算量: 不透明度の導出は色が変わったときだけ（描画のたびに再導出しない）。
//       タグ工程は座標（priceToCoordinate）を再発行しない（線工程の結果を使う）。
// 検定できない範囲: canvas の実合成・実際の透け具合は fake では観測できない（実 UI 検証へ委譲）。

import { test } from 'node:test';
import assert from 'node:assert/strict';

import {
  PriceLevelLinesPrimitive, mostTransparentTagAlpha, flattenColorOverBase,
} from '../js/adapter/front/price_level_lines_primitive.js';
import { contrastRatio, mixChannels } from '../js/domain/color_value.js';
import { CHROME_CURRENT } from '../js/usecase/chrome_tokens.js';

// 価格 → y（媒体座標）。labels 検定と同じ一次写像・範囲外 null。
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

// 描画命令と、その時点の globalAlpha を記録する fake。
function fakeTarget({ width = 1540 } = {}) {
  const ops = [];
  let fill = null;
  let alpha = 1;
  let fontPx = 12;
  const ctx = {
    save() {}, restore() {}, beginPath() {},
    moveTo(x, y) { ops.push({ op: 'moveTo', x, y }); },
    lineTo() {}, stroke() {}, setLineDash() {},
    measureText: (t) => ({ width: t.length * fontPx * 0.6 }),
    fillRect(x, y, w, h) { ops.push({ op: 'fillRect', x, y, w, h, color: fill, alpha }); },
    fillText(text, x, y) { ops.push({ op: 'fillText', text, x, y, color: fill, alpha }); },
    set globalAlpha(v) { alpha = v; }, get globalAlpha() { return alpha; },
    set fillStyle(v) { fill = v; }, get fillStyle() { return fill; },
    set strokeStyle(_v) {}, get strokeStyle() { return null; },
    set lineWidth(_v) {}, get lineWidth() { return 1; },
    set font(v) { fontPx = parseFloat(v); }, get font() { return `${fontPx}px`; },
    set textAlign(_v) {}, get textAlign() { return null; },
    set textBaseline(_v) {}, get textBaseline() { return null; },
  };
  return {
    ops,
    useBitmapCoordinateSpace(fn) {
      fn({
        context: ctx,
        bitmapSize: { width, height: 800 },
        mediaSize: { width, height: 800 },
        horizontalPixelRatio: 1,
        verticalPixelRatio: 1,
      });
    },
  };
}

function build({ levels = LEVELS, computeTagAlpha } = {}) {
  const p = new PriceLevelLinesPrimitive(computeTagAlpha ? { computeTagAlpha } : undefined);
  p.attached({
    chart: {},
    series: { priceToCoordinate: Y_OF },
    requestUpdate: () => {},
  });
  p.setLevels(levels);
  return p;
}

const pick = (target, op) => target.ops.filter((o) => o.op === op);

// 現行パレットでの選定入力（実装と同じ材料。値はすべて台帳 CHROME_CURRENT から引く）。
function currentPaletteInputs() {
  return {
    fills: [
      CHROME_CURRENT.priceLine, CHROME_CURRENT.pairLineLoss,
      CHROME_CURRENT.pairLineWin, CHROME_CURRENT.pairLineLoss,
    ],
    textColor: CHROME_CURRENT.layoutBackground,
    underlays: [
      CHROME_CURRENT.layoutBackground,
      CHROME_CURRENT.candleUp,
      CHROME_CURRENT.candleDown,
      flattenColorOverBase(CHROME_CURRENT.tickvolBand, CHROME_CURRENT.layoutBackground),
    ],
  };
}

// 検定側の照合器（仕様のオラクル）: 不透明度 a のタグ塗りを下地へ合成した色と抜き文字の
//   コントラストの最小値。合成は mixChannels（8bit・canvas と同じ丸め）・比は contrastRatio。
function minContrastAt({ fills, textColor, underlays }, a) {
  let min = Infinity;
  for (const f of fills) {
    for (const u of underlays) {
      min = Math.min(min, contrastRatio(textColor, mixChannels(u, f, a)));
    }
  }
  return min;
}

test('TC-TL01 paneViews は 2 面（線=従来どおり zOrder 無宣言・タグ=top）', () => {
  const p = build();
  const views = p.paneViews();
  assert.equal(views.length, 2, 'タグ専用の paneView が無い');
  assert.equal('zOrder' in views[0] && typeof views[0].zOrder === 'function', false,
    '線の paneView に zOrder が生えている（従来の描画順が変わる）');
  assert.equal(typeof views[1].zOrder, 'function', 'タグの paneView が zOrder を宣言していない');
  assert.equal(views[1].zOrder(), 'top', 'タグが前面（top）でない');
});

test('TC-TL02 線は線の面だけ・タグはタグの面だけに描かれる（責務の分離）', () => {
  const p = build();
  const [linesView, tagsView] = p.paneViews();
  const linesTarget = fakeTarget();
  linesView.renderer().draw(linesTarget);
  const tagsTarget = fakeTarget();
  tagsView.renderer().draw(tagsTarget);
  assert.equal(pick(linesTarget, 'moveTo').length, 5, '線の面に線が出ていない');
  assert.deepEqual(
    [pick(linesTarget, 'fillRect').length, pick(linesTarget, 'fillText').length], [0, 0],
    '線の面にタグが混ざっている（前面化されていない）',
  );
  assert.equal(pick(tagsTarget, 'fillRect').length, 5, 'タグの面にタグが出ていない');
  assert.equal(pick(tagsTarget, 'fillText').length, 5);
  assert.equal(pick(tagsTarget, 'moveTo').length, 0, 'タグの面に線が混ざっている');
});

test('TC-TL03 タグ面の座標源は線工程の y 表（描画と掴みと同じ単一ソース）', () => {
  const p = build();
  const [linesView, tagsView] = p.paneViews();
  const combined = fakeTarget();
  p.draw(combined);   // 公開契約: 1 フレーム全描画（線 → タグ）
  const split = fakeTarget();
  linesView.renderer().draw(split);
  tagsView.renderer().draw(split);
  assert.deepEqual(
    pick(split, 'fillRect').map((r) => [r.x, r.y, r.w, r.h]),
    pick(combined, 'fillRect').map((r) => [r.x, r.y, r.w, r.h]),
    '面を分けるとタグの位置が変わる（座標源が 2 つある）',
  );
});

test('TC-TL04 detach 後はタグ面も描かない（座標源を手放したら全面が止まる）', () => {
  const p = build();
  const [linesView, tagsView] = p.paneViews();
  linesView.renderer().draw(fakeTarget());   // 一度描いて y 表・タグ素材を作る
  p.detached();
  const after = fakeTarget();
  linesView.renderer().draw(after);
  tagsView.renderer().draw(after);
  assert.deepEqual(after.ops, [], 'detach 後に描いている');
});

test('TC-TL05 塗りは算出した不透明度・抜き文字は不透明（裁定: 右端のままで半透明）', () => {
  const p = build();
  const target = fakeTarget();
  p.draw(target);
  const expected = mostTransparentTagAlpha(currentPaletteInputs());
  assert.equal(expected < 1, true, '前提: 現行パレットで半透明が成立しない（裁定と矛盾）');
  for (const r of pick(target, 'fillRect')) {
    assert.equal(r.alpha, expected, `塗りの不透明度が選定値でない: ${r.alpha}`);
  }
  for (const t of pick(target, 'fillText')) {
    assert.equal(t.alpha, 1, '抜き文字まで透けている（読めなくなる）');
  }
});

test('TC-TL06 不透明度は「AA 4.5 を割らない最も透ける値」（決め打ちでない）', () => {
  const inputs = currentPaletteInputs();
  const a = mostTransparentTagAlpha(inputs);
  assert.ok(minContrastAt(inputs, a) >= 4.5, `選定値が AA を割っている: ${minContrastAt(inputs, a)}`);
  assert.ok(a > 0, '不透明度 0（塗り無し）は選ばれ得ない（AA 1.0 で必ず割る）');
  assert.ok(minContrastAt(inputs, Math.max(0, a - 0.01)) < 4.5,
    'もう 1 段透かしても AA を満たす＝最も透ける値になっていない');
  assert.ok(a < 1, '現行パレットで半透明が成立するはず（成立しないなら報告して止める裁定）');
});

test('TC-TL07 全域性: 解釈できない色・到達不能な床では不透明（従来挙動）へ縮退する', () => {
  // 文字色 = 塗り色 = 下地（合成結果が常に文字色・コントラスト 1.0 固定）
  //   → どの不透明度でも AA に到達しない → 1（不透明＝従来挙動）へ縮退。
  assert.equal(mostTransparentTagAlpha({
    fills: ['#26a69a'], textColor: '#26a69a', underlays: ['#26a69a'],
  }), 1);
  // 下地が文字と対比を持つ極端例では 0（塗り無し）も定義上は可行になる。本番の選定入力は
  //   下地に**文字色そのもの（地）を必ず含む**ため、0 は構成上選ばれない（TC-TL06 の a>0）。
  assert.equal(mostTransparentTagAlpha({
    fills: ['#26a69a'], textColor: '#26a69a', underlays: ['#131722'],
  }), 0);
  // 解釈できない色が混ざる → 1（例外を投げない・従来と同じ見た目に落とす）。
  assert.equal(mostTransparentTagAlpha({
    fills: ['not-a-color'], textColor: '#131722', underlays: ['#131722'],
  }), 1);
  assert.equal(mostTransparentTagAlpha(null), 1);
  // 半透明の下地は先に地へ合成してから渡す（その合成器も全域的）。
  assert.equal(flattenColorOverBase('rgba(41, 98, 255, 0.07)', '#131722'),
    mixChannels('#131722', '#2962ff', 0.07));
  assert.equal(flattenColorOverBase('#26a69a', '#131722'), '#26a69a', '不透明色は素通し');
  assert.equal(flattenColorOverBase('broken', '#131722'), null, '解釈できない下地は null');
});

// --------------------------------------------------------------------------- //
// 計算量（絶対命令 2026-08-28）: 発行した計算 − 出力に使った計算 = 0 を Spy で固定する。
//   回数そのものは期待値に焼き込まない（固定するのは無駄の不在であって実装詳細ではない）。
// --------------------------------------------------------------------------- //

for (const drawCount of [2, 10]) {
  test(`計算量: 不透明度の導出は色の変化時だけ（描画 ${drawCount} 回で増えない）`, () => {
    const issued = [];
    const spy = (inputs) => { issued.push(inputs); return mostTransparentTagAlpha(inputs); };
    const p = build({ computeTagAlpha: spy });
    for (let i = 0; i < drawCount; i += 1) {
      p.draw(fakeTarget());
    }
    // 同一の色を配り直しても入力が変わらなければ再導出しない。
    p.setChromeColors({ priceLine: CHROME_CURRENT.priceLine });
    // 使った導出 = 採用された不透明度の状態数（構築時の 1 つ）。発行 − 使用 = 0。
    assert.equal(issued.length - 1, 0,
      `描画・同値配信で不透明度を再導出している: issued=${issued.length}`);
    // 色が実際に変わったときは 1 回だけ導出し、その結果が出力に使われる。
    p.setChromeColors({ priceLine: '#123456' });
    const target = fakeTarget();
    p.draw(target);
    assert.equal(issued.length - 2, 0, '色の変化 1 回に対して導出が 1 回でない');
    const applied = new Set(pick(target, 'fillRect').map((r) => r.alpha));
    assert.equal(applied.size, 1, '導出した不透明度が塗りに使われていない');
  });
}

for (const levelCount of [2, 5]) {
  test(`計算量: 1 フレームの座標発行 − 線の本数 = 0（タグ工程は再発行しない・${levelCount} 本）`, () => {
    const entries = Array.from({ length: levelCount }, (_, i) => 58700 + i * 100);
    const levels = {
      direction: 'long', entryPrices: entries, stopPrice: null, takePrice: null, losscutPrice: null,
    };
    const p = new PriceLevelLinesPrimitive();
    let issued = 0;
    p.attached({
      chart: {},
      series: { priceToCoordinate: (price) => { issued += 1; return Y_OF(price); } },
      requestUpdate: () => {},
    });
    p.setLevels(levels);
    issued = 0;
    const [linesView, tagsView] = p.paneViews();
    const target = fakeTarget();
    linesView.renderer().draw(target);
    tagsView.renderer().draw(target);
    // 使用 = 描いた線（＝タグの置き場所にも使う）。発行 − 使用 = 0（タグ工程で再発行しない）。
    assert.equal(issued - pick(target, 'moveTo').length, 0,
      `タグ工程が座標を再発行している: issued=${issued}`);
    assert.equal(pick(target, 'fillRect').length, levelCount, '前提: 全タグが描かれている');
  });
}

test('計算量ゲートの検出力: 2 フレーム描けば発行は 1 フレームぶんを超える（Spy が生きている）', () => {
  const p = new PriceLevelLinesPrimitive();
  let issued = 0;
  p.attached({
    chart: {},
    series: { priceToCoordinate: (price) => { issued += 1; return Y_OF(price); } },
    requestUpdate: () => {},
  });
  p.setLevels(LEVELS);
  issued = 0;
  const target = fakeTarget();
  p.draw(target);
  p.draw(target);
  assert.notEqual(issued - pick(target, 'moveTo').length / 2, 0, '変異（再描画）を検出できていない');
});
