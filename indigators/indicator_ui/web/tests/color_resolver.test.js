// color_resolver.test.js — 色の決定（純関数）の単体テスト
//   （基本設計_指標カラーテーマ.md §4.5 指標 5 段 / §4.6 クロム 2 段 / §4.7 tfModifier /
//    §4.8 計算.時間足 / §5.8 系列名→トークン・§7.4 段階 2 通過条件 3〜6・8）。
//
// resolver は DOM・Storage・lightweight-charts のいずれにも依存しない純関数であり、
//   「色の決定」という 1 つの責務だけを持つ（SRP）。適用（applySeriesStyle / applyOptions /
//   setProperty）は別のモジュールが担う。よって本テストは値の写像だけを全分岐で固定する。

import test from 'node:test';
import assert from 'node:assert/strict';

import {
  resolveSeriesColor, resolveChromeColor, resolveChromeSlotColor, resolveDerivedChromeColor,
  offsetChannels, applyTfModifier, resolveInstanceTimeframe, roleForSeriesName,
  buildColorRoleIndex, DEFAULT_SERIES_COLOR,
} from '../js/usecase/color_resolver.js';
import { CHROME_DEFAULT, CHROME_CURRENT, CHROME_SLOTS } from '../js/usecase/chrome_tokens.js';
import { COLOR_ROLES, ColorRole } from '../js/domain/color_roles.js';
import { get } from '../js/usecase/catalog.js';
import { TF_CODES } from '../js/domain/tf_meta.js';
import { expandSeriesNamePattern } from '../js/adapter/front/series_name_matcher.js';

const theme = (roleColors, tfModifier = null) => ({ themeId: 'thm#1', name: 't', roleColors, tfModifier });

// =========================================================================
// §4.5 指標系列の解決順（5 段）— 通過条件 3
// =========================================================================

test('§4.5 ステップ 1: colorLocked === true かつ色が在れば最優先で返す', () => {
  const styles = { s: { color: '#abcdef', colorLocked: true } };
  const color = resolveSeriesColor({
    styles, seriesName: 's', role: ColorRole.RANGE, theme: theme({ range: '#ffffff' }),
    timeframe: '1D', payloadColor: '#111111',
  });
  assert.equal(color, '#abcdef');
});

test('§4.5 ステップ 1: colorLocked=true でも色が不正なら次段へ降格する', () => {
  const styles = { s: { color: 'not-a-color', colorLocked: true } };
  const color = resolveSeriesColor({
    styles, seriesName: 's', role: ColorRole.RANGE, theme: theme({ range: '#ffffff' }),
    timeframe: '1D', payloadColor: '#111111',
  });
  assert.equal(color, '#ffffff');
});

test('§4.5 ステップ 1: colorLocked が false/未設定ならロック段を通らない', () => {
  for (const locked of [false, null, undefined, 'true', 1]) {
    const styles = { s: { color: '#abcdef', colorLocked: locked } };
    const color = resolveSeriesColor({
      styles, seriesName: 's', role: ColorRole.RANGE, theme: theme({ range: '#ffffff' }),
      timeframe: '1D', payloadColor: '#111111',
    });
    assert.equal(color, '#ffffff', String(locked));
  }
});

test('§4.5 ステップ 2: テーマがトークンを宣言していれば意味色を返す', () => {
  const color = resolveSeriesColor({
    styles: null, seriesName: 's', role: ColorRole.PRIMARY,
    theme: theme({ primary: '#010203' }), timeframe: '1D', payloadColor: '#111111',
  });
  assert.equal(color, '#010203');
});

test('§4.5 R-4: role が null ならステップ 2 を飛ばして payload 色へ降格する', () => {
  const color = resolveSeriesColor({
    styles: null, seriesName: 's', role: null,
    theme: theme({ primary: '#010203' }), timeframe: '1D', payloadColor: '#111111',
  });
  assert.equal(color, '#111111');
});

