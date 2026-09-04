// price_pick_resolver の銘柄仕様（呼び値）対応（ISSUE-368 スライス S-5）のテスト。
//
// 設計入力（唯一の仕様源）: .doc/POSITION_SIZING_CHART_INTEGRATION_DESIGN.md
//   「追補: 工程 2」丸めの適用点 経路 1・2（**スナップ候補と素のクリック価格の両方**を resolver で
//    量子化する。`chart_renderer.js` は 0 バイト改変＝銘柄仕様を renderer に持ち込まない）、
//   同「フェイルセーフ」（仕様が解決できないとき **無音で生値に落とさない**。値ではなく機能を落とし
//    `reason='no_symbol_spec'` と案内文言を出す。静的既定への無音フォールバックは採らない）、
//   依頼者裁定 2026-08-20（「スナップ候補も刻みへ丸める」）。
//
// 除去する原因: チャートから拾った価格が生の浮動小数（実測 `62707.710070965324`）のまま
//   下流（モーダル・水準）へ流れ、ゴーストの表示（`62,708`）と食い違っていた。
//
// `spec` の 3 状態を分けることが本スライスの契約である（**null と undefined を同義にしない**）:
//   - `spec` 未指定（undefined）: 銘柄仕様を**扱わない**呼び出し。従来どおり量子化しない
//     （snap 規則そのものを検定する既存 TC-PR01〜07 の契約。1 バイトも改変しない）。
//   - `spec === null`          : 解決を試みて**失敗した**。フェイルクローズ（機能を落とす）。
//   - `spec = {tick}`          : 解決できた。刻みへ丸める。
//
// 構造: Arrange-Act-Assert（AAA）。fake renderer を注入（DOM・lwc 非依存）。

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { readdirSync, readFileSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';

import {
  resolvePickedPrice, NO_SYMBOL_SPEC, MSG_NO_SYMBOL_SPEC, OTHER_PANE,
} from '../js/adapter/front/price_pick_resolver.js';

// 価格ペイン（pane 0）は y=0..299、下段（pane 1）は y=300..399。
//   価格は y=0 で 62807.710070965324（実 UI 実測の生値と同じ小数部）から 1px=1 で下がる。
const RAW_TOP = 62807.710070965324;

function fakeRenderer({ candidates = [] } = {}) {
  return {
    priceAtCoordinate: (y) => (Number.isFinite(y) ? RAW_TOP - y : null),
    paneIndexAtCoordinate: (y) => {
      if (!Number.isFinite(y)) return null;
      if (y >= 0 && y < 300) return 0;
      if (y >= 300 && y < 400) return 1;
      return null;
    },
    snapCandidatesAt: () => candidates,
  };
}

test('TC-QR01 素のクリック価格を刻みへ丸めて返す（生の浮動小数を下流へ流さない）', () => {
  // Arrange: y=100 の素の価格は 62707.710070965324（実 UI 実測の値）。刻みは JP225 の 1。
  const renderer = fakeRenderer({ candidates: [] });
  // Act
  const got = resolvePickedPrice({
    renderer, x: 50, y: 100, tolerancePx: 6, spec: { tick: 1 },
  });
  // Assert
  assert.equal(got.price, 62708);
  assert.equal(got.snapped, false);
});

test('TC-QR02 スナップ候補も刻みへ丸めてから吸わせる（裁定 2026-08-20）', () => {
  // Arrange: 候補は指標系列の生値（小数つき）。素のクリック価格（62707.71…）の近傍にある。
  const renderer = fakeRenderer({ candidates: [{ kind: 'series', label: 'sma20', price: 62705.4321 }] });
  // Act
  const got = resolvePickedPrice({
    renderer, x: 50, y: 100, tolerancePx: 6, spec: { tick: 1 },
  });
  // Assert: 吸った先も刻み上（候補の生値 62705.4321 が水準へ入らない）。
  assert.equal(got.price, 62705);
  assert.equal(got.snapped, true);
  assert.equal(got.candidate.label, 'sma20', '候補の素性（表示に使う）は保つ');
});

test('TC-QR03 候補の量子化は元の候補オブジェクトを書き換えない（列挙側の状態を汚さない）', () => {
  // Arrange: renderer が同じ配列を再利用しても値が壊れないこと（候補は renderer の所有物）。
  const candidates = [{ kind: 'series', label: 'sma20', price: 62705.4321 }];
  const renderer = fakeRenderer({ candidates });
  // Act
  resolvePickedPrice({
    renderer, x: 50, y: 100, tolerancePx: 6, spec: { tick: 1 },
  });
  // Assert
  assert.equal(candidates[0].price, 62705.4321, '呼び出し側の候補が丸められている（破壊的変更）');
});

test('TC-QR04 銘柄仕様が解決できない（null）ときは確定せず理由を返す（無音で生値に落とさない）', () => {
  // Arrange: renderer は正常＝価格自体は取れる状況（それでも値を通さない）。
  const renderer = fakeRenderer({ candidates: [] });
  // Act
  const got = resolvePickedPrice({
    renderer, x: 50, y: 100, tolerancePx: 6, spec: null,
  });
  // Assert
  assert.equal(got.price, null, '刻みが不明なまま価格を作ってはならない');
  assert.equal(got.reason, NO_SYMBOL_SPEC);
  assert.equal(got.snapped, false);
});

test('TC-QR05 仕様の欠落は下段ペイン判定より先に見る（機能全体が落ちている理由を出す）', () => {
  // Arrange: 下段ペイン（y=350）かつ仕様なし。理由コードは 1 つしか返せないため順序を固定する。
  const renderer = fakeRenderer({ candidates: [] });
  // Act
  const got = resolvePickedPrice({
    renderer, x: 50, y: 350, tolerancePx: 6, spec: null,
  });
  // Assert: 「下段だから無効」ではなく「刻みが不明だから機能ごと無効」が実際の理由。
  assert.equal(got.reason, NO_SYMBOL_SPEC);
  assert.notEqual(got.reason, OTHER_PANE);
});

test('TC-QR06 tick が正の有限数でない仕様も解決失敗として扱う（0 除算・素通しを作らない）', () => {
  // Arrange
  const renderer = fakeRenderer({ candidates: [] });
  // Act / Assert: 台帳が壊れた場合に「丸めない価格」を黙って通さない。
  for (const spec of [{ tick: null }, { tick: 0 }, { tick: -1 }, { tick: NaN }, {}]) {
    const got = resolvePickedPrice({
      renderer, x: 50, y: 100, tolerancePx: 6, spec,
    });
    assert.equal(got.price, null, `tick=${JSON.stringify(spec)} で価格を作っている`);
    assert.equal(got.reason, NO_SYMBOL_SPEC);
  }
});

test('TC-QR07 spec 未指定（undefined）は従来どおり量子化しない（既存の呼び出し契約は不変）', () => {
  // Arrange: 既存 TC-PR01 と同じ呼び方（spec を渡さない）。
  const renderer = fakeRenderer({ candidates: [] });
  // Act
  const got = resolvePickedPrice({ renderer, x: 50, y: 100, tolerancePx: 6 });
  // Assert: 「未指定」は失敗ではない（銘柄仕様を扱わない呼び出し＝snap 規則そのものの検定）。
  assert.equal(got.price, RAW_TOP - 100);
  assert.equal(got.reason, null);
});

test('TC-QR08 px 許容の換算は量子化前の価格差で行う（刻みが許容を歪めない）', () => {
  // Arrange: 1px = 1 価格。許容 6px ＝ 価格差 6。候補は 9 価格ぶん離れており許容の外。
  const renderer = fakeRenderer({ candidates: [{ kind: 'series', label: 'sma20', price: 62716.71 }] });
  // Act
  const got = resolvePickedPrice({
    renderer, x: 50, y: 100, tolerancePx: 6, spec: { tick: 1 },
  });
  // Assert: 吸わずに素のクリック価格（量子化済み）。
  assert.equal(got.snapped, false);
  assert.equal(got.price, 62708);
});

test('TC-QR09 量子化で同値へ潰れた候補は先頭が勝つ（決定性・snap 規則の既定を保つ）', () => {
  // Arrange: 2 つの候補は刻み 1 の上では同じ 62706 になる。
  const renderer = fakeRenderer({
    candidates: [
      { kind: 'series', label: 'first', price: 62705.6 },
      { kind: 'ohlc', label: 'close', price: 62706.4 },
    ],
  });
  // Act
  const got = resolvePickedPrice({
    renderer, x: 50, y: 100, tolerancePx: 6, spec: { tick: 1 },
  });
  // Assert: `snap_price_resolver.js:16-17`「同距離は候補配列の先頭が勝つ」がそのまま効く。
  assert.equal(got.price, 62706);
  assert.equal(got.candidate.label, 'first');
});

// ---------------------------------------------------------------------------
// 案内文言の単一ソース（構造ガード・既存 TC-PR08 と同型）
//
//   理由コードを増やしたら文言も同じ場所で増える、を機械的に固定する。宣言（コメント）では
//   担保しない（プロジェクト規約: 制約は git/ソース実測で強制）。
// ---------------------------------------------------------------------------

test('TC-QR10 「刻み不明」の案内文言の定義は front 配下に 1 か所だけ', () => {
  // Arrange
  const frontDir = fileURLToPath(new URL('../js/adapter/front/', import.meta.url));
  const sources = readdirSync(frontDir)
    .filter((name) => name.endsWith('.js'))
    .map((name) => [name, readFileSync(join(frontDir, name), 'utf8')]);
  // Act
  const holders = sources
    .filter(([, src]) => src.includes(`'${MSG_NO_SYMBOL_SPEC}'`) || src.includes(`"${MSG_NO_SYMBOL_SPEC}"`))
    .map(([name]) => name);
  // Assert
  assert.deepEqual(
    holders,
    ['price_pick_resolver.js'],
    `案内文言の定義が複製されている（取り残しの原因）: ${holders.join(', ')}`,
  );
});

test('TC-QR11 案内文言は裁定どおりの内容である（理由が読み手に伝わる）', () => {
  // Arrange / Act / Assert: 文言そのものが仕様（設計「フェイルセーフ」節の文）。
  assert.equal(
    MSG_NO_SYMBOL_SPEC,
    'この銘柄の価格の刻みが不明なため、チャートからの価格指定を無効にしています',
  );
  assert.equal(NO_SYMBOL_SPEC, 'no_symbol_spec');
});

// ---------------------------------------------------------------------------
// 丸めの第 2 実装の禁止（原因 β の再演防止・構造ガード）
//
//   本ブランチは既に「モーダルだけ書式化してゴーストに生値が残る」を起こしている
//   （`price_format.js:7-11` に記録）。**量子化の式は domain の 1 か所**にしか置かない。
// ---------------------------------------------------------------------------

test('TC-QR12 front は丸めの式を自前で持たない（呼ぶのは domain の quantize だけ）', () => {
  // Arrange: 量子化の式（`Math.round(x / tick) * tick`）の骨格。
  const QUANTIZE_EXPR = /Math\.round\s*\([^)]*\/\s*\w*[Tt]ick[^)]*\)\s*\*/;
  const frontDir = fileURLToPath(new URL('../js/adapter/front/', import.meta.url));
  // Act / Assert
  for (const name of readdirSync(frontDir).filter((n) => n.endsWith('.js'))) {
    const code = readFileSync(join(frontDir, name), 'utf8').replace(/\/\/.*$/gm, '');
    assert.equal(QUANTIZE_EXPR.test(code), false, `${name} が丸めを自前で実装している（第 2 実装）`);
  }
  // 定義側に在ること（ガードが空振りしていないことの確認）。
  const domain = readFileSync(fileURLToPath(new URL('../js/domain/price_quantize.js', import.meta.url)), 'utf8');
  assert.match(domain, QUANTIZE_EXPR, 'domain に量子化の実装が無い（ガードが無意味）');
});
