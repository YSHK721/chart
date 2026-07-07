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
    if (this._controller.isRecomputing()) {
      return;
    }
    await this._controller.recomputeAllApplied({ mode: 'latest' });
    const candles = await this._loadCandles(this._datasetRef, this._getTimeframe());
    // 価格の最新足反映。suppressPriceUpdate 時は skip（player が唯一の書き手＝巻き戻し防止）。
    if (candles && candles.length > 0 && !this._suppressPriceUpdate) {
      this._renderer.updateLastCandle(candles[candles.length - 1]);
    }
  }
}
