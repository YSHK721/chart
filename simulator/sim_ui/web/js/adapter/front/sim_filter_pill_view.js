// 抽出フィルタのピル/件数（View・F-7 点18）。
//
// 役割: 移植元 main.js:151-162 の subscribeFilter の DOM 副作用を担う。ヒートマップの
//   セルクリック等で抽出フィルタが立つと、#clearFilter（解除ピル）を可視にし、#detailCount
//   に件数文言を出す。✕ クリックで linkage.applyFilter(null, "")（解除）。
//
// なぜ「新規」でよいか: この副作用は移植元では main.js の boot() 内にインラインで書かれて
//   おり、export された関数ではない。main.js は自己 boot entry で import 不可なので、
//   ここは移植元を写すのではなく v5 流に結線し直す（doc §流用: main.js は結線のみ新規）。
//   件数文言はユーザー向け UI 文字列で、report_ui の表示規則（chart/table/compare の
//   定義）ではない（＝複製 0 の対象外）。
//
// DOM は sim_display_view が生成・所有し、本 View はその要素へ副作用を当てるだけ。

/** 抽出ピル/件数の副作用を担う View を返す。 */
export function createSimFilterPillView() {
  let clearEl = null;
  let countEl = null;

  return {
    /** ピル/件数の要素と linkage を受け取り、✕ クリックの解除を結線する。 */
    wire({ clearFilter, detailCount, linkage } = {}) {
      clearEl = clearFilter || null;
      countEl = detailCount || null;
      if (clearEl && linkage) {
        clearEl.onclick = () => linkage.applyFilter(null, "");
      }
    },

    /** subscribeFilter 通知の DOM 反映（filter=null で解除表示）。 */
    reflect(filter, label) {
      if (clearEl) clearEl.style.display = filter ? "inline-block" : "none";
      if (countEl) {
        countEl.textContent = filter ? ` · 抽出 ${filter.size} 件 (${label || ""})` : "";
      }
    },
  };
}
