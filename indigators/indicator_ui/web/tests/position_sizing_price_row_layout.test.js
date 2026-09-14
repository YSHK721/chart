// 価格行のレイアウト構造ガード（ISSUE-434 工程 3）。
//
// 除去する原因（実測 2026-08-20・実ブラウザ 1600x1000 / ライブ 8000）:
//   ダイアログは 720px の 3 カラム＝1 列 221px。価格行は 1 本の flex 行に
//     [.ps-row-label（flex: 0 0 46% ＝縮まない・102px）]
//     [gap 8px × 2 ＝ 16px]
//     [input（flex: 1 1 auto; min-width: 0 ＝ 0 まで潰れる）]
//     [.ps-pick「チャートで指定」（flex: 0 0 auto ＝縮まない・92px）]
//   を並べていた。残りが 221 − 102 − 16 − 92 = 14px しか無く、建値が入力できなかった。
//   通常行（`.ps-row:not(.ps-price-row)`）の入力欄は同じ列で 111px。入力欄に 111px を残すには
//   ボタン幅が 221 − 102 − 16 − 111 = **−8px** ＝負になる。つまりボタンの文字を短くする・
//   padding を削る類の調整では原理的に解決しない（幅予算そのものが足りない）。
//
// 裁定（2026-08-20）: **価格行だけ 2 行にする**。ラベルを上の行へ出し、下の行に
//   [入力欄]＋[チャートで指定] を並べる。入力欄は 221 − 92 − 8 = 121px（通常行の 111px より広い）。
//   ラベル文言・ボタン文言は変えない。対象は価格行 5 つのみ。
//
// **この検定が保証できないこと（重要）**: node の最小 DOM にはレイアウト計算が無いため、
//   「入力欄が実際に何 px になるか」は検定できない（14px と 121px を区別できない）。
//   ここで固定するのは
//     (a) マークアップ上の親子関係（ラベルと入力欄が同じ flex 行で幅を奪い合わない）
//     (b) 配信 CSS に当該規則が在ること（既存 position_sizing_styles_present.test.js と同型）
//   の 2 点だけである。実 px の確認は実 UI 検証の責務。
//
// 構造: Arrange-Act-Assert（AAA）。実物の共有配線で組む（補助は tests/support）。

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

import { boot, priceInput, dialogRoot, flatten } from './support/position_sizing_boot.js';

const JP225_REF = 'jp225_tick';
const CSS = readFileSync(fileURLToPath(new URL('../css/position_sizing.css', import.meta.url)), 'utf8');

const PRICE_TARGETS = ['entry:0', 'entry:1', 'entry:2', 'stop', 'take'];

const classesOf = (el) => String(el?.className ?? '').split(/\s+/).filter(Boolean);
const hasClass = (el, c) => classesOf(el).includes(c);

// ---------------------------------------------------------------------------
// (a) マークアップ構造
// ---------------------------------------------------------------------------

test('TC-PR01 価格行のラベルは入力欄と同じ flex 行に居ない（幅予算を奪い合わない）', () => {
  // Arrange: 実物の配線でモーダルを開く（既定 K=3 ＝ 建値 3 本 ＋ 損切り ＋ 利確）。
  const ctx = boot(JP225_REF);
  const root = dialogRoot(ctx);
  const labels = flatten(root).filter((e) => hasClass(e, 'ps-row-label'));

  for (const target of PRICE_TARGETS) {
    // Act
    const input = priceInput(ctx, target);
    assert.ok(input, `価格欄が無い: ${target}`);
    const line = input.parentNode;
    // Assert: 入力欄の親（＝入力欄が幅を分け合う flex 行）にラベルが同居していない。
    const labelInSameLine = labels.filter((l) => l.parentNode === line);
    assert.deepEqual(
      labelInSameLine.map((l) => l.textContent), [],
      `${target}: ラベルが入力欄と同じ行に居る（46% を先取りされて入力欄が潰れる）`,
    );
  }
});

test('TC-PR02 価格行の入力欄と「チャートで指定」は同じ行に並ぶ（1 行目はラベルだけ）', () => {
  // Arrange
  const ctx = boot(JP225_REF);
  const root = dialogRoot(ctx);
  const picks = flatten(root).filter((e) => e.dataset && e.dataset.psPick !== undefined);

  for (const target of PRICE_TARGETS) {
    // Act
    const input = priceInput(ctx, target);
    const pick = picks.find((p) => p.dataset.psPick === target);
    assert.ok(pick, `ピッカー起動ボタンが無い: ${target}`);
    // Assert
    assert.equal(pick.parentNode, input.parentNode, `${target}: ボタンが入力欄と別の行に落ちている`);
    assert.equal(
      hasClass(input.parentNode, 'ps-price-controls'), true,
      `${target}: 入力欄とボタンを収める行に .ps-price-controls が無い（CSS が当たらない）`,
    );
    // 行（.ps-price-row）の直下は [ラベル] と [.ps-price-controls] の 2 つだけ。
    const row = input.parentNode.parentNode;
    assert.equal(hasClass(row, 'ps-price-row'), true, `${target}: 価格行の直下に控えていない`);
    assert.deepEqual(
      row.children.map((c) => (hasClass(c, 'ps-row-label') ? 'label' : classesOf(c).join('.'))),
      ['label', 'ps-price-controls'],
      `${target}: 価格行の子が [ラベル][入力欄+ボタン] の 2 段になっていない`,
    );
  }
});

