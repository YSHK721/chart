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
  // 加法 delta では地を変えたときに効果が消えた（実測: analysisTint は地 #ffffff で対地 CR 1.0000
  //   ＝地と同一、replayBoundaryDim は地 #000000 で 1.0000）。これらの意味は「地からわずかに離れた
  //   色」であって特定の色相シフトではないため、**対地コントラスト比の目標**で持つ。CR は地に対する
  //   相対量なので、地が変わっても離れ具合が保たれる（ISSUE-346 と同じ規律）。
  //   目標値は現行の暗い地 #131722 での実測 CR（設計値ではない・台帳テストが逆算で照合する）。
  { id: 'dimCandle', token: ColorRole.SURFACE, current: '#16191f', mechanism: 'js', derivedFrom: ColorRole.SURFACE, crTarget: 1.0167 },
  { id: 'analysisTint', token: ColorRole.SURFACE, current: '#1b1a24', mechanism: 'js', derivedFrom: ColorRole.SURFACE, crTarget: 1.0396 },
  { id: 'replayBoundaryDim', token: ColorRole.SURFACE, current: '#090d18', mechanism: 'js', derivedFrom: ColorRole.SURFACE, crTarget: 1.0840 },
  // --- アプリ UI クロム（§7.4 段階 5-D・app.css 全面接続）---------------
  // 上の 20 点（チャート本体）の**後ろ**へ足す。前 20 点の並びが動かないことが、本段階の追加が
  //   チャート本体の配線へ波及していないことの実証になる（台帳テストが位置で固定する）。
  //
  // accent / danger を語彙へ足した以上、本表に配線点が無ければ「宣言できるが何にも効かない」
  //   死語になる（通過条件 4）。CHROME_TOKENS は本表から導くため、ここに在ることが死語でない
  //   ことの構成上の保証である。
  { id: 'uiSurface', token: ColorRole.SURFACE, current: '#131722', mechanism: 'css' },
  { id: 'uiPanel', token: ColorRole.SURFACE, current: '#1e222d', mechanism: 'css', ramp: Object.freeze({ toward: 'anchor', k: Object.freeze([0.04661, 0.047414, 0.049774]) }) },
  { id: 'uiMenuSurface', token: ColorRole.SURFACE, current: '#1c2030', mechanism: 'css', ramp: Object.freeze({ toward: 'anchor', k: Object.freeze([0.038136, 0.038793, 0.063348]) }) },
  { id: 'uiFieldDisabled', token: ColorRole.SURFACE, current: '#181b24', mechanism: 'css', ramp: Object.freeze({ toward: 'anchor', k: Object.freeze([0.021186, 0.017241, 0.00905]) }) },
  { id: 'uiDivider', token: ColorRole.SURFACE, current: '#23272f', mechanism: 'css', ramp: Object.freeze({ toward: 'anchor', k: Object.freeze([0.067797, 0.068966, 0.058824]) }) },
  { id: 'uiChipSurface', token: ColorRole.SURFACE, current: 'rgba(19, 23, 34, 0.72)', mechanism: 'css', alpha: 0.72 },
  { id: 'uiOverlaySurface', token: ColorRole.SURFACE, current: 'rgba(19, 23, 34, 0.82)', mechanism: 'css', alpha: 0.82 },
  { id: 'uiReadoutSurface', token: ColorRole.GRID, current: 'rgba(30, 34, 45, .82)', mechanism: 'css', derivedFrom: ColorRole.GRID, delta: Object.freeze([-1, -3, -3]), alpha: 0.82 },
  { id: 'uiToastSurface', token: ColorRole.GRID, current: 'rgba(28, 32, 48, .95)', mechanism: 'css', derivedFrom: ColorRole.GRID, delta: Object.freeze([-3, -5, 0]), alpha: 0.95 },
  { id: 'uiBorder', token: ColorRole.BORDER, current: '#2a2e39', mechanism: 'css' },
  { id: 'uiBorderStrong', token: ColorRole.BORDER, current: '#363a45', mechanism: 'css', ramp: Object.freeze({ toward: 'anchor', k: Object.freeze([0.056338, 0.057416, 0.060606]) }) },
  { id: 'uiRowHover', token: ColorRole.BORDER, current: '#363b49', mechanism: 'css', ramp: Object.freeze({ toward: 'anchor', k: Object.freeze([0.056338, 0.062201, 0.080808]) }) },
  { id: 'uiChipBorderHover', token: ColorRole.BORDER, current: '#3a4050', mechanism: 'css', ramp: Object.freeze({ toward: 'anchor', k: Object.freeze([0.075117, 0.086124, 0.116162]) }) },
  { id: 'uiToggleOff', token: ColorRole.BORDER, current: '#3a3d47', mechanism: 'css', ramp: Object.freeze({ toward: 'anchor', k: Object.freeze([0.075117, 0.07177, 0.070707]) }) },
  { id: 'uiToggleOffHover', token: ColorRole.BORDER, current: '#44474f', mechanism: 'css', ramp: Object.freeze({ toward: 'anchor', k: Object.freeze([0.122066, 0.119617, 0.111111]) }) },
  { id: 'uiText', token: ColorRole.TEXT, current: '#d1d4dc', mechanism: 'css' },
  { id: 'uiTextStrong', token: ColorRole.TEXT, current: '#ffffff', mechanism: 'css', ramp: Object.freeze({ toward: 'anchor', k: Object.freeze([1, 1, 1]) }) },
  { id: 'uiTextHeading', token: ColorRole.TEXT, current: '#e6e9ef', mechanism: 'css', ramp: Object.freeze({ toward: 'anchor', k: Object.freeze([0.456522, 0.488372, 0.542857]) }) },
  { id: 'uiTextChip', token: ColorRole.TEXT, current: '#b8bec9', mechanism: 'css', ramp: Object.freeze({ toward: 'surface', k: Object.freeze([0.131579, 0.116402, 0.102151]) }) },
  { id: 'uiTextLabel', token: ColorRole.TEXT, current: '#b2b5be', mechanism: 'css', ramp: Object.freeze({ toward: 'surface', k: Object.freeze([0.163158, 0.164021, 0.16129]) }) },
  { id: 'uiTextAux', token: ColorRole.TEXT, current: '#9aa0ad', mechanism: 'css', ramp: Object.freeze({ toward: 'surface', k: Object.freeze([0.289474, 0.275132, 0.252688]) }) },
  { id: 'uiTextWeak', token: ColorRole.TEXT, current: '#787b86', mechanism: 'css', ramp: Object.freeze({ toward: 'surface', k: Object.freeze([0.468421, 0.470899, 0.462366]) }) },
  { id: 'uiTextDisabled', token: ColorRole.TEXT, current: '#5d616b', mechanism: 'css', ramp: Object.freeze({ toward: 'surface', k: Object.freeze([0.610526, 0.608466, 0.607527]) }) },
  { id: 'uiTextOnDisabled', token: ColorRole.TEXT, current: '#6b7088', mechanism: 'css', ramp: Object.freeze({ toward: 'surface', k: Object.freeze([0.536842, 0.529101, 0.451613]) }) },
  { id: 'uiTextOnAccent', token: ColorRole.TEXT, current: '#cfd8ff', mechanism: 'css', derivedFrom: ColorRole.TEXT, delta: Object.freeze([-2, 4, 35]) },
  { id: 'uiAccent', token: ColorRole.ACCENT, current: '#2962ff', mechanism: 'css' },
  { id: 'uiAccentHover', token: ColorRole.ACCENT, current: '#1e53e5', mechanism: 'css', derivedFrom: ColorRole.ACCENT, delta: Object.freeze([-11, -15, -26]) },
  { id: 'uiAccentSubtle', token: ColorRole.ACCENT, current: '#2962ff22', mechanism: 'css', alpha: 34 / 255 },
  { id: 'uiAccentGlow', token: ColorRole.ACCENT, current: 'rgba(41, 98, 255, 0.6)', mechanism: 'css', alpha: 0.6 },
  { id: 'uiAccentDisabled', token: ColorRole.ACCENT, current: '#2a3354', mechanism: 'css', derivedFrom: ColorRole.ACCENT, delta: Object.freeze([1, -47, -171]) },
  { id: 'uiDanger', token: ColorRole.DANGER, current: '#ef5350', mechanism: 'css' },
  { id: 'uiDangerText', token: ColorRole.DANGER, current: '#e0564a', mechanism: 'css', derivedFrom: ColorRole.DANGER, delta: Object.freeze([-15, 3, -6]) },
  { id: 'uiDangerSolid', token: ColorRole.DANGER, current: '#b03a30', mechanism: 'css', derivedFrom: ColorRole.DANGER, delta: Object.freeze([-63, -25, -32]) },
  // POC は市場構造の指標であって破壊的操作の警告ではないため alert（警戒・外れ値）へ束ねる。
  //   danger（破壊・エラー）に束ねると、削除ボタンの色を変えただけで読取欄の POC が動く。
  { id: 'uiPocMarker', token: ColorRole.ALERT, current: '#ff6b6b', mechanism: 'css', derivedFrom: ColorRole.ALERT, delta: Object.freeze([31, -55, 33]) },
  { id: 'uiLiveOn', token: ColorRole.DANGER, current: '#7b2233', mechanism: 'css', derivedFrom: ColorRole.DANGER, delta: Object.freeze([-116, -49, -29]) },
  { id: 'uiLiveOnHover', token: ColorRole.DANGER, current: '#93293e', mechanism: 'css', derivedFrom: ColorRole.DANGER, delta: Object.freeze([-92, -42, -18]) },
  { id: 'uiAlert', token: ColorRole.ALERT, current: '#e0a24a', mechanism: 'css' },
  { id: 'uiAlertStrong', token: ColorRole.ALERT, current: '#e0b84a', mechanism: 'css', derivedFrom: ColorRole.ALERT, delta: Object.freeze([0, 22, 0]) },
  { id: 'uiAlertStar', token: ColorRole.ALERT, current: '#f0b400', mechanism: 'css', derivedFrom: ColorRole.ALERT, delta: Object.freeze([16, 18, -74]) },
  { id: 'uiAlertBorder', token: ColorRole.ALERT, current: '#5a4a18', mechanism: 'css', derivedFrom: ColorRole.ALERT, delta: Object.freeze([-134, -88, -50]) },
  { id: 'uiAlertSurface', token: ColorRole.ALERT, current: '#2a2410', mechanism: 'css', derivedFrom: ColorRole.ALERT, delta: Object.freeze([-182, -126, -58]) },
  { id: 'uiAlertTint', token: ColorRole.ALERT, current: 'rgba(224, 162, 74, 0.08)', mechanism: 'css', alpha: 0.08 },
  { id: 'uiBullish', token: ColorRole.BULLISH, current: '#26a69a', mechanism: 'css' },
  { id: 'uiReplayPanel', token: ColorRole.SURFACE, current: '#222735', mechanism: 'css', ramp: Object.freeze({ toward: 'anchor', k: Object.freeze([0.063559, 0.068966, 0.085973]) }) },
  { id: 'uiReplayWell', token: ColorRole.SURFACE, current: '#0c0e15', mechanism: 'css', ramp: Object.freeze({ toward: 'anchor', k: Object.freeze([0.029661, 0.038793, 0.058824]) }) },
  { id: 'uiReplayTrack', token: ColorRole.SURFACE, current: '#1f2431', mechanism: 'css', ramp: Object.freeze({ toward: 'anchor', k: Object.freeze([0.050847, 0.056034, 0.067873]) }) },
  { id: 'uiReplaySurface', token: ColorRole.SURFACE, current: '#161a25', mechanism: 'css', ramp: Object.freeze({ toward: 'anchor', k: Object.freeze([0.012712, 0.012931, 0.013575]) }) },
  { id: 'uiReplayThumb', token: ColorRole.BORDER, current: '#4a4e5a', mechanism: 'css', ramp: Object.freeze({ toward: 'anchor', k: Object.freeze([0.150235, 0.15311, 0.166667]) }) },
  { id: 'uiReplayText', token: ColorRole.TEXT, current: '#e6e8ea', mechanism: 'css', derivedFrom: ColorRole.TEXT, delta: Object.freeze([21, 20, 14]) },
  // --- 取引マーカー（§7.4 段階 5-E・チャート上の描画物）---------------
  // 実測（trade_markers_renderer.js）: 同一ファイル内で `#26a69a` が 2 つの意味を担っていた。
  //   :138 profit > 0（成果）と :146 side === 'buy'（方向）である。リテラルが同じでも意味が
  //   違うため**別の配線点**へ割る。同じ slot へ束ねると「利益は緑・買いは青」が表現できない。
  { id: 'tradeProfit', token: ColorRole.PROFIT, current: '#26a69a', mechanism: 'css' },
  { id: 'tradeLoss', token: ColorRole.LOSS, current: '#ef5350', mechanism: 'css' },
  { id: 'tradeSideBuy', token: ColorRole.BULLISH, current: '#26a69a', mechanism: 'css' },
  { id: 'tradeSideSell', token: ColorRole.BEARISH, current: '#ef5350', mechanism: 'css' },
  // 売買ペア線（canvas）。`pair.win` は勝ち負け＝**成果**なので profit / loss を束ねる。
  //   canvas は CSS 変数を解決できないため機構は 'js'（色は注入で届く）。
  { id: 'pairLineWin', token: ColorRole.PROFIT, current: '#26a69a', mechanism: 'js' },
  { id: 'pairLineLoss', token: ColorRole.LOSS, current: '#ef5350', mechanism: 'js' },
  // 取引密度帯（canvas・背景）。(41,98,255) は #2962ff＝accent の低不透明度。
  { id: 'tickvolBand', token: ColorRole.ACCENT, current: 'rgba(41, 98, 255, 0.07)', mechanism: 'js', alpha: 0.07 },
  // ウォーターマーク（(209,212,220) は #d1d4dc＝text の高不透明度）。
  //   実測でどこからも参照されていない export だが、削除は承認事項のため値を保ったまま畳む。
  { id: 'watermark', token: ColorRole.TEXT, current: 'rgba(209, 212, 220, 0.9)', mechanism: 'js', alpha: 0.9 },
  // pane σ 水準線の 2 端点（中心からの距離で 穏やか→過熱 を線形補間する）。
  //   ISSUE-360 が MP の HSL 色相ランプと対比して「2 端点の RGB 線形補間」と名指した系であり、
  //   端点 2 色から現行の見た目を厳密に再現できる（MP と違い潰れない）。
  //   接続前はチャネル配列（[46,125,50] / [211,47,47]）で書かれており、`#`・`rgba(` を見る
  //   走査では検出できなかった。配線点化して配列そのものを消す。
  //   穏やか端は neutral をクロムで使う唯一の配線点なので、トークン既定＝この現行値にできる
  //   （CHROME_DEFAULT.neutral）。過熱端は alert を束ねるが、alert のクロム既定（琥珀 #e0a24a）
  //   とは**別の色**（赤 #d32f2f）である。#7 paneSeparatorHover と同じ関係なので、5-D が確立した
  //   3 機構のうち delta（加法・有彩色の濃淡）で表す。実測差分であって設計値ではない。
  { id: 'levelSchemeCalm', token: ColorRole.NEUTRAL, current: '#2e7d32', mechanism: 'js' },
  { id: 'levelSchemeHot', token: ColorRole.ALERT, current: '#d32f2f', mechanism: 'js', derivedFrom: ColorRole.ALERT, delta: Object.freeze([-13, -115, -27]) },
  // --- Market Profile（canvas・§7.4 段階 5-E）---------------------------
  // ISSUE-360 が対象外にしたのは heatColor() の HSL 色相ランプ **1 つだけ**で、以下 16 点は
  //   その射程外である。新語は 1 つも要らない（実測でチャネルが既存トークンと一致する）。
  //
  //   方向 8 点: rgba(38,166,154,·) = #26a69a = bullish / rgba(239,83,80,·) = #ef5350 = bearish
  //   これらは「その意味の色の低不透明度」なので alpha 付き配線点として持つ（派生ではない）。
  { id: 'mpPocLine', token: ColorRole.ALERT, current: '#ff3b3b', mechanism: 'js', derivedFrom: ColorRole.ALERT, delta: Object.freeze([31, -103, -15]) },
  { id: 'mpPocStar', token: ColorRole.ALERT, current: '#ffd54a', mechanism: 'js', derivedFrom: ColorRole.ALERT, delta: Object.freeze([31, 51, 0]) },
  // Value Area は出来高の 70% を含む帯＝語彙定義の range（通常域・分位バンド）と概念が一致する。
  { id: 'mpVaLine', token: ColorRole.RANGE, current: 'rgba(168, 41, 174, 0.5)', mechanism: 'js', alpha: 0.5 },
  // リプレイ時点 T の縦線＝「今この瞬間」を指す＝highlight。
  { id: 'mpCursorLine', token: ColorRole.HIGHLIGHT, current: 'rgba(120, 190, 255, 0.9)', mechanism: 'js', alpha: 0.9 },
  { id: 'mpSessTintUp', token: ColorRole.BULLISH, current: 'rgba(38, 166, 154, 0.12)', mechanism: 'js', alpha: 0.12 },
  { id: 'mpSessTintDown', token: ColorRole.BEARISH, current: 'rgba(239, 83, 80, 0.12)', mechanism: 'js', alpha: 0.12 },
  { id: 'mpOhlcUp', token: ColorRole.BULLISH, current: 'rgba(38, 166, 154, 0.8)', mechanism: 'js', alpha: 0.8 },
  { id: 'mpOhlcDown', token: ColorRole.BEARISH, current: 'rgba(239, 83, 80, 0.8)', mechanism: 'js', alpha: 0.8 },
  // 日別 POC の白・縞・日付ラベルは「読ませる文字／最も控えめな標」＝text の濃淡。
  //   地に対する明度差で意味を作るため ramp（地に相対）で持つ: 暗い地では明るく、明るい地では
  //   暗くなる。加法 delta だと白い地で白い縞が消える（5-D で実測した飽和と同じ病因）。
  { id: 'mpSessPoc', token: ColorRole.TEXT, current: 'rgba(255,255,255,0.95)', mechanism: 'js', alpha: 0.95 },
  { id: 'mpTfpBgUp', token: ColorRole.BULLISH, current: 'rgba(38, 166, 154, 0.1)', mechanism: 'js', alpha: 0.1 },
  { id: 'mpTfpBgDown', token: ColorRole.BEARISH, current: 'rgba(239, 83, 80, 0.1)', mechanism: 'js', alpha: 0.1 },
  { id: 'mpTfpBgUpDim', token: ColorRole.BULLISH, current: 'rgba(38, 166, 154, 0.04)', mechanism: 'js', alpha: 0.04 },
  { id: 'mpTfpBgDownDim', token: ColorRole.BEARISH, current: 'rgba(239, 83, 80, 0.04)', mechanism: 'js', alpha: 0.04 },
  // 縞の 2 段は「不透明度だけが違う同じ色」。値の綴り（.05 / .015）は現行を逐語で保つ。
  { id: 'mpStripeOdd', token: ColorRole.TEXT, current: 'rgba(255,255,255,.05)', mechanism: 'js', alpha: 0.05 },
  { id: 'mpStripeEven', token: ColorRole.TEXT, current: 'rgba(255,255,255,.015)', mechanism: 'js', alpha: 0.015 },
  { id: 'mpDateLabel', token: ColorRole.TEXT, current: 'rgba(154,164,178,.6)', mechanism: 'js', derivedFrom: ColorRole.TEXT, delta: Object.freeze([-55, -48, -42]), alpha: 0.6 },
  // 時間足プロファイルのツールチップ（DOM 要素なので CSS 機構が使える＝注入不要）。
  //   文字色は既存の uiText を再利用する（同じ意味に席を増やさない）ため配線点は 2 点だけ。
  { id: 'tfpTooltipSurface', token: ColorRole.SURFACE, current: 'rgba(19,23,34,0.92)', mechanism: 'css', alpha: 0.92 },
  { id: 'tfpTooltipBorder', token: ColorRole.TEXT, current: 'rgba(154,164,178,0.35)', mechanism: 'css', derivedFrom: ColorRole.TEXT, delta: Object.freeze([-55, -48, -42]), alpha: 0.35 },
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
  // 段階 5-D。値は app.css の現行リテラル（選択中・focus の青／エラー・破壊操作の赤）。
  [ColorRole.ACCENT]: '#2962ff',
  [ColorRole.DANGER]: '#ef5350',
  // 診断（W-C1/2/3）の琥珀・お気に入り星・A 方式注記。いずれも「警戒」の意味であり alert が
  //   正しい割当先（highlight は「今この瞬間の値」を指す語で、警告色ではない）。
  [ColorRole.ALERT]: '#e0a24a',
  // 段階 5-E。値は trade_markers_renderer.js / pair_lines_primitive.js の現行リテラル。
  //   bullish / bearish と同値だが**別のトークン**である（同値であることと同義であることは違う）。
  [ColorRole.PROFIT]: '#26a69a',
  [ColorRole.LOSS]: '#ef5350',
  // 段階 5-E。neutral をクロムで束ねるのは σ 水準線の穏やか端のみで、その現行値がそのまま
  //   トークン既定になる（common/level_colors.py の _CALM と一致する緑）。
  [ColorRole.NEUTRAL]: '#2e7d32',
  // 段階 5-E。range をクロムで束ねるのは MP の Value Area 帯線のみ（値は現行の紫）。
  [ColorRole.RANGE]: '#a829ae',
});

