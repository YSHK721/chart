// CurrentPriceView（adapter/front/current_price_view.js）— 現在値の大型表示ビュー。
//
// 左上凡例スタック（#chart-overlay-tl）先頭に現在値（最新足の終値）を大きく常時表示し、
//   視認性を高める（ユーザー指示 2026-07-23・フォントサイズは CSS #current-price が単一情報源）。
// カラースキーム: 前回表示値との比較で上げ（is-up）/下げ（is-down）のクラスを付け替える。
//   同値は直前の方向を維持（クラス据え置き）。初回・値なしは方向クラス無し（中立色）。
//   色の実体はローソクと同スキーム（app.css: 上げ #26a69a / 下げ #ef5350）。
//
// 設計方針（CrosshairReadoutView と同方針）:
//   - usecase/domain を参照しない adapter 層ビュー。lightweight-charts に触れない。
//   - DOM は注入（document / elementId）。テストは fake document を渡す。
//   - 値が null / 非有限 / 対象要素不在でも安全（クラッシュしない・空表示）。

import { fmtValue } from './format.js';

export class CurrentPriceView {
  // document: DOM 実装（注入）。elementId: 描画先要素の id。
  constructor({ document, elementId }) {
    this._document = document ?? null;
    this._elementId = elementId;
    // 直前に表示した値（方向判定の基準）。null=未表示。
    this._prevValue = null;
    // 現在の方向クラス（'is-up' | 'is-down' | ''）。同値のとき据え置くために保持する。
    this._direction = '';
  }

  // 現在値を描画する。value が null/非有限なら空表示（方向状態もリセット）。
  render(value) {
    const el = this._document?.getElementById?.(this._elementId) ?? null;
    if (!el) {
      return;
    }
    if (value === null || value === undefined || !Number.isFinite(value)) {
      el.textContent = '';
      el.className = '';
      this._prevValue = null;
      this._direction = '';
      return;
    }
    if (this._prevValue !== null && value !== this._prevValue) {
      this._direction = value > this._prevValue ? 'is-up' : 'is-down';
    }
    this._prevValue = value;
    el.textContent = fmtValue(value);
    el.className = this._direction;
  }
}
