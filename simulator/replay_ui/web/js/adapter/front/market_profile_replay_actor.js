// market_profile_replay_actor.js — Market Profile の slim 制御アクター（replay フレーム駆動・tick 逐次成長）。
//
// 設計入力: Phase2 arch 確定「slim MarketProfileReplayActor（present actor 非移植）」。present の
//   MarketProfileActor は sessions/scrub/snapshot/setReplayCursor/_applyProfileMargin/getCandles 依存を
//   持ち込む（YAGNI/throw）ため移植しない。本アクターは replay の render/animateForming が駆動する
//   最小 5 API に限定する（SRP）。依存はすべて抽象（duck-typing）へ向け、composition root が具象を注入する（DIP）。
//
//   公開 API:
//     - enterBar(now): base=1・src=dwell・now=T（因果）で forming を取得し accumulator を init、base のみ描画。
//         await で ready 保証（feedTick 取りこぼし防止）。バー入場ごとに accumulator を作り直す＝rollover 兼。
//     - feedTick(sec,mid): _enabled && _accumulator のとき addTick（O(1)・HTTP無）→ throttle で snapshot 描画。
//     - settleTick(): 確定時に最終 snapshot を強制描画（throttle 無視）。
//     - setEnabled(on): 初回のみ mainSeries.attachPrimitive（非提供時 skip）→ primitive.setVisible(on)。
//     - isEnabled(): トグル状態。
//
// 非破壊方針: primitive は初回有効化まで attach しない（OFF 時チャートに触れない）。forming 取得失敗
//   （null）・base 必須フィールド欠損（空 profile）は前回描画を保持（NaN 混入を防ぐ＝既存 fetch null と同じ）。

import { FORMING_MIN_INTERVAL_MS } from '../../replay/timing.js';

// MP-05 presence ガード（present actor と同基準）: base=1 応答の必須フィールド（レンジ/グリッド/base 配列）が
//   すべて有限/配列のときだけ true。欠損（無ローソク等の空 profile）は null 扱いで増分に入れず前回描画保持。
//   JSON の明示 null は Number(null)===0（有限）で誤通過するため、各必須数値は `!= null` を先に課す。
function _finiteNum(x) {
  return x != null && Number.isFinite(Number(x));
}
function _hasBaseFields(f) {
  return !!f
    && _finiteNum(f.priceMin)
    && _finiteNum(f.priceMax)
    && _finiteNum(f.nBins) && Number(f.nBins) > 0
    && _finiteNum(f.gridW) && Number(f.gridW) > 0
    && Array.isArray(f.baseFine);
}

export class MarketProfileReplayActor {
  // formingClient: fetchForming(args)->payload|null。primitive: setProfile/setVisible/(attach 経由)。
  // makeAccumulator: ()->DwellAccumulator（init/addTick/snapshot）。mainSeries: attachPrimitive（v5・非提供時 skip）。
  // getContext: ()->{datasetRef,timeframe,...}（取得時点のチャート状態を遅延読み取り）。
  // now: ()->ms（throttle 用の時計・テストで注入）。throttleMs: snapshot 間隔（既定 FORMING_MIN_INTERVAL_MS）。
  constructor({
    formingClient, makeAccumulator, primitive, mainSeries, getContext, now, throttleMs,
  } = {}) {
    this._formingClient = formingClient ?? null;
    this._makeAccumulator = typeof makeAccumulator === 'function' ? makeAccumulator : null;
    this._primitive = primitive;
    this._mainSeries = mainSeries ?? null;
    this._getContext = typeof getContext === 'function' ? getContext : () => ({});
    this._now = typeof now === 'function'
      ? now
      : (typeof performance !== 'undefined' ? () => performance.now() : () => Date.now());
    this._throttleMs = Number.isFinite(throttleMs) ? throttleMs : FORMING_MIN_INTERVAL_MS;
    this._enabled = false;
    this._attached = false;
    this._accumulator = null;   // 現在の DwellAccumulator（null＝未 enter）。
    this._formingStart = null;  // 現在バーの formingStart。
    this._lastSnapMs = -1e9;    // 最後に snapshot を描画した時刻（throttle 基準）。
    // メニュー（controller）由来の forming param。buildFormingUrl が反映する bins/va のみ保持する
    //   （効かない飾り param は保持しない）。未設定（null）は forming 取得に載せない＝backend 既定。
    this._params = {};
  }

