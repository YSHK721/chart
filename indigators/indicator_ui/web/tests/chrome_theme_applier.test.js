// chrome_theme_applier.test.js — 解決済みクロム色を 2 機構へ配信する協働子（A-11）
//   （基本設計_指標カラーテーマ.md §4.3 FR-C12・§5.2 UC-C02 手順 2・§5.7 F-C10/F-C11）。
//
// 同一のトークン表から (a) lightweight-charts のオプションと (b) CSS カスタムプロパティの
//   2 つを同時に駆動する。これが無いと「現在値ラインだけ変わって現在値表示が旧色に残る」
//   （E-28 の 2 機構分裂）が起きる。
//
// applier は resolver を知らず、値を受け取って配るだけ（DIP）。よって本テストは
//   「どこへ何を書いたか」だけを固定し、色の決定規則は color_resolver.test.js が持つ。

import test from 'node:test';
import assert from 'node:assert/strict';

import { ChromeThemeApplier } from '../js/adapter/front/chrome_theme_applier.js';
import { resolveAllChrome } from '../js/usecase/color_resolver.js';
import { CHROME_SLOTS, CHROME_CURRENT, CHROME_DEFAULT } from '../js/usecase/chrome_tokens.js';
import { COLOR_ROLES, ColorRole } from '../js/domain/color_roles.js';

// chromeSink は applyChromeColors(slots) だけを持てばよい（§7.3 ISP）。lwc のオプション経路への
//   写像は ChartRenderer（唯一の upstream 隔離点・ISSUE-262）が持ち、その値の検証は
//   chart_renderer_chrome.test.js が担う。本ファイルは扇形分岐と縮退だけを固定する。
function makeSinks() {
  const sinkCalls = [];
  const props = new Map();
  const removed = [];
  return {
    chromeSink: { applyChromeColors: (slots) => sinkCalls.push(slots) },
    rootStyle: {
      setProperty: (k, v) => props.set(k, v),
      removeProperty: (k) => { props.delete(k); removed.push(k); },
    },
    sinkCalls, props, removed,
  };
}

const theme = (roleColors) => ({ themeId: 'thm#1', name: 't', roleColors, tfModifier: null });

// =========================================================================
// resolveAllChrome（純関数・値の組み立て）
// =========================================================================

test('resolveAllChrome: テーマ未設定なら全 slot が現行リテラル・全 chrome トークンが現行既定', () => {
  const { slots, tokens } = resolveAllChrome(null);
  for (const [id, current] of Object.entries(CHROME_CURRENT)) {
    assert.equal(slots[id], current, id);
  }
  for (const [token, def] of Object.entries(CHROME_DEFAULT)) {
    assert.equal(tokens[token], def, token);
  }
});

test('resolveAllChrome: 語彙の全トークンに席がある（クロムで束ねないトークンは未宣言なら null）', () => {
  const { tokens } = resolveAllChrome(null);
  assert.deepEqual(Object.keys(tokens).sort(), [...COLOR_ROLES].sort());
  // 「クロムの既定を持つか」は**その意味をクロムのどこかが束ねているか**で決まる。
  //   段階 5-E で MP の Value Area 帯線が range を、σ 水準線の穏やか端が neutral を束ねたため、
  //   この 2 語はクロム既定を持つ側へ移った（席の有無ではなく既定の有無が変わっただけ）。
  //   束ねる配線点が 1 つも無いトークンは依然 null（CSS 側は removeProperty で前回値を残さない）。
  const boundByChrome = new Set(CHROME_SLOTS.map((s) => s.token));
  for (const token of COLOR_ROLES) {
    if (boundByChrome.has(token)) {
      assert.notEqual(tokens[token], null, `${token}: クロムが束ねているのに既定が無い`);
    } else {
      assert.equal(tokens[token], null, `${token}: クロムが束ねていないのに既定がある`);
    }
  }
  // 束ねられていない代表例（指標の本体線）。
  assert.equal(tokens[ColorRole.PRIMARY], null);
});

test('resolveAllChrome: 宣言されたトークンは slot・token の両方へ届く', () => {
  const { slots, tokens } = resolveAllChrome(theme({ surface: '#202020', primary: '#0f0f0f' }));
  assert.equal(tokens.surface, '#202020');
  assert.equal(tokens.primary, '#0f0f0f', '指標側トークンも CSS へ公開する（v2 の接続点）');
  assert.equal(slots.layoutBackground, '#202020');
  assert.equal(slots.dimCandle, '#212121', '減光も追随する（対地 CR 目標 1.0167）');
  assert.equal(slots.replayBoundaryDim, '#272727', '減光も追随する（対地 CR 目標 1.0840）');
  assert.equal(slots.gridVertLines, CHROME_CURRENT.gridVertLines, '未宣言トークンは現行のまま');
});

// =========================================================================
// JS 機構（chart.applyOptions / mainSeries.applyOptions）
// =========================================================================

