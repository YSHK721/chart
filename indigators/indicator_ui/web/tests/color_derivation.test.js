// color_derivation.test.js — 宣言済みトークンから残りのトークンを導出する（段階 5-B・導出）。
//
// 目的（依頼者指示 2026-08-09）: 全色を変更可能にするとトークンが増えて認知負荷が上がる。
//   それを「ユーザーが決める項目を減らす」ことで相殺する。基点 5 語（surface / bullish /
//   bearish / alert / primary）を宣言すれば、残り 9 語は導出で埋まる。
//
// 規律（この 3 つが本ファイルの固定対象）:
//   1. 部分写像 — 導出元が無ければ導出先のキーを**生成しない**。これが恒等（D-11）の唯一の
//      保証で、`expandRoleColors({})` が `{}` であることに帰着する。
//   2. 明示 > 導出 — 宣言済みのキーは絶対に上書きしない。
//   3. 出口の型 — 導出結果が hex6 にならなければキーを生成しない（color_value.js の
//      「hex6 か null」規律に乗る）。
//
// 構造: Arrange-Act-Assert（AAA）。

import { test } from 'node:test';
import assert from 'node:assert/strict';

import {
  BASE_ROLE_TOKENS, DERIVED_ROLE_TOKENS, expandRoleColors,
} from '../js/usecase/color_derivation.js';
import { COLOR_ROLES } from '../js/domain/color_roles.js';

// 語彙 14 語へ「互いに異なる」値を割り当てた宣言（上書きの検出用）。値は語彙の並び順から機械的に
//   作る（手書きの表を置くと、語彙が増えたときに取り残しが出る）。
const ALL_DECLARED = Object.fromEntries(
  COLOR_ROLES.map((t, i) => [t, `#${String(i + 1).padStart(2, '0')}0102`]),
);

// =========================================================================
// TC-CD01 恒等（D-11）: 宣言が無ければ 1 キーも生成しない
// =========================================================================

test('TC-CD01 expandRoleColors: 空の宣言は空のまま（部分写像＝恒等の保証）', () => {
  // Arrange
  const declared = {};
  // Act
  const out = expandRoleColors(declared);
  // Assert
  assert.deepEqual(out, {}, '導出元が 1 つも無いので導出先も 1 つも生成しない');
});

// =========================================================================
// TC-CD02 明示 > 導出（通過条件 3）: 宣言済みキーは 1 つも上書きされない
// =========================================================================

test('TC-CD02 expandRoleColors: 14 語すべてを宣言したら 14 語すべてが宣言値のまま返る', () => {
  // Arrange
  const declared = { ...ALL_DECLARED };
  // Act
  const out = expandRoleColors(declared);
  // Assert
  for (const token of COLOR_ROLES) {
    assert.equal(out[token], ALL_DECLARED[token], `${token}: 宣言値が導出値で上書きされた`);
  }
});

// =========================================================================
// 導出表（1 トークン = 1 テスト）
//
// 期待値は**現行値から逆算した係数**で確定する。係数の逆算は現行 14 色（PRESET_THEMES「基本」）
//   を標本として、各規則の軸上で最小二乗により求めた（実測値は色ごとの test 内に逐語で記す）。
//   導出は**既定値**であって現行値の再現ではないため、一致は求めない。各 test は
//   「導出値」と「現行値との差」の両方を固定し、係数を動かしたときにどちらも落ちるようにする。
// =========================================================================

// 現行値（PRESET_THEMES「基本」の 14 色）。導出値との近さの実測対象。
const CURRENT = Object.freeze({
  bullish: '#00bfa5', bearish: '#ff5252', neutral: '#90a4ae', alert: '#ffa726',
  primary: '#42a5f5', secondary: '#7e57c2', range: '#26c6da', level: '#78909c',
  muted: '#546e7a', surface: '#131722', grid: '#1f2530', border: '#2a2e39',
  text: '#d1d4dc', highlight: '#f5f5f5',
});

// 現行値からの各チャネル差の絶対値の最大（近さの実測指標）。
function maxChannelDelta(a, b) {
  const ch = (h) => [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16));
  return Math.max(...ch(a).map((v, i) => Math.abs(v - ch(b)[i])));
}

test('TC-CD03 grid ← surface: 地の反対側へ 0.058 寄せる（現行 #131722→#1f2530 の逆算）', () => {
  // Arrange: 逆算の実測 — チャネル別 t = [0.0508, 0.0603, 0.0633] / 最小二乗 t = 0.0579。
  const declared = { surface: CURRENT.surface };
  // Act
  const out = expandRoleColors(declared);
  // Assert
  assert.equal(out.grid, '#21242f', 'mix(surface, 対比側, 0.058)');
  assert.equal(maxChannelDelta(out.grid, CURRENT.grid), 2, '現行 #1f2530 との最大チャネル差');
});

