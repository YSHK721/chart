// price_pick_controller.js — アーム式ピッカー本体（ISSUE-368 スライス 8-d）。
//
// 設計入力: .doc/POSITION_SIZING_CHART_INTEGRATION_DESIGN.md
//   「追加要件裁定 R-P1」（モーダルの「チャートで指定」でピッカーモードへ入り、クロスヘア追従の
//    ゴースト線＋採用予定価格を表示。クリックで確定・Esc／モーダル取消で解除。**入力先が常に一意**）、
//   「R-P2」（採用予定値をツールチップで明示）、
//   「ピッカー経路の実測検証」7 裁定（2026-08-20: 下段ペインでは確定させず案内する）。
//
// 責務（SRP）: アーム状態の保持と、ホバー表示・確定・解除の 3 遷移だけ。
//   - 価格の解決は `price_pick_resolver`（8-c と**同一の 1 本**）。ここに座標変換を持たない。
//   - 水準の更新はしない。確定した値は `onConfirm(target, price)` で呼び出し側（モーダル）へ渡す。
//
// 縦パン抑止を二重化する理由（スライス 4 の実測と同一）:
//   1. `renderer.setUserInteraction(false)` は lwc の handleScroll/handleScale しか落とさない。
//   2. アプリ自前の縦価格パンは `scale_controller` が `priceScale.setVisibleRange` を直叩きするため、
//      縦パンブロッカーでしか止まらない。
//   片方だけだと、ピッカー中にチャートが縦へ動いて狙った価格を押せない。
//
// ホスト要素は本 View が所有する（overlay_host の規約・index.html は 1 枚も触らない）。

import { ensureOverlayHost } from './overlay_host.js';
// 価格の書式は**モーダルと同じ単一ソース**から取る（第 2 実装を作らない）。ゴーストは
//   「これから置く水準線に添える価格」なので、参照実装 :777（数直線マーカー）の規則を使う。
import { priceOnLine } from './price_format.js';
// 案内文言（裁定 2026-08-20「下段ペインで押しても何も起きない状態を作らない」）は
//   理由コードと同居する単一ソースから取る。右クリック（8-c）と同じ文言を写さない。
import {
  resolvePickedPrice, OTHER_PANE, MSG_OTHER_PANE, DEFAULT_PICK_TOLERANCE_PX,
} from './price_pick_resolver.js';

const HOST_CLASS = 'price-pick-ghost';

// OHLC 候補の label はフィールド名（表示文言は View の責務＝8-b の取り決め）。ここで日本語へ写す。
const OHLC_LABEL = Object.freeze({
  open: '始値', high: '高値', low: '安値', close: '終値',
});

export class PricePickController {
  /**
   * @param {object} deps
   * @param {object} deps.container チャート要素（#chart）。
   * @param {object} deps.renderer  ChartRenderer（座標変換・候補列挙・setUserInteraction）。
   * @param {object} deps.document  DOM 実装（注入）。
   * @param {?Function} [deps.registerVerticalPanBlocker] (predicate) => unregister。
   * @param {?Function} [deps.onConfirm] (target, price) => void。確定時の書き戻し。
   * @param {?Function} [deps.onArmChange] (armed: boolean, target: ?string) => void。アーム状態の変化通知。
   *   アーム中はモーダルがチャートを覆ってはならない（実 UI 実測 2026-08-20）。本 class は
   *   モーダルを知らないので、状態だけを外へ知らせる（表示の決定は呼び出し側＝DIP）。
   * @param {object} [deps.anchor] 版面要素の直接注入（既定は document から .chart-wrap）。
   * @param {number} [deps.tolerancePx] スナップ許容（px）。
   */
  constructor({
    container, renderer, document: doc = null,
    registerVerticalPanBlocker = null, onConfirm = null, anchor = null,
    onArmChange = null, tolerancePx = DEFAULT_PICK_TOLERANCE_PX,
  } = {}) {
    this._container = container ?? null;
    this._renderer = renderer ?? null;
    this._doc = doc;
    this._registerBlocker = registerVerticalPanBlocker;
    this._onConfirm = typeof onConfirm === 'function' ? onConfirm : null;
    this._onArmChange = typeof onArmChange === 'function' ? onArmChange : null;
    this._releaseInteraction = null;
    this._anchor = anchor;
    this._tolerancePx = tolerancePx;
    this._target = null;          // アーム中の入力先（'entry:i' / 'stop' / 'take'）または null
    this._unregisterBlocker = null;
    this._host = null;
    this._line = null;
    this._label = null;
  }

  isArmed() {
    return this._target !== null;
  }

  armedTarget() {
    return this._target;
  }

  install() {
    const container = this._container;
    if (!container || typeof container.addEventListener !== 'function') {
      return;   // SSR/テスト防御（共有配線の他アダプターと同一規約）。
    }
    // 縦パンブロッカーは install 時に 1 度だけ登録し、述語で「アーム中か」を答える
    //   （arm のたびに登録・解除すると、登録口の実装差で解除漏れが起きうる）。
    if (typeof this._registerBlocker === 'function') {
      this._unregisterBlocker = this._registerBlocker(() => this.isArmed());
    }
    container.addEventListener('pointermove', (e) => {
      if (this.isArmed()) {
        this._preview(e);
      }
    });
    container.addEventListener('click', (e) => {
      if (this.isArmed()) {
        this._confirm(e);
      }
    });
    const doc = this._doc;
    if (doc && typeof doc.addEventListener === 'function') {
      doc.addEventListener('keydown', (e) => {
        if (e && e.key === 'Escape' && this.isArmed()) {
          this.disarm();
        }
      });
    }
  }