test('§4.5 R-5 / F-C9: roleColors の値が hex6 でなければ未宣言として降格する', () => {
  for (const bad of ['#FFF', '#FFFFFF', 'rgb(1,2,3)', 'red', '', null, 123, '#gggggg']) {
    const color = resolveSeriesColor({
      styles: null, seriesName: 's', role: ColorRole.PRIMARY,
      theme: theme({ primary: bad }), timeframe: '1D', payloadColor: '#111111',
    });
    assert.equal(color, '#111111', String(bad));
  }
  // 保存値は正規化済みの小文字 6 桁のみを受理する。
  assert.equal(resolveSeriesColor({
    styles: null, seriesName: 's', role: ColorRole.PRIMARY,
    theme: theme({ primary: '#0a0b0c' }), timeframe: '1D', payloadColor: '#111111',
  }), '#0a0b0c');
});

test('§4.5 R-5: 語彙外の role はステップ 2 を通らない（前方互換）', () => {
  const color = resolveSeriesColor({
    styles: null, seriesName: 's', role: 'nonsense',
    theme: theme({ nonsense: '#010203' }), timeframe: '1D', payloadColor: '#111111',
  });
  assert.equal(color, '#111111');
});

test('§4.5 ステップ 3（U-5）: テーマ未設定時はロックなし個別色が生き残る（現行挙動の保存）', () => {
  const styles = { s: { color: 'rgba(1, 2, 3, 0.5)' } };
  const color = resolveSeriesColor({
    styles, seriesName: 's', role: ColorRole.PRIMARY, theme: null,
    timeframe: '1D', payloadColor: '#111111',
  });
  assert.equal(color, 'rgba(1, 2, 3, 0.5)');
});

test('§4.5 ステップ 3: テーマが当該トークンを宣言していなければ個別色が生きる', () => {
  const styles = { s: { color: '#c0ffee' } };
  const color = resolveSeriesColor({
    styles, seriesName: 's', role: ColorRole.PRIMARY, theme: theme({ range: '#ffffff' }),
    timeframe: '1D', payloadColor: '#111111',
  });
  assert.equal(color, '#c0ffee');
});

test('§4.5 ステップ 4: payload 色（backend 既定）へ降格する', () => {
  for (const c of ['#abc', '#aabbcc', 'rgb(1,2,3)', 'rgba(1,2,3,0.5)', 'RGBA(1,2,3,0.5)']) {
    assert.equal(resolveSeriesColor({
      styles: null, seriesName: 's', role: null, theme: null, timeframe: '1D', payloadColor: c,
    }), c, c);
  }
});

test('§4.5 ステップ 5: payload 色も無ければ既定色 #2962ff', () => {
  for (const c of [null, undefined, '', 'nope', 0]) {
    assert.equal(resolveSeriesColor({
      styles: null, seriesName: 's', role: null, theme: null, timeframe: '1D', payloadColor: c,
    }), DEFAULT_SERIES_COLOR, String(c));
  }
  assert.equal(DEFAULT_SERIES_COLOR, '#2962ff');
});

test('§4.5: styles に当該系列のエントリが無くても全域的に解決する', () => {
  assert.equal(resolveSeriesColor({
    styles: { other: { color: '#000000' } }, seriesName: 's', role: null, theme: null,
    timeframe: '1D', payloadColor: '#111111',
  }), '#111111');
  assert.equal(resolveSeriesColor({}), DEFAULT_SERIES_COLOR);
  assert.equal(resolveSeriesColor({ styles: undefined, seriesName: undefined }), DEFAULT_SERIES_COLOR);
});

