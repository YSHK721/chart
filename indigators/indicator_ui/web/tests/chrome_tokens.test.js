// chrome_tokens.test.js — チャートクロム配線点の台帳テスト
//   （基本設計_指標カラーテーマ.md §4.2 配線点 20 点・§4.6 CHROME_DEFAULT・§7.4 段階 1 通過条件 6）。
//
// 本表は「クロムの現行リテラルの単一情報源」である。これが 3 ファイル（chart_bootstrap /
//   chart_renderer / replay_boundary_dim）と CSS 3 宣言に散在したままだと、「テーマなし」へ
//   戻すときの復元値が二重定義になる（§7.2 S1 が棄却された理由そのもの）。
//   よって件数（20）・トークン束ね・現行値の逐語一致を全数で固定する。

import test from 'node:test';
import assert from 'node:assert/strict';

import {
  CHROME_SLOTS, CHROME_CURRENT, CHROME_DEFAULT, CHROME_TOKENS, chromeSlot,
} from '../js/usecase/chrome_tokens.js';
import { isColorRole } from '../js/domain/color_roles.js';

// --- §4.2 の表（逐語）----------------------------------------------------
// [# , slot id, 現行値, 機構, トークン]
const LEDGER = [
  [1, 'layoutBackground', '#131722', 'js', 'surface'],
  [2, 'backgroundFallback', '#131722', 'js', 'surface'],
  [3, 'gridVertLines', '#1f2530', 'js', 'grid'],
  [4, 'gridHorzLines', '#1f2530', 'js', 'grid'],
  [5, 'layoutTextColor', '#d1d4dc', 'js', 'text'],
  [6, 'paneSeparator', '#2a2e39', 'js', 'border'],
  [7, 'paneSeparatorHover', 'rgba(178,181,189,0.2)', 'js', 'border'],
  [8, 'rightPriceScaleBorder', '#2a2e39', 'js', 'border'],
  [9, 'timeScaleBorder', '#2a2e39', 'js', 'border'],
  [10, 'candleUp', '#26a69a', 'js', 'bullish'],
  [11, 'candleDown', '#ef5350', 'js', 'bearish'],
  [12, 'candleUpRestore', '#26a69a', 'js', 'bullish'],
  [13, 'candleDownRestore', '#ef5350', 'js', 'bearish'],
  [14, 'priceLine', '#ff9800', 'js', 'highlight'],
  [15, 'currentPriceUp', '#26a69a', 'css', 'bullish'],
  [16, 'currentPriceDown', '#ef5350', 'css', 'bearish'],
  [17, 'currentPriceNeutral', '#d1d4dc', 'css', 'text'],
  [18, 'dimCandle', '#16191f', 'js', 'surface'],
  [19, 'analysisTint', '#1b1a24', 'js', 'surface'],
  [20, 'replayBoundaryDim', '#090d18', 'js', 'surface'],
];

