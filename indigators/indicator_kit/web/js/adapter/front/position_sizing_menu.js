// position_sizing_menu.js — ポジションサイズ計算機のツールバー入口（DOM アダプター・両アプリ共有）。
//
// 設計入力（唯一の仕様源）: .doc/POSITION_SIZING_CHART_INTEGRATION_DESIGN.md
//   §6 アダプター設計（器＝`app_chrome_view.installChartToolbar` が生成する空マウント
//   `#position-sizing-menu`／項目 DOM は本モジュールが生成する／**index.html は 1 枚も触らない**）、
//   §6「協働子は import せずコールバック注入・遅延参照（color_theme と同一規約）」。
//
// 責務（SRP）: ツールバー上のトリガー DOM の生成と、押下の通知だけ。計算機モーダルの生成・表示は
//   `position_sizing_dialog.js` の責務で、本モジュールはそれを **import しない**（注入された
//   `onOpen` を呼ぶだけ＝DIP）。呼び出し先の解決は押された時点（遅延参照）で行われるため、
//   配線順（install の前後）に依存しない。
//
// なぜドロップダウンではないか: 計算機の操作対象は 1 つ（モーダルを開く）であり、選ぶ項目が
//   存在しない。行のないポップを置くと「開く → 1 行だけのメニュー → 開く」という空回りの階層が
//   増える（認知負荷の最小化）。項目が実在するようになった時点で color_theme_menu と同型の
//   ポップを足せばよい（そのときも器と外側クリック規約は変わらない）。
//
// DOM 不在（SSR・テスト最小 fake）・器不在のページは no-op（chart_template_menu と同型の防御）。

export class PositionSizingMenu {
  /**
   * @param {object} opts
   * @param {object} opts.document DOM 実装（注入。null 可＝no-op）。
   * @param {?function} [opts.onOpen] トリガー押下＝計算機モーダルを開く要求。
   */
  constructor({ document: doc = null, onOpen = null } = {}) {
    this._doc = doc;
    this._onOpen = typeof onOpen === 'function' ? onOpen : null;
    this._trigger = null;
  }

  install() {
    const doc = this._doc;
    if (!doc || typeof doc.getElementById !== 'function' || typeof doc.createElement !== 'function') {
      return;   // DOM 不在（SSR・テスト最小 fake）は no-op。
    }
    const mount = doc.getElementById('position-sizing-menu');
    if (!mount || typeof mount.appendChild !== 'function') {
      return;   // 器が無いページ（器の所有は app_chrome_view）。
    }
    const trigger = doc.createElement('button');
    trigger.type = 'button';
    trigger.id = 'position-sizing-menu-trigger';
    trigger.className = 'tb-interval position-sizing-menu-trigger';
    trigger.title = 'ポジションサイズ計算機';
    const label = doc.createElement('span');
    label.id = 'position-sizing-menu-label';
    label.textContent = 'サイズ';
    trigger.append(label);
    mount.appendChild(trigger);
    this._trigger = trigger;

    // ISSUE-366: トリガーで伝播を止めない（止めると他メニューの外側クリック判定まで殺す）。
    trigger.addEventListener('click', () => {
      this._onOpen?.();
    });
  }
}