test('TC-PR03 価格行以外は 1 行のまま（他の行を巻き込まない）', () => {
  // Arrange: 勝率 p・ペイオフ比 R・口座残高 E 等（data-ps-field を持つ通常行）。
  const ctx = boot(JP225_REF);
  const root = dialogRoot(ctx);
  const fields = flatten(root).filter((e) => e.dataset && e.dataset.psField !== undefined);
  assert.ok(fields.length > 0, '通常行が 1 つも無い（前提の崩れ）');

  for (const input of fields) {
    // Act
    const row = input.parentNode;
    // Assert: 通常行はこれまでどおり [ラベル][入力欄] が同じ行に並ぶ。
    assert.equal(hasClass(row, 'ps-row'), true, `${input.dataset.psField}: 通常行の構造が変わっている`);
    assert.equal(
      row.children.some((c) => hasClass(c, 'ps-row-label')), true,
      `${input.dataset.psField}: ラベルが同じ行から外れた（価格行の是正が波及している）`,
    );
  }
});

// ---------------------------------------------------------------------------
// (b) 配信 CSS の規則（既存 position_sizing_styles_present.test.js と同型の固定）
// ---------------------------------------------------------------------------

test('TC-PR04 価格行は縦積みでラベルの 46% 基底を持ち込まない（CSS 規則）', () => {
  // Arrange / Act / Assert
  assert.match(
    CSS, /\.ps-price-row\s*\{[^}]*flex-direction\s*:\s*column/,
    '価格行が横 1 行のまま（ラベルが入力欄と幅を奪い合う）',
  );
  assert.match(
    CSS, /\.ps-price-row\s*\{[^}]*align-items\s*:\s*stretch/,
    '縦積みで align-items:center が残ると行が内容幅に縮む',
  );
  assert.match(
    CSS, /\.ps-price-row\s+\.ps-row-label\s*\{[^}]*flex\s*:\s*0\s+0\s+auto/,
    'ラベルの flex: 0 0 46% が縦方向では「高さ 46%」として効いてしまう',
  );
  assert.match(
    CSS, /\.ps-price-controls\s*\{[^}]*display\s*:\s*flex/,
    '入力欄とボタンを収める行が横並びにならない',
  );
});

test('TC-PR05 入力欄は読める下限を持ち、狭いときはボタンが折り返す（列幅が縮んでも潰れない）', () => {
  // 実測の教訓: `.ps-row input` の `min-width: 0` は「flex 既定の auto を打ち消して縮ませる」
  //   ためのもので、縮まないボタンと同じ行に置くと入力欄を 0 まで潰す働きをする。
  //   価格行では下限を戻し、入りきらないときはボタンを次の行へ逃がす。
  // Arrange / Act / Assert
  const priceInputRule = CSS.match(/\.ps-price-row\s+input\s*\{[^}]*\}/)?.[0] ?? '';
  assert.notEqual(priceInputRule, '', '.ps-price-row input の規則が無い');
  const min = priceInputRule.match(/min-width\s*:\s*([^;}]+)/)?.[1]?.trim();
  assert.ok(min && !/^0(px|em|rem|%)?$/.test(min), `入力欄の下限が 0 のまま（潰れる）: ${min ?? 'なし'}`);
  assert.match(
    CSS, /\.ps-price-controls\s*\{[^}]*flex-wrap\s*:\s*wrap/,
    '入りきらないときにボタンが入力欄を押し潰す（折り返さない）',
  );
});

test('TC-PR06 入力欄の flex 基準は内容依存でない（intrinsic 幅で段が折り返さない）', () => {
  // 実ブラウザ実測 2026-08-20（ライブ 8000/live・1600x1000・是正前の `flex: 1 1 auto`）:
  //   input の内容由来の基準幅（flex-basis:auto が採る値）= **169px**。
  //   169 + gap 8 + ボタン 92 = 269 > 列 220.7 のため `flex-wrap: wrap` が発動し、
  //   入力欄が 1 段を独占（220.7px）してボタンが次段へ落ちた＝**3 段**（rowH 78px・sameLine false）。
  //   裁定は 2 段（ラベル ／ 入力欄＋ボタン）なので、基準幅を内容から切り離す必要がある。
  //   `flex-basis: 0` にすると折り返し判定に使う仮の主軸サイズは min-width（84px）まで下がり、
  //   84 + 8 + 92 = 184 ≤ 220.7 で同じ段に収まる（実測: inputW 120.6px・sameLine true・rowH 46px）。
  // Arrange
  const rule = CSS.match(/\.ps-price-row\s+input\s*\{[^}]*\}/)?.[0] ?? '';
  // Act
  const basis = rule.match(/flex\s*:\s*\S+\s+\S+\s+([^;}]+)/)?.[1]?.trim();
  // Assert
  assert.ok(basis !== undefined, `.ps-price-row input に 3 値の flex 指定が無い: ${rule}`);
  assert.equal(basis, '0', `入力欄の flex 基準が内容依存（${basis}）＝ intrinsic 幅で段が折り返す`);
});
