// chrome_ramp_polarity.test.js — 配線点の派生を「地の極性に対して相対」にする（段階 5-D 追補）。
//
// 病因（実測 2026-08-09）: 派生を**絶対チャネル空間の加法**（delta）で持つと、地の極性に対して
//   相対でないため、明るい地で飽和・反転する。実測: surface=#ffffff / text=#111111 で 52 slot 中
//   14 が飽和し、面の系（uiPanel / uiMenuSurface / uiDivider / uiFieldDisabled / uiReplayPanel）が
//   すべて #ffffff に潰れて構造が消え、文字ランプは uiTextStrong(#3f3c34) が uiText(#111111) より
//   薄いという**反転**を起こした。
//
// 是正: 各 slot を「その slot に意味を与える軸」上の**相対位置**（チャネル別係数 k）で持つ。軸の
//   終点は地の極性から決まる（contrastAnchor）。ISSUE-346 が語彙側で確立した「方向は明度ではなく
//   **地に対して**定義する」という規律を、配線点の派生へ広げたものである。
//
// なぜ加法 delta では直らないかの実測（本検定の設計根拠）:
//   uiReplayWell（地より暗い唯一の面）で 2 案を全数試した結果、
//     案 A（加法 delta のまま）      … 地 #000000 で地と同一に潰れる
//     案 B（暗い側へ比率で寄せる）    … 地 #ffffff で地と同一に潰れる
//   潰れない方向は **contrastAnchor 側だけ**である（anchor は定義上つねに余地を持つ）。
//
// 係数 k は現行値からの逆算（k = delta / 余地）であり、基準の地（surface #131722 / text #d1d4dc）で
//   現行リテラルを構成上**厳密に**再現する。推測値は 1 つも無い。

import test from 'node:test';
import assert from 'node:assert/strict';

import { resolveAllChrome, resolveChromeSlotColor } from '../js/usecase/color_resolver.js';
import { CHROME_SLOTS, CHROME_CURRENT } from '../js/usecase/chrome_tokens.js';
import { expandRoleColors } from '../js/usecase/color_derivation.js';
import { PRESET_THEMES } from '../js/usecase/color_themes.js';
import { contrastRatio } from '../js/domain/color_value.js';

// 通過条件 2 が要求する代表的な地。両端（純白・純黒）と中間灰を含む＝伸びしろが最も狭い地。
const GROUNDS = ['#131722', '#0d1b3e', '#ffffff', '#f5f5f5', '#808080', '#000000'];

// 地から現実的なテーマを組む。text 等は導出表が surface から埋める（実際の読み出し経路と同じ）。
function themeFor(surface) {
  return { roleColors: expandRoleColors({ surface, primary: '#42a5f5' }) };
}

const rampSlots = () => CHROME_SLOTS.filter((s) => s.ramp != null);
const idsOf = (list) => list.map((s) => s.id);

// 面の系・文字の系（＝地の極性に相対化する対象）。
const SURFACE_RAMPS = [
  'uiPanel', 'uiMenuSurface', 'uiFieldDisabled', 'uiDivider',
  'uiReplayPanel', 'uiReplayTrack', 'uiReplaySurface', 'uiReplayWell',
];
const TEXT_RAMPS = [
  'uiTextStrong', 'uiTextHeading', 'uiTextChip', 'uiTextLabel',
  'uiTextAux', 'uiTextWeak', 'uiTextDisabled', 'uiTextOnDisabled',
];
// 構造線の系。uiReplayThumb は加法 delta のままだと白地で #ffffff へ飽和する（実測）。
const BORDER_RAMPS = [
  'uiBorderStrong', 'uiRowHover', 'uiChipBorderHover',
  'uiToggleOff', 'uiToggleOffHover', 'uiReplayThumb',
];

// uiTextStrong は「地に対して最も強い文字」＝ anchor そのものなので、暗い地で #ffffff・明るい地で
//   #000000 になるのが**意図どおり**である（飽和ではなく到達点）。通過条件 2 の明示された例外。
const SATURATION_EXEMPT = new Set(['uiTextStrong']);

