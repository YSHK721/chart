// tf_period_profile_actor.js — 時間足毎profile列の描画コーディネータ（DOM/chart 非依存・全注入）。
//
// 役割: 可視レンジ変化（スクロール/ズーム）契機で jitter buffer に窓を確保（先読み含む）させ、ready 列を
//   primitive へ供給する。先読み完了（onReady）で再描画し、スクロール到達時に待ちが無いようにする
//   （ジッターバッファ）。tf 切替は jitter buffer 側でキャッシュ破棄。enabled=false で列を消す。
//
// 注入: jitterBuffer / primitive / getTimeframe() / getVisibleRange()（{from,to} UNIX 秒 or null）。

export class TfPeriodProfileActor {
  constructor({ jitterBuffer, primitive, getTimeframe, getVisibleRange }) {
    this._buf = jitterBuffer;
    this._primitive = primitive;
    this._getTimeframe = getTimeframe;
    this._getVisibleRange = getVisibleRange;
    this._enabled = false;
  }

  // 有効化/無効化。false で primitive の tf-period 列を消す（通常/他モードへ復帰）。
  setEnabled(on) {
    this._enabled = !!on;
    if (!this._enabled) {
      this._primitive.setTfPeriods(null, null);
      return;
    }
    this.refresh();
  }

  isEnabled() { return this._enabled; }

  // 可視レンジ契機の再取得＋描画。ensure で窓＋先読みを確保し、現時点 ready の列を即描画する
  //   （未取得ぶんは onReady で後から埋まり再描画される）。
  refresh() {
    if (!this._enabled) return;
    const tf = this._getTimeframe();
    const r = this._getVisibleRange ? this._getVisibleRange() : null;
    if (!r || r.from == null || r.to == null || !(r.from < r.to)) return;
    this._buf.ensure(tf, r.from, r.to);
    this._render(r.from, r.to);
  }

  // 先読み完了フック（jitterBuffer.onReady から呼ばれる）: 現可視レンジで再描画する。
  onChunkReady() {
    if (!this._enabled) return;
    const r = this._getVisibleRange ? this._getVisibleRange() : null;
    if (r && r.from != null && r.to != null && r.from < r.to) {
      this._render(r.from, r.to);
    }
  }

  _render(from, to) {
    this._primitive.setTfPeriods(this._buf.getColumns(from, to), this._buf.unit());
  }
}
