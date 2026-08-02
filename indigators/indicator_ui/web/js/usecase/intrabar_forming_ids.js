// INTRABAR_FORMING_IDS — 形成中バー（足内）更新で末尾点を追従させる指標の登録リスト（単一情報源）。
//
// 設計（ライブ・リプレイ同一設計への統一・ユーザー裁定 2026-07-22）:
//   - 指標の末尾点は「価格（形成中バー）の更新と同じ粒度」で追従する。
//     ライブ＝tick 粒度（LiveTickPlayer の tick 適用に同期・coalesce 付き）／
//     リプレイ＝「最新足更新」選択モードの粒度（recomputeFormingLatest・ISSUE-145）。
//   - 全再計算（remove+redraw）はバー確定時のみ（リプレイ＝毎バーその場計算／ライブ＝新確定足検知時）。
//   - 本リストは両モードが同一実体を参照する（replay 側は symlink 経由 import）。
//
// 登録条件（ISSUE-145 の確定規約）: line/histogram 本体を持ち、因果窓（当該バー除外）の
//   バンド系列が末尾差分で据え置かれる指標。horizontal_line のみの指標（アニメ可能な本体なし）
//   と帯系（tgp 等・足内で動かすべきでない）は対象外。
export const INTRABAR_FORMING_IDS = new Set([
  'moving_averages',
  'profit_mfi', 'profit_rsi', 'profit_stc', 'profit_oscillator2',
  'profit_osi_ma', 'profit_hlband', 'profit_mfi_macd', 'profit_rsi_macd',
  // 標準化窓 W を持つ profit_* のうち、本体（line/histogram）を持つ 6 指標（推奨A）。
  //   因果窓ゆえ過去点は repaint せず、最新点のみ forming で動く（実証済み）。profit_hl_band は
  //   horizontal_line のみ（アニメ可能な本体なし）のため対象外＝末尾差分では動かない。
  'profit_adx_needle', 'profit_arctan', 'profit_oscillator',
  'profit_rmm', 'profit_volatility', 'profit_rmm_macd',
  // btlm_trail_marod / ma_marod（別pane オシレータ・line 本体）。足内 tick で MAROD 線の末尾点を
  //   追従させ「結果までの過程」を可視化する。σ/分位/イベント分位の水準線は当該バー除外の因果窓ゆえ
  //   非リペイントで据え置き（ISSUE-145 の実証済みパターン）。
  'btlm_trail_marod',
  'ma_marod',
  // btlm_trail（価格 pane の mean/分位バンド・全系列 line）。ユーザー裁定 2026-07-22
  //   「指標はティック粒度で更新」に従い登録（旧設計の帯系除外を本指標のみ解除）。OLS 端点
  //   （mean）は形成中バーでも意味を持ち、末尾差分で毎 tick 追従する。
  'btlm_trail',
  // tickvol（ティックボリューム・専用 pane のヒストグラム）。形成中バーの tick 数は tick の
  //   到来ごとに増えるため、登録しないと「ローソクは立っているのに直下のバーだけ無い」状態に
  //   なる（full 再計算は確定足までしか含まない）。ライブは server の forming_bar が実 tick 数
  //   （len(mids)）を持つので値は実測そのもの。リプレイの形成中バーは tick 数を持たない
  //   （forming_plan.js の formingStatesAt が OHLC のみ）ため NaN＝点が立たず、確定時に入る。
  'tickvol',
]);
