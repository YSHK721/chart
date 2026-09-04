// series_drawer_theme.test.js — σ 水準線ランプ・ウォーターマークをテーマ配線点へ接続する（段階 5-E）。
//
// なぜ本ファイルが重要か（走査では捕まらないリテラル）:
//   σ 水準線の 2 端点は `SCHEME_CALM = [46, 125, 50]` という**チャネル配列**で書かれていた。
//   `#` と `rgba(` だけを見る走査テストはこれを 1 件も検出できないため、見逃したまま
//   「リテラル 0 件」と主張できてしまう。よって配線点化して配列そのものを消す。
//
// 意味の割当（コード内コメントと語彙定義の照合）:
//   実装コメントは「中心＝穏やか」「両極端＝過熱」と書いている。語彙定義では
//   neutral＝基準・中立（中心線）、alert＝警戒・外れ値（通常域を外れた／過熱している）。
//   よって calm 端点は neutral、hot 端点は alert である。
//
// ISSUE-360 の射程: 同 ISSUE は MP の TPO バーが「HSL 色相ランプなので 2 色から再現できない」と
//   したうえで、pane σ 水準線（series_drawer.js:49-54）を「**2 端点の RGB 線形補間**」として
//   明示的に対比している。つまり本ファイルのランプは 2 色から厳密に再現できる側である。

import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

import { schemeColor, LEVEL_LINE_DIM, WATERMARK_COLOR } from '../js/adapter/front/series_drawer.js';
import { CHROME_CURRENT, chromeSlot } from '../js/usecase/chrome_tokens.js';
import { resolveChromeSlotColor } from '../js/usecase/color_resolver.js';

const SRC = readFileSync(
  fileURLToPath(new URL('../js/adapter/front/series_drawer.js', import.meta.url)), 'utf8',
);

function stripComments(src) {
  return src
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .replace(/(^|[^:])\/\/[^\n]*/g, '$1');
}

test('TC-SD-T01 恒等: 接続前の schemeColor と出力が全域で一致する（t = 0 / 0.5 / 1）', () => {
  // Arrange: 接続前の実装（SCHEME_CALM=[46,125,50] / SCHEME_HOT=[211,47,47] / dim=0.55）を
  //   そのまま再現した参照値。設計値ではなく、変更前のコードが返していた値そのもの。
  const lerp = (a, b, t) => a + (b - a) * t;
  const reference = (t) => {
    const calm = [46, 125, 50];
    const hot = [211, 47, 47];
    const r = Math.round(lerp(calm[0], hot[0], t) * 0.55);
    const g = Math.round(lerp(calm[1], hot[1], t) * 0.55);
    const b = Math.round(lerp(calm[2], hot[2], t) * 0.55);
    return `rgb(${r}, ${g}, ${b})`;
  };
  // Act / Assert: 境界値（下限 0・上限 1）と中間。
  for (const t of [0, 0.25, 0.5, 0.75, 1]) {
    assert.equal(schemeColor(t, LEVEL_LINE_DIM), reference(t), `t=${t}`);
  }
});

test('TC-SD-T02 恒等: ランプの端点は台帳の現行値から来る（値を写経していない）', () => {
  // Arrange / Act / Assert
  assert.equal(CHROME_CURRENT.levelSchemeCalm, '#2e7d32', '穏やか端（緑）の現行値');
  assert.equal(CHROME_CURRENT.levelSchemeHot, '#d32f2f', '過熱端（赤）の現行値');
  // t=0 は calm 端点の dim 版、t=1 は hot 端点の dim 版になる。
  const dimOf = (hex) => {
    const ch = [1, 3, 5].map((i) => Math.round(parseInt(hex.slice(i, i + 2), 16) * LEVEL_LINE_DIM));
    return `rgb(${ch[0]}, ${ch[1]}, ${ch[2]})`;
  };
  assert.equal(schemeColor(0, LEVEL_LINE_DIM), dimOf(CHROME_CURRENT.levelSchemeCalm));
  assert.equal(schemeColor(1, LEVEL_LINE_DIM), dimOf(CHROME_CURRENT.levelSchemeHot));
});