// 通過条件 8: 適用履歴に依存しない（R-6）。
test('通過条件 8: テーマ A → テーマ B（B は当該トークン未宣言）で未適用時と同一色へ戻る', () => {
  const args = { styles: null, seriesName: 's', role: ColorRole.RANGE, timeframe: '1D', payloadColor: '#111111' };
  const none = resolveSeriesColor({ ...args, theme: null });
  const a = resolveSeriesColor({ ...args, theme: theme({ range: '#ffffff' }) });
  const b = resolveSeriesColor({ ...args, theme: theme({ primary: '#222222' }) });
  assert.equal(a, '#ffffff');
  assert.equal(b, none, 'B 適用後の色がテーマ未適用時と一致しない（適用履歴に依存している）');
  assert.equal(b, '#111111');
});

// =========================================================================
// §4.7 applyTfModifier — 通過条件 6
// =========================================================================

test('§4.7: l = 0 は恒等（未宣言の足・tfModifier=null を含む）', () => {
  assert.equal(applyTfModifier('#123456', null, '1D'), '#123456');
  assert.equal(applyTfModifier('#123456', {}, '1D'), '#123456');
  assert.equal(applyTfModifier('#123456', { '1D': 0 }, '1D'), '#123456');
  assert.equal(applyTfModifier('#123456', { '5m': 0.5 }, '1D'), '#123456');
  assert.equal(applyTfModifier('#123456', { '1D': NaN }, '1D'), '#123456');
  assert.equal(applyTfModifier('#123456', { '1D': Infinity }, '1D'), '#123456');
});

test('§4.7: l = 1 は #ffffff、l = -1 は #000000（端点）', () => {
  assert.equal(applyTfModifier('#123456', { '1D': 1 }, '1D'), '#ffffff');
  assert.equal(applyTfModifier('#123456', { '1D': -1 }, '1D'), '#000000');
});

test('§4.7: l は [-1, 1] へクランプされる', () => {
  assert.equal(applyTfModifier('#123456', { '1D': 5 }, '1D'), '#ffffff');
  assert.equal(applyTfModifier('#123456', { '1D': -5 }, '1D'), '#000000');
});

test('§4.7: 丸めは小数第 3 位（floor(l*1000 + 0.5)/1000）', () => {
  // 0.0004 は 0 へ丸まる＝恒等。0.0005 は 0.001 へ丸まる＝恒等でない。
  assert.equal(applyTfModifier('#000000', { '1D': 0.0004 }, '1D'), '#000000');
  assert.notEqual(applyTfModifier('#808080', { '1D': 0.5 }, '1D'), '#808080');
});

test('§4.7: チャネル計算は白/黒への線形寄せ＋四捨五入（floor(c + 0.5)）', () => {
  // #808080 = (128,128,128)。l=0.5 → 128 + (255-128)*0.5 = 191.5 → floor(192) = 192 = 0xc0
  assert.equal(applyTfModifier('#808080', { '1D': 0.5 }, '1D'), '#c0c0c0');
  // l=-0.5 → 128 * 0.5 = 64 = 0x40
  assert.equal(applyTfModifier('#808080', { '1D': -0.5 }, '1D'), '#404040');
});