// テーマの対象外（段階 5-D）。**例外は暗黙にせず、ここへ明示登録する**。
//
//   走査テストは「本表に列挙された値を除き、CSS に色リテラルは 0 件」という形で書く。よって
//   例外を増やすには本表へ足すしかなく、見逃しが構造的に起こらない（黙って素通りする経路が無い）。
//
//   影（box-shadow）を対象外にする理由: 影は「色」ではなく**奥行き**である。地が白いテーマでも
//   影は黒であることが正しく、surface から delta で導くと白地で影が明色化して影として機能しなく
//   なる（実測: 黒 rgb(0,0,0) は surface #131722 から L1 距離 76 で、最近傍トークンですらこの距離）。
//   17 語目 `shadow` を足す案は採らない（テーマ編集ダイアログの行が増え、目的 1 に反する）。
export const THEME_EXEMPT_LITERALS = Object.freeze([
  Object.freeze({ literal: 'rgba(0, 0, 0, .55)', reason: 'shadow' }),
  Object.freeze({ literal: 'rgba(0, 0, 0, .5)', reason: 'shadow' }),
  Object.freeze({ literal: 'rgba(0,0,0,0.5)', reason: 'shadow' }),
  Object.freeze({ literal: 'rgba(0, 0, 0, .45)', reason: 'shadow' }),
  // 段階 5-E で JS 側の対象外が 2 種現れた。**台帳は 1 つに保つ**（CSS 用と JS 用に割ると
  //   同一概念に 2 つの名前ができ、次に例外を足す人がどちらへ書くか分からなくなって必ず
  //   取り残しが出る）。理由の集合が「色として扱うと壊れるもの」に閉じていることは
  //   js_literals_single_source.test.js が 3 種の逐語列挙で固定する。
  //
  //   transparent: α=0 は「塗らない」の表現であって色ではない。トークンへ束ねると、テーマが
  //   その意味の色を宣言した瞬間に**不透明になり得る**（sessions のローソク透明化が壊れる）。
  Object.freeze({ literal: 'rgba(0,0,0,0)', reason: 'transparent' }),
  //   input-sentinel: `<input type="color">` は値を空にできないため、未指定状態にも見た目上の
  //   値が要る。これは「未宣言」という**状態**を表す番人であって描画色ではない。どのトークンへ
  //   束ねてもテーマがこの値を動かし、未指定表示の意味が壊れる。
  Object.freeze({ literal: '#000000', reason: 'input-sentinel' }),
]);

const SLOT_BY_ID = new Map(CHROME_SLOTS.map((s) => [s.id, s]));

// 配線点 1 行を返す。未知 id は null（全域的・呼び出し側に型ガードを要求しない）。
export function chromeSlot(id) {
  return SLOT_BY_ID.get(id) ?? null;
}
