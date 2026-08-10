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
import { contrastRatio } from '../js/domain/color_value.js';
import {
  DIAGNOSTIC, diagnoseTheme, SURFACE_CONTRAST_MIN,
} from '../js/usecase/color_diagnostics.js';

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
  // 段階 5-D。app.css の現行リテラル（PRESET「基本」も同値）。
  accent: '#2962ff', danger: '#ef5350',
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

test('TC-CD06 level ← text と surface: 対地 CR が目標 5.249 になる点（混合比ではない・ISSUE-346）', () => {
  // Arrange: 目標は**混合比ではなく対地コントラスト比**で持つ（ISSUE-346 の抜本是正）。混合比は
  //   地が変わっても一定だが CR は一定にならないため、1 標本から逆算した比を他の地へ持ち込むと
  //   容易に W-C2（閾値 3.0）を割る。目標値 5.249 は現行の暗い地 #131722 での level の実測 CR。
  //   目標には上限があり（TC-CD26 参照）、伸びしろの狭い地では geomean で抑えられる。
  //   導出元 text 自体が surface からの導出（連鎖）である点も同時に固定する。
  const declared = { surface: CURRENT.surface };
  // Act
  const out = expandRoleColors(declared);
  // Assert
  assert.equal(out.level, '#8a8b91', 'surface→text 上で対地 CR が 5.249 に最も近い点');
  assert.equal(contrastRatio(out.level, CURRENT.surface).toFixed(4), '5.2692', '対地 CR の実測');
  assert.equal(maxChannelDelta(out.level, CURRENT.level), 18, '現行 #78909c との最大チャネル差');
});

test('TC-CD06b level ← text と surface: 宣言済み text があればそれを導出元に使う（明示 > 導出）', () => {
  // Arrange: text を宣言すると、導出された text ではなく宣言値が導出元になる（ランプの終点が
  //   替わるので、同じ目標 CR でも選ばれる点が替わる）。
  const declared = { surface: CURRENT.surface, text: CURRENT.text };
  // Act
  const out = expandRoleColors(declared);
  // Assert
  assert.equal(out.text, CURRENT.text, '宣言値は不変');
  assert.equal(out.level, '#878b94', '導出元が宣言値へ替わるので level も替わる');
});

