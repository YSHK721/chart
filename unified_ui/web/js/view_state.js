// 閲覧状態の capture / restore（スケルトン — Red フェーズ）。
//
// 契約（基本設計書 §3 切替動作 1./4.）:
//   モード切替時に現在の閲覧状態（timeframe・表示指標構成・可視レンジ）を capture し、
//   反対モード再構築後に restore する。
//   - captureState(source): source の timeframe getter / 指標構成 getter / 可視レンジ getter を
//     読み取り `{timeframe, indicators, visibleRange}` を返す
//   - restoreState(target, state): state を target の対応 setter へ適用する（呼び出し順・引数を契約）
//   - capture → restore のラウンドトリップで等価な状態が復元される
//
// controller / chart 実体には非依存（getter/setter を持つ抽象 source/target を受ける）。
//
// Red フェーズ: シグネチャのみ。本体は未実装で throw する。

/**
 * 閲覧状態を capture する。
 * @param {{getTimeframe:()=>*, getIndicators:()=>*, getVisibleRange:()=>*}} source 状態取得元
 * @returns {{timeframe:*, indicators:*, visibleRange:*}} capture された状態
 */
export function captureState(source) {
  return {
    timeframe: source.getTimeframe(),
    indicators: source.getIndicators(),
    visibleRange: source.getVisibleRange(),
  };
}

/**
 * capture した状態を target へ restore する。
 * @param {{setTimeframe:(v:*)=>void, setIndicators:(v:*)=>void, setVisibleRange:(v:*)=>void}} target 状態適用先
 * @param {{timeframe:*, indicators:*, visibleRange:*}} state 適用する状態
 * @returns {void}
 */
export function restoreState(target, state) {
  // 契約順序: timeframe → indicators → visibleRange。
  target.setTimeframe(state.timeframe);
  target.setIndicators(state.indicators);
  target.setVisibleRange(state.visibleRange);
}