  isEnabled() {
    return this._enabled;
  }

  // メニュー（controller）から forming param（bins/va）を受け取り保持する。次回 enterBar の
  //   fetchForming 引数へ合流する（buildFormingUrl が &bins= / &va= へ反映＝effective）。
  //   未知/効かない param は buildFormingUrl が無視するため、そのまま保持しても副作用はない。
  setParams(params = {}) {
    this._params = { ...(params || {}) };
  }

  // トグル。ON: 初回のみ attach → 表示。OFF: 非表示（取得しない）。
  setEnabled(enabled) {
    this._enabled = !!enabled;
    if (this._enabled) {
      this._ensureAttached();
    }
    if (this._primitive && typeof this._primitive.setVisible === 'function') {
      this._primitive.setVisible(this._enabled);
    }
  }

  // バー入場（render が now=T で呼ぶ）。base=1・src=dwell・now=T で forming 取得 → accumulator を作り直し
  //   （rollover reset）→ base のみ描画。forming tick は畳まない（feedTick が育てる＝二重計上回避）。
  //   await で ready を保証する（直後の feedTick 取りこぼし防止）。無効時・取得失敗時・欠損時は前回保持（非破壊）。
  async enterBar(now) {
    if (!this._enabled || !this._formingClient || !this._makeAccumulator) {
      return;
    }
    // セッション窓 MP: base 累積下限 from=当日始まり=floor(now,86400)（UTC 日境界・秒）。これにより
    //   combined = [当日始まり, now) ＝古典的 Market Profile（1 日の TPO 形成）＝価格域が当日へ集中し
    //   forming の tick 成長が明瞭になる。now は因果 T をそのまま透過（未来リーク防止）。
    const from = Math.floor(now / 86400) * 86400;
    // setParams で保持した bins/va を forming 取得へ合流する（buildFormingUrl が反映＝effective）。
    //   src=dwell / base=1 / now=T / from（当日始まり）は不変（session窓・tick-live 挙動不変）。
    const forming = await this._formingClient.fetchForming({
      ...this._getContext(), ...this._params, src: 'dwell', base: 1, now, from,
    });
    if (!forming) {
      return; // null は前回描画を保持（非破壊）。
    }
    if (!_hasBaseFields(forming)) {
      return; // 空 profile（必須フィールド欠損）は前回描画を保持（NaN 混入を防ぐ）。
    }
    const acc = this._makeAccumulator();
    acc.init({
      baseFine: forming.baseFine,
      baseKmin: forming.baseKmin,
      activeTable: forming.activeTable,
      priceMin: forming.priceMin,
      priceMax: forming.priceMax,
      nBins: forming.nBins,
      gridW: forming.gridW,
      formingStart: forming.formingStart,
    });
    this._accumulator = acc;
    this._formingStart = forming.formingStart;
    this._lastSnapMs = this._now();
    this._draw(); // base のみ描画。
  }

  // ライブ tick（animateForming が 1 点ずつ呼ぶ）。addTick は常に反映（O(1)・HTTP無）、snapshot は throttle。
  feedTick(sec, mid) {
    if (!this._enabled || !this._accumulator) {
      return; // 停止/OFF/未 enter は no-op（addTick 停止＝停止で成長しない・MP OFF 無干渉）。
    }
    this._accumulator.addTick(sec, mid);
    const t = this._now();
    if (t - this._lastSnapMs >= this._throttleMs) {
      this._lastSnapMs = t;
      this._draw();
    }
  }

  // 確定時の最終 snapshot を強制描画する（throttle 無視）。
  settleTick() {
    if (!this._enabled || !this._accumulator) {
      return;
    }
    this._lastSnapMs = this._now();
    this._draw();
  }

  // 現在の accumulator の snapshot を primitive へ反映する（内部）。
  _draw() {
    if (this._primitive && typeof this._primitive.setProfile === 'function' && this._accumulator) {
      this._primitive.setProfile(this._accumulator.snapshot());
    }
  }

  // primitive を mainSeries へ一度だけ attach する（attachPrimitive 非提供時は skip＝後方互換）。
  _ensureAttached() {
    if (this._attached) {
      return;
    }
    if (this._mainSeries && typeof this._mainSeries.attachPrimitive === 'function') {
      this._mainSeries.attachPrimitive(this._primitive);
      this._attached = true;
    }
  }
}
