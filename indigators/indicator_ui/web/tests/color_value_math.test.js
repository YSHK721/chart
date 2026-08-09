// color_value_math.test.js — color_value.js に加法する「色の数学」5 関数の単体テスト。
//
// 検証対象は値に閉じた演算のみ（意味＝ColorRole・解決順・適用点は本テストの対象外）。
//
// 全域性の規律（color_value.js:52-56 のコメント）に乗る:
//   すべての公開関数の戻り値は「isNormalizedHex を満たす hex6」か「null」のどちらかで、
//   `#NaN0405` のような "hex6 に見えない保存形" も `#7f.8...` のような小数混入も作らない。
//   数値を返す 2 関数（relativeLuminance / contrastRatio）は「有限数」か「null」。
//
// 受理集合（本テストが固定する決定）: 入力色は **6 桁 hex のみ**（大文字小文字は問わない）。
//   3 桁 hex・rgb() は受理しない（設計指示の「hex6 でない → null」を逐語で適用する）。
//   出力は常に小文字 6 桁（§4.4 の保存形）。

import test from 'node:test';
import assert from 'node:assert/strict';

import {
  isColorValue, isNormalizedHex, clampChannel, toChannels, channelsToHex, normalizeHexColor,
  mixChannels, relativeLuminance, contrastRatio, rotateHue, desaturate,
} from '../js/domain/color_value.js';

// --- 共通の題材 ---------------------------------------------------------
const BLACK = '#000000';
const WHITE = '#ffffff';
const RED = '#ff0000';
const GREEN = '#00ff00';
const BLUE = '#0000ff';

// 決定論的な走査用サンプル（Math.random / Date.now は使わない＝F.I.R.S.T Repeatable）。
const STEPS = [0, 51, 102, 153, 204, 255];
const SAMPLE_COLORS = [];
for (const r of STEPS) {
  for (const g of STEPS) {
    for (const b of STEPS) {
      SAMPLE_COLORS.push(channelsToHex([r, g, b]));
    }
  }
}

// 色として不正な入力（「非文字列」「hex6 でない」の両方を並べる）。
const BAD_COLORS = [
  null, undefined, '', '#abc', '#abcd', '#abcde', '#abcdefa', 'abcdef', '#gghhii',
  'rgb(1,2,3)', 'rgba(1,2,3,0.5)', '#12345', 0, 1, NaN, {}, [], () => {}, true,
];
// 数値として不正な入力（非有限数・非数値）。
const BAD_NUMBERS = [NaN, Infinity, -Infinity, '0', '1', null, undefined, {}, [], () => {}];

function gray(n) {
  return channelsToHex([n, n, n]);
}

// ========================================================================
// mixChannels
// ========================================================================

test('TC-CV01 mixChannels: 端点 t=0 は a、t=1 は b（出力は保存形の小文字）', () => {
  // Arrange / Act / Assert
  assert.equal(mixChannels(BLACK, WHITE, 0), BLACK);
  assert.equal(mixChannels(BLACK, WHITE, 1), WHITE);
  assert.equal(mixChannels('#123456', '#ABCDEF', 0), '#123456');
  assert.equal(mixChannels('#123456', '#ABCDEF', 1), '#abcdef', '大文字入力でも出力は保存形');
});

test('TC-CV02 mixChannels: a===b なら任意の t で a（恒等）', () => {
  for (const c of SAMPLE_COLORS) {
    for (const t of [0, 0.25, 0.5, 1 / 3, 0.75, 1]) {
      assert.equal(mixChannels(c, c, t), c, `${c} @ t=${t}`);
    }
  }
});

