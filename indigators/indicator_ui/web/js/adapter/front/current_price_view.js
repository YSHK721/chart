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

import { fmtPrice } from './format.js';
import { ensureOverlayStackSlot } from './overlay_host.js';

export class CurrentPriceView {
  // document: DOM 実装（注入）。elementId: 描画先要素の id（CSS 契約名）。
  //   ISSUE-277 の残 / ISSUE-278 #16: 欄そのものを本 View が所有し、版面（.chart-wrap）配下の
  //   左上スタックへ生成する。配信 3 ページへ `<div id="current-price">` を手書き複製する義務を
  //   無くす（取り残しが表示の全滅を招く経路を断つ）。スタック内の順序は構築順で決まるため、
  //   合成根は本 View を読み取り欄より先に構築する。
  // priceDigits: 表示桁（銘柄仕様の `digits`・ISSUE-368 A-3）。**注入のみ**で、ここでは解決も
  //   既定値の決定もしない（権威は Python 台帳ただ 1 つ・front の解決点は chart_app_wiring の
  //   1 か所）。未注入・解決不能なら従来の表示へ落ちる＝無音で桁を決めない。
  constructor({ document, elementId, anchor = null, priceDigits = null }) {
    this._document = document ?? null;
    this._elementId = elementId;
    this._anchor = anchor;
    this._priceDigits = priceDigits;
    // 構築時に欄を確保する（描画順に依存せず DOM の並びを決めるため）。生成不能環境は null。
    this._el = ensureOverlayStackSlot(this._document, { id: elementId, anchor });
    // 直前に表示した値（方向判定の基準）。null=未表示。
    this._prevValue = null;
    // 現在の方向クラス（'is-up' | 'is-down' | ''）。同値のとき据え置くために保持する。
    this._direction = '';
  }

  // 現在値を描画する。value が null/非有限なら空表示（方向状態もリセット）。
  render(value) {
    // 自分で確保した欄を優先し、無ければ id 解決へ落ちる（ページが宣言している旧構成・
    //   fake document のテストと後方互換）。どちらも無ければ安全に no-op。
    const el = this._el ?? this._document?.getElementById?.(this._elementId) ?? null;
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
    el.textContent = fmtPrice(value, this._priceDigits);
    el.className = this._direction;
  }
}