// --- アプリ UI クロムの配線点（段階 5-D で追加・逐語）---------------------
// チャート本体の 20 点（上表）とは別の器に置く。上表は「lightweight-charts と replay 減光の
//   配線点」で件数が固定されているのに対し、本表は app.css の接続に伴って増える。器を分けると
//   上表の 20 点が増減していないことを、本表の増加と独立に固定できる。
const UI_LEDGER = [
  ['uiSurface', '#131722', 'css', 'surface'],
  ['uiPanel', '#1e222d', 'css', 'surface'],
  ['uiMenuSurface', '#1c2030', 'css', 'surface'],
  ['uiFieldDisabled', '#181b24', 'css', 'surface'],
  ['uiDivider', '#23272f', 'css', 'surface'],
  ['uiChipSurface', 'rgba(19, 23, 34, 0.72)', 'css', 'surface'],
  ['uiOverlaySurface', 'rgba(19, 23, 34, 0.82)', 'css', 'surface'],
  ['uiReadoutSurface', 'rgba(30, 34, 45, .82)', 'css', 'grid'],
  ['uiToastSurface', 'rgba(28, 32, 48, .95)', 'css', 'grid'],
  ['uiBorder', '#2a2e39', 'css', 'border'],
  ['uiBorderStrong', '#363a45', 'css', 'border'],
  ['uiRowHover', '#363b49', 'css', 'border'],
  ['uiChipBorderHover', '#3a4050', 'css', 'border'],
  ['uiToggleOff', '#3a3d47', 'css', 'border'],
  ['uiToggleOffHover', '#44474f', 'css', 'border'],
  ['uiText', '#d1d4dc', 'css', 'text'],
  ['uiTextStrong', '#ffffff', 'css', 'text'],
  ['uiTextHeading', '#e6e9ef', 'css', 'text'],
  ['uiTextChip', '#b8bec9', 'css', 'text'],
  ['uiTextLabel', '#b2b5be', 'css', 'text'],
  ['uiTextAux', '#9aa0ad', 'css', 'text'],
  ['uiTextWeak', '#787b86', 'css', 'text'],
  ['uiTextDisabled', '#5d616b', 'css', 'text'],
  ['uiTextOnDisabled', '#6b7088', 'css', 'text'],
  ['uiTextOnAccent', '#cfd8ff', 'css', 'text'],
  ['uiAccent', '#2962ff', 'css', 'accent'],
  ['uiAccentHover', '#1e53e5', 'css', 'accent'],
  ['uiAccentSubtle', '#2962ff22', 'css', 'accent'],
  ['uiAccentGlow', 'rgba(41, 98, 255, 0.6)', 'css', 'accent'],
  ['uiAccentDisabled', '#2a3354', 'css', 'accent'],
  ['uiDanger', '#ef5350', 'css', 'danger'],
  ['uiDangerText', '#e0564a', 'css', 'danger'],
  ['uiDangerSolid', '#b03a30', 'css', 'danger'],
  ['uiPocMarker', '#ff6b6b', 'css', 'alert'],
  ['uiLiveOn', '#7b2233', 'css', 'danger'],
  ['uiLiveOnHover', '#93293e', 'css', 'danger'],
  ['uiAlert', '#e0a24a', 'css', 'alert'],
  ['uiAlertStrong', '#e0b84a', 'css', 'alert'],
  ['uiAlertStar', '#f0b400', 'css', 'alert'],
  ['uiAlertBorder', '#5a4a18', 'css', 'alert'],
  ['uiAlertSurface', '#2a2410', 'css', 'alert'],
  ['uiAlertTint', 'rgba(224, 162, 74, 0.08)', 'css', 'alert'],
  ['uiBullish', '#26a69a', 'css', 'bullish'],
  ['uiReplayPanel', '#222735', 'css', 'surface'],
  ['uiReplayWell', '#0c0e15', 'css', 'surface'],
  ['uiReplayTrack', '#1f2431', 'css', 'surface'],
  ['uiReplaySurface', '#161a25', 'css', 'surface'],
  ['uiReplayThumb', '#4a4e5a', 'css', 'border'],
  ['uiReplayText', '#e6e8ea', 'css', 'text'],
];

// §4.2 / §4.6 の派生差分（E-29 の実測値。設計で決めた値ではない）。
// 減光・tint の対地 CR 目標（現行の暗い地 #131722 での実測 CR。設計値ではない）。
const DIM_CR = {
  dimCandle: 1.0167,
  analysisTint: 1.0396,
  replayBoundaryDim: 1.0840,
};

// チャート本体の配線点（上表 20 点）は本段階で 1 点も増減していない。これが増えていないことは、
//   app.css の接続がチャート本体の配線へ波及していないことの実証である。
test('§4.2: チャート本体の配線点は 20 点のまま（id・現行値・機構・トークンが表と全数一致）', () => {
  const head = CHROME_SLOTS.slice(0, 20);
  assert.equal(head.length, 20);
  assert.deepEqual(
    head.map((s) => [s.id, s.current, s.mechanism, s.token]),
    LEDGER.map(([, id, current, mechanism, token]) => [id, current, mechanism, token]),
  );
});

test('段階 5-D: アプリ UI クロムの配線点が逐語で一致する（台帳が単一情報源）', () => {
  const tail = CHROME_SLOTS.slice(20);
  assert.deepEqual(
    tail.map((s) => [s.id, s.current, s.mechanism, s.token]),
    UI_LEDGER,
  );
  assert.equal(CHROME_SLOTS.length, LEDGER.length + UI_LEDGER.length);
});

test('§4.2: 束ねるトークンは語彙内で、チャート本体の 7 種を含む', () => {
  const used = [...new Set(CHROME_SLOTS.map((s) => s.token))];
  for (const t of ['bearish', 'border', 'bullish', 'grid', 'highlight', 'surface', 'text']) {
    assert.ok(used.includes(t), t);
  }
  assert.deepEqual([...CHROME_TOKENS].sort(), [...used].sort());
  // すべて語彙内であること（クロム専用の別語彙を作らない＝FR-C11）。
  for (const t of CHROME_TOKENS) {
    assert.ok(isColorRole(t), t);
  }
});

test('CHROME_CURRENT は slot id → 現行リテラルの逐語写像（単一情報源）', () => {
  assert.deepEqual(CHROME_CURRENT, Object.fromEntries([
    ...LEDGER.map(([, id, cur]) => [id, cur]),
    ...UI_LEDGER.map(([id, cur]) => [id, cur]),
  ]));
  assert.ok(Object.isFrozen(CHROME_CURRENT));
  assert.ok(Object.isFrozen(CHROME_SLOTS));
});