function channels(value) {
  const hex = value.match(/#[0-9a-f]{6}/i);
  if (hex) return [1, 3, 5].map((i) => parseInt(hex[0].slice(i, i + 2), 16));
  const m = value.match(/rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)/);
  return m ? m.slice(1, 4).map(Number) : null;
}

// --- 通過条件 1: 恒等は不変 ----------------------------------------------

test('TC-RP01 恒等は不変: テーマ未設定なら ramp を持つ slot も現行リテラルと文字列一致する', () => {
  // Arrange / Act
  const { slots } = resolveAllChrome(null);
  // Assert
  for (const s of rampSlots()) {
    assert.equal(slots[s.id], CHROME_CURRENT[s.id], `${s.id}: 現行リテラルから動いた`);
  }
});

test('TC-RP02 恒等: PRESET「基本」を選んでも ramp を持つ slot は現行リテラルのまま', () => {
  // Arrange: PRESET「基本」は語彙 16 語を**明示宣言**し、その surface / text / border は
  //   CHROME_DEFAULT と同値である。ここがずれるとプリセットを選んだ瞬間に UI クロムが動く。
  //   （導出だけで組んだテーマでは border が導出値 #2b2e38 になり 1 チャネルずれる。それは
  //   「宣言した border に従っている」正しい挙動なので、恒等の検定には明示宣言を使う。）
  const theme = { roleColors: PRESET_THEMES[0].roleColors };
  // Act / Assert
  for (const s of rampSlots()) {
    // uiReplayWell だけは方向を反転させた（下の TC-RP07 が理由と実測を持つ）。
    if (s.id === 'uiReplayWell') continue;
    assert.equal(resolveChromeSlotColor({ slotId: s.id, theme }), CHROME_CURRENT[s.id], s.id);
  }
});

// --- 通過条件 2: 飽和しない ----------------------------------------------

test('TC-RP03 面の系・文字の系・構造線の系は、どの地でも #ffffff / #000000 へ潰れない', () => {
  // Arrange
  const targets = [...SURFACE_RAMPS, ...TEXT_RAMPS, ...BORDER_RAMPS]
    .filter((id) => !SATURATION_EXEMPT.has(id));
  const failures = [];
  // Act
  for (const ground of GROUNDS) {
    const { slots } = resolveAllChrome(themeFor(ground));
    for (const id of targets) {
      const v = slots[id];
      if (v === '#ffffff' || v === '#000000') failures.push(`${ground} → ${id}=${v}`);
    }
  }
  // Assert
  assert.deepEqual(failures, [], `飽和した配線点:\n${failures.join('\n')}`);
});

test('TC-RP04 例外は uiTextStrong のみで、それは anchor への到達点である（飽和ではない）', () => {
  // Arrange / Act / Assert
  assert.deepEqual([...SATURATION_EXEMPT], ['uiTextStrong']);
  assert.equal(resolveChromeSlotColor({ slotId: 'uiTextStrong', theme: themeFor('#131722') }), '#ffffff');
  assert.equal(resolveChromeSlotColor({ slotId: 'uiTextStrong', theme: themeFor('#ffffff') }), '#000000');
});

// --- 通過条件 3: 文字ランプの順序が保たれる ------------------------------

test('TC-RP05 文字ランプの対地コントラスト比は、どの地でも強い順を保つ（反転しない）', () => {
  // Arrange: 通過条件 3 が指定する順序。
  const ORDER = ['uiTextStrong', 'uiText', 'uiTextLabel', 'uiTextAux', 'uiTextWeak', 'uiTextDisabled'];
  const failures = [];
  // Act
  for (const ground of GROUNDS) {
    const { slots } = resolveAllChrome(themeFor(ground));
    const crs = ORDER.map((id) => ({ id, cr: contrastRatio(slots[id], ground) }));
    for (let i = 1; i < crs.length; i += 1) {
      if (!(crs[i - 1].cr > crs[i].cr)) {
        failures.push(`${ground}: ${crs[i - 1].id}(${crs[i - 1].cr.toFixed(3)}) <= ${crs[i].id}(${crs[i].cr.toFixed(3)})`);
      }
    }
  }
  // Assert
  assert.deepEqual(failures, [], `文字ランプが反転した:\n${failures.join('\n')}`);
});