test('TC-CD04 border ← surface: grid よりさらに寄せる 0.100（現行 #131722→#2a2e39 の逆算）', () => {
  // Arrange: 逆算の実測 — チャネル別 t = [0.0975, 0.0991, 0.1041] / 最小二乗 t = 0.1001。
  const declared = { surface: CURRENT.surface };
  // Act
  const out = expandRoleColors(declared);
  // Assert
  assert.equal(out.border, '#2b2e38', 'mix(surface, 対比側, 0.100)');
  assert.equal(maxChannelDelta(out.border, CURRENT.border), 1, '現行 #2a2e39 との最大チャネル差');
});

test('TC-CD05 text ← surface: コントラストが立つ側へ 0.820 寄せる（現行 #131722→#d1d4dc の逆算）', () => {
  // Arrange: 逆算の実測 — チャネル別 t = [0.8051, 0.8147, 0.8416] / 最小二乗 t = 0.8196。
  const declared = { surface: CURRENT.surface };
  // Act
  const out = expandRoleColors(declared);
  // Assert
  assert.equal(out.text, '#d5d5d7', 'mix(surface, 対比側, 0.820)');
  assert.equal(maxChannelDelta(out.text, CURRENT.text), 5, '現行 #d1d4dc との最大チャネル差');
});

test('TC-CD05b text ← surface: 明るい地では暗い側へ寄る（「明度を上げる」ではなく「対比を立てる」）', () => {
  // Arrange: 地が白なら対比側は黒。方向が地に対して定義されていることの固定。
  const declared = { surface: '#ffffff' };
  // Act
  const out = expandRoleColors(declared);
  // Assert
  assert.equal(out.text, '#2e2e2e');
  assert.equal(out.grid, '#f0f0f0', 'grid も同じ軸（白地では暗くなる）');
});

test('TC-CD06 level ← text と surface: 中間 0.609（現行 #131722/#d1d4dc→#78909c の逆算）', () => {
  // Arrange: 逆算の実測 — チャネル別 t = [0.5316, 0.6402, 0.6559] / 最小二乗 t = 0.6085。
  //   導出元 text 自体が surface からの導出（連鎖）である点も同時に固定する。
  const declared = { surface: CURRENT.surface };
  // Act
  const out = expandRoleColors(declared);
  // Assert
  assert.equal(out.level, '#898b90', 'mix(surface, text, 0.609)');
  assert.equal(maxChannelDelta(out.level, CURRENT.level), 17, '現行 #78909c との最大チャネル差');
});

test('TC-CD06b level ← text と surface: 宣言済み text があればそれを導出元に使う（明示 > 導出）', () => {
  // Arrange: text を宣言すると、導出された text ではなく宣言値が導出元になる。
  const declared = { surface: CURRENT.surface, text: CURRENT.text };
  // Act
  const out = expandRoleColors(declared);
  // Assert
  assert.equal(out.text, CURRENT.text, '宣言値は不変');
  assert.equal(out.level, '#878a93', '導出元が宣言値へ替わるので level も替わる');
});

test('TC-CD07 muted ← level と surface: さらに地寄り 0.300（現行 #78909c/#131722→#546e7a の逆算）', () => {
  // Arrange: 逆算の実測 — チャネル別 t = [0.3564, 0.2810, 0.2787] / 最小二乗 t = 0.2995。
  const declared = { surface: CURRENT.surface };
  // Act
  const out = expandRoleColors(declared);
  // Assert
  assert.equal(out.muted, '#66686f', 'mix(level, surface, 0.300)');
  assert.equal(maxChannelDelta(out.muted, CURRENT.muted), 18, '現行 #546e7a との最大チャネル差');
});

test('TC-CD08 highlight ← text: 対比側へさらに 0.761（現行 #d1d4dc→#f5f5f5 の逆算・厳密一致）', () => {
  // Arrange: 逆算の実測 — チャネル別 t = [0.7826, 0.7674, 0.7143] / 最小二乗 t = 0.7611。
  //   導出 9 語のうち唯一、現行値と**厳密に一致**する（最大チャネル差 0）。
  const declared = { surface: CURRENT.surface };
  // Act
  const out = expandRoleColors(declared);
  // Assert
  assert.equal(out.highlight, '#f5f5f5', 'mix(text, 対比側, 0.761)');
  assert.equal(maxChannelDelta(out.highlight, CURRENT.highlight), 0, '現行 #f5f5f5 と厳密一致');
});

test('TC-CD08b highlight ← text: 導出元に surface を要する（方向は地に対して定義される）', () => {
  // Arrange: 「最も明るい」ではなく「地から最も遠い」。地が無ければ方向が決まらないので
  //   導出しない（部分写像）。設計表の導出元 text に surface を足した唯一の点。
  const declared = { text: CURRENT.text };
  // Act
  const out = expandRoleColors(declared);
  // Assert
  assert.equal('highlight' in out, false, 'surface が無ければ highlight を生成しない');
});

