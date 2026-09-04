// 接点マーカー表示トグルの結線（View・F-7 FR-18）。
//
// 役割: 移植元 main.js wireContactsToggle と同流儀。#toggleContacts ボタン（DOM は
//   sim_display_view が生成・所有する）へ、接点マーカーの表示/非表示切替を結線する。
//
// 真実源は **renderer**（接点トグル state を持つのは lwc5_chart_renderer 側）。View は
//   独自 state を持たない——コピーを持つと、renderer が別経路（区間切替の再描画等）で
//   state を変えたときにボタン表示が食い違う（ISSUE-379 の「独自 dedupe が真実源と乖離」
//   と同型）。ボタンの .on は setContactsVisible が返す新 state をそのまま反映する。
//
// lwc・report.json・CSS には触らない（結線だけ）。

/** 接点トグルを結線する View を返す。 */
export function createSimContactsToggleView() {
  return {
    /** btn（sim_display_view 所有）へ接点トグルを結線する。btn 不在なら何もしない。 */
    wire({ btn, renderer } = {}) {
      if (!btn || !renderer) return;
      // 初期表示は renderer の現在 state に同期（既定は表示・on）。
      btn.classList.toggle("on", renderer.contactsVisible());
      btn.onclick = () => {
        // 真実源（renderer）を反転し、その戻り（新 state）だけを表示へ反映する。
        const now = renderer.setContactsVisible(!renderer.contactsVisible());
        btn.classList.toggle("on", now);
      };
    },
  };
}