test('TC-CV03 mixChannels: 中点は丸めて保存形になる（小数チャネルを作らない）', () => {
  // 0 と 255 の中点は 127.5。丸めずに組み立てると "#7f.87f.87f.8" となり hex6 でなくなる。
  const mid = mixChannels(BLACK, WHITE, 0.5);
  assert.ok(isNormalizedHex(mid), `中点が保存形でない: ${String(mid)}`);
  assert.equal(mid, '#808080', '127.5 は正方向へ丸める（既存 normalizeHexColor の Math.round と同一規則）');
  // 全サンプルの総当りで「保存形か null」の不変条件を確認する。
  for (const a of SAMPLE_COLORS) {
    for (const t of [0.1, 0.5, 0.9, 1 / 3]) {
      const out = mixChannels(a, '#0f1e2d', t);
      assert.ok(isNormalizedHex(out), `${a} @ t=${t} → ${String(out)}`);
    }
  }
});

test('TC-CV04 mixChannels: t は [0,1] へクランプする（境界外は端点と同一結果）', () => {
  assert.equal(mixChannels(BLACK, WHITE, -0.001), BLACK);
  assert.equal(mixChannels(BLACK, WHITE, -1), BLACK);
  assert.equal(mixChannels(BLACK, WHITE, -1e9), BLACK);
  assert.equal(mixChannels(BLACK, WHITE, 1.001), WHITE);
  assert.equal(mixChannels(BLACK, WHITE, 2), WHITE);
  assert.equal(mixChannels(BLACK, WHITE, 1e9), WHITE);
});

test('TC-CV05 mixChannels: 不正入力は null（例外を投げない）', () => {
  for (const bad of BAD_COLORS) {
    assert.equal(mixChannels(bad, WHITE, 0.5), null, `a=${String(bad)}`);
    assert.equal(mixChannels(BLACK, bad, 0.5), null, `b=${String(bad)}`);
  }
  for (const bad of BAD_NUMBERS) {
    assert.equal(mixChannels(BLACK, WHITE, bad), null, `t=${String(bad)}`);
  }
});

// ========================================================================
// relativeLuminance（WCAG 2.x）
// ========================================================================

test('TC-CV06 relativeLuminance: 端点は黒 0・白 1', () => {
  assert.equal(relativeLuminance(BLACK), 0);
  assert.equal(relativeLuminance(WHITE), 1);
});

test('TC-CV07 relativeLuminance: 原色は WCAG 2.x の係数そのもの（0.2126 / 0.7152 / 0.0722）', () => {
  // 線形化後の値が 1 になるため、原色の輝度は係数と一致する（定義の逐語確認）。
  assert.ok(Math.abs(relativeLuminance(RED) - 0.2126) < 1e-12);
  assert.ok(Math.abs(relativeLuminance(GREEN) - 0.7152) < 1e-12);
  assert.ok(Math.abs(relativeLuminance(BLUE) - 0.0722) < 1e-12);
  assert.equal(relativeLuminance('#FF0000'), relativeLuminance(RED), '大文字入力でも同値');
});

test('TC-CV08 relativeLuminance: 値域は [0,1] で、灰の階調に対し狭義単調増加', () => {
  let prev = -1;
  for (let n = 0; n <= 255; n += 1) {
    const y = relativeLuminance(gray(n));
    assert.ok(Number.isFinite(y), `gray(${n})`);
    assert.ok(y >= 0 && y <= 1, `gray(${n}) = ${y} が [0,1] 外`);
    assert.ok(y > prev, `gray(${n}) が単調でない`);
    prev = y;
  }
  for (const c of SAMPLE_COLORS) {
    const y = relativeLuminance(c);
    assert.ok(Number.isFinite(y) && y >= 0 && y <= 1, `${c} → ${String(y)}`);
  }
});

test('TC-CV09 relativeLuminance: 不正入力は null（NaN を作らない）', () => {
  for (const bad of BAD_COLORS) {
    assert.equal(relativeLuminance(bad), null, String(bad));
  }
});

// ========================================================================
// contrastRatio（WCAG 2.x）
// ========================================================================

test('TC-CV10 contrastRatio: 白と黒は 21（上限・定義値）', () => {
  assert.equal(contrastRatio(WHITE, BLACK), 21);
  assert.equal(contrastRatio(BLACK, WHITE), 21, '引数順に依らない');
});

