// chart_toast_view.js — 版面上の一時メッセージ（ユーザー指示 2026-08-09）。
//
// 責務（SRP）: 短い文言を数秒だけ出す。何を出すかは知らない（呼び出し側が決める）。
//
// なぜ必要か: クリップボードへの書き込みは**画面に何の変化も起こさない**。成否を出さないと、
//   権限拒否や非 secure context で失敗しても利用者は成功と区別できない（無症状の失敗）。
//
// ホスト要素は本 View が版面（.chart-wrap）配下へ生成し所有する（overlay_host の規約）。
// タイマー実装は注入（テストは fake を渡す＝実時間を待たない）。

import { ensureOverlayHost } from './overlay_host.js';

const HOST_CLASS = 'chart-toast';
const DEFAULT_MS = 1600;

export class ChartToastView {
  constructor({
    document: doc, anchor = null, durationMs = DEFAULT_MS,
    setTimeout: setTimeoutImpl = (typeof globalThis !== 'undefined' ? globalThis.setTimeout.bind(globalThis) : null),
    clearTimeout: clearTimeoutImpl = (typeof globalThis !== 'undefined' ? globalThis.clearTimeout.bind(globalThis) : null),
  } = {}) {
    this._doc = doc ?? null;
    this._anchor = anchor ?? null;
    this._durationMs = durationMs;
    this._setTimeout = setTimeoutImpl;
    this._clearTimeout = clearTimeoutImpl;
    this._host = null;
    this._timer = null;
  }

  _root() {
    if (this._host && this._host.isConnected !== false) {
      return this._host;
    }
    this._host = ensureOverlayHost(this._doc, { className: HOST_CLASS, anchor: this._anchor });
    return this._host;
  }

  // 文言を出して durationMs 後に消す。連続表示は前のタイマーを畳んで置き換える（積み上がらない）。
  show(text) {
    const host = this._root();
    if (!host) {
      return;
    }
    host.textContent = String(text ?? '');
    if (host.classList && typeof host.classList.remove === 'function') {
      host.classList.remove('is-hidden');
    }
    if (this._timer !== null && typeof this._clearTimeout === 'function') {
      this._clearTimeout(this._timer);
      this._timer = null;
    }
    if (typeof this._setTimeout === 'function') {
      this._timer = this._setTimeout(() => {
        this._timer = null;
        this.hide();
      }, this._durationMs);
    }
  }

  hide() {
    const host = this._host;
    if (host && host.classList && typeof host.classList.add === 'function') {
      host.classList.add('is-hidden');
    }
  }
}