// --- 通過条件 4: 面が地から分離する --------------------------------------

test('TC-RP06 面の系は、どの地でも surface と異なる色になる（構造が消えない）', () => {
  // Arrange
  const failures = [];
  // Act
  for (const ground of GROUNDS) {
    const { slots } = resolveAllChrome(themeFor(ground));
    for (const id of ['uiPanel', 'uiMenuSurface', 'uiDivider']) {
      const a = channels(slots[id]);
      const b = channels(ground);
      if (a && b && a.every((v, i) => v === b[i])) failures.push(`${ground} → ${id}=${slots[id]}`);
    }
  }
  // Assert
  assert.deepEqual(failures, [], `面が地に溶けた:\n${failures.join('\n')}`);
});

test('TC-RP07 uiReplayWell は方向を anchor 側へ反転させた（潰れない方向が他に無いため）', () => {
  // 現行の暗い地では地より**暗い**（delta 全チャネル負）。その方向を保つ表現は、
  //   加法 delta なら地 #000000 で、暗い側への比率なら地 #ffffff で、それぞれ地と同一に潰れる
  //   （いずれも実測済み）。anchor 側は定義上つねに余地があるため、方向を反転させた。
  //   帰結として、地を明示宣言したテーマでは replay バーの窪みが地より明るくなる。
  const dark = resolveChromeSlotColor({ slotId: 'uiReplayWell', theme: themeFor('#131722') });
  assert.notEqual(dark, CHROME_CURRENT.uiReplayWell, '反転させた以上、基準の地でも現行値とは異なる');
  // ただし「地から分離している」ことは保つ（窪みが見えなくなってはいけない）。
  for (const ground of GROUNDS) {
    const v = resolveChromeSlotColor({ slotId: 'uiReplayWell', theme: themeFor(ground) });
    assert.notEqual(channels(v).join(), channels(ground).join(), `${ground}: 地に溶けた`);
  }
});

// --- 台帳の規律 -----------------------------------------------------------

test('TC-RP08 ramp と加法 delta は排他（同じ slot が 2 つの派生規則を持たない）', () => {
  for (const s of CHROME_SLOTS) {
    assert.equal(s.ramp != null && s.delta != null, false, `${s.id}: ramp と delta を同時に持つ`);
  }
});

test('TC-RP09 ramp を持つ slot は面・文字・構造線の系に限る（有彩色は地に依存させない）', () => {
  // accent / danger / alert の濃淡は「そのトークン自身の暗い版・明るい版」であって地の関数では
  //   ない。ここまで地に相対化すると意味が壊れるため、加法 delta のまま据え置く。
  assert.deepEqual(
    idsOf(rampSlots()).sort(),
    [...SURFACE_RAMPS, ...TEXT_RAMPS, ...BORDER_RAMPS].sort(),
  );
  for (const s of CHROME_SLOTS.filter((x) => x.delta != null && x.id.startsWith('ui'))) {
    assert.ok(['accent', 'danger', 'alert', 'grid', 'text'].includes(s.token),
      `${s.id}: 加法 delta を残してよいのは有彩色系と微小オフセットのみ（token=${s.token}）`);
  }
});

test('TC-RP10 ramp の係数はチャネル別 3 要素で、いずれも有限（推測値・欠損が無い）', () => {
  for (const s of rampSlots()) {
    assert.equal(s.ramp.k.length, 3, s.id);
    assert.ok(s.ramp.k.every(Number.isFinite), s.id);
    assert.ok(['anchor', 'surface'].includes(s.ramp.toward), `${s.id}: toward=${s.ramp.toward}`);
  }
});