test('TC-CV11 contrastRatio: 同一色は 1（下限）', () => {
  for (const c of SAMPLE_COLORS) {
    assert.equal(contrastRatio(c, c), 1, c);
  }
});

test('TC-CV12 contrastRatio: 対称かつ値域は [1,21]', () => {
  for (const a of SAMPLE_COLORS) {
    const r1 = contrastRatio(a, '#131722');
    const r2 = contrastRatio('#131722', a);
    assert.equal(r1, r2, `対称性 ${a}`);
    assert.ok(Number.isFinite(r1) && r1 >= 1 && r1 <= 21, `${a} → ${String(r1)}`);
  }
});

test('TC-CV13 contrastRatio: 不正入力は null', () => {
  for (const bad of BAD_COLORS) {
    assert.equal(contrastRatio(bad, WHITE), null, `a=${String(bad)}`);
    assert.equal(contrastRatio(WHITE, bad), null, `b=${String(bad)}`);
  }
});

// ========================================================================
// rotateHue
// ========================================================================

test('TC-CV14 rotateHue: 0 度・360 度・-360 度は恒等（HSL 往復が値を壊さない）', () => {
  for (const c of SAMPLE_COLORS) {
    assert.equal(rotateHue(c, 0), c, `${c} @0`);
    assert.equal(rotateHue(c, 360), c, `${c} @360`);
    assert.equal(rotateHue(c, -360), c, `${c} @-360`);
    assert.equal(rotateHue(c, 720), c, `${c} @720`);
  }
  assert.equal(rotateHue('#ABCDEF', 0), '#abcdef', '大文字入力でも出力は保存形');
});

test('TC-CV15 rotateHue: 純色の 120 度回転は R→G→B（既知値）', () => {
  assert.equal(rotateHue(RED, 120), GREEN);
  assert.equal(rotateHue(GREEN, 120), BLUE);
  assert.equal(rotateHue(BLUE, 120), RED);
  assert.equal(rotateHue(RED, 240), BLUE);
  assert.equal(rotateHue(RED, -120), BLUE, '負の回転も同一円周上');
});

test('TC-CV16 rotateHue: 無彩色は色相を持たないため回転しても不動', () => {
  for (let n = 0; n <= 255; n += 17) {
    const g = gray(n);
    for (const deg of [1, 45, 90, 180, 270, 359]) {
      assert.equal(rotateHue(g, deg), g, `${g} @${deg}`);
    }
  }
});

test('TC-CV17 rotateHue: 彩度・明度を保つ（HSL の S,L は max/min のみの関数＝両者が不変）', () => {
  // 実装非依存の不変条件で検証する（テスト側で HSL を再実装しない）。
  //   L = (max+min)/2、S = (max-min)/(1-|2L-1|) はいずれも {max, min} だけで決まる。
  for (const c of SAMPLE_COLORS) {
    const src = toChannels(c);
    const srcPair = [Math.min(...src), Math.max(...src)];
    for (const deg of [30, 90, 150, 210, 300]) {
      const out = rotateHue(c, deg);
      assert.ok(isNormalizedHex(out), `${c} @${deg} → ${String(out)}`);
      const dst = toChannels(out);
      assert.deepEqual([Math.min(...dst), Math.max(...dst)], srcPair,
        `${c} @${deg}: 彩度・明度が動いた（${out}）`);
    }
  }
});

test('TC-CV18 rotateHue: 不正入力は null（非 hex6・非有限の角度）', () => {
  for (const bad of BAD_COLORS) {
    assert.equal(rotateHue(bad, 90), null, `hex=${String(bad)}`);
  }
  for (const bad of BAD_NUMBERS) {
    assert.equal(rotateHue(RED, bad), null, `deg=${String(bad)}`);
  }
});

// ========================================================================
// desaturate
// ========================================================================

