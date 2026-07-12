// tf_period_profile_actor.js — 時間足毎profile列の描画コーディネータ（DOM/chart 非依存・全注入）。
//
// 役割: 可視レンジ変化（スクロール/ズーム）契機で jitter buffer に窓を確保（先読み含む）させ、ready 列を
//   primitive へ供給する。先読み完了（onReady）で再描画し、スクロール到達時に待ちが無いようにする
//   （ジッターバッファ）。tf 切替は jitter buffer 側でキャッシュ破棄。enabled=false で列を消す。
//
// 注入: jitterBuffer / primitive / getTimeframe() / getVisibleRange()（{from,to} UNIX 秒 or null）。

export class TfPeriodProfileActor {
  // renderer（任意）: candle 透明化の書き手（setCandleTransparency）。tf-period 列を描く日別モードでは、
  //   MarketProfileActor が透明化を本 actor へ委ねる（初回の日別タイルちらつき防止・ISSUE-055）。本 actor は
  //   「列が実際に描けたら透明化 true・無効化で false」を担い、列が来るまで candle を可視のままにして空白を防ぐ。
  // getSrc（任意）: 集計方式（null=従来 min-unit カウント / 'zp'=超過占有）を返す関数。未注入は
  //   常に null＝既存挙動不変。src は jitter buffer の ensure へ透過され、変更時はキャッシュ破棄される。
  constructor({ jitterBuffer, primitive, getTimeframe, getVisibleRange, renderer, getSrc }) {
    this._buf = jitterBuffer;
    this._primitive = primitive;
    this._getTimeframe = getTimeframe;
    this._getVisibleRange = getVisibleRange;
    this._renderer = renderer ?? null;
    this._getSrc = typeof getSrc === 'function' ? getSrc : () => null;
    this._enabled = false;
  }

  // candle 透明化を委譲書き込みする（renderer 未注入時は no-op＝後方互換）。
  _setCandleTransparency(on) {
    if (this._renderer && typeof this._renderer.setCandleTransparency === 'function') {
      this._renderer.setCandleTransparency(!!on);
    }
  }

  // 有効化/無効化。false で primitive の tf-period 列を消す（通常/他モードへ復帰）。
  setEnabled(on) {
    this._enabled = !!on;
    if (!this._enabled) {
      this._primitive.setTfPeriods(null, null);
      this._setCandleTransparency(false); // 列を消したら candle を可視へ復元（委譲時のみ有効）。
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
    this._buf.ensure(tf, r.from, r.to, this._getSrc());
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
    const cols = this._buf.getColumns(from, to);
    this._primitive.setTfPeriods(cols, this._buf.unit());
    // 列が実際に描けたときだけ candle を透明化する（それまでは可視＝初回の「候補足→空白→列」の空白を回避）。
    //   同値の applyOptions は no-op ゆえ毎 render 呼んでもちらつかない（冪等）。委譲時（renderer 注入時）のみ。
    this._setCandleTransparency(Array.isArray(cols) && cols.length > 0);
  }
}