test('TC-CD07 muted ← level と surface: 対地 CR が目標 3.3 になる点（W-C2 に余裕を持たせる）', () => {
  // Arrange: 目標は現行の暗い地での実測 3.217 ではなく **max(3.217, 3.0 × 1.1) = 3.3** を採る。
  //   3.217 は自分の診断の閾値 3.0 に対して余裕が 7% しかなく、8bit の量子化だけで割り得る
  //   （ISSUE-346 が muted について指摘した「余裕の薄さ」そのもの）。10% の余裕を明示的に置く。
  const declared = { surface: CURRENT.surface };
  // Act
  const out = expandRoleColors(declared);
  // Assert
  assert.equal(out.muted, '#686a71', 'level→surface 上で対地 CR が 3.3 に最も近い点');
  assert.equal(contrastRatio(out.muted, CURRENT.surface).toFixed(4), '3.3142', '対地 CR の実測');
  assert.equal(maxChannelDelta(out.muted, CURRENT.muted), 20, '現行 #546e7a との最大チャネル差');
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

test('TC-CD12 台帳: 基点 7 語 ＋ 導出 9 語 = 語彙 16 語の全数を過不足なく覆う', () => {
  // Arrange / Act
  const covered = [...BASE_ROLE_TOKENS, ...DERIVED_ROLE_TOKENS];
  // Assert
  // 段階 5-D で足した accent / danger は**導出しない**（導出規則を置くとプリセット「基本」の
  //   見た目が現行からずれる。ずらす理由がない）。BASE_ROLE_TOKENS は「導出先でないもの」の
  //   計算結果にすぎず、ユーザーへ宣言を強制しない（未宣言なら slot.current ＝現行色に落ちる）。
  assert.equal(BASE_ROLE_TOKENS.length, 7, '基点は 7 語');
  assert.equal(DERIVED_ROLE_TOKENS.length, 9, '導出は 9 語');
  assert.ok(BASE_ROLE_TOKENS.includes('accent'), 'accent は導出しない');
  assert.ok(BASE_ROLE_TOKENS.includes('danger'), 'danger は導出しない');
  assert.deepEqual([...covered].sort(), [...COLOR_ROLES].sort(), '語彙 16 語と全数一致');
  assert.equal(new Set(covered).size, covered.length, '基点と導出が重複しない');
});

test('TC-CD13 通過条件 2: 基点 7 語の宣言だけで 16 キーが生成される', () => {
  // Arrange: 基点 7 語には現行値（PRESET「基本」）を入れる。
  const declared = Object.fromEntries(BASE_ROLE_TOKENS.map((t) => [t, CURRENT[t]]));
  // Act
  const out = expandRoleColors(declared);
  // Assert
  assert.deepEqual(Object.keys(out).sort(), [...COLOR_ROLES].sort(), '16 キーが揃う');
  assert.deepEqual(out, {
    // 基点（宣言値そのまま）
    surface: '#131722',
    bullish: '#00bfa5',
    bearish: '#ff5252',
    alert: '#ffa726',
    primary: '#42a5f5',
    accent: '#2962ff',
    danger: '#ef5350',
    // 導出（導出表どおり）
    grid: '#21242f',
    border: '#2b2e38',
    text: '#d5d5d7',
    level: '#8a8b91',
    muted: '#686a71',
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
// ISSUE-346 の是正: 地に従属する導出（level / muted）は混合比ではなく対地 CR の目標で持つ
//
// 病因（実測 2026-08-09）: 混合比は地が変わっても一定だが、コントラスト比は一定にならない。
//   現行の暗い地 #131722 の 1 標本から逆算した比を他の地へ持ち込むと、導出された muted が
//   自分の診断 W-C2（閾値 3.0）を割った（純黒 2.998 / 純白 2.434 / 明るい紙 2.399 / 中間灰 1.948）。
//   CR は地に対する相対量なので、目標として持てば地を変えても保たれる。
//
// 目標:  level = min(5.249, geomean(3.3, CR(text, surface)))  /  muted = 3.3
//   level の目標を**絶対値だけ**にできない理由（実測）: 伸びしろは地に依存し、中間灰 #808080 では
//   surface→text の軸で到達できる CR の上限が 4.5393 しかない。絶対目標 5.249 を押し通すと端点
//   （＝text）へ丸まり、W-C2 を消す代わりに W-C1（level == text）を作る＝症状の移動になる。
//   伸びしろに依存する量は伸びしろで抑える。CR は比のスケールなので中点は幾何平均を採る。
// =========================================================================

// 検定に使う代表的な地。表（ISSUE-346）の 6 種に、伸びしろの狭い高彩度色を足す。
const SURFACE_SAMPLES = Object.freeze([
  '#131722', // 現行（暗い地）
  '#0d1b3e', // 濃紺
  '#000000', // 純黒
  '#ffffff', // 純白
  '#f5f5f5', // 明るい紙
  '#808080', // 中間灰（伸びしろが最小に近い）
  '#5d60ff', // 到達可能な最大 CR が全域最小（4.5826・実測）
]);

// 基点 5 語のうち surface 以外（表と同じ標本）。
const BASE_NON_SURFACE = Object.freeze({
  bullish: '#00bfa5', bearish: '#ff5252', alert: '#ffa726', primary: '#42a5f5',
});

const expandFor = (surface) => expandRoleColors({ ...BASE_NON_SURFACE, surface });

test('TC-CD26 通過条件: 導出された level / muted はどの地でも W-C2 を発火させない（全数）', () => {
  // Arrange: 地を替えて全数で回す。基点色（bullish / alert / primary / range）が明るい地で W-C2 を
  //   発火させるのは「白地に teal・orange は実際に読みにくい」という**正しい指摘**であって欠陥では
  //   ないため、ここで固定するのは導出 2 語に限る。
  for (const surface of SURFACE_SAMPLES) {
    const roleColors = expandFor(surface);
    // Act
    const fired = diagnoseTheme({ roleColors })
      .filter((d) => d.code === DIAGNOSTIC.surfaceContrast)
      .filter((d) => d.tokens.includes('level') || d.tokens.includes('muted'));
    // Assert
    assert.deepEqual(fired, [], `${surface}: 導出 2 語が W-C2 を発火させた`);
    for (const token of ['level', 'muted']) {
      assert.ok(contrastRatio(roleColors[token], surface) >= SURFACE_CONTRAST_MIN,
        `${surface} / ${token}: CR ${contrastRatio(roleColors[token], surface)} < ${SURFACE_CONTRAST_MIN}`);
    }
  }
});

test('TC-CD27 通過条件: 目標 CR は地に依らず保たれる（混合比では成立しない性質）', () => {
  // Arrange: 混合比で持っていたときの実測は muted CR が 3.217 → 1.948 まで動いた（地に追随しない）。
  //   目標 CR 方式なら、伸びしろが足りる限り地を替えても目標付近に留まる。
  const measured = [];
  for (const surface of SURFACE_SAMPLES) {
    const roleColors = expandFor(surface);
    measured.push([surface, contrastRatio(roleColors.muted, surface)]);
  }
  // Act / Assert: muted の目標 3.3 は全標本で到達可能（level の目標下限が 3.3 を上回るため）。
  for (const [surface, cr] of measured) {
    assert.ok(Math.abs(cr - 3.3) < 0.05, `${surface}: muted CR ${cr} が目標 3.3 から離れた`);
  }
  // level は伸びしろで抑えられるため、上限 5.249 か geomean のどちらかに乗る。
  for (const surface of SURFACE_SAMPLES) {
    const roleColors = expandFor(surface);
    const crText = contrastRatio(roleColors.text, surface);
    const target = Math.min(5.249, Math.sqrt(3.3 * crText));
    assert.ok(Math.abs(contrastRatio(roleColors.level, surface) - target) < 0.05,
      `${surface}: level CR が目標 ${target} から離れた`);
  }
});

test('TC-CD28 射程: CR(text, surface) > 3.3355 の地では text / level / muted が 3 段に分離する', () => {
  // Arrange: 境界 3.3355 は 2^24 = 16,777,216 の地を全数走査して特定した実測値（走査は
  //   「地ごとに text を mix 0.820 で作り、その対地 CR を測る」という閉じた式なので全数が可能）。
  //   境界より上では 3 語が互いに異なり、対地 CR が muted < level < text の順序を保つ。順序が
  //   崩れると「非強調」が「参照水準」より目立つ＝語の意味が壊れる。
  for (const surface of SURFACE_SAMPLES) {
    const roleColors = expandFor(surface);
    const { text, level, muted } = roleColors;
    const crText = contrastRatio(text, surface);
    assert.ok(crText > 3.3355, `${surface}: 前提（射程内）を満たさない CR(text)=${crText}`);
    // Act / Assert
    assert.equal(new Set([text, level, muted]).size, 3, `${surface}: 3 語が分離しない`);
    assert.ok(contrastRatio(muted, surface) < contrastRatio(level, surface),
      `${surface}: muted が level より目立つ`);
    assert.ok(contrastRatio(level, surface) < crText, `${surface}: level が text より目立つ`);
  }
});

test('TC-CD29 保証: 射程外の地では梯子の潰れを W-C1 が知らせる（黙って嘘の色を作らない）', () => {
  // Arrange: CR(text, surface) の全域最小は 3.3172（@ #ec0202・2^24 全数走査の実測）で、muted の
  //   目標 3.3 との差はわずか 0.52%。3 段の梯子を載せる余地が物理的に無い地が存在する
  //   （実測 147 地 / 16,777,216＝0.00088%・いずれも高彩度の深紅〜赤紫）。
  //   件数は検定に書かない（text の規則を是正すれば変わる数であり、固定すると是正のたびに落ちる）。
  //   固定するのは「そこで導出が黙らず、診断が知らせる」という**保証**である。
  //   根因は text が混合比のままであること（別 ISSUE。本段階の範囲外）。
  const cases = [
    ['#ec0202', ['level', 'text']], // 上段が潰れる（level が text へ届いてしまう）
    ['#ea0042', ['level', 'muted']], // 下段が潰れる
  ];
  for (const [surface, expectedPair] of cases) {
    const roleColors = expandFor(surface);
    // Act
    const collisions = diagnoseTheme({ roleColors }).filter((d) => d.code === DIAGNOSTIC.collision);
    // Assert: 潰れた組が逐語で報告される。
    assert.deepEqual(
      collisions.map((d) => [...d.tokens].sort()),
      [[...expectedPair].sort()],
      `${surface}: 潰れた組が W-C1 として報告されない`,
    );
    // 梯子の語が必ず 1 つは含まれる＝「参照水準が隣の段と見分けられない」と伝わる。
    assert.ok(collisions.some((d) => d.tokens.includes('level')),
      `${surface}: level の潰れが知らされない`);
    // 潰れても W-C2 は割らない（色は読める。読めないのではなく**意味が分かれない**）。
    for (const token of ['text', 'level', 'muted']) {
      assert.ok(contrastRatio(roleColors[token], surface) >= SURFACE_CONTRAST_MIN,
        `${surface} / ${token}: 射程外でも W-C2 は割らないはず`);
    }
  }
});

test('TC-CD30 恒等: 現行の暗い地と濃紺では診断が 0 件のまま（見た目の回帰が無い）', () => {
  // Arrange: ISSUE-346 の是正が、現行値と実際に使われる暗いテーマの領域を動かしていないこと。
  //   2^24 全数走査では W-C2 の発火は 0 件だった（走査方法: 地ごとに text → level → muted を
  //   導出し、対地 CR が 3.0 を下回るかを数える）。全数走査は実行時間が見合わないため検定には
  //   入れず、代表 2 地で固定する。
  for (const surface of ['#131722', '#0d1b3e']) {
    // Act
    const diags = diagnoseTheme({ roleColors: expandFor(surface) });
    // Assert
    assert.deepEqual(diags, [], `${surface}: 診断 0 件のはず`);
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
