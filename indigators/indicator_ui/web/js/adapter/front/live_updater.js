// LiveUpdater（adapter/front/live_updater.js）— チャート 1 分間隔ライブ更新（served のみ）。
//
// 設計入力: ライブ更新仕様。served（B方式）で 60 秒ごとに「controller 経由の再計算 ＋
//   /candles 再取得 → 最新足を ChartRenderer.updateLastCandle で差分反映」する。
//
// 隔離・注入方針（DOM/ネット/タイマー非依存）:
//   - setInterval / clearInterval / loadCandles（/candles）/ getTimeframe は注入（テストで
//     フェイク化。実タイマー・実ネット・実 DOM に依存しない）。
//   - 競合ガードは controller.isRecomputing() の単一権威を参照（LiveUpdater は独自フラグを
//     持たない）。再計算中の tick はスキップする。
//   - series.update を呼ぶのは ChartRenderer のみ（renderer.updateLastCandle 経由・隔離維持）。

export class LiveUpdater {
  constructor({
    controller,
    renderer,
    loadCandles,
    datasetRef,
    getTimeframe,
    setInterval: setIntervalImpl = (typeof globalThis !== 'undefined' ? globalThis.setInterval : undefined),
    clearInterval: clearIntervalImpl = (typeof globalThis !== 'undefined' ? globalThis.clearInterval : undefined),
    intervalMs = 60000,
    // 価格の最新足更新（updateLastCandle）を抑止する（ISSUE-049）。LiveTickPlayer が価格の唯一の
    //   書き手になるときのみ composition root が true を渡す。既定 false＝従来挙動 byte 不変
    //   （再計算 recomputeAllApplied は抑止対象外・従来どおり実行する）。
    suppressPriceUpdate = false,
  }) {
    this._controller = controller;
    this._renderer = renderer;
    this._loadCandles = loadCandles;
    this._datasetRef = datasetRef;
    this._getTimeframe = getTimeframe;
    this._setInterval = setIntervalImpl;
    this._clearInterval = clearIntervalImpl;
    this._intervalMs = intervalMs;
    this._suppressPriceUpdate = suppressPriceUpdate;
    // 稼働中の interval ハンドル（null=停止中）。多重 start 防止の判定にも用いる。
    this._timerId = null;
    // バー確定検知の baseline（統一設計 2026-07-22）: 前回 tick の末尾バー time と時間足。
    //   末尾 time の前進＝新確定足 → full 再計算。時間足切替は baseline 取り直しのみ。
    this._lastBarTime = null;
    this._lastTf = null;
  }

  // ライブ更新を開始する。多重 start は無視する（稼働中なら二重に setInterval しない）。
  start() {
    if (this._timerId !== null) {
      return;
    }
    this._timerId = this._setInterval(() => this._tick(), this._intervalMs);
  }

  // ライブ更新を停止する（clearInterval）。冪等。
  stop() {
    if (this._timerId === null) {
      return;
    }
    this._clearInterval(this._timerId);
    this._timerId = null;
  }

  // 1 tick: 再計算中ならスキップ。そうでなければ再計算 → candles 再取得 → 最新足を反映。
  async _tick() {
    // ISSUE-151 追補: バー確定検知は isRecomputing に依らず毎 tick 必ず実行する。旧実装の
    //   「再計算中は tick 全体をスキップ」は、forming 再計算が高頻度な統一設計下で 60 秒 tick が
    //   連続被弾し検知が飢餓する欠陥だった。requestFullRecompute は coalesce/pending 必達のため
    //   再計算中に要求しても安全（保留→完了時ドレイン）。
    const candles = await this._loadCandles(this._datasetRef, this._getTimeframe());
    if (!candles || candles.length === 0) {
      return;
    }
    // 統一設計（2026-07-22）: 全再計算はバー確定時のみ（リプレイの毎バーその場計算と同一意味論）。
    //   新しい確定足の出現＝末尾バー time の前進を検知したときだけ full 再計算する。足内の指標
    //   追従は tick 粒度（LiveTickPlayer→requestFormingRecompute）が担う。時間足切替直後は
    //   baseline を取り直すだけ（切替側が既に全再計算済み＝二重再計算を避ける）。
    const tf = this._getTimeframe();
    const lastTime = candles[candles.length - 1].time;
    const tfChanged = this._lastTf !== tf;
    const newBar = !tfChanged && this._lastBarTime !== null && lastTime > this._lastBarTime;
    this._lastTf = tf;
    this._lastBarTime = lastTime;
    if (newBar) {
      // requestFullRecompute（coalesce/pending 付き・ISSUE-151）: 直接 await せず要求として積む。
      //   forming 実行中でも pending に保持され必ずドレインされる（取り落としによる帯系停止の防止）。
      //   本 60 秒経路は補完網で、主駆動はバー確定イベント（player onBarClose / forming period 前進）。
      this._controller.requestFullRecompute();
    }
    // 価格・欠落補完は再計算バッチ中を避ける（描画の混線防止。次 tick で回復＝遅延のみで喪失なし）。
    if (this._controller.isRecomputing()) {
      return;
    }
    // 欠落補完（ISSUE-106）: 休止中に確定足を取りこぼしていれば renderer が setData 全置換で
    //   再同期する。suppressPriceUpdate でも実施する（player は現在足のみの書き手であり、
    //   過去確定足の補完は抑止対象外。現在足の巻き戻し防止は renderer 側で保証）。
    const resynced = typeof this._renderer.resyncMissedCandles === 'function'
      ? this._renderer.resyncMissedCandles(candles)
      : false;
    // 価格の最新足反映。suppressPriceUpdate 時は skip（player が唯一の書き手＝巻き戻し防止）。
    //   再同期実施時は末尾も反映済みのため差分更新は不要。
    if (!resynced && !this._suppressPriceUpdate) {
      this._renderer.updateLastCandle(candles[candles.length - 1]);
    }
  }
}