test('TC-CV19 desaturate: 出力は無彩色（3 チャネルが等しい）', () => {
  for (const c of SAMPLE_COLORS) {
    const out = desaturate(c);
    assert.ok(isNormalizedHex(out), `${c} → ${String(out)}`);
    const [r, g, b] = toChannels(out);
    assert.equal(r, g, `${c} → ${out}`);
    assert.equal(g, b, `${c} → ${out}`);
  }
});

test('TC-CV20 desaturate: 相対輝度を保つ（8bit 階調で到達可能な最良点＝隣接階調より近い）', () => {
  for (const c of SAMPLE_COLORS) {
    const target = relativeLuminance(c);
    const out = desaturate(c);
    const n = toChannels(out)[0];
    const err = Math.abs(relativeLuminance(out) - target);
    // 量子化誤差の範囲内であること（1 階調ぶんの輝度差は最大でも約 0.009）。
    assert.ok(err < 0.005, `${c} → ${out}: Δ輝度 ${err}`);
    // かつ、隣接階調より真に近い（＝最良の無彩色を選んでいる）。
    for (const m of [n - 1, n + 1]) {
      if (m < 0 || m > 255) continue;
      const alt = Math.abs(relativeLuminance(gray(m)) - target);
      assert.ok(err <= alt, `${c} → ${out}: gray(${m}) の方が近い`);
    }
  }
});

test('TC-CV21 desaturate: 無彩色は不動点（黒・白・中間灰）', () => {
  for (let n = 0; n <= 255; n += 1) {
    const g = gray(n);
    assert.equal(desaturate(g), g, g);
  }
});

test('TC-CV22 desaturate: 不正入力は null', () => {
  for (const bad of BAD_COLORS) {
    assert.equal(desaturate(bad), null, String(bad));
  }
});

// ========================================================================
// 全域性の横断確認・既存公開面の回帰
// ========================================================================

test('TC-CV23 加法 5 関数の戻り値は「保存形 hex6 か null」／「有限数か null」に限られる', () => {
  const colorOut = [
    (c) => mixChannels(c, '#5a6b7c', 0.37),
    (c) => rotateHue(c, 137),
    (c) => desaturate(c),
  ];
  const numberOut = [
    (c) => relativeLuminance(c),
    (c) => contrastRatio(c, '#131722'),
  ];
  for (const c of [...SAMPLE_COLORS, ...BAD_COLORS]) {
    const valid = typeof c === 'string' && /^#[0-9a-fA-F]{6}$/.test(c);
    for (const f of colorOut) {
      const out = f(c);
      assert.ok(out === null || isNormalizedHex(out), `${String(c)} → ${String(out)}`);
      assert.equal(out === null, !valid, `${String(c)}: null 判定が受理集合と一致しない`);
    }
    for (const f of numberOut) {
      const out = f(c);
      assert.ok(out === null || Number.isFinite(out), `${String(c)} → ${String(out)}`);
      assert.equal(out === null, !valid, `${String(c)}: null 判定が受理集合と一致しない`);
    }
  }
});

test('TC-CV24 既存公開面は不変（加法によって振る舞いが変わっていない）', () => {
  assert.equal(isColorValue('#abc'), true);
  assert.equal(isColorValue('rgb(1,2,3)'), true);
  assert.equal(isColorValue(null), false);
  assert.equal(isNormalizedHex('#abcdef'), true);
  assert.equal(isNormalizedHex('#ABCDEF'), false);
  assert.equal(clampChannel(-5), 0);
  assert.equal(clampChannel(300), 255);
  assert.equal(clampChannel(128), 128);
  assert.deepEqual(toChannels('#0a141e'), [10, 20, 30]);
  assert.equal(channelsToHex([10, 20, 30]), '#0a141e');
  assert.equal(channelsToHex([NaN, 20, 30]), null);
  assert.equal(normalizeHexColor('#ABC'), '#aabbcc');
  assert.equal(normalizeHexColor('rgb(1,2,3)'), '#010203');
  assert.equal(normalizeHexColor('nonsense'), null);
});
