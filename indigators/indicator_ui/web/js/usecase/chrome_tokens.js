// chrome_tokens.js — チャートクロム配線点 → 色の意味（ColorRole）の対応表と現行リテラルの
//   単一情報源（基本設計_指標カラーテーマ.md §4.2・§4.6）。
//
// 設計入力（§7.2 S1 が棄却された理由）: クロムの現行色は JS 3 ファイル（chart_bootstrap.js /
//   chart_renderer.js / replay_boundary_dim.js）と CSS 3 宣言に散在していた。この状態のまま
//   テーマを載せると、「テーマなし」へ戻すときの復元値をもう一度書き写すことになり**二重定義**
//   になる。よってテーマ適用の前提として、現行値をここへ 1 表に畳む（E-21・E-22 の重複解消の起点）。
//
// 責務は**データのみ**（SRP）。色の決定（テーマ有無による解決・派生の合成）は color_resolver.js が
//   担い、配信（chart.applyOptions / :root への setProperty）は chrome_theme_applier.js が担う。
//   本モジュールは DOM・Storage・lightweight-charts のいずれにも依存しない。
//
// 既定値の単位が「配線点（slot）」であることの根拠（恒等性の要・§7.4 段階 1 通過条件 6 / D-11）:
//   #7 paneSeparatorHover の現行値は rgba(178,181,189,0.2) で、束ねるトークン border の現行値
//   #2a2e39 とは**別の色**である。既定をトークン単位（CHROME_DEFAULT[token]）だけで持つと、
//   テーマ未設定時に #7 が rgba(42,46,57,0.2) へ変わり現行の見た目が壊れる。よって「テーマ未宣言
//   時に返す値」は slot.current（逐語）を正とし、CHROME_DEFAULT はテーマがトークンを宣言しない
//   ときの**トークン単位の**既定（CSS カスタムプロパティの供給値・§4.3）として持つ。
//
// OCP: 配線点を 1 点足すときは本表へ 1 行足すだけで、resolver・applier・UI・永続化は不変。
// ライブ・リプレイ共有（replay 側は symlink 参照＝単一実体）。

import { ColorRole } from '../domain/color_roles.js';