test('§5.2 手順 2: JS 機構へは 1 回だけ、全 20 配線点の値をまとめて渡す', () => {
  const s = makeSinks();
  new ChromeThemeApplier({ chromeSink: s.chromeSink, rootStyle: s.rootStyle })
    .apply(resolveAllChrome(null));
  assert.equal(s.sinkCalls.length, 1);
  assert.deepEqual(Object.keys(s.sinkCalls[0]).sort(), Object.keys(CHROME_CURRENT).sort());
});

test('通過条件 1: テーマ未設定なら JS 機構へ渡す値が現行リテラルと文字列一致（恒等）', () => {
  const s = makeSinks();
  new ChromeThemeApplier({ chromeSink: s.chromeSink, rootStyle: s.rootStyle })
    .apply(resolveAllChrome(null));
  assert.deepEqual(s.sinkCalls[0], { ...CHROME_CURRENT });
});

// =========================================================================
// CSS 機構（:root への setProperty）
// =========================================================================

test('§4.3: 解決した 14 トークンを --ct-<token> として :root へ書く', () => {
  const s = makeSinks();
  new ChromeThemeApplier({ chromeSink: s.chromeSink, rootStyle: s.rootStyle })
    .apply(resolveAllChrome(theme({ bullish: '#00ff00', primary: '#0f0f0f' })));
  assert.equal(s.props.get('--ct-bullish'), '#00ff00');
  assert.equal(s.props.get('--ct-primary'), '#0f0f0f');
  // クロム既定を持つトークンは現行既定が書かれる。
  assert.equal(s.props.get('--ct-surface'), CHROME_DEFAULT.surface);
  assert.equal(s.props.get('--ct-text'), CHROME_DEFAULT.text);
});

test('§4.3: 値が無いトークン（クロムが束ねない・未宣言）は書かずに removeProperty する', () => {
  // 例は `primary`（指標の本体線）を使う。段階 5-E 以前は `range` を例にしていたが、MP の
  //   Value Area 帯線が range を束ねたためクロム既定を持つ側へ移り、例として成立しなくなった。
  //   規則（値が無いトークンは書かずに消す＝適用履歴に依存しない）は不変で、替えたのは例だけである。
  const s = makeSinks();
  const applier = new ChromeThemeApplier({ chromeSink: s.chromeSink, rootStyle: s.rootStyle });
  applier.apply(resolveAllChrome(theme({ primary: '#111111' })));
  assert.equal(s.props.get('--ct-primary'), '#111111');
  // テーマを外すと前回の書き込みが残らない（適用履歴に依存しない）。
  applier.apply(resolveAllChrome(null));
  assert.equal(s.props.has('--ct-primary'), false);
  assert.ok(s.removed.includes('--ct-primary'));
});

test('CSS 機構の fallback と一致: --ct-text/--ct-bullish/--ct-bearish が現在値表示へ届く', () => {
  const s = makeSinks();
  new ChromeThemeApplier({ chromeSink: s.chromeSink, rootStyle: s.rootStyle })
    .apply(resolveAllChrome(null));
  assert.equal(s.props.get('--ct-text'), CHROME_CURRENT.currentPriceNeutral);
  assert.equal(s.props.get('--ct-bullish'), CHROME_CURRENT.currentPriceUp);
  assert.equal(s.props.get('--ct-bearish'), CHROME_CURRENT.currentPriceDown);
});

// =========================================================================
// 縮退（F-C10 / F-C11）
// =========================================================================

test('F-C10: chart が applyOptions を持たなければ JS 配信を no-op にし、CSS 配信は継続する', () => {
  const s = makeSinks();
  const applier = new ChromeThemeApplier({ chromeSink: {}, rootStyle: s.rootStyle });
  assert.doesNotThrow(() => applier.apply(resolveAllChrome(null)));
  assert.equal(s.props.get('--ct-surface'), CHROME_DEFAULT.surface, 'CSS 機構は生きている');
});

test('F-C11: rootStyle が無ければ CSS 配信を no-op にし、JS 配信は継続する', () => {
  const s = makeSinks();
  const applier = new ChromeThemeApplier({ chromeSink: s.chromeSink, rootStyle: null });
  assert.doesNotThrow(() => applier.apply(resolveAllChrome(null)));
  assert.equal(s.sinkCalls.length, 1, 'JS 機構は生きている');
});

test('両 sink 不在でも例外を投げない（SSR・単体テスト）', () => {
  const applier = new ChromeThemeApplier({});
  assert.doesNotThrow(() => applier.apply(resolveAllChrome(null)));
  assert.doesNotThrow(() => applier.apply(null));
});

test('applier は resolver を import していない（DIP: 値を受け取って配るだけ）', async () => {
  const { readFileSync } = await import('node:fs');
  const { fileURLToPath } = await import('node:url');
  const src = readFileSync(fileURLToPath(new URL('../js/adapter/front/chrome_theme_applier.js', import.meta.url)), 'utf8');
  // 依存の有無を見る（コメント中の言及は文書であって依存ではない）。
  const imports = [...src.matchAll(/(?:from|import)\s*\(?\s*['"]([^'"]+)['"]/g)].map((m) => m[1]);
  assert.deepEqual(imports, [], `import を持たない値配信の協働子であること: ${imports.join(', ')}`);
});
