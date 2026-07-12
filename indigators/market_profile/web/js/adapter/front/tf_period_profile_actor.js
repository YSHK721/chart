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
  // readyTimeoutMs（任意・既定 800ms）: ISSUE-069「揃ってから一括表示」の上限タイムアウト。可視範囲の
  //   全チャンクが ready になるまで描画を保留し、揃った時点で 1 回だけ一括描画する。時間内に揃わない
  //   場合は上限到達で現時点 ready 分を描く（永久保留の防止）。setTimeout/clearTimeout は注入可（テスト用）。
  constructor({
    jitterBuffer, primitive, getTimeframe, getVisibleRange, renderer, getSrc,
    readyTimeoutMs = 800, setTimeoutFn, clearTimeoutFn,
  }) {
    this._buf = jitterBuffer;
    this._primitive = primitive;
    this._getTimeframe = getTimeframe;
    this._getVisibleRange = getVisibleRange;
    this._renderer = renderer ?? null;
    this._getSrc = typeof getSrc === 'function' ? getSrc : () => null;
    this._enabled = false;
    this._readyTimeoutMs = readyTimeoutMs;
    this._setTimeout = typeof setTimeoutFn === 'function' ? setTimeoutFn
      : (fn, ms) => setTimeout(fn, ms);
    this._clearTimeout = typeof clearTimeoutFn === 'function' ? clearTimeoutFn
      : (id) => clearTimeout(id);
    this._pendingRange = null;  // 準備待ち中の可視レンジ（揃うまで描画保留）。
    this._pendingTimer = null;  // 上限タイムアウト timer id。
  }

  // 保留状態を破棄する（無効化・再スケジュール時）。前回描画は保持（clear しない＝ちらつき回避）。
  _clearPending() {
    if (this._pendingTimer != null) {
      this._clearTimeout(this._pendingTimer);
      this._pendingTimer = null;
    }
    this._pendingRange = null;
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
      this._clearPending();
      this._primitive.setTfPeriods(null, null);
      this._setCandleTransparency(false); // 列を消したら candle を可視へ復元（委譲時のみ有効）。
      return;
    }
    this.refresh();
  }

  isEnabled() { return this._enabled; }

  // 可視レンジ契機の再取得＋描画。ensure で窓＋先読みを確保し、可視範囲の列が**揃ってから一括描画**する
  //   （ISSUE-069）。揃うまで前回描画を保持（逐次描画のちらつき/部分表示を回避）。上限タイムアウトで
  //   時間内に揃わなければ現時点 ready 分を描く（フォールバック）。
  refresh() {
    if (!this._enabled) return;
    const tf = this._getTimeframe();
    const r = this._getVisibleRange ? this._getVisibleRange() : null;
    if (!r || r.from == null || r.to == null || !(r.from < r.to)) return;
    this._buf.ensure(tf, r.from, r.to, this._getSrc());
    this._schedule(r.from, r.to);
  }

  // 揃っていれば即一括描画、未なら保留＋上限タイムアウトを張る（onChunkReady で揃い次第 commit）。
  _schedule(from, to) {
    this._clearPending();
    if (typeof this._buf.allReady === 'function' && this._buf.allReady(from, to)) {
      this._render(from, to);   // 全 ready → 一括描画。
      return;
    }
    this._pendingRange = { from, to };
    this._pendingTimer = this._setTimeout(() => {
      this._pendingTimer = null;
      const pr = this._pendingRange;
      this._pendingRange = null;
      if (this._enabled && pr) {
        this._render(pr.from, pr.to); // 上限到達 → 現時点 ready 分で描画（部分フォールバック）。
      }
    }, this._readyTimeoutMs);
  }

  // 先読み完了フック（jitterBuffer.onReady から呼ばれる）: 保留中の可視レンジが揃ったら一括描画する。
  //   保留が無い（既に一括描画済み）場合は何もしない＝逐次再描画しない（ISSUE-069）。
  onChunkReady() {
    if (!this._enabled || !this._pendingRange) return;
    const { from, to } = this._pendingRange;
    if (typeof this._buf.allReady === 'function' && this._buf.allReady(from, to)) {
      this._clearPending();
      this._render(from, to);   // 揃った → 一括描画（タイムアウト前に完了）。
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
