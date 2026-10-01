// result_chart_area_view.js — 版面（.chart-wrap）に置く「売買履歴チャートの器」を生成し所有する View。
//
// なぜ在るか（2026-09-26 依頼者指示「上のチャートが結果を表示」）:
//   sim のジョブ結果（売買マーク・トリガー指標・口座のパネル）を下部ペインの小さな結果ビューアでは
//   なく売買履歴チャートで見る。ライブチャート（#chart）の状態には触れない——ライブの適用済み
//   指標は「直近 N 本」で計算されており、ジョブの過去期間へ差し替えると別期間の値が残るため。
//   結果を見ている間だけ器をライブのチャートの上に重ね、閉じればライブへそのまま戻る。
//
// 責務（SRP）: 器を作り、出す・隠すを body の状態クラスで切り替えることと、版面の最大化
//   （チャート／明細・参照 report_ui layout.js の 3 状態）を body の状態クラスで表すことだけ。
//   中に何を描くかは sim の表示層が持つ（器は host() で渡す＝DIP。sim は統合ページの id を
//   知らない）。見た目（重ね方・最大化の版面）は index.html の統合層 CSS が持つ。
//
// 器の構造（2026-09-27）: 器（#um-result-chart）＝ 描画先（host()）＋「⛶ チャート最大化」ボタン。
//   ボタンを描画先の外に置くのは、sim の表示層が描画先を空にして描き直す（dispose で子を全部外す）
//   ため。チャート最大化中は下部ペインが消えるので、復元ボタンはチャート側に要る（参照と同じ配置）。

/** 器の id。 */
export const RESULT_CHART_AREA_ID = 'um-result-chart';
/** 器を出している間 body に付く状態クラス。 */
export const RESULT_CHART_SHOWN_CLASS = 'um-result-chart-shown';
/** 版面の最大化の状態クラス（mode → クラス。normal はクラス無し）。CSS は index.html が持つ。 */
export const LAYOUT_MODE_CLASSES = Object.freeze({
  chart: 'um-layout-chart',
  detail: 'um-layout-detail',
});
/** チャート最大化ボタンの id（参照 report_ui の #maxChart に相当）。 */
export const MAX_CHART_BUTTON_ID = 'um-max-chart';

/**
 * @param {Document} doc 統合ページの DOM
 */
export function createResultChartAreaView({ doc } = {}) {
  let area = null;
  let host = null;
  let maxChart = null;
  let onMaxChart = null;
  return {
    /** 器を `chartWrap`（版面）の直下へ置く（二重 mount は無視）。 */
    mount(chartWrap) {
      if (area) return area;
      area = doc.createElement('div');
      area.id = RESULT_CHART_AREA_ID;
      host = doc.createElement('div');
      host.className = 'um-result-chart-host';
      area.appendChild(host);
      maxChart = doc.createElement('button');
      maxChart.type = 'button';
      maxChart.id = MAX_CHART_BUTTON_ID;
      maxChart.className = 'um-max-btn';
      maxChart.textContent = '⛶ チャート最大化';
      maxChart.addEventListener('click', () => { if (onMaxChart) onMaxChart(); });
      area.appendChild(maxChart);
      chartWrap.appendChild(area);
      return area;
    },
    /** 描画先（sim の表示層へ渡す）。 */
    host() { return host; },
    /** 器をライブのチャートの上に出す。 */
    show() { doc.body.classList.add(RESULT_CHART_SHOWN_CLASS); },
    /** 器を隠し、ライブのチャートへ戻す（最大化も解く＝版面を残さない）。 */
    hide() {
      doc.body.classList.remove(RESULT_CHART_SHOWN_CLASS);
      this.setLayoutMode('normal');
    },
    /**
     * 版面の最大化を表す（'normal' | 'chart' | 'detail'）。状態遷移（どのボタンでどこへ行くか）は
     * 呼び出し側（sim の表示層・参照の nextLayoutMode）が持ち、ここは表すだけ。
     */
    setLayoutMode(mode) {
      for (const [name, cls] of Object.entries(LAYOUT_MODE_CLASSES)) {
        doc.body.classList.toggle(cls, name === mode);
      }
      if (maxChart) maxChart.textContent = mode === 'chart' ? '↙ 復元' : '⛶ チャート最大化';
    },
    /** 「⛶ チャート最大化」を押したときの処理を登録する（sim の表示層が状態遷移を持つ）。 */
    onMaxChart(fn) { onMaxChart = typeof fn === 'function' ? fn : null; },
  };
}
