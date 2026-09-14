// scroll_to_latest_button.js — 「最新のバーまでスクロール」ボタン（ISSUE-116・DOM アダプター）。
//
// 設計入力: ユーザー指示（2026-07-18）「過去に遡って現在に戻りたい場合の UI を追加。常時表示ではなく、
//   特定の範囲にマウスオーバーされたときに表示する」＝ TradingView の » ボタン相当（ss2026071891636.jpg）。
//
// 仕様:
//   - ホットゾーン: チャート右 HOT_ZONE_X_FRAC（20%）× 下 HOT_ZONE_Y_FRAC（50%）の領域。
//   - 表示条件: ホットゾーン内へのポインタホバー かつ 最新足が可視範囲外（renderer.isLatestBarVisible()
//     が false）。最新足が見えている間はホバーしても出さない（戻る操作が不要なため）。
//   - クリック: renderer.scrollToRealTime()（常設右余白 rightOffset を尊重して最新足へ復帰）→非表示。
//   - pointerleave（チャート外へ退出）: 非表示。
//
// 責務（SRP）: ボタン DOM の生成・表示制御・クリック委譲に限定。lwc へは触れない（renderer の
//   isLatestBarVisible / scrollToRealTime のみに依存＝DIP）。container/doc 不在（SSR/テスト）は no-op。

const HOT_ZONE_X_FRAC = 0.8; // x >= width×0.8（右 20%）でホットゾーン
const HOT_ZONE_Y_FRAC = 0.5; // y >= height×0.5（下半分）でホットゾーン

export class ScrollToLatestButton {
  // { container, renderer, document }
  //   container: チャートコンテナ要素（pointer イベント購読・ボタンの親）。
  //   renderer : ChartRenderer（isLatestBarVisible / scrollToRealTime を消費）。
  constructor({ container, renderer, document: doc }) {
    this._container = container;
    this._renderer = renderer;
    this._doc = doc;
    this._btn = null;
  }

  install() {
    const doc = this._doc;
    const container = this._container;
    if (!doc || typeof doc.createElement !== 'function'
        || !container || typeof container.addEventListener !== 'function'
        || typeof container.appendChild !== 'function') {
      return; // DOM 不在（SSR/テスト最小 fake）は no-op（防御）。
    }
    const btn = doc.createElement('button');
    btn.type = 'button';
    btn.className = 'scroll-latest-btn is-hidden';
    btn.textContent = '»';
    btn.title = '最新のバーまでスクロール';
    btn.addEventListener('click', () => {
      if (typeof this._renderer.scrollToRealTime === 'function') {
        // 速度 x2（ユーザー指示 2026-07-18）。ライブ追従の catch-up は従来速度のまま。
        this._renderer.scrollToRealTime({ speed: 2 });
      }
      this._setVisible(false); // 復帰後は不要（最新足が可視になる）。
    });
    container.appendChild(btn);
    this._btn = btn;

    container.addEventListener('pointermove', (e) => this._onPointerMove(e));
    container.addEventListener('pointerleave', () => this._setVisible(false));
  }

  _onPointerMove(e) {
    this._setVisible(this._inHotZone(e) && !this._isLatestVisible());
  }

  _inHotZone(e) {
    const rect = typeof this._container.getBoundingClientRect === 'function'
      ? this._container.getBoundingClientRect() : null;
    if (!rect || !(rect.width > 0) || !(rect.height > 0)) {
      return false;
    }
    const x = e.clientX - rect.left;
    const y = e.clientY - rect.top;
    return x >= rect.width * HOT_ZONE_X_FRAC && y >= rect.height * HOT_ZONE_Y_FRAC;
  }

  _isLatestVisible() {
    // isLatestBarVisible 非提供（後方互換 Fake）は true＝最新扱い（ボタンを出さない安全側）。
    return typeof this._renderer.isLatestBarVisible === 'function'
      ? !!this._renderer.isLatestBarVisible()
      : true;
  }

  _setVisible(on) {
    const btn = this._btn;
    if (!btn || !btn.classList || typeof btn.classList.toggle !== 'function') {
      return;
    }
    btn.classList.toggle('is-hidden', !on);
  }
}
