// result_chart_area_view.js — 上のチャート領域に置く「sim 結果チャートの器」を生成し所有する View。
//
// なぜ在るか（2026-09-26 依頼者指示「上のチャートが結果を表示」）:
//   sim のジョブ結果（売買マーク・トリガー指標・口座のパネル）を下部ペインの小さな結果ビューアでは
//   なく上のチャート領域で見る。ライブのチャート（#chart）の状態には触れない——ライブの適用済み
//   指標は「直近 N 本」で計算されており、ジョブの過去期間へ差し替えると別期間の値が残るため。
//   結果を見ている間だけ器をライブのチャートの上に重ね、閉じればライブへそのまま戻る。
//
// 責務（SRP）: 器を作り、出す・隠すを body の状態クラスで切り替えることだけ。中に何を描くかは
//   sim の表示層が持つ（器は host() で渡す＝DIP。sim は統合ページの id を知らない）。
//   見た目（重ね方）は index.html の統合層 CSS が持つ。

/** 器の id。 */
export const RESULT_CHART_AREA_ID = 'um-result-chart';
/** 器を出している間 body に付く状態クラス。 */
export const RESULT_CHART_SHOWN_CLASS = 'um-result-chart-shown';

/**
 * @param {Document} doc 統合ページの DOM
 */
export function createResultChartAreaView({ doc } = {}) {
  let area = null;
  return {
    /** 器を `chartWrap`（上のチャートの版面）の直下へ置く（二重 mount は無視）。 */
    mount(chartWrap) {
      if (area) return area;
      area = doc.createElement('div');
      area.id = RESULT_CHART_AREA_ID;
      chartWrap.appendChild(area);
      return area;
    },
    /** 描画先（sim の表示層へ渡す）。 */
    host() { return area; },
    /** 器をライブのチャートの上に出す。 */
    show() { doc.body.classList.add(RESULT_CHART_SHOWN_CLASS); },
    /** 器を隠し、ライブのチャートへ戻す。 */
    hide() { doc.body.classList.remove(RESULT_CHART_SHOWN_CLASS); },
  };
}
