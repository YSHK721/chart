// replay_popup.js — リプレイバーのドロップダウン共通殻（DOM 副作用のみ）。
//
// 役割は 1 つだけ: 「アンカー要素の上に浮く箱」の生成・配置・開閉と、外側クリックでの自動クローズ。
// 中身（期間メニュー／カレンダー／速度メニュー）は呼び出し側が body へ描く（本モジュールは中身を知らない）。
//
// ビュー自動介入の禁止（ISSUE-164）に従い、開く／閉じるはユーザーの明示イベント起点のみ。

export class ReplayPopup {
  constructor({ document: doc, className = 'rp-pop' }) {
    this._doc = doc;
    // DOM 実体を持たない環境（決定論テストの fake document 等）では箱を作らず全操作を no-op に
    //   する。再生駆動側は「メニューが開けないだけ」で従来どおり動く（回帰ゼロ）。
    this._el = (doc && typeof doc.createElement === 'function') ? doc.createElement('div') : null;
    this._anchor = null;
    if (!this._el) return;
    this._el.className = className;
    this._el.style.display = 'none';
    this._onDocDown = (ev) => {
      if (!this.isOpen()) return;
      const t = ev.target;
      if (this._el.contains(t) || (this._anchor && this._anchor.contains(t))) return;
      this.close();
    };
    if (typeof doc.addEventListener === 'function') {
      doc.addEventListener('mousedown', this._onDocDown);
    }
    const host = doc.body || doc.documentElement;
    if (host && typeof host.appendChild === 'function') host.appendChild(this._el);
  }

  get body() { return this._el; }

  isOpen() { return !!this._el && this._el.style.display !== 'none'; }

  // アンカーの直上（バーはチャート下端にあるため上向き）に開く。
  open(anchor) {
    if (!this._el) return;
    this._anchor = anchor;
    this._el.style.display = 'block';
    if (!anchor || typeof anchor.getBoundingClientRect !== 'function') return;
    const r = anchor.getBoundingClientRect();
    const h = this._el.offsetHeight || 0;
    const w = this._el.offsetWidth || 0;
    const vw = (this._doc.defaultView && this._doc.defaultView.innerWidth) || 0;
    let left = r.left;
    if (vw && left + w > vw - 8) left = Math.max(8, vw - 8 - w);
    this._el.style.left = `${Math.round(left)}px`;
    this._el.style.top = `${Math.round(r.top - h - 6)}px`;
  }

  close() {
    if (!this._el) return;
    this._el.style.display = 'none';
    this._anchor = null;
  }

  clear() { if (this._el) this._el.innerHTML = ''; }

  destroy() {
    if (!this._el) return;
    if (typeof this._doc.removeEventListener === 'function') {
      this._doc.removeEventListener('mousedown', this._onDocDown);
    }
    if (this._el.parentNode) this._el.parentNode.removeChild(this._el);
  }
}