test('TC-CD09 secondary ← primary: 色相 +55 度（現行 #42a5f5→#7e57c2 の色相差の逆算）', () => {
  // Arrange: 逆算の実測 — primary の色相 206.82 度・secondary の色相 261.87 度 → 差 55.05 度。
  //   rotateHue は彩度・明度（max/min）を保つため、現行 secondary（primary より低彩度・低明度）
  //   とは色相以外がずれる。最大チャネル差 51 は導出 9 語で 2 番目に大きい（実測）。
  const declared = { primary: CURRENT.primary };
  // Act
  const out = expandRoleColors(declared);
  // Assert
  assert.equal(out.secondary, '#8342f5', 'rotateHue(primary, 55)');
  assert.equal(maxChannelDelta(out.secondary, CURRENT.secondary), 51, '現行 #7e57c2 との最大チャネル差');
});

test('TC-CD10 range ← primary: 色相 -20 度（現行 #42a5f5→#26c6da の色相差の逆算）', () => {
  // Arrange: 逆算の実測 — primary の色相 206.82 度・range の色相 186.67 度 → 差 -20.15 度。
  //   当初表の「primary と alert の中間 0.5」は逆算不能だった（現行 range は 2 色を結ぶ線分上に
  //   無く、最小二乗 t = 0.0046 は range ≒ primary の退化解。0.5 を採ると #a1a68e で差 123）。
  //   現行プリセットは range を「中間」ではなく第 3 の寒色として置いており、色相で逆算し直すと
  //   差は 28 まで縮む。secondary（+55 度）と対称な同一の規則になる。
  const declared = { primary: CURRENT.primary };
  // Act
  const out = expandRoleColors(declared);
  // Assert
  assert.equal(out.range, '#42e1f5', 'rotateHue(primary, -20)');
  assert.equal(maxChannelDelta(out.range, CURRENT.range), 28, '現行 #26c6da との最大チャネル差');
});

test('TC-CD10b range ← primary: primary が無ければ生成しない（部分写像）', () => {
  // Arrange: alert を宣言しても range は導けない（導出元は primary ただ 1 つ）。
  const declared = { alert: CURRENT.alert };
  // Act
  const out = expandRoleColors(declared);
  // Assert
  assert.equal('range' in out, false);
  assert.equal('secondary' in out, false, 'primary 由来の他の語も生成されない');
});

test('TC-CD11 neutral ← primary: desaturate（相対輝度を保つ無彩色化・依頼者確定）', () => {
  // Arrange: 係数を持たない規則。相対輝度 primary=0.34661 を保つ灰を選ぶ（8bit 階調で到達可能な
  //   最良点）。現行 #90a4ae との最大チャネル差は 15 だが、コントラスト比は 1.022 ＝ 明度は
  //   ほぼ同じで、違いは現行が残している僅かな青みだけ（実測）。
  const declared = { primary: CURRENT.primary };
  // Act
  const out = expandRoleColors(declared);
  // Assert
  assert.equal(out.neutral, '#9f9f9f', 'desaturate(primary)');
  assert.equal(maxChannelDelta(out.neutral, CURRENT.neutral), 15, '現行 #90a4ae との最大チャネル差');
});

// =========================================================================
// 台帳（通過条件 6）: 導出表が語彙 14 語の全数を覆う
// =========================================================================

test('TC-CD12 台帳: 基点 5 語 ＋ 導出 9 語 = 語彙 14 語の全数を過不足なく覆う', () => {
  // Arrange / Act
  const covered = [...BASE_ROLE_TOKENS, ...DERIVED_ROLE_TOKENS];
  // Assert
  assert.equal(BASE_ROLE_TOKENS.length, 5, '基点は 5 語');
  assert.equal(DERIVED_ROLE_TOKENS.length, 9, '導出は 9 語');
  assert.deepEqual([...covered].sort(), [...COLOR_ROLES].sort(), '語彙 14 語と全数一致');
  assert.equal(new Set(covered).size, covered.length, '基点と導出が重複しない');
});

test('TC-CD13 通過条件 2: 基点 5 語の宣言だけで 14 キーが生成される', () => {
  // Arrange: 基点 5 語には現行値（PRESET「基本」）を入れる。
  const declared = Object.fromEntries(BASE_ROLE_TOKENS.map((t) => [t, CURRENT[t]]));
  // Act
  const out = expandRoleColors(declared);
  // Assert
  assert.deepEqual(Object.keys(out).sort(), [...COLOR_ROLES].sort(), '14 キーが揃う');
  assert.deepEqual(out, {
    // 基点（宣言値そのまま）
    surface: '#131722',
    bullish: '#00bfa5',
    bearish: '#ff5252',
    alert: '#ffa726',
    primary: '#42a5f5',
    // 導出（導出表どおり）
    grid: '#21242f',
    border: '#2b2e38',
    text: '#d5d5d7',
    level: '#898b90',
    muted: '#66686f',
    highlight: '#f5f5f5',
    secondary: '#8342f5',
    range: '#42e1f5',
    neutral: '#9f9f9f',
  });
});