  /**
   * ピッカーモードへ入る（モーダルの「チャートで指定」から呼ぶ）。
   * 後勝ち: 別の欄で押し直したら入力先が差し替わる（入力先は常に一意＝R-P1）。
   */
  arm(target) {
    this._target = target;
    this._suppressInteraction();
    // アーム中はチャートがポインタを受け取れる必要がある（モーダルが覆っていると R-P1 が
    //   成立しない・実 UI 実測 2026-08-20）。どう見せるかは呼び出し側の責務。
    this._onArmChange?.(true, this._target);
  }

  /** 解除（Esc・モーダル側の取消・確定後）。冪等。 */
  disarm() {
    if (!this.isArmed()) {
      return;
    }
    this._target = null;
    this._hideGhost();
    this._releaseInteraction?.();
    this._releaseInteraction = null;
    this._onArmChange?.(false, null);
  }

  // ---- 内部 ----

  _containerXY(e) {
    const c = this._container;
    const r = c && typeof c.getBoundingClientRect === 'function' ? c.getBoundingClientRect() : null;
    const cx = Number(e && e.clientX) || 0;
    const cy = Number(e && e.clientY) || 0;
    return r ? { x: cx - r.left, y: cy - r.top } : { x: cx, y: cy };
  }

  _resolve(e) {
    const { x, y } = this._containerXY(e);
    return resolvePickedPrice({
      renderer: this._renderer, x, y, tolerancePx: this._tolerancePx,
    });
  }

  // ホバー: ゴースト線と採用予定価格を出す（下段ペインは線を出さず案内だけ）。
  _preview(e) {
    const resolved = this._resolve(e);
    const { y } = this._containerXY(e);
    if (resolved.price === null || resolved.price === undefined) {
      this._showGhost(y, resolved.reason === OTHER_PANE ? MSG_OTHER_PANE : '', { line: false });
      return;
    }
    this._showGhost(y, pickLabel(resolved), { line: true });
  }

  // 確定: 価格が取れたときだけ書き戻して解除する。取れないときはアームを続ける（押し直せる）。
  _confirm(e) {
    const resolved = this._resolve(e);
    if (resolved.price === null || resolved.price === undefined) {
      this._preview(e);   // 案内を出したまま待つ（黙って何も起きない状態にしない）。
      return;
    }
    const target = this._target;
    this.disarm();
    if (this._onConfirm) {
      this._onConfirm(target, resolved.price);
    }
  }

  // lwc 操作の抑止を**登録**する（ChartRenderer.suppressInteraction）。drag と同時に抑止しても
  //   互いの解除で巻き添えにならない（単数スロット `setUserInteraction` の奪い合いを避ける）。
  _suppressInteraction() {
    const renderer = this._renderer;
    if (this._releaseInteraction || !renderer || typeof renderer.suppressInteraction !== 'function') {
      return;
    }
    this._releaseInteraction = renderer.suppressInteraction();
  }

  // 版面（.chart-wrap）配下のホストを用意する（無ければ作り・あれば再利用）。
  _ensureHost() {
    if (this._host) {
      return this._host;
    }
    const host = ensureOverlayHost(this._doc, { className: HOST_CLASS, anchor: this._anchor });
    if (!host) {
      return null;   // DOM 非対応環境は描画しない（縮退）。
    }
    const line = this._doc.createElement('div');
    line.className = 'price-pick-line';
    const label = this._doc.createElement('div');
    label.className = 'price-pick-label';
    host.appendChild(line);
    host.appendChild(label);
    this._host = host;
    this._line = line;
    this._label = label;
    return host;
  }

  _showGhost(y, text, { line = true } = {}) {
    const host = this._ensureHost();
    if (!host) {
      return;
    }
    // 版面基準の y（ホストは .chart-wrap 配下・座標はチャート要素基準）。両者の上端は
    //   同一の版面レイアウトに従うため、ChartContextMenu と同じ扱いにする。
    host.style.top = `${Math.round(y)}px`;
    this._line.style.display = line ? '' : 'none';
    this._label.textContent = text;
    if (host.classList && typeof host.classList.remove === 'function') {
      host.classList.remove('is-hidden');
    }
  }

  _hideGhost() {
    const host = this._host;
    if (host && host.classList && typeof host.classList.add === 'function') {
      host.classList.add('is-hidden');
    }
    if (this._label) {
      this._label.textContent = '';
    }
  }
}

// 採用予定価格の表示文字列。どこへ吸ったか（候補名）まで出す（R-P2「採用予定値を明示」）。
function pickLabel(resolved) {
  const price = priceOnLine(resolved.price);   // 生の浮動小数を画面に出さない（実 UI 実測の是正）。
  if (!resolved.snapped || !resolved.candidate) {
    return price;
  }
  const c = resolved.candidate;
  const name = c.kind === 'ohlc' ? (OHLC_LABEL[c.label] ?? c.label) : c.label;
  return name ? `${price}（${name}）` : price;
}