test('TC-SD-T03 通過条件 2: series_drawer.js に素の色リテラルが残っていない（チャネル配列を含む）', () => {
  // Arrange
  const code = stripComments(SRC);
  // Act: 文字列形式の色。
  const strings = [...code.matchAll(/#[0-9a-fA-F]{3,8}|rgba?\([^)]*\)|hsla?\([^)]*\)/g)]
    .map((m) => m[0]).filter((v) => !v.includes('${'));
  // Act: **チャネル配列形式**の色（[46, 125, 50] のような 0..255 の 3 つ組）。
  //   これを見ないと、配列で書かれた色を見逃したまま 0 件と主張できてしまう。
  const arrays = [...code.matchAll(/\[\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(\d{1,3})\s*\]/g)]
    .filter((m) => [1, 2, 3].every((i) => Number(m[i]) <= 255))
    .map((m) => m[0]);
  // Assert
  assert.deepEqual(strings, [], `文字列の色リテラルが残っている: ${strings.join(' / ')}`);
  assert.deepEqual(arrays, [], `チャネル配列の色リテラルが残っている: ${arrays.join(' / ')}`);
});

test('TC-SD-T04 検出器の自己検査: チャネル配列形の色を実際に検出できる（走査が空振りしていない）', () => {
  // TC-SD-T03 の「0 件」は、検出器が何も見つけられないだけでも成立してしまう。よって
  //   同じ検出器が既知の陽性標本を捉えることを、同じ場所で示す（5-C の検出器自己検査と同じ）。
  // Arrange: 接続前のコードに実在した綴り。
  const positive = 'const SCHEME_CALM = [46, 125, 50];\nconst SCHEME_HOT = [211, 47, 47];';
  // Act
  const hits = [...positive.matchAll(/\[\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(\d{1,3})\s*\]/g)]
    .filter((m) => [1, 2, 3].every((i) => Number(m[i]) <= 255))
    .map((m) => m[0]);
  // Assert
  assert.deepEqual(hits, ['[46, 125, 50]', '[211, 47, 47]'], 'チャネル配列検出器が陽性標本を逃した');
  // 色でない 3 つ組（256 以上を含む）は拾わない＝誤検出で走査が使い物にならなくならない。
  const negative = 'const SIZES = [300, 12, 4];';
  const falseHits = [...negative.matchAll(/\[\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(\d{1,3})\s*\]/g)]
    .filter((m) => [1, 2, 3].every((i) => Number(m[i]) <= 255));
  assert.deepEqual(falseHits, [], '色でない 3 つ組を色と誤認している');
});

test('TC-SD-T05 通過条件 5: 穏やか端は neutral・過熱端は alert（意味に忠実な割当）', () => {
  // Arrange / Act / Assert
  assert.equal(chromeSlot('levelSchemeCalm').token, 'neutral');
  assert.equal(chromeSlot('levelSchemeHot').token, 'alert');
});

test('TC-SD-T06 テーマ宣言がランプ端点に届く（配線が名前だけでない）', () => {
  // 穏やか端は neutral のクロム既定そのもの（非派生）＝宣言値がそのまま出る。
  // 過熱端は alert のクロム既定（琥珀 #e0a24a）とは別の色（赤 #d32f2f）なので、5-D の
  //   delta 機構で表す。よって宣言値そのものではなく「宣言値からの実測差分」が出る。
  //   ここで確かめるのは**届いていること**であり、届いた先の合成規則は下の恒等テストが持つ。
  // Arrange
  const theme = { roleColors: { neutral: '#102030', alert: '#405060' } };
  // Act
  const calm = resolveChromeSlotColor({ slotId: 'levelSchemeCalm', theme });
  const hot = resolveChromeSlotColor({ slotId: 'levelSchemeHot', theme });
  // Assert
  assert.equal(calm, '#102030', '穏やか端に neutral の宣言が届いていない');
  assert.notEqual(hot, CHROME_CURRENT.levelSchemeHot, '過熱端に alert の宣言が届いていない');
  // delta は加法（有彩色の濃淡）。0..255 でクランプされる。
  const ch = (h) => [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16));
  const want = ch('#405060').map((v, i) => Math.max(0, Math.min(255, v + [-13, -115, -27][i])));
  assert.deepEqual(ch(hot), want, '過熱端の合成が実測差分どおりでない');
});

test('TC-SD-T07b 恒等: alert が未宣言なら過熱端は現行リテラルのまま（派生化で見た目が動かない）', () => {
  // 派生に切り替えたことで既定の見た目が動いていないことの実証（通過条件 1）。
  // Arrange / Act / Assert
  assert.equal(resolveChromeSlotColor({ slotId: 'levelSchemeHot', theme: null }), '#d32f2f');
  assert.equal(resolveChromeSlotColor({ slotId: 'levelSchemeCalm', theme: null }), '#2e7d32');
  // alert のクロム既定から実測差分で現行値へ厳密に戻れる（設計値ではなく逆算）。
  const themeAlertDefault = { roleColors: { alert: '#e0a24a' } };
  assert.equal(resolveChromeSlotColor({ slotId: 'levelSchemeHot', theme: themeAlertDefault }), '#d32f2f');
});

test('TC-SD-T07 恒等: ウォーターマーク色は現行リテラルのまま（未使用 export の値を変えない）', () => {
  // WATERMARK_COLOR は実測でどこからも参照されていない（export のみ）。使われていないからと
  //   いって値を変えたり消したりはしない（削除は承認事項）。二重定義だけを消す。
  // Arrange / Act / Assert
  assert.equal(WATERMARK_COLOR, 'rgba(209, 212, 220, 0.9)');
  assert.equal(WATERMARK_COLOR, CHROME_CURRENT.watermark, '台帳の現行値と一致しない');
  assert.equal(chromeSlot('watermark').token, 'text', '(209,212,220) は #d1d4dc＝文字色');
});