test('TC-CD14 台帳: 導出 9 語はいずれも基点 5 語の宣言のみから到達できる（連鎖の閉性）', () => {
  // Arrange
  const declared = Object.fromEntries(BASE_ROLE_TOKENS.map((t) => [t, CURRENT[t]]));
  // Act
  const out = expandRoleColors(declared);
  // Assert
  for (const token of DERIVED_ROLE_TOKENS) {
    assert.ok(/^#[0-9a-f]{6}$/.test(out[token]), `${token}: 到達不能または hex6 でない`);
  }
});

// =========================================================================
// 全域性（LSP）と出口の型規律
// =========================================================================

test('TC-CD15 expandRoleColors: 非オブジェクト入力でも例外を投げず {} を返す（全域関数）', () => {
  // Arrange / Act / Assert
  assert.deepEqual(expandRoleColors(null), {});
  assert.deepEqual(expandRoleColors(undefined), {});
  assert.deepEqual(expandRoleColors('#131722'), {});
  assert.deepEqual(expandRoleColors(3), {});
  assert.deepEqual(expandRoleColors(['#131722']), {});
});

test('TC-CD16 expandRoleColors: 導出元が保存形 hex6 でなければ導出先を生成しない', () => {
  // Arrange: 保存形（小文字 6 桁）でない値は導出元として使わない。値の書き方の吸収は
  //   normalizeHexColor の責務であり、本関数は正規化済みの入力を受ける（変換点を増やさない）。
  const cases = ['#ABC', '#131722 ', 'rgb(19,23,34)', '', null, 42, {}];
  for (const bad of cases) {
    // Act
    const out = expandRoleColors({ surface: bad });
    // Assert
    assert.equal('grid' in out, false, `surface=${JSON.stringify(bad)} から grid を作った`);
    assert.equal('text' in out, false, `surface=${JSON.stringify(bad)} から text を作った`);
  }
});

test('TC-CD17 expandRoleColors: 語彙外キーは導出に使わず、そのまま持ち越す（射影側の責務を侵さない）', () => {
  // Arrange: 語彙の検査は normalizeRoleColors（F-C3）が担う。本関数は色の**演算**だけを担い、
  //   語彙の判定を二重に持たない（判定源を 2 箇所に置かない）。
  const declared = { surface: CURRENT.surface, __unknown__: '#123456' };
  // Act
  const out = expandRoleColors(declared);
  // Assert
  assert.equal(out.__unknown__, '#123456', '入力に在るキーは落とさない');
  assert.equal(out.grid, '#21242f', '導出は語彙内のトークンについてのみ起きる');
});

test('TC-CD19 expandRoleColors: 「宣言済み」はキーの在席ではなく読める色の在席で判定する', () => {
  // Arrange: キーの在席（`in`）で判定すると、値が色でないキーが導出を**黙って抑止**する。
  //   結果として出口の型（生成キーは必ず hex6）が入力由来の非 hex6 値で破れる。判定は
  //   「保存形 hex6 が入っているか」で行い、読めない値は未宣言として扱う（F-C9 と同じ規律）。
  for (const junk of [null, undefined, '', 'red', '#ABC', 42, {}]) {
    // Act
    const out = expandRoleColors({ surface: CURRENT.surface, grid: junk });
    // Assert
    assert.equal(out.grid, '#21242f', `grid=${JSON.stringify(junk)} が導出を抑止した`);
  }
});

test('TC-CD20 expandRoleColors: 生成したキーは必ず保存形 hex6（出口の型・全数）', () => {
  // Arrange: 読めない値を混ぜても、返り値の**語彙内キー**はすべて hex6 になる。
  const declared = {
    surface: CURRENT.surface, primary: CURRENT.primary, alert: CURRENT.alert,
    grid: null, text: 'red', neutral: '',
  };
  // Act
  const out = expandRoleColors(declared);
  // Assert
  for (const token of COLOR_ROLES) {
    if (token in out) {
      assert.ok(/^#[0-9a-f]{6}$/.test(out[token]), `${token}: ${JSON.stringify(out[token])}`);
    }
  }
});

test('TC-CD18 expandRoleColors: 入力を破壊しない（純関数）', () => {
  // Arrange
  const declared = { surface: CURRENT.surface };
  const before = JSON.stringify(declared);
  // Act
  expandRoleColors(declared);
  // Assert
  assert.equal(JSON.stringify(declared), before);
});
