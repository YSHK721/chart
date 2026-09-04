// dashboard_area_view.js — dashboard 表示層の器（#um-dashboard-area）を生成し所有する View。
//
// なぜ独立モジュールか（ISSUE-460）:
//   - 置き場所（#app 配下・チャートと排他）は**統合層の決定**だが、DOM の生成は合成根の
//     仕事ではない（`the_root_never_builds_the_dashboard_dom_itself` が固定する規約。
//     合成根が DOM を作り始めると中央 factory へ育って OCP 違反になる）。
//     bottom_pane_view と同じく「器を所有する小さな View」として切り出す。
//   - 表示・非表示はここでは扱わない: index.html のモード CSS（.um-mode-dashboard）が
//     body クラスで切り替える（#replay-bar と同じ表駆動の性質）。
//
// 設計書 §4.6（依頼者裁定 2026-08-29）: ダッシュボードは**チャート画面には置かない**。
//   dashboard モードではチャートを隠し、この器が版面全体を使う。

export const DASHBOARD_AREA_ID = 'um-dashboard-area';

/**
 * #app 配下に dashboard の器を生成して返す（既に在れば同じものを返す＝再入安全）。
 *
 * @param {Document} doc
 * @returns {HTMLElement} dashboard 表示層へ host として渡す器
 */
export function mountDashboardArea(doc) {
  const existing = doc.getElementById(DASHBOARD_AREA_ID);
  if (existing) return existing;
  const app = doc.getElementById('app');
  if (!app) {
    // アンカー不在は配信ページの構造が壊れている（フェイルクローズ・無言で別の場所へ挿さない）。
    throw new Error('dashboard_area_view: アンカー #app が見つかりません');
  }
  const area = doc.createElement('div');
  area.id = DASHBOARD_AREA_ID;
  app.appendChild(area);
  return area;
}