// 配線点の一覧（§4.2 の表と同順・同内容）。
//   id          … 配線点の識別子（実装側はこの id で現行値を引く）
//   token       … 束ねる色の意味（テーマが宣言したときに効く）
//   current     … 現行リテラル（テーマ未宣言時に返す値。**逐語**）
//   mechanism   … 'js'（lightweight-charts のオプション）/ 'css'（カスタムプロパティ経由）
//   alpha       … 不透明度を持つ配線点のみ。トークン解決後に rgba() を組む係数（§4.6）
//   derivedFrom … 派生配線点のみ。従属先トークン
//   delta       … 派生配線点のみ。従属先からのチャネル別整数オフセット（E-29 の**実測差分**）
export const CHROME_SLOTS = Object.freeze([
  // --- 面・構造線・文字（§4.2 #1〜#9）---
  { id: 'layoutBackground', token: ColorRole.SURFACE, current: '#131722', mechanism: 'js' },
  { id: 'backgroundFallback', token: ColorRole.SURFACE, current: '#131722', mechanism: 'js' },
  { id: 'gridVertLines', token: ColorRole.GRID, current: '#1f2530', mechanism: 'js' },
  { id: 'gridHorzLines', token: ColorRole.GRID, current: '#1f2530', mechanism: 'js' },
  { id: 'layoutTextColor', token: ColorRole.TEXT, current: '#d1d4dc', mechanism: 'js' },
  { id: 'paneSeparator', token: ColorRole.BORDER, current: '#2a2e39', mechanism: 'js' },
  // 不透明度 0.2 は現行値を維持する（トークンは色の意味のみを持ち、不透明度を含まない・§4.6）。
  { id: 'paneSeparatorHover', token: ColorRole.BORDER, current: 'rgba(178,181,189,0.2)', mechanism: 'js', alpha: 0.2 },
  { id: 'rightPriceScaleBorder', token: ColorRole.BORDER, current: '#2a2e39', mechanism: 'js' },
  { id: 'timeScaleBorder', token: ColorRole.BORDER, current: '#2a2e39', mechanism: 'js' },
  // --- ローソク・現在値ライン（§4.2 #10〜#14）---
  // #10/#11 は lwc の up/border/wick の 3 経路をまとめた 1 配線点（書き換わるオプションは 6 個）。
  { id: 'candleUp', token: ColorRole.BULLISH, current: '#26a69a', mechanism: 'js' },
  { id: 'candleDown', token: ColorRole.BEARISH, current: '#ef5350', mechanism: 'js' },
  // 日別プロファイルのローソク透明化からの復元色（chart_renderer）。#10/#11 と同値でなければ
  //   透明化→復元でローソクの色が変わるため、同じトークンへ束ねる。
  { id: 'candleUpRestore', token: ColorRole.BULLISH, current: '#26a69a', mechanism: 'js' },
  { id: 'candleDownRestore', token: ColorRole.BEARISH, current: '#ef5350', mechanism: 'js' },
  // 現在値ライン: 値の上下と無関係に常時同色（ISSUE-084「candle 色に依存しない固定色」）＝現在地。
  { id: 'priceLine', token: ColorRole.HIGHLIGHT, current: '#ff9800', mechanism: 'js' },
  // --- 現在値表示（CSS 機構・§4.2 #15〜#17）---
  // 現在値「表示」は前回表示値との比較で上下を示す＝方向。ライン（#14）とは意味が異なるため
  //   別トークンを割り当てる（ちぐはぐではなく意味に忠実な割り当て）。
  { id: 'currentPriceUp', token: ColorRole.BULLISH, current: '#26a69a', mechanism: 'css' },
  { id: 'currentPriceDown', token: ColorRole.BEARISH, current: '#ef5350', mechanism: 'css' },
  { id: 'currentPriceNeutral', token: ColorRole.TEXT, current: '#d1d4dc', mechanism: 'css' },
  // --- surface 派生（§4.2 #18〜#20・E-29）---
  // いずれも背景からのチャネル別整数オフセットで厳密に表せる。独立トークンにすると、背景を
  //   変えたときにこれらだけ旧色に残る（依頼者が指摘した破綻がそのまま起きる）。
  { id: 'dimCandle', token: ColorRole.SURFACE, current: '#16191f', mechanism: 'js', derivedFrom: ColorRole.SURFACE, delta: Object.freeze([3, 2, -3]) },
  { id: 'analysisTint', token: ColorRole.SURFACE, current: '#1b1a24', mechanism: 'js', derivedFrom: ColorRole.SURFACE, delta: Object.freeze([8, 3, 2]) },
  { id: 'replayBoundaryDim', token: ColorRole.SURFACE, current: '#090d18', mechanism: 'js', derivedFrom: ColorRole.SURFACE, delta: Object.freeze([-10, -10, -10]) },
].map(Object.freeze));

// slot id → 現行リテラル（実装側が「現行値」を 1 箇所から引くための写像）。
export const CHROME_CURRENT = Object.freeze(
  Object.fromEntries(CHROME_SLOTS.map((s) => [s.id, s.current])),
);

// 配線点が束ねるトークンの集合（重複を除いた出現順）。
export const CHROME_TOKENS = Object.freeze([...new Set(CHROME_SLOTS.map((s) => s.token))]);

// トークン単位の現行既定（§4.6 resolveChromeColor のステップ 2）。
//   CSS カスタムプロパティ（§4.3）へ供給する値でもある。単色かつ非派生の配線点の current と
//   一致することは台帳テストが固定する（#7 のみ不透明度により別値＝上のコメント参照）。
export const CHROME_DEFAULT = Object.freeze({
  [ColorRole.SURFACE]: '#131722',
  [ColorRole.GRID]: '#1f2530',
  [ColorRole.BORDER]: '#2a2e39',
  [ColorRole.TEXT]: '#d1d4dc',
  [ColorRole.BULLISH]: '#26a69a',
  [ColorRole.BEARISH]: '#ef5350',
  [ColorRole.HIGHLIGHT]: '#ff9800',
});

const SLOT_BY_ID = new Map(CHROME_SLOTS.map((s) => [s.id, s]));

// 配線点 1 行を返す。未知 id は null（全域的・呼び出し側に型ガードを要求しない）。
export function chromeSlot(id) {
  return SLOT_BY_ID.get(id) ?? null;
}
