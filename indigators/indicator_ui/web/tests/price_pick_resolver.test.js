// price_pick_resolver.js（クリック座標 → 採用価格の解決・ISSUE-368 スライス 8-c/8-d 共通）のテスト。
//
// 設計入力（唯一の仕様源）: .doc/POSITION_SIZING_CHART_INTEGRATION_DESIGN.md
//   「ピッカー経路の実測検証」2（`snapCandidatesAt(x)` と `paneIndexAtCoordinate(y)` を使う）、
//   同 4（**px 許容→価格差は `priceAtCoordinate(y)` と `priceAtCoordinate(y+tolPx)` の差で換算**する。
//        `priceToCoordinate` を front に生やさない）、
//   同 7 **裁定済（2026-08-20）**: オシレーターペイン上のクリックは**無効化＋案内**
//        （価格ペインのみ入力有効・下段は確定しない）、
//   「R-P3」（右クリックも R-P1 と**同一のスナップ規則**を使う＝解決器の単一ソース）。
//
// 責務: 右クリック（8-c）とアーム式ピッカー（8-d）が**同じ 1 本**を呼ぶこと。ここが 2 本に割れると
//   「右クリックとピッカーで入る価格が違う」という再現の難しい食い違いが生まれる。
// 構造: Arrange-Act-Assert（AAA）。fake renderer を注入（DOM・lwc 非依存）。

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { readdirSync, readFileSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';

import {
  resolvePickedPrice, OTHER_PANE, MSG_OTHER_PANE, MSG_NO_PRICE,
} from '../js/adapter/front/price_pick_resolver.js';

// 価格ペイン（pane 0）は y=0..299、下段（pane 1）は y=300..399。
//   価格は y=0 で 59000、1px あたり 1 下がる線形（実 lwc の外挿と同じく範囲外もクランプしない）。
function fakeRenderer({ candidates = [], panes = true } = {}) {
  return {
    priceAtCoordinate: (y) => (Number.isFinite(y) ? 59000 - y : null),
    paneIndexAtCoordinate: (y) => {
      if (!panes || !Number.isFinite(y)) return null;
      if (y >= 0 && y < 300) return 0;
      if (y >= 300 && y < 400) return 1;
      return null;
    },
    snapCandidatesAt: () => candidates,
  };
}

test('TC-PR01 価格ペインで候補が近傍に無ければ素のクリック価格を採用する（任意の場所で入力）', () => {
  // Arrange: 候補なし。y=100 → 58900。
  const renderer = fakeRenderer({ candidates: [] });
  // Act
  const got = resolvePickedPrice({ renderer, x: 50, y: 100, tolerancePx: 6 });
  // Assert
  assert.equal(got.price, 58900);
  assert.equal(got.snapped, false);
});

test('TC-PR02 px 許容の内側にある候補へスナップする（許容は priceAtCoordinate の差で換算）', () => {
  // Arrange: 1px = 1 価格。許容 6px ＝ 価格差 6。候補は 4 価格ぶん離れている。
  const renderer = fakeRenderer({ candidates: [{ kind: 'series', label: 'sma20', price: 58904 }] });
  // Act
  const got = resolvePickedPrice({ renderer, x: 50, y: 100, tolerancePx: 6 });
  // Assert
  assert.equal(got.price, 58904);
  assert.equal(got.snapped, true);
  assert.equal(got.candidate.label, 'sma20');
});

test('TC-PR03 px 許容の外側の候補には吸わない（換算が効いている＝価格差で判定していない）', () => {
  // Arrange: 候補は 9 価格ぶん（＝9px）離れており、許容 6px の外。
  const renderer = fakeRenderer({ candidates: [{ kind: 'series', label: 'sma20', price: 58909 }] });
  // Act
  const got = resolvePickedPrice({ renderer, x: 50, y: 100, tolerancePx: 6 });
  // Assert
  assert.equal(got.price, 58900);
  assert.equal(got.snapped, false);
});

test('TC-PR04 下段（オシレーター）ペインのクリックは確定しない（裁定 2026-08-20）', () => {
  // Arrange: y=350 は pane 1。coordinateToPrice はクランプ無しで「価格らしき値」を返すため、
  //   価格として受け取ると異常値が入る。
  const renderer = fakeRenderer({ candidates: [] });
  // Act
  const got = resolvePickedPrice({ renderer, x: 50, y: 350, tolerancePx: 6 });
  // Assert
  assert.equal(got.price, null, '下段ペインの座標から価格を作ってはならない');
  assert.equal(got.reason, OTHER_PANE, '案内表示（ツールチップ）の分岐に使う理由コードを返す');
});

test('TC-PR05 ペイン判定ができない環境（panes 非提供）は確定しない（フェイルクローズ）', () => {
  // Arrange: paneIndexAtCoordinate が null＝価格ペインだと確認できない。
  const renderer = fakeRenderer({ candidates: [], panes: false });
  // Act
  const got = resolvePickedPrice({ renderer, x: 50, y: 100, tolerancePx: 6 });
  // Assert
  assert.equal(got.price, null, '価格ペインだと確認できないまま価格を作らない');
});

test('TC-PR06 価格が取れない座標（可視範囲外）は確定しない（0 へ倒さない）', () => {
  // Arrange: priceAtCoordinate が null を返す（lwc の範囲外）。
  const renderer = { ...fakeRenderer(), priceAtCoordinate: () => null };
  // Act
  const got = resolvePickedPrice({ renderer, x: 50, y: 100, tolerancePx: 6 });
  // Assert
  assert.equal(got.price, null);
});

test('TC-PR07 renderer 未注入・面の欠落は例外にせず「確定しない」で返す', () => {
  // Arrange / Act / Assert
  assert.equal(resolvePickedPrice({ renderer: null, x: 1, y: 1 }).price, null);
  assert.equal(resolvePickedPrice({ renderer: {}, x: 1, y: 1 }).price, null);
});

// ---------------------------------------------------------------------------
// 案内文言の単一ソース（構造ガード）
//
//   宣言（コメント）ではなく機械的検査で担保する（プロジェクト規約: 制約は git/ソース実測で強制）。
//   本ブランチでは同一文言 '価格チャート上で指定してください' が右クリック項目（8-c）と
//   ピッカー（8-d）の 2 モジュールへ手書き複製されていた。裁定の文言が変わったとき片方だけ
//   直る＝「取り残し」がそのまま実 UI の食い違いになる。定義は 1 か所であることを固定する。
// ---------------------------------------------------------------------------

test('TC-PR08 案内文言の定義は front 配下に 1 か所だけ（複製を機械的に禁止する）', () => {
  // Arrange: front 配下の全 .js を読む（テストは対象外＝検定は文言を直書きしてよい）。
  const frontDir = fileURLToPath(new URL('../js/adapter/front/', import.meta.url));
  const sources = readdirSync(frontDir)
    .filter((name) => name.endsWith('.js'))
    .map((name) => [name, readFileSync(join(frontDir, name), 'utf8')]);
  // Act / Assert
  for (const [message, owner] of [
    [MSG_OTHER_PANE, 'price_pick_resolver.js'],
    [MSG_NO_PRICE, 'price_pick_resolver.js'],
  ]) {
    const holders = sources
      .filter(([, src]) => src.includes(`'${message}'`) || src.includes(`"${message}"`))
      .map(([name]) => name);
    assert.deepEqual(
      holders,
      [owner],
      `案内文言「${message}」の定義が ${owner} 以外にもある（複製＝取り残しの原因）: ${holders.join(', ')}`,
    );
  }
});

// ---------------------------------------------------------------------------
// 表示書式の単一ソース（実 UI 実測 2026-08-20 の再発防止）
//
//   差分 2 でモーダル側だけを書式化した結果、ピッカーのゴーストに生の浮動小数が残った。
//   「同じ規則を 2 か所に書かない」を宣言ではなく機械的検査で担保する
//   （案内文言の TC-PR08 と同型）。書式の定義は price_format.js だけが持ち、
//   計算機の View は**呼ぶだけ**にする。
// ---------------------------------------------------------------------------

test('TC-PR09 計算機の View は書式を自前で持たない（定義は price_format.js の 1 か所）', () => {
  // Arrange: 書式の実装に使う API（数値 → 文字列の丸め・桁区切り）。
  const FORMAT_API = /\.toFixed\(|\.toLocaleString\(/;
  const frontDir = fileURLToPath(new URL('../js/adapter/front/', import.meta.url));
  const views = ['position_sizing_dialog.js', 'price_pick_controller.js', 'position_sizing_context_items.js'];
  // Act / Assert
  for (const name of views) {
    const src = readFileSync(join(frontDir, name), 'utf8');
    // コメント（規則の出典を引用している）は対象外＝実行されるコードだけを見る。
    const code = src.replace(/\/\/.*$/gm, '');
    assert.equal(
      FORMAT_API.test(code),
      false,
      `${name} が書式を自前で持っている（price_format.js を呼ぶ。第 2 実装は取り残しを生む）`,
    );
  }
  // 定義側には在ること（ガードが空振りしていないことの確認）。
  const shared = readFileSync(join(frontDir, 'price_format.js'), 'utf8');
  assert.match(shared, FORMAT_API, '共有モジュールに書式の実装が無い（ガードが無意味）');
});