test('§4.7: 出力は常に小文字 6 桁 hex（0 詰め）', () => {
  // (1,2,3) × 0.5 = (0.5, 1, 1.5) → floor(x + 0.5) = (1, 1, 2)。0 詰めで 2 桁になる。
  const out = applyTfModifier('#010203', { '1D': -0.5 }, '1D');
  assert.match(out, /^#[0-9a-f]{6}$/);
  assert.equal(out, '#010102');
});

test('§4.7: hex6 以外の入力はそのまま返す（変調式の入力を 1 形式に固定）', () => {
  for (const v of ['rgba(1,2,3,1)', '#abc', 'red', null, undefined, 12]) {
    assert.equal(applyTfModifier(v, { '1D': 0.5 }, '1D'), v, String(v));
  }
});

test('§4.5 ステップ 2 は tfModifier を通す（意味色 × 時間足の明度差）', () => {
  const color = resolveSeriesColor({
    styles: null, seriesName: 's', role: ColorRole.PRIMARY,
    theme: theme({ primary: '#808080' }, { '5m': -0.5 }), timeframe: '5m', payloadColor: null,
  });
  assert.equal(color, '#404040');
  // 宣言のない足は恒等。
  assert.equal(resolveSeriesColor({
    styles: null, seriesName: 's', role: ColorRole.PRIMARY,
    theme: theme({ primary: '#808080' }, { '5m': -0.5 }), timeframe: '1D', payloadColor: null,
  }), '#808080');
});

// =========================================================================
// §4.6 クロムの解決順（2 段）— 通過条件 4・5
// =========================================================================

test('§4.6 通過条件 4: resolveChromeColor は 7 トークン全数で 2 段とも解決する', () => {
  for (const token of Object.keys(CHROME_DEFAULT)) {
    // ステップ 2（テーマ未設定・未宣言・不正値）→ 現行既定。
    assert.equal(resolveChromeColor({ token, theme: null }), CHROME_DEFAULT[token], token);
    assert.equal(resolveChromeColor({ token, theme: theme({}) }), CHROME_DEFAULT[token], token);
    assert.equal(resolveChromeColor({ token, theme: theme({ [token]: '#FFF' }) }), CHROME_DEFAULT[token], token);
    // ステップ 1（宣言あり）→ 意味色。
    assert.equal(resolveChromeColor({ token, theme: theme({ [token]: '#0a0b0c' }) }), '#0a0b0c', token);
  }
});

test('§4.6: 未知トークンでも例外を投げない（全域性・LSP）', () => {
  assert.equal(resolveChromeColor({ token: 'nonsense', theme: null }), null);
  assert.equal(resolveChromeColor({}), null);
});

test('§4.7: クロムは tfModifier を適用しない（時間足で地が動かない）', () => {
  const t = theme({ surface: '#808080' }, Object.fromEntries(TF_CODES.map((c) => [c, -0.5])));
  assert.equal(resolveChromeColor({ token: ColorRole.SURFACE, theme: t }), '#808080');
  // 引数として時間足を受け取らない（そもそも変調しようがない）。
  assert.equal(resolveChromeColor.length <= 1, true);
});

// --- 配線点単位の解決（恒等性の要）---
test('通過条件 6 の恒等: テーマ未設定なら全 20 配線点が現行リテラルを逐語で返す', () => {
  for (const slot of CHROME_SLOTS) {
    assert.equal(resolveChromeSlotColor({ slotId: slot.id, theme: null }), CHROME_CURRENT[slot.id], slot.id);
  }
});

test('#7: 不透明度を持つ配線点はテーマ宣言時に hex + 現行 alpha を rgba() で組む', () => {
  // 未設定時は現行リテラルそのもの（border の既定色ではない）。
  assert.equal(resolveChromeSlotColor({ slotId: 'paneSeparatorHover', theme: null }),
    'rgba(178,181,189,0.2)');
  // border を宣言したときだけ合成へ切り替わる。
  assert.equal(resolveChromeSlotColor({ slotId: 'paneSeparatorHover', theme: theme({ border: '#0a141e' }) }),
    'rgba(10, 20, 30, 0.2)');
});

test('派生配線点はテーマ宣言時に surface からのオフセットで解決する', () => {
  // #202020 = (32,32,32)。
  const t = theme({ surface: '#202020' });
  assert.equal(resolveChromeSlotColor({ slotId: 'dimCandle', theme: t }), '#23221d'); // +3,+2,-3 → (35,34,29)
  assert.equal(resolveChromeSlotColor({ slotId: 'analysisTint', theme: t }), '#282322'); // +8,+3,+2 → (40,35,34)
  assert.equal(resolveChromeSlotColor({ slotId: 'replayBoundaryDim', theme: t }), '#161616'); // -10 → (22,22,22)
});

test('resolveChromeSlotColor は未知 slot でも例外を投げない', () => {
  assert.equal(resolveChromeSlotColor({ slotId: 'nope', theme: null }), null);
  assert.equal(resolveChromeSlotColor({}), null);
});

// --- §4.6 派生・offsetChannels（通過条件 5）---
test('通過条件 5: offsetChannels が #131722 と実測差分から現行 3 色を厳密に再現する', () => {
  assert.equal(offsetChannels('#131722', [3, 2, -3]), '#16191f');
  assert.equal(offsetChannels('#131722', [8, 3, 2]), '#1b1a24');
  assert.equal(offsetChannels('#131722', [-10, -10, -10]), '#090d18');
});

test('offsetChannels は 0..255 でクランプし、整数演算のみで行う', () => {
  assert.equal(offsetChannels('#000000', [-1, -1, -1]), '#000000');
  assert.equal(offsetChannels('#ffffff', [1, 1, 1]), '#ffffff');
  assert.equal(offsetChannels('#ffffff', [-255, -255, -255]), '#000000');
  assert.equal(offsetChannels('#010203', [0, 0, 0]), '#010203');
});

test('offsetChannels は hex6 以外・不正 delta を素通しする（全域性）', () => {
  assert.equal(offsetChannels('rgba(1,2,3,1)', [1, 1, 1]), 'rgba(1,2,3,1)');
  assert.equal(offsetChannels('#131722', null), '#131722');
  assert.equal(offsetChannels('#131722', [1, 2]), '#131722');
});

test('resolveDerivedChromeColor は surface を解決してからオフセットする（恒等を含む）', () => {
  assert.equal(resolveDerivedChromeColor({ delta: [3, 2, -3], theme: null }), '#16191f');
  assert.equal(resolveDerivedChromeColor({ delta: [-10, -10, -10], theme: theme({ surface: '#202020' }) }), '#161616');
});

// =========================================================================
// §4.8 計算.時間足の解決
// =========================================================================

test('§4.8: "chart" / 未設定 / 未知値はチャート時間足へ落ちる', () => {
  for (const tf of ['chart', null, undefined, '', 'nonsense', 3]) {
    assert.equal(resolveInstanceTimeframe({ timeframe: tf }, '1h'), '1h', String(tf));
  }
  assert.equal(resolveInstanceTimeframe(null, '1h'), '1h');
  assert.equal(resolveInstanceTimeframe(undefined, '1h'), '1h');
});

test('§4.8: TF_CODES の値はそのまま固定足として返る', () => {
  for (const code of TF_CODES) {
    assert.equal(resolveInstanceTimeframe({ timeframe: code }, '1h'), code, code);
  }
});

// =========================================================================
// §5.8 系列名 → トークンの解決
// =========================================================================

const expand = expandSeriesNamePattern;

test('§5.8 規則 1: 静的 SeriesDef は seriesName 完全一致で解決する', () => {
  const def = get('moving_averages');
  assert.equal(roleForSeriesName({ def, seriesName: 'MA', expandPattern: expand }), ColorRole.PRIMARY);
  assert.equal(roleForSeriesName({ def, seriesName: 'Smoothing', expandPattern: expand }), ColorRole.SECONDARY);
  assert.equal(roleForSeriesName({ def, seriesName: 'Upper', expandPattern: expand }), ColorRole.RANGE);
  assert.equal(roleForSeriesName({ def, seriesName: 'Lower', expandPattern: expand }), ColorRole.RANGE);
});

test('§5.8 規則 2: 動的 SeriesDef はパターン展開集合の包含で解決する', () => {
  const def = get('profit_band');
  assert.equal(roleForSeriesName({ def, seriesName: 'pOH 95%', expandPattern: expand }), ColorRole.BULLISH);
  assert.equal(roleForSeriesName({ def, seriesName: 'nOL 51%', expandPattern: expand }), ColorRole.BEARISH);
  assert.equal(roleForSeriesName({ def, seriesName: 'pOL 99%', expandPattern: expand }), ColorRole.RANGE);
  assert.equal(roleForSeriesName({ def, seriesName: 'nOH 80%', expandPattern: expand }), ColorRole.RANGE);
});

test('§5.8 規則 3 / F-C7: 未知系列は null（エラーにしない）', () => {
  const def = get('moving_averages');
  assert.equal(roleForSeriesName({ def, seriesName: 'nonexistent', expandPattern: expand }), null);
  assert.equal(roleForSeriesName({ def: null, seriesName: 'MA', expandPattern: expand }), null);
  assert.equal(roleForSeriesName({}), null);
});

test('§5.8: 水準線（horizontal_line）の SeriesDef は解決対象から除外される', () => {
  // btlm_trail_marod は同名の line（primary）と 0% 水平基準線（level）を宣言する。
  //   水準線は priceLine 経路で applySeriesStyle に到達せず、実描画系列名として現れない（E-10）。
  //   除外しないと同名 2 件が競合し解決が非決定になる。
  for (const id of ['btlm_trail_marod', 'ma_marod']) {
    const def = get(id);
    assert.equal(roleForSeriesName({ def, seriesName: id, expandPattern: expand }), ColorRole.PRIMARY, id);
  }
  // 水準線しか持たない指標は、その名前でも解決しない（描画対象でないため）。
  const prp = get('price_range_power');
  assert.equal(roleForSeriesName({ def: prp, seriesName: 'price_range_power', expandPattern: expand }), null);
});

test('§5.8 規則 4: 同名複数一致は同一トークンのため順序が結果に影響しない（cvfe）', () => {
  const def = get('cvfe');
  // cvfe_mid は level_dash と line の 2 宣言。どちらを採っても neutral。
  assert.equal(roleForSeriesName({ def, seriesName: 'cvfe_mid', expandPattern: expand }), ColorRole.NEUTRAL);
  assert.equal(roleForSeriesName({ def, seriesName: 'cvfe_u1', expandPattern: expand }), ColorRole.RANGE);
  assert.equal(roleForSeriesName({ def, seriesName: 'cvfe_evq_med_hi', expandPattern: expand }), ColorRole.ALERT);
});

test('§5.8: params を渡すと動的パターンの *FromParam 展開に追随する', () => {
  const def = get('profit_band');
  // 静的 pcts に無い値でも、params 由来なら期待集合に入る（既存 expandSeriesNamePattern の契約）。
  const index = buildColorRoleIndex({ def, params: { probabilities: [0.51] }, expandPattern: expand });
  assert.ok(index instanceof Map);
  assert.equal(index.get('pOH 95%'), ColorRole.BULLISH, '静的 pcts 由来の名前は引き続き解決する');
});

test('buildColorRoleIndex はパターン展開を 1 度だけ行い、同じ結果を返す', () => {
  const def = get('profit_band');
  let calls = 0;
  const counting = (...a) => { calls += 1; return expand(...a); };
  const index = buildColorRoleIndex({ def, expandPattern: counting });
  assert.equal(calls, 4, '動的 SeriesDef 4 件につき 1 回ずつ');
  assert.equal(index.size, 28);
  for (const [name, role] of index) {
    assert.equal(roleForSeriesName({ def, seriesName: name, expandPattern: expand }), role, name);
  }
});

test('roleForSeriesName の戻り値は常に語彙内か null（全域性）', () => {
  for (const def of [get('cvfe'), get('tickvol'), get('btlm_trail'), get('market_profile')]) {
    for (const name of ['x', '', 'MA', 'cvfe_mid', def.id]) {
      const r = roleForSeriesName({ def, seriesName: name, expandPattern: expand });
      assert.ok(r === null || COLOR_ROLES.includes(r), `${def.id}/${name} → ${String(r)}`);
    }
  }
});
