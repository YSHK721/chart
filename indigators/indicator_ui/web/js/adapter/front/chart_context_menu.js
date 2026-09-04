// chart_context_menu.js — チャート版面の右クリックメニュー（ユーザー指示 2026-08-09）。
//
// 設計入力: ユーザー指示（2026-08-09）「チャートペイン上の任意のローソク足上で右クリックすると
//   『情報をコピーする』項目を追加」。
//
// 責務（SRP）: **開閉と DOM 生成だけ**。項目が何をするかは知らない（項目は注入＝OCP:
//   項目を増やしても本 class は改変不要）。項目は { label, onSelect(context) } の配列で受け取り、
//   context には右クリック位置（チャート要素の左上基準 x/y）を渡す。
//
// ホスト要素の所有: 本 View が版面（.chart-wrap）配下へ自分で生成して所有する（overlay_host の規約）。
//   配信 3 ページ（indicator_ui / replay_ui / unified_ui）の HTML へ手書き複製しない。
//
// 位置: 版面の左上を原点とする絶対配置。右クリック座標は clientX/Y − 版面矩形で求める
//   （offsetX は event target＝lwc 内部 canvas 基準になり版面座標と一致しない）。
//
// DOM 非依存: document / container は注入。DOM 不在（SSR・最小 fake）は install が no-op。

import { ensureOverlayHost } from './overlay_host.js';
import { installMenuCloseHandler, removeMenuCloseHandler } from './menu_document_close.js';

const HOST_CLASS = 'chart-context-menu';

export class ChartContextMenu {
  /**
   * @param {object} deps
   * @param {object} deps.document   DOM 実装（注入）。
   * @param {object} deps.container  右クリックを受けるチャート要素（#chart）。
   * @param {Array}  deps.items      [{ label, onSelect(context) }]。空なら install しても何も出さない。
   * @param {object} [deps.anchor]   版面要素の直接注入（既定は document から .chart-wrap を引く）。
   */
  constructor({ document: doc, container, items = [], anchor = null } = {}) {
    this._doc = doc ?? null;
    this._container = container ?? null;
    this._items = Array.isArray(items) ? items : [];
    this._anchor = anchor ?? null;
    this._host = null;
    this._keydownHandler = null;
  }

  install() {
    const doc = this._doc;
    const container = this._container;
    if (!doc || typeof doc.createElement !== 'function') {
      return;   // DOM 非対応（SSR・スタブ document）は no-op。
    }
    if (!container || typeof container.addEventListener !== 'function' || this._items.length === 0) {
      return;
    }
    container.addEventListener('contextmenu', (e) => {
      // ブラウザ標準メニューは出さない（本メニューと二重に出ると項目が隠れる）。
      if (typeof e.preventDefault === 'function') {
        e.preventDefault();
      }
      this._open(e);
    });

    // 外側クリック・Esc で閉じる（他メニューと同じ規律。document リスナは共有レジストリで 1 個）。
    //   root は関数で渡す: ホスト要素は最初に開くときまで生成しない（install 時点では未生成）。
    installMenuCloseHandler(doc, 'chart-context', { root: () => this._host, close: () => this.close() });
    if (typeof doc.addEventListener === 'function') {
      this._keydownHandler = (e) => {
        if (e && e.key === 'Escape') {
          this.close();
        }
      };
      doc.addEventListener('keydown', this._keydownHandler);
    }
  }

  // 明示 teardown（統合 UI のモード切替のように document リスナが残る経路向け）。
  dispose() {
    removeMenuCloseHandler(this._doc, 'chart-context');
    if (this._doc && typeof this._doc.removeEventListener === 'function' && this._keydownHandler) {
      this._doc.removeEventListener('keydown', this._keydownHandler);
    }
    this._keydownHandler = null;
  }

  close() {
    const host = this._host;
    if (host && host.classList && typeof host.classList.add === 'function') {
      host.classList.add('is-hidden');
    }
  }

  _root() {
    if (this._host && this._host.isConnected !== false) {
      return this._host;
    }
    this._host = ensureOverlayHost(this._doc, { className: HOST_CLASS, anchor: this._anchor });
    return this._host;
  }

  // 版面（.chart-wrap）左上を原点とする座標。矩形が引けない環境（fake）は clientX/Y をそのまま使う。
  _anchorXY(e) {
    const host = this._host;
    const parent = host && host.parentElement ? host.parentElement : null;
    const r = parent && typeof parent.getBoundingClientRect === 'function'
      ? parent.getBoundingClientRect() : null;
    const cx = Number(e && e.clientX) || 0;
    const cy = Number(e && e.clientY) || 0;
    return r ? { x: cx - r.left, y: cy - r.top } : { x: cx, y: cy };
  }

  // チャート要素（#chart）左上を原点とする座標＝足の解決に使う x。
  _containerXY(e) {
    const c = this._container;
    const r = c && typeof c.getBoundingClientRect === 'function' ? c.getBoundingClientRect() : null;
    const cx = Number(e && e.clientX) || 0;
    const cy = Number(e && e.clientY) || 0;
    return r ? { x: cx - r.left, y: cy - r.top } : { x: cx, y: cy };
  }

  _open(e) {
    const doc = this._doc;
    const host = this._root();
    if (!host) {
      return;
    }
    host.innerHTML = '';
    const context = this._containerXY(e);
    for (const item of this._items) {
      const btn = doc.createElement('button');
      btn.type = 'button';
      btn.className = 'chart-context-item';
      btn.textContent = item.label;
      // ISSUE-366: 伝播は止めない（止めると他メニューの外側クリック判定まで殺す）。
      //   自分は下で明示的に閉じ、共有レジストリは「ホストの内側」と判定して二重に閉じない。
      btn.addEventListener('click', () => {
        this.close();
        if (typeof item.onSelect === 'function') {
          item.onSelect(context);
        }
      });
      host.appendChild(btn);
    }
    const { x, y } = this._anchorXY(e);
    host.style.left = `${Math.round(x)}px`;
    host.style.top = `${Math.round(y)}px`;
    if (host.classList && typeof host.classList.remove === 'function') {
      host.classList.remove('is-hidden');
    }
  }
}