test('chromeSlot(id) は表の行を返し、未知 id は null（全域的）', () => {
  const ALL = [...LEDGER.map(([, ...r]) => r), ...UI_LEDGER];
  for (const [id, current, mechanism, token] of ALL) {
    const s = chromeSlot(id);
    assert.ok(s, id);
    assert.equal(s.current, current);
    assert.equal(s.mechanism, mechanism);
    assert.equal(s.token, token);
  }
  assert.equal(chromeSlot('nonexistent'), null);
  assert.equal(chromeSlot(null), null);
  assert.equal(chromeSlot(undefined), null);
});

// --- §4.6 CHROME_DEFAULT（トークン単位の現行既定）------------------------
test('§4.6: CHROME_DEFAULT は束ねる全トークンに定義を持つ（未定義返却が起きない＝LSP）', () => {
  assert.deepEqual(CHROME_DEFAULT, Object.freeze({
    surface: '#131722',
    grid: '#1f2530',
    border: '#2a2e39',
    text: '#d1d4dc',
    bullish: '#26a69a',
    bearish: '#ef5350',
    highlight: '#ff9800',
    accent: '#2962ff',
    danger: '#ef5350',
    alert: '#e0a24a',
  }));
  for (const t of CHROME_TOKENS) {
    assert.equal(typeof CHROME_DEFAULT[t], 'string', t);
    assert.match(CHROME_DEFAULT[t], /^#[0-9a-f]{6}$/, t);
  }
});

// 恒等性の要（§7.4 段階 1 通過条件 6・D-11）。
//   トークン単位の既定（CHROME_DEFAULT）と配線点単位の現行値（slot.current）が食い違う配線点は
//   #7 だけであり、それは「不透明度を持つ」ことに由来する（現行 rgba(178,181,189,0.2) は border
//   の #2a2e39 とは別の色）。よって既定値の単一情報源は**配線点単位**でなければならず、
//   トークン単位の既定で置き換えるとテーマ未設定時の見た目が変わる。
test('恒等性: 単色かつ非派生の配線点は CHROME_DEFAULT[token] と一致する', () => {
  // 派生の規則は 2 つある（加法 delta / 地に相対な ramp）。どちらも「トークン既定そのものではない」
  //   ため除外する。ramp を除外し忘れると、派生値をトークン既定と比べて誤って落ちる。
  for (const s of CHROME_SLOTS) {
    if (s.alpha != null || s.derivedFrom != null || s.ramp != null) continue;
    assert.equal(s.current, CHROME_DEFAULT[s.token], `${s.id}`);
  }
});

test('E-29 相当: ramp の係数は基準の地から現行値を厳密に再現する（設計値ではなく逆算）', () => {
  // k = delta / 余地 という逆算であることを、台帳の値だけから再計算して確かめる。
  //   軸の終点は基準の地（暗い）なので anchor = 白、surface 向きは CHROME_DEFAULT.surface。
  const rgb = (hex) => [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16));
  const WHITE = [255, 255, 255];
  const SURFACE = rgb(CHROME_DEFAULT.surface);
  for (const s of CHROME_SLOTS.filter((x) => x.ramp != null)) {
    // 反転させた 1 点（uiReplayWell）は再現しない。理由と実測は chrome_ramp_polarity.test.js。
    if (s.id === 'uiReplayWell') continue;
    const from = rgb(CHROME_DEFAULT[s.token]);
    const to = s.ramp.toward === 'surface' ? SURFACE : WHITE;
    const got = from.map((v, i) => Math.round(v + (to[i] - v) * s.ramp.k[i]));
    assert.deepEqual(got, rgb(s.current), `${s.id}`);
  }
});

test('恒等性: チャート本体で不透明度を持つ配線点は #7 のみで、トークン既定とは別の色である', () => {
  const withAlpha = CHROME_SLOTS.slice(0, 20).filter((s) => s.alpha != null);
  assert.equal(withAlpha.length, 1);
  assert.equal(withAlpha[0].id, 'paneSeparatorHover');
  assert.equal(withAlpha[0].alpha, 0.2);
  assert.notEqual(withAlpha[0].current, CHROME_DEFAULT[withAlpha[0].token]);
});

