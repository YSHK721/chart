// mp_chart_layout.js — チャートレイアウト（primitive の attach／右マージン確保）ロール（ISSUE-181・SRP）。
// @upstream-isolation: mp_chart_layout.js
//
// 設計入力（ISSUE-181）: MarketProfileActor は 6 アクター同居の神クラスで、その 1 つが
//   「チャートレイアウト」（旧 market_profile_actor.js の _applyProfileMargin / _attachTarget /
//   _ensureAttached / detach）だった。変更要求の出所は「プロファイル表示のためのチャート面の確保
//   （右マージン率）と primitive の attach 寿命」のみで、取得パラメータ・表示モード遷移・
//   リプレイ・tick 成長とは独立している。
//
// 状態も一緒に移す（ISSUE-181 対応方針・参照実装 mp_primitive_roles.js の分割手法に倣う）:
//   attach 済みフラグ（_attached）と attach 先（mainSeries）・描画対象（primitive）・
//   チャート面ポート（renderer）の参照を本クラスが所有する。host はこれらを持たない。
//
// 不変条件（挙動不変）:
//   - attach は一度だけ（_attached ガード）。attachPrimitive 非提供時は skip（後方互換）。
//   - detach 時は右マージンとローソク透明化を必ず復元する（MP 削除で取り残さない）。
//   - 本ロールはビュー（ズーム・スクロール・可視レンジ）へ一切介入しない。右マージン率の設定は
//     抽出前と同一の呼び出し（renderer.setRightMarginFraction）のみで、新規の自動介入は足さない
//     （ISSUE-164 のユーザー裁定）。

// MP 表示中の右マージン（プロファイル専用領域＝試作 PROFILE_FRAC。バーとローソクの重なり回避）。
const PROFILE_MARGIN_FRACTION = 0.30;

export class MpChartLayout {
  constructor({ primitive, mainSeries, renderer } = {}) {
    this._primitive = primitive;
    this._mainSeries = mainSeries;
    this._renderer = renderer ?? null;
    this._attached = false;
  }

  // 右マージン（プロファイル専用領域）の ON/OFF。renderer 非対応時は skip（後方互換）。
  applyProfileMargin(on) {
    if (this._renderer && typeof this._renderer.setRightMarginFraction === 'function') {
      this._renderer.setRightMarginFraction(on ? PROFILE_MARGIN_FRACTION : null);
    }
  }

  // attach 対象（ProfileSink ファサード経由なら下層 ISeriesPrimitive を取り出す・mp_primitive_roles）。
  attachTarget() {
    const p = this._primitive;
    return (p && typeof p.seriesPrimitive === 'function') ? p.seriesPrimitive() : p;
  }

  // primitive を mainSeries へ一度だけ attach する（attachPrimitive 非提供時は skip）。
  ensureAttached() {
    if (this._attached) {
      return;
    }
    if (this._mainSeries && typeof this._mainSeries.attachPrimitive === 'function') {
      this._mainSeries.attachPrimitive(this.attachTarget());
      this._attached = true;
    }
  }

  // primitive を mainSeries から取り外す（detachPrimitive 非提供時は skip＝後方互換）。
  //   凡例からの削除（close）で呼び、次回有効化で再 attach できるよう _attached を戻す。
  detach() {
    if (this._attached && this._mainSeries && typeof this._mainSeries.detachPrimitive === 'function') {
      this._mainSeries.detachPrimitive(this.attachTarget());
    }
    this._attached = false;
    this.applyProfileMargin(false); // 右マージン復元（MP 削除で取り残さない）。
    // sessions のローソク透明化を必ず復元する（MP 削除でローソクを不透明へ戻す＝取り残さない）。
    if (this._renderer && typeof this._renderer.setCandleTransparency === 'function') {
      this._renderer.setCandleTransparency(false);
    }
  }
}
