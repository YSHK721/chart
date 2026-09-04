// mp_display_mode.js — MP 表示モード enum（normal/sessions/replay/ticklive）の単一台帳（ISSUE-134 OCP）。
//
// 従来 mode === 'sessions' / mode !== 'sessions' / mode === 'normal' の直接比較と _applyMode の
//   mode 別 if 連鎖が market_profile_actor.js / growth_window.js / catalog_entry.js の 3 層に散在し、
//   モード追加（replay/sessions → ticklive/rolling の実績あり）時に複数箇所の同時修正を要した。本台帳へ
//   per-mode の宣言的属性を集約し、各呼出面は属性名で参照する（新モードは台帳 1 箇所への追記で完結＝OCP）。
//
// 属性の定義（各属性は従来の boolean 比較と 1:1 一致＝挙動変更ゼロ）:
//   - isNormal   : 通常モード（全期間累積）か（旧 mode === 'normal'）。解像度/期間パラメータの有効判定に使う。
//   - splitByDay : 日別プロファイル分割モードか（旧 mode === 'sessions'）。セッション集計窓・tf-period 列・
//                  src option の各判定に使う。
//   - transition : actor._applyMode が実行する状態遷移経路 'normal'|'sessions'|'replay'|'ticklive'。
//                  未知 mode は 'normal'（＝従来の「未知の mode は 'normal' 扱い（安全側）」の吸収先）。
//
// 注意（別 enum との区別）: 「リプレイバーの anchor モード（anchor/rolling）」は本表示モードとは別 enum で
//   ある（market_profile_actor._replayExtra の replayBar.mode()＝anchor|rolling）。混同を避けるため本台帳
//   には含めない（rolling は表示モードの値ではない）。

export const MP_DISPLAY_MODES = Object.freeze({
  normal: Object.freeze({ isNormal: true, splitByDay: false, transition: 'normal' }),
  sessions: Object.freeze({ isNormal: false, splitByDay: true, transition: 'sessions' }),
  replay: Object.freeze({ isNormal: false, splitByDay: false, transition: 'replay' }),
  ticklive: Object.freeze({ isNormal: false, splitByDay: false, transition: 'ticklive' }),
});

// 未知 mode のフォールバック（宣言的述語は false＝旧 === 比較と一致・遷移経路は 'normal'＝安全側）。
const _UNKNOWN_MODE = Object.freeze({ isNormal: false, splitByDay: false, transition: 'normal' });

// mode → 表示モード台帳エントリ（未知 mode は安全側フォールバックを返す）。
export function mpDisplayMode(mode) {
  return MP_DISPLAY_MODES[mode] ?? _UNKNOWN_MODE;
}