test('恒等性: 不透明度を持つ配線点の current は必ず rgba() 形（hex に潰れていない）', () => {
  for (const s of CHROME_SLOTS.filter((x) => x.alpha != null && x.id.startsWith('ui'))) {
    assert.ok(/^rgba\(|^#[0-9a-f]{8}$/.test(s.current), `${s.id}: ${s.current}`);
    assert.ok(s.alpha > 0 && s.alpha < 1, `${s.id}: alpha は開区間 (0,1)`);
  }
});

// --- §4.2 派生（E-29 の実測差分）----------------------------------------
test('§4.2: チャート本体の派生配線点は 3 点で、いずれも surface に従属し対地 CR 目標を持つ', () => {
  // 加法 delta から対地コントラスト比の目標へ改めた（段階 5-D 追補 2）。加法では地を変えたときに
  //   効果が消えた（実測: analysisTint は地 #ffffff で対地 CR 1.0000＝地と同一）。
  const derived = CHROME_SLOTS.slice(0, 20).filter((s) => s.derivedFrom != null);
  assert.equal(derived.length, 3);
  for (const s of derived) {
    assert.equal(s.derivedFrom, 'surface', s.id);
    assert.equal(s.token, 'surface', s.id);
    assert.equal(s.delta, undefined, `${s.id}: 加法 delta は残っていない`);
    assert.equal(s.crTarget, DIM_CR[s.id], s.id);
  }
});

test('§4.2: 派生配線点の従属先は必ず自分のトークン（別トークンへ横流ししない）', () => {
  // 従属先とトークンが食い違うと、「border を変えたのに surface 派生の面だけ動く」といった
  //   説明のつかない連動が生まれる。resolveChromeSlotColor は token の宣言値を基点に delta を
  //   当てるので、derivedFrom は token と一致していなければ意味が二重になる。
  for (const s of CHROME_SLOTS.filter((x) => x.derivedFrom != null)) {
    assert.equal(s.derivedFrom, s.token, s.id);
    // 派生の表し方は加法 delta か対地 CR 目標のいずれか 1 つ（排他）。
    if (s.crTarget != null) {
      assert.equal(s.delta, undefined, s.id);
      assert.ok(s.crTarget > 1, `${s.id}: CR は 1 より大きい（地と別の色である）`);
      continue;
    }
    assert.equal(s.delta.length, 3, s.id);
    assert.ok(s.delta.every(Number.isInteger), `${s.id}: delta は整数`);
  }
});

test('E-29: 派生差分はトークン既定から現行値を厳密に再現する（設計値ではなく実測差分）', () => {
  // delta は「決めた係数」ではなく「現行値どうしの差」である。ここが崩れると、テーマ未宣言時の
  //   恒等（通過条件 1）は slot.current が守るものの、テーマを宣言した瞬間に色が跳ぶ。
  const rgb = (v) => (v.startsWith('#')
    ? [1, 3, 5].map((i) => parseInt(v.slice(i, i + 2), 16))
    : v.match(/rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)/).slice(1, 4).map(Number));
  for (const s of CHROME_SLOTS.filter((x) => x.delta != null)) {
    const [br, bg, bb] = rgb(CHROME_DEFAULT[s.token]);
    const [r, g, b] = rgb(s.current);
    assert.deepEqual([r - br, g - bg, b - bb], s.delta, s.id);
  }
});

// --- 機構の分割（§4.3・FR-C12）-------------------------------------------
test('§4.3: CSS 機構の配線点は現在値表示 3 点 ＋ アプリ UI クロムである', () => {
  const css = CHROME_SLOTS.filter((s) => s.mechanism === 'css');
  assert.deepEqual(css.slice(0, 3).map((s) => s.id),
    ['currentPriceUp', 'currentPriceDown', 'currentPriceNeutral']);
  assert.deepEqual(css.slice(0, 3).map((s) => s.token), ['bullish', 'bearish', 'text']);
  assert.deepEqual(css.slice(3).map((s) => s.id), UI_LEDGER.map(([id]) => id));
  assert.equal(css.length, 3 + UI_LEDGER.length);
});

test('§4.2: 「現在値」は 2 機構にまたがり、ライン(JS)と表示(CSS)で別トークンを持つ', () => {
  // 現在値ライン＝値の上下と無関係に常時同色（highlight）。
  assert.equal(chromeSlot('priceLine').token, 'highlight');
  assert.equal(chromeSlot('priceLine').mechanism, 'js');
  // 現在値表示＝前回表示値との比較で上下を色で示す（bullish/bearish、未確定は text）。
  assert.equal(chromeSlot('currentPriceUp').token, 'bullish');
  assert.equal(chromeSlot('currentPriceDown').token, 'bearish');
  assert.equal(chromeSlot('currentPriceNeutral').token, 'text');
});

test('slot id は一意（表の行が重複しない）', () => {
  const ids = CHROME_SLOTS.map((s) => s.id);
  assert.equal(new Set(ids).size, ids.length);
});
